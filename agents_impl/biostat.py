"""Agent Biostatistique — génération du SAP (plan d'analyse statistique).

MVP : gabarits déterministes par type d'étude, reconduisant uniquement des
opérations du catalogue. Phase 1 : rédaction LLM contrainte au catalogue +
contre-vérification schéma. Le SAP est ensuite verrouillé (hash) au gate G3.
"""
from __future__ import annotations

from agents_impl.base import Contexte, depot, sortie
from core.state import Etat


def _gabarit_usage_cosmetique(spec: dict, dq: dict) -> list[dict]:
    ep = spec["endpoint_principal"]
    groupe = spec.get("variable_groupe", "groupe")
    return [
        {"id": "A0a", "role": "descriptif", "op": "descriptif_continu",
         "var": ep, "par": groupe},
        {"id": "A1", "role": "primaire", "op": "t_test_welch", "var": ep,
         "par": groupe, "contraste": spec.get("contraste", ["produit", "controle"]),
         "hypotheses": ["normalite_par_groupe"],
         "fallback": {"si": "non_normal", "op": "mann_whitney"},
         "multiplicite": "primaire_unique"},
        {"id": "A2", "role": "safety", "op": "proportion_exacte",
         "var": spec.get("var_reaction", "reaction_grade"),
         "definition": f">= {spec.get('seuil_grade_reaction', 2)}",
         "par": groupe},
    ]


def _gabarit_observationnel(spec: dict, dq: dict) -> list[dict]:
    return _gabarit_usage_cosmetique(spec, dq)  # MVP : même trame, lexique contrôlé en aval


GABARITS = {
    "test_usage_controle": _gabarit_usage_cosmetique,
    "tolerance_cutanee": _gabarit_usage_cosmetique,
    "observationnelle_transversale": _gabarit_observationnel,
    "cas_temoins": _gabarit_observationnel,
}


def fabriquer(ctx: Contexte):
    ctx.producteur = "agent.biostat"

    def agent(etat: Etat, entrees: dict) -> dict:
        spec, dq = entrees["spec"], entrees["dq"]
        fabrique = GABARITS.get(etat.type_etude)
        if fabrique is None:
            from core.exceptions import ErreurLogique
            raise ErreurLogique(
                f"pas de gabarit SAP MVP pour {etat.type_etude!r} → escalade humaine")
        analyses = fabrique(spec, dq)
        sap = {
            "version_gabarit": "sap-1.0.0",
            "study_id": etat.study_id, "type_etude": etat.type_etude,
            "endpoint_principal": {
                "variable": spec["endpoint_principal"],
                "unique": True,
                "justification": "critère pré-spécifié au protocole"},
            "endpoints_secondaires": spec.get("endpoints_secondaires", []),
            "population_analyse": {
                "definition": spec.get("population",
                                       "tous sujets avec mesure du critère principal"),
                "exclusions": "règles pré-spécifiées uniquement (journal dédié)"},
            "analyses": analyses,
            "gestion_multiplicite": "gatekeeping : primaire d'abord ; Holm si secondaires confirmatoires ; exploratoire étiqueté (plafond CC 0,5)",
            "gestion_manquants": {
                "strategie": "cas complets si taux ≤ 5 % ; sinon MI requise (hors MVP → escalade)",
                "sensibilite": ["analyse sans outliers critiques documentés (agent anomalie)"]},
            "estimand": ({"strategie_evenements_intercurrents": "treatment_policy",
                          "cadre": "ICH E9(R1)"}
                         if etat.type_etude == "essai_randomise" else None),
            "decisions_puissance": {
                "note": "calcul de puissance a priori exigé à G3 si confirmatoire"},
            "pre_enregistrement": "hash du SAP déposé au verrou G3 avant tout calcul",
        }
        art = depot(ctx, etat.study_id, "sap", "sap", sap,
                    utilisant=[entrees.get("intention_ref"), entrees.get("dq_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=["gabarit déterministe conforme au catalogue d'ops",
                                   "les opérations hors catalogue auraient bloqué"],
                      sap=sap, sap_ref=art.ref, sap_sha256=art.sha256)
    return agent
