"""Agent Biostatistique — rédaction du SAP (plan d'analyse statistique).

Deux chemins, MÊME artefact verrouillable :
- `llm` : le provider propose les analyses via schéma contraint dont l'enum `op`
  EST le catalogue fermé ; l'agent revérifie ensuite les RÈGLES MÉTIER de façon
  déterministe (une seule primaire, variables existantes, fallback obligatoire
  pour les tests paramétriques). Toute violation ⇒ repli gabarit journalisé ;
- `deterministe` (défaut) : gabarits par type d'étude.

Le LLM ne choisit JAMAIS une méthode hors catalogue et ne CALCULE rien.
"""
from __future__ import annotations

from agents_impl.base import Contexte, depot, sortie
from core.exceptions import ErreurLogique
from core.state import Etat
from llm import prompts, schemas
from llm.exceptions import ErreurLLM
from llm.generation import generer_contraint
from stats_catalogue.ops import OPS


def _gabarit_usage_cosmetique(spec: dict, dq: dict) -> list[dict]:
    ep = spec["endpoint_principal"]
    groupe = spec.get("variable_groupe", "groupe")
    contraste = spec.get("contraste", ["produit", "controle"])
    marge = spec.get("marge_equivalence")
    if marge:   # objectif d'équivalence : TOST, marge Δ pré-définie (ICH E9)
        a1 = {"id": "A1", "role": "primaire", "op": "tost_equivalence",
              "var": ep, "par": groupe, "contraste": contraste,
              "marge": float(marge),
              "hypotheses": ["normalite_par_groupe"],
              "note": "pas de fallback rangs en MVP — échec de précondition "
                      "= déviation G3"}
    else:
        a1 = {"id": "A1", "role": "primaire", "op": "t_test_welch", "var": ep,
              "par": groupe, "contraste": contraste,
              "hypotheses": ["normalite_par_groupe"],
              "fallback": {"si": "non_normal", "op": "mann_whitney"}}
    return [
        {"id": "A0a", "role": "descriptif", "op": "descriptif_continu",
         "var": ep, "par": groupe},
        a1,
        {"id": "A2", "role": "safety", "op": "proportion_exacte",
         "var": spec.get("var_reaction", "reaction_grade"),
         "definition": f">= {spec.get('seuil_grade_reaction', 2)}",
         "par": groupe},
    ]


def _gabarit_observationnel(spec: dict, dq: dict) -> list[dict]:
    return _gabarit_usage_cosmetique(spec, dq)  # MVP : même trame, lexique contrôlé en aval


def _gabarit_cas_temoins(spec: dict, dq: dict) -> list[dict]:
    """Cas-témoins : primaire = OR (apparié conditionnel si appariement 1:1).
    Aucun ajustement multivarié au MVP — le lexique d'association est imposé
    par le SAP (garde_fous_observationnel) et contrôlé à la relecture."""
    expo, issue = spec.get("var_exposition"), spec.get("var_issue")
    if not expo or not issue:
        raise ErreurLogique("cas-témoins : 'var_exposition' et 'var_issue' "
                            "(statut cas/témoin) requis dans la spec")
    analyses = [{"id": "A0a", "role": "descriptif", "op": "proportion_wilson",
                 "var": expo, "par": issue, "modalite": 1,
                 "note": "prévalence d'exposition par statut (descriptif)"}]
    if spec.get("appariement"):
        paire = spec.get("var_paire")
        if not paire:
            raise ErreurLogique("cas-témoins appariée : 'var_paire' requis "
                                "(appariement 1:1 déclaré)")
        analyses.append({"id": "A1", "role": "primaire", "op": "or_apparie",
                         "var": expo, "var_exposition": expo,
                         "var_issue": issue, "var_paire": paire,
                         "note": "OR conditionnel des paires discordantes "
                                 "(principe McNemar)"})
    else:
        analyses.append({"id": "A1", "role": "primaire",
                         "op": "odds_ratio_cas_temoins", "var": expo,
                         "var_exposition": expo, "var_issue": issue,
                         "note": "OR de Woolf + p exacte de Fisher"})
    return analyses


def _gabarit_cohorte(spec: dict, dq: dict) -> list[dict]:
    """Cohorte : primaire = HR/log-rang si temps d'événement, sinon RR.
    Le RR de fin de suivi est conservé en secondaire descriptif."""
    expo, ev = spec.get("var_exposition"), spec.get("var_evenement")
    if not expo or not ev:
        raise ErreurLogique("cohorte : 'var_exposition' et 'var_evenement' "
                            "requis dans la spec")
    groupe = spec.get("variable_groupe", expo)
    contraste = spec.get("contraste", ["expose", "non_expose"])
    analyses = [{"id": "A0a", "role": "descriptif", "op": "proportion_wilson",
                 "var": ev, "par": groupe, "modalite": 1,
                 "note": "incidence brute de l'événement par bras (descriptif)"}]
    if spec.get("var_temps_event"):
        analyses.append({"id": "A1", "role": "primaire", "op": "km_logrank_hr",
                         "var": ev, "par": groupe, "contraste": contraste,
                         "var_evenement": ev,
                         "var_temps_event": spec["var_temps_event"],
                         "note": "log-rang + HR (Peto) — risques relatifs "
                                 "constants SUPPOSÉS, à confirmer au G3"})
        analyses.append({"id": "A2", "role": "secondaire",
                         "op": "risque_relatif_cohorte", "var": ev,
                         "par": groupe, "contraste": contraste,
                         "var_evenement": ev,
                         "note": "RR de fin de suivi (descriptif, non ajusté)"})
    else:
        analyses.append({"id": "A1", "role": "primaire",
                         "op": "risque_relatif_cohorte", "var": ev,
                         "par": groupe, "contraste": contraste,
                         "var_evenement": ev})
    return analyses


def _gabarit_stabilite(spec: dict, dq: dict) -> list[dict]:
    bornes = spec.get("bornes_acceptation", {})
    if not bornes or not spec.get("var_temps"):
        raise ErreurLogique("stabilité : 'bornes_acceptation' et 'var_temps' "
                            "requis dans la spec")
    ep = spec["endpoint_principal"]
    return [{"id": f"ST-{var}", "role": "primaire" if var == ep else "secondaire",
             "op": "tendance_lineaire", "var": var,
             "par_temps": spec["var_temps"],
             "bornes": bornes[var]} for var in bornes]


GABARITS = {
    "test_usage_controle": _gabarit_usage_cosmetique,
    "tolerance_cutanee": _gabarit_usage_cosmetique,
    "observationnelle_transversale": _gabarit_observationnel,
    "cas_temoins": _gabarit_cas_temoins,
    "cohorte_prospective": _gabarit_cohorte,
    "cohorte_retrospective": _gabarit_cohorte,
    "stabilite": _gabarit_stabilite,
}

OPS_PARAMETRIQUES = {"t_test_welch"}

OPS_ASSOCIATION = {"odds_ratio_cas_temoins", "or_apparie",
                   "risque_relatif_cohorte", "km_logrank_hr"}

# Clés métier exigées par op d'association (contre-vérification déterministe)
CLES_METIER_ASSOCIATION = {
    "odds_ratio_cas_temoins": ["var_exposition", "var_issue"],
    "or_apparie": ["var_exposition", "var_issue", "var_paire"],
    "risque_relatif_cohorte": ["var_evenement"],
    "km_logrank_hr": ["var_evenement", "var_temps_event"],
}


def _verifier_regles_metier(analyses: list[dict], spec: dict) -> None:
    """Contre-vérification déterministe de toute proposition LLM."""
    if sum(1 for a in analyses if a.get("role") == "primaire") != 1:
        raise ErreurLogique("exactement UNE analyse primaire exigée")
    variables = set(spec.get("variables", {}))
    for a in analyses:
        if a["op"] not in OPS:
            raise ErreurLogique(f"{a['id']} : op hors catalogue")
        if a["var"] not in variables:
            raise ErreurLogique(f"{a['id']} : variable {a['var']!r} absente de la spec")
        if a["op"] in OPS_PARAMETRIQUES and not a.get("fallback"):
            raise ErreurLogique(f"{a['id']} : test paramétrique sans fallback "
                                "pré-spécifié")
        if a.get("fallback") and a["fallback"]["op"] not in OPS:
            raise ErreurLogique(f"{a['id']} : fallback hors catalogue")
        for cle in CLES_METIER_ASSOCIATION.get(a["op"], []):
            if not a.get(cle):
                raise ErreurLogique(f"{a['id']} : {a['op']} exige {cle!r}")
            if a[cle] not in variables:
                raise ErreurLogique(f"{a['id']} : {cle}={a[cle]!r} absente "
                                    "de la spec")


def _defauts(analyses: list[dict], spec: dict) -> list[dict]:
    groupe = spec.get("variable_groupe", "groupe")
    for a in analyses:
        a.setdefault("par", groupe)
        if a["op"] in OPS_PARAMETRIQUES or a["op"] == "mann_whitney":
            a.setdefault("contraste", spec.get("contraste",
                                               ["produit", "controle"]))
    return analyses


def fabriquer(ctx: Contexte, llm=None):
    ctx.producteur = "agent.biostat"

    def agent(etat: Etat, entrees: dict) -> dict:
        spec, dq = entrees["spec"], entrees["dq"]
        fabrique = GABARITS.get(etat.type_etude)
        if fabrique is None:
            raise ErreurLogique(
                f"pas de gabarit SAP MVP pour {etat.type_etude!r} → escalade humaine")

        analyses = fabrique(spec, dq)
        multiplicite = ("gatekeeping : primaire d'abord ; Holm si secondaires "
                        "confirmatoires ; exploratoire étiqueté (plafond CC 0,5)")
        strat_manquants = ("cas complets si taux ≤ 5 % ; sinon MI requise "
                           "(hors MVP → escalade)")
        mode, assumptions = "gabarit_deterministe", [
            "gabarit déterministe conforme au catalogue d'ops"]

        if llm is not None:
            try:
                dq_resume = {"score_dq": dq.get("score_dq"),
                             "n": dq.get("n_lignes")}
                obj, meta_gen = generer_contraint(
                    llm, tache="proposition_sap",
                    systeme=prompts.systeme_biostat(),
                    utilisateur=prompts.utilisateur_biostat(
                        etat.type_etude, spec, dq_resume),
                    schema=schemas.PROPOSITION_SAP)
                propo = _defauts(list(obj["analyses"]), spec)
                _verifier_regles_metier(propo, spec)      # contre-vérification
                analyses = propo
                multiplicite = obj["gestion_multiplicite"]
                strat_manquants = obj["gestion_manquants_strategie"]
                mode = "llm"
                assumptions = ["analyses proposées par LLM, revalidées "
                               "mécaniquement (catalogue + règles métier)"]
                ctx.audit.log("llm", "GENERATION", meta_gen)
            except (ErreurLLM, ErreurLogique) as e:
                assumptions.append(f"proposition LLM rejetée ({e}) — repli "
                                   "gabarit déterministe")
                ctx.audit.log("agent.biostat", "REPLI_LLM", {"erreur": str(e)[:300]})

        sap = {
            "version_gabarit": "sap-1.0.0", "mode_proposition": mode,
            "study_id": etat.study_id, "type_etude": etat.type_etude,
            "endpoint_principal": {
                "variable": spec["endpoint_principal"], "unique": True,
                "justification": "critère pré-spécifié au protocole"},
            "endpoints_secondaires": spec.get("endpoints_secondaires", []),
            "population_analyse": {
                "definition": spec.get(
                    "population",
                    "tous sujets avec mesure du critère principal"),
                "exclusions": "règles pré-spécifiées uniquement (journal dédié)"},
            "analyses": analyses,
            "gestion_multiplicite": multiplicite,
            "gestion_manquants": {"strategie": strat_manquants,
                                  "sensibilite": ["analyse sans outliers "
                                                  "critiques documentés"]},
            "estimand": ({"strategie_evenements_intercurrents": "treatment_policy",
                          "cadre": "ICH E9(R1)"}
                         if etat.type_etude == "essai_randomise" else None),
            "decisions_puissance": {
                "note": "calcul de puissance a priori exigé à G3 si confirmatoire"},
            "pre_enregistrement": "hash du SAP déposé au verrou G3 avant tout calcul",
        }
        if etat.type_etude in ("cas_temoins", "cohorte_prospective",
                               "cohorte_retrospective"):
            sap["garde_fous_observationnel"] = {
                "lexique": ("association ≠ causalité — vocabulaire causal "
                            "interdit au rapport (contrôlé par la relecture)"),
                "ajustement": ("analyse non ajustée (univariée) en MVP : toute "
                               "ampleur rapportée reste exploratoire ; "
                               "l'ajustement multivarié est une décision "
                               "humaine (G3)"),
                "biais": ("confusion non mesurée, biais de sélection et "
                          "d'information à documenter en section limites"),
                "reference": "STROBE — reporting des études observationnelles",
            }
        art = depot(ctx, etat.study_id, "sap", "sap", sap,
                    utilisant=[entrees.get("intention_ref"),
                               entrees.get("dq_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=assumptions,
                      sap=sap, sap_ref=art.ref, sap_sha256=art.sha256)
    return agent
