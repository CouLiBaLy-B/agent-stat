"""
Câblage du pipeline de bout en bout (MVP) — cf. ARCHITECTURE.md §C.1.

Ordre nominal :
  ingestion/qualification ─G1?→ [DQ ∥ biais ∥ EDA] ─G2→ SAP ─G3 (HUMAIN)→
  verrou SAP → hypothèses → contrôle verrou → calcul inférentiel → manquants →
  anomalies + safety ─G4?→ conformité ─G4/blocage?→ scores → rédaction →
  critique croisée (≤2 allers-retours ─G5?→) ─G6 (HUMAIN)→ export.

`donnees` (contrat d'entrée MVP) :
  raw           : {"metadata": {...}, "pieces": [...]}
  datasets      : {"rows": [ {var: valeur, ...}, ... ]}
  spec          : variables, endpoint_principal, variable_groupe, contraste,
                  covariables_baseline, var_reaction, seuil_grade_reaction, …
  ingredients   : [{"inci", "noael_mg_kg_j", "sed_mg_kg_j"}]   (cosmétique)
  composition   : [{"inci", "concentration_pct"}]
  methodes_test : ["in vitro OCDE 439", "test d'usage sous contrôle", …]
"""
from __future__ import annotations

from pathlib import Path

from agents_impl import analyses, biostat, comprehension, dataqualite, gardefous, redaction
from agents_impl.base import Contexte
from core.audit import JournalAudit
from core.bus import Bus
from core.exceptions import ErreurLogique
from core.gates import FichierDecisionsProvider, GestionnaireGates
from core.state import Etat, Phase
from core.store import StoreArtefacts
from orchestration import scores as moteur_scores
from orchestration.orchestrator import Orchestrateur

ROLES_GATES = {
    "G1": ["methodologiste"],
    "G3": ["biostatisticien"],
    "G4": ["safety_assessor", "toxicologue", "expert_reglementaire"],
    "G5": ["redacteur_scientifique_senior"],
    "G6": ["biostatisticien", "responsable_etude", "safety_assessor"],
}
ALLERS_RETOURS_MAX = 2


def construire_systeme(racine_runtime: str, decisions_path: str,
                       backoff_base_s: float = 0.0):
    """Assemble l'environnement d'exécution (MVP : fichiers locaux)."""
    racine = Path(racine_runtime)
    store = StoreArtefacts(racine / "store")
    audit = JournalAudit(racine / "audit.jsonl")
    bus = Bus()
    gates = GestionnaireGates(FichierDecisionsProvider(decisions_path),
                              audit, ROLES_GATES)
    ctx = Contexte(store=store, audit=audit,
                   checkpoints_dir=str(racine / "checkpoints"))
    registry = {
        "agent.comprehension": comprehension.fabriquer(ctx),
        "agent.dataqualite":   dataqualite.fabriquer(ctx),
        "agent.biostat":       biostat.fabriquer(ctx),
        "agent.eda":           analyses.fabriquer_eda(ctx),
        "agent.biais":         analyses.fabriquer_biais(ctx),
        "agent.hypotheses":    analyses.fabriquer_hypotheses(ctx),
        "agent.manquants":     analyses.fabriquer_manquants(ctx),
        "agent.anomalies":     analyses.fabriquer_anomalies(ctx),
        "agent.inferentiel":   analyses.fabriquer_inferentiel(ctx),
        "agent.securite":      gardefous.fabriquer_securite(ctx),
        "agent.conformite":    gardefous.fabriquer_conformite(ctx),
        "agent.relecture":     gardefous.fabriquer_relecture(ctx),
        "agent.redaction":     redaction.fabriquer_redaction(ctx),
        "agent.exporteur":     redaction.fabriquer_exporteur(
            ctx, str(racine / "exports")),
    }
    orch = Orchestrateur(store, bus, audit, gates, registry,
                         checkpoints_dir=ctx.checkpoints_dir,
                         backoff_base_s=backoff_base_s)
    return {"store": store, "audit": audit, "bus": bus,
            "orchestrateur": orch, "ctx": ctx, "racine": racine}


def run_pipeline(etat: Etat, sys_: dict, donnees: dict) -> Etat:
    o, store = sys_["orchestrateur"], sys_["store"]
    spec = donnees["spec"]

    # [1-2] Ingestion + qualification (G1 si confiance insuffisante) ----------
    etat.phase = Phase.QUALIFICATION_ETUDE.value
    intention = o.execute(etat, "ingestion_qualification",
                          "agent.comprehension", {"raw": donnees["raw"]})
    etat.type_etude = intention["type_etude"]
    etat.confiance_qualification = intention["confidence"]
    if intention.get("needs_human"):
        o.gate_humain(etat, "G1", intention["artefacts"][0], sla_h=48)

    # [3-4] fan-out : data quality ∥ biais ∥ EDA → jointure ; G2 ---------------
    etat.phase = Phase.CONTROLE_QUALITE_DONNEES.value
    dq, _biais, _eda = o.execute_parallel(etat, [
        ("data_quality", "agent.dataqualite",
         {"datasets": donnees["datasets"], "spec": spec}),
        ("biais", "agent.biais",
         {"datasets": donnees["datasets"], "spec": spec}),
        ("eda", "agent.eda",
         {"datasets": donnees["datasets"], "spec": spec}),
    ])
    o.gate_g2_data_quality(etat, dq)
    dq_content = store.lire_json(dq["dq_ref"])

    # [5-6] SAP → G3 HUMAIN → verrou SHA-256 ----------------------------------
    etat.phase = Phase.PLAN_ANALYSE.value
    sap = o.execute(etat, "plan_analyse", "agent.biostat",
                    {"spec": spec, "dq": dq_content,
                     "intention_ref": intention["artefacts"][0].ref,
                     "dq_ref": dq["dq_ref"]})
    o.gate_humain(etat, "G3", sap["artefacts"][0], sla_h=72)
    etat.verrous["sap_sha256"] = sap["sap_sha256"]

    # [7] hypothèses → contrôle du verrou → calcul inférentiel -----------------
    etat.phase = Phase.CALCUL_STATISTIQUE.value
    hypo = o.execute(etat, "controle_hypotheses", "agent.hypotheses",
                     {"sap": sap["sap"], "spec": spec,
                      "datasets": donnees["datasets"], "sap_ref": sap["sap_ref"]})
    art_sap_courant = store.resoudre("sap", etat.study_id, "sap")
    o.verifier_verrou_sap(etat, art_sap_courant.sha256)
    resultats = o.execute(etat, "calcul_inferentiel", "agent.inferentiel",
                          {"sap": sap["sap"], "spec": spec,
                           "datasets": donnees["datasets"],
                           "verdicts_hypotheses": hypo["verdicts"],
                           "sap_ref": sap["sap_ref"],
                           "assumptions_ref": hypo["assumptions_ref"]})

    # [8] manquants -------------------------------------------------------------
    etat.phase = Phase.GESTION_MANQUANTS.value
    manq = o.execute(etat, "gestion_manquants", "agent.manquants",
                     {"sap": sap["sap"], "spec": spec,
                      "datasets": donnees["datasets"], "sap_ref": sap["sap_ref"]})

    # [9-10] anomalies + safety (G4 conditionnel) + conformité ------------------
    etat.phase = Phase.EVALUATION_SECURITE_TOLERANCE.value
    anom = o.execute(etat, "detection_anomalies", "agent.anomalies",
                     {"dq": dq_content, "datasets": donnees["datasets"],
                      "dq_ref": dq["dq_ref"]})
    safety = o.execute(
        etat, "evaluation_securite", "agent.securite",
        {"datasets": donnees["datasets"], "spec": spec,
         "ingredients": donnees.get("ingredients", []),
         "results_ref": resultats["results_ref"]},
        gate_apres=lambda et, s: o.gate_g4_safety(et, s, s["artefacts"][0]))
    conf = o.execute(
        etat, "conformite", "agent.conformite",
        {"composition": donnees.get("composition", []),
         "methodes_test": donnees.get("methodes_test", []),
         "spec": spec, "dq": dq_content, "dq_ref": dq["dq_ref"]},
        gate_apres=lambda et, s: o.gate_conformite(et, s, s["artefacts"][0]))

    # scores RA/CC ---------------------------------------------------------------
    dq_score = etat.scores["fiabilite_donnees"]
    interpretables = all(
        ana.get("resultat", ana).get("interpretable", True)
        if "par_groupe" not in ana
        else all(r.get("interpretable", False) for r in ana["par_groupe"].values())
        for ana in resultats["resultats"].values())
    ra = moteur_scores.calculer_ra(
        hypotheses_ok=1.0,
        concordance_sensibilite=1.0 if anom["anomalies_ref"] and not anom.get(
            "suspicion_manipulation") else 0.7,
        diagnostics=1.0 if interpretables else 0.6,
        multiplicite_ok=True,
        validation_interne=0.5)                  # MVP — déclaré en limite
    cc = moteur_scores.calculer_cc(dq=dq_score, ra=ra, pre_enregistre=True)
    etat.scores.update({"robustesse_analyse": ra, "confiance_conclusion": cc,
                        "verbalisation_cc": moteur_scores.verbaliser_cc(cc)})

    # [11] rédaction --------------------------------------------------------------
    etat.phase = Phase.REDACTION_RAPPORT.value
    actions = []
    if _biais.get("biais_critiques"):
        actions.append("Documenter les déséquilibres de baseline en limite "
                       "et envisager une sensibilité ajustée.")
    entrees_redac = {
        "sap": sap["sap"], "resultats": resultats,
        "dq": dq_content, "safety": safety, "conformite": conf,
        "scores": etat.scores, "spec": spec, "actions": actions,
        "dq_assumptions": dq["assumptions"],
        "analyse_assumptions": resultats["assumptions"] + manq["assumptions"],
        "sap_art": store.get(sap["sap_ref"]),
        "results_art": store.get(resultats["results_ref"]),
        "dq_art": store.get(dq["dq_ref"]),
        "safety_art": store.get(safety["safety_ref"]),
        "compliance_art": store.get(conf["compliance_ref"])}
    draft = o.execute(etat, "redaction", "agent.redaction", entrees_redac)

    # [12] critique croisée — arbitrage borné à 2 allers-retours -------------------
    etat.phase = Phase.CRITIQUE_CROISEE.value
    for tour in range(ALLERS_RETOURS_MAX + 1):
        critique = o.execute(etat, f"critique_{tour}", "agent.relecture",
                             {"rapport": draft["rapport"], "resultats": resultats,
                              "scores": etat.scores,
                              "report_ref": draft["report_ref"],
                              "results_ref": resultats["results_ref"]})
        if not critique["objections_bloquantes"]:
            break
        if tour == ALLERS_RETOURS_MAX:
            o.gate_humain(etat, "G5", critique["artefacts"][0], sla_h=72)
            break
        o.audit.log("orchestrateur", "ARBITRAGE_REDACTION",
                    {"tour": tour, "objections": [
                        ob["id"] for ob in critique["objections_bloquantes"]]})
        draft = o.execute(etat, f"redaction_reprise_{tour}", "agent.redaction",
                          {**entrees_redac, "objections": critique["objections"]})

    # [13] validation finale G6 (HUMAIN) → [14] export -------------------------------
    etat.phase = Phase.VALIDATION_HUMAINE.value
    o.gate_humain(etat, "G6", draft["artefacts"][0], sla_h=120)
    etat.phase = Phase.EXPORT.value
    export = o.execute(etat, "export", "agent.exporteur",
                       {"rapport": draft["rapport"]})
    etat.phase, etat.statut = Phase.TERMINE.value, "TERMINE"
    o.audit.log("orchestrateur", "PIPELINE_TERMINE",
                {"export": export["export_chemin"], "scores": etat.scores})
    return etat
