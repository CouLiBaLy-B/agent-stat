"""Dossiers de preuves des gates humains — la « vue » avant décision.

Un dossier rassemble, pour un humain habilité :
- l'artefact à valider (ref + SHA-256, statut, producteur, provenance) ;
- les scores DQ / RA / CC et la verbalisation de confiance ;
- la substance métier selon le gate (résumé SAP à G3, signaux à G4, objections
  à G5, décision proposée à G6) ;
- la liste des preuves traçables `art://…#sha256:…` ;
- les consignes FAIL-CLOSED (champs exigés, rôles recevables, aucune
  validation par défaut) et la commande de signature exacte.

Rien ici ne décide : le fichier produit est une AIDE à la décision humaine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.state import Etat
from core.store import Artefact, StoreArtefacts

DESCRIPTIONS_GATES = {
    "G1": "qualification du type d'étude (ambiguïté de compréhension du besoin)",
    "G3": "validation du SAP AVANT verrouillage (anti p-hacking : hash déposé)",
    "G4": "arbitrage sécurité (signaux tolérance / MoS / conformité sous réserve)",
    "G5": "arbitrage de la critique croisée (objections bloquantes > 2 allers-retours)",
    "G6": "validation finale du rapport avant export",
}

PIECES_MINIMALES = {
    "G1": ["intention"],
    "G3": ["sap", "dq_report"],
    "G4": ["safety_report ou compliance_report", "results_inferential"],
    "G5": ["critique", "rapport_draft"],
    "G6": ["rapport_draft", "critique", "results_inferential", "safety_report",
           "compliance_report"],
}

CHAMPS_EXIGES = ("statut (VALIDATED|REFUSED)", "validateur_id", "role",
                 "motif (≥ 10 caractères, justifiant la décision)",
                 "signature_ref", "pieces_consultees (liste non vide)")


def _extraire(store: StoreArtefacts, art: Artefact) -> dict:
    try:
        return store.lire_json(art.ref)
    except Exception:
        return {}


def _resume_gate(store: StoreArtefacts, etat: Etat, gate_id: str,
                 art: Artefact, payload: dict) -> dict:
    """Substance métier à examiner, selon le gate (best-effort)."""
    resume: dict = {}
    if gate_id == "G1":
        resume = {
            "type_etude_pressenti": payload.get("type_etude_pressenti"),
            "confiance": payload.get("confiance"),
            "justification": payload.get("justification"),
            "contradictions": payload.get("contradictions", []),
            "objectif": payload.get("objectif"),
        }
    elif gate_id == "G3":
        resume = {
            "endpoint_principal": payload.get("endpoint_principal"),
            "population": payload.get("population_analyse"),
            "analyses": [
                {"id": a.get("id"), "role": a.get("role"), "op": a.get("op"),
                 "var": a.get("var")}
                for a in payload.get("analyses", [])],
            "gestion_multiplicite": payload.get("gestion_multiplicite"),
            "gestion_manquants": payload.get("gestion_manquants"),
            "garde_fous_observationnel":
                payload.get("garde_fous_observationnel"),
            "mode_proposition": payload.get("mode_proposition"),
        }
        try:  # contexte données disponible à ce stade
            dq = store.lire_json(store.resoudre(
                "dq", etat.study_id, "dq_report").ref)
            resume["dq"] = {"score_dq": dq.get("score_dq"),
                            "sous_scores": dq.get("sous_scores"),
                            "nb_anomalies": len(dq.get("anomalies", []))}
        except Exception:
            pass
    elif gate_id == "G4":
        if art.type == "safety":
            resume = {"signaux": payload.get("signaux", []),
                      "mos_min": payload.get("mos_min"),
                      "grade_max_observe": payload.get("grade_max_observe"),
                      "zones_incertitude": payload.get("zones_incertitude")}
        else:  # compliance
            resume = {"verdict": payload.get("verdict"),
                      "regles_ko": payload.get("regles_ko"),
                      "regles_incertaines": payload.get("regles_incertaines"),
                      "couverture_inci": payload.get("couverture_inci")}
    elif gate_id == "G5":
        resume = {"verdict": payload.get("verdict"),
                  "nb_bloquantes": payload.get("nb_bloquantes"),
                  "objections": payload.get("objections", [])}
    elif gate_id == "G6":
        resume = {"decision_proposee": payload.get("decision_proposee"),
                  "conclusion_directionnelle":
                      payload.get("conclusion_directionnelle"),
                  "limites": payload.get("limites", [])}
        for type_, name, cle in (
                ("compliance", "compliance_report", "verdict"),
                ("safety", "safety_report", "signaux")):
            try:
                p = store.lire_json(store.resoudre(type_, etat.study_id, name).ref)
                resume[cle] = p.get(cle)
            except Exception:
                pass
    return {k: v for k, v in resume.items() if v is not None}


def construire_dossier(store: StoreArtefacts, etat: Etat, gate_id: str,
                       art: Artefact, sla_h: int,
                       roles_requis: list[str] | None = None) -> dict:
    roles = list(roles_requis or [])
    payload = _extraire(store, art)
    preuves = [f"{art.ref}#sha256:{art.sha256}"]
    for ref in getattr(art, "prov_used", []) or []:
        try:
            preuves.append(f"{ref}#sha256:{store.get(ref).sha256}")
        except Exception:
            preuves.append(f"{ref}#sha256:?")
    return {
        "gate_id": gate_id, "objet": DESCRIPTIONS_GATES.get(gate_id, ""),
        "sla_h": sla_h, "roles_requis": roles,
        "study_id": etat.study_id, "run_id": etat.run_id,
        "phase_pipeline": etat.phase, "statut_pipeline": etat.statut,
        "scores": dict(etat.scores),
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "artefact_a_valider": {
            "ref": art.ref, "sha256": art.sha256, "statut": art.statut,
            "producteur": art.producteur, "version": art.version,
            "prov_used": getattr(art, "prov_used", [])},
        "resume_metier": _resume_gate(store, etat, gate_id, art, payload),
        "preuves": preuves,
        "pieces_minimales_a_consulter": PIECES_MINIMALES.get(gate_id, []),
        "consignes_fail_closed": {
            "champs_exiges": list(CHAMPS_EXIGES),
            "statuts_recevables": ["VALIDATED", "REFUSED"],
            "avertissements": [
                "AUCUNE validation par défaut : l'absence de décision bloque "
                "le pipeline à l'expiration du SLA.",
                "Un REFUSED doit être motivé et déclenche l'escalade / le "
                "rejeu conformément aux règles du workflow.",
                "Vérifier le SHA-256 de l'artefact avant de signer : toute "
                "modification post-verrou invalide la décision.",
                "Cette signature engage le validateur identifié ; elle est "
                "chaînée au journal d'audit (preuve WORM).",
            ]},
    }


def rendre_markdown(dossier: dict) -> str:
    s = dossier["scores"]
    art = dossier["artefact_a_valider"]

    def _bloc(obj) -> str:
        return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) \
               + "\n```"

    lignes = [
        f"# Dossier de décision — Gate {dossier['gate_id']}",
        "",
        f"**Objet** : {dossier['objet']}",
        f"**Étude** : {dossier['study_id']} · **run** {dossier['run_id']} · "
        f"phase {dossier['phase_pipeline']} · statut {dossier['statut_pipeline']}",
        f"**SLA** : {dossier['sla_h']} h · **rôles recevables** : "
        + (", ".join(dossier["roles_requis"]) or "n/a"),
        "",
        "## Scores de fiabilité",
        f"- DQ (fiabilité données) = {s.get('fiabilite_donnees')}",
        f"- RA (robustesse analyse) = {s.get('robustesse_analyse')}",
        f"- CC (confiance conclusion) = {s.get('confiance_conclusion')} "
        f"(**{s.get('verbalisation_cc') or 'n/a'}**)",
        "",
        "## Artefact à valider (vérifier le hash avant de signer)",
        f"- **ref** : `{art['ref']}` · **sha256** : `{art['sha256']}`",
        f"- producteur : {art['producteur']} · statut : {art['statut']} · "
        f"version v{art['version']}",
        "",
        "## Substance à examiner",
        _bloc(dossier["resume_metier"]),
        "",
        "## Preuves traçables",
        *[f"- `{p}`" for p in dossier["preuves"]],
        "",
        "## Pièces minimales à consulter avant décision",
        *[f"- {p}" for p in dossier["pieces_minimales_a_consulter"]],
        "",
        "## Consignes FAIL-CLOSED",
        *[f"⚠ {a}" for a in dossier["consignes_fail_closed"]["avertissements"]],
        "",
        "Champs exigés de la décision :",
        *[f"- {c}" for c in dossier["consignes_fail_closed"]["champs_exiges"]],
        "",
        "## Comment signer",
        "```bash",
        f"python3 -m ui_gates.cli sign --racine <RUNTIME> \\",
        f"    --gate {dossier['gate_id']} --validateur u:<id> \\",
        f"    --role <role recevable> --decision VALIDATED|REFUSED \\",
        f"    --motif \"<justification ≥ 10 caractères>\" \\",
        f"    --pieces <piece1> <piece2>",
        "```",
        "Puis reprendre le pipeline bloqué :",
        "```bash",
        "python3 -m ui_gates.cli resume --racine <RUNTIME> \\",
        "    --etat <dernier_checkpoint.json> --donnees <donnees.json>",
        "```",
        "",
    ]
    return "\n".join(lignes)


def exporter(store: StoreArtefacts, racine_exports: str | Path, etat: Etat,
             gate_id: str, art: Artefact, sla_h: int,
             roles_requis: list[str] | None = None) -> tuple[Path, Path]:
    """Écrit le dossier (md + json) et retourne les chemins."""
    dossier = construire_dossier(store, etat, gate_id, art, sla_h, roles_requis)
    cible = Path(racine_exports)
    cible.mkdir(parents=True, exist_ok=True)
    p_md = cible / f"dossier_{gate_id}.md"
    p_json = cible / f"dossier_{gate_id}.json"
    p_md.write_text(rendre_markdown(dossier), encoding="utf-8")
    p_json.write_text(json.dumps(dossier, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    return p_md, p_json
