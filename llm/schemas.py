"""Schémas de sortie des tâches LLM (contrats opposables, versionnés)."""
from stats_catalogue.ops import OPS

INTENTION = {
    "type": "object", "additionalProperties": False,
    "required": ["type_etude", "confiance", "justification"],
    "properties": {
        "type_etude": {"type": "string", "enum": [
            "observationnelle_transversale", "cas_temoins",
            "cohorte_retrospective", "cohorte_prospective", "essai_randomise",
            "tolerance_cutanee", "innocuite_cosmetique", "test_usage_controle",
            "stabilite", "perception_utilisateur", "indetermine"]},
        "confiance": {"type": "number", "minimum": 0, "maximum": 1},
        "justification": {"type": "string", "minLength": 10},
        "endpoints_pressentis": {"type": "array", "items": {"type": "string"}},
        "contraintes": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
    },
}

ANALYSE_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "role", "op", "var"],
    "properties": {
        "id": {"type": "string", "pattern": "^A[0-9][A-Za-z0-9]*$"},
        "role": {"type": "string", "enum": ["primaire", "secondaire",
                                            "descriptif", "safety",
                                            "ajustement", "sensibilite"]},
        # rôle 'ajustement' (A3, SAP verrouillé) : modèle codé
        # (logistique si 'var_issue', Cox si 'var_temps_event' + 'var_evenement'),
        # exposition IMPOSÉE en 1re covariable, EPV ≥ 5 — revérifié aval
        "op": {"type": "string", "enum": sorted(OPS)},   # ← catalogue fermé
        "var": {"type": "string"},
        "par": {"type": "string"},
        "contraste": {"type": "array", "minItems": 2, "maxItems": 2,
                      "items": {"type": "string"}},
        "par_temps": {"type": "string"},
        "marge": {"type": "number", "minimum": 0},
        # sensibilité stabilité « passage au grand mail » (rôle sensibilite,
        # op tendance_fenetre_glissante) — paramètres pré-déclarés ; les
        # bornes strictes (> 0, horizon ≤ 12, seuil+direction ensemble ou
        # déduits des bornes_acceptation) sont revérifiées en aval par les
        # règles métier déterministes (biostat) puis par l'op elle-même
        "fenetre_mois": {"type": "number", "minimum": 0},
        "horizon_mois": {"type": "number", "minimum": 0, "maximum": 12},
        "spec_limite": {"type": "number"},
        "direction": {"type": "string", "enum": ["inferieur", "superieur"]},
        # ruban MNAR δ-ajusté (op tipping_point_mnar_smd) — grille en
        # σ-unités et groupe pénalisé, scénario pré-déclaré (E9(R1)) ;
        # 0.0 dans la grille, δ ≤ 5 σ et groupe ∈ contraste revérifiés aval
        "deltas": {"type": "array", "minItems": 1, "maxItems": 16,
                   "items": {"type": "number"}},
        "groupe_mnar": {"type": "string"},
        # ajustement multivarié pré-déclaré (A3) — catalogue borné, règles
        # métier revérifiées en aval : covariables EXACTEMENT celles de la
        # spec, epv_min ≥ 5, exposition imposée en 1re position
        "covariables": {"type": "array", "minItems": 1,
                        "items": {"type": "string"}},
        "epv_min": {"type": "number", "minimum": 5},
        # volet observationnel (cas-témoins / cohorte) — modalités binaires
        # 0/1 au MVP (documenté ; strings refusées par le contrat)
        "var_exposition": {"type": "string"},
        "var_issue": {"type": "string"},
        "var_evenement": {"type": "string"},
        "var_temps_event": {"type": "string"},
        "var_paire": {"type": "string"},
        "modalite": {"type": "integer"},
        "modalite_evenement": {"type": "integer"},
        "fallback": {"type": "object", "additionalProperties": False,
                     "required": ["si", "op"],
                     "properties": {
                         "si": {"type": "string", "enum": ["non_normal"]},
                         "op": {"type": "string", "enum": sorted(OPS)}}},
    },
}

PROPOSITION_SAP = {
    "type": "object", "additionalProperties": False,
    "required": ["analyses", "gestion_multiplicite", "gestion_manquants_strategie"],
    "properties": {
        "analyses": {"type": "array", "minItems": 1, "items": ANALYSE_ITEM},
        "gestion_multiplicite": {"type": "string", "minLength": 10},
        "gestion_manquants_strategie": {"type": "string", "minLength": 10},
        "justification_globale": {"type": "string"},
    },
}
