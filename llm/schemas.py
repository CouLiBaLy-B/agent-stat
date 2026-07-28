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
                                            "descriptif", "safety"]},
        "op": {"type": "string", "enum": sorted(OPS)},   # ← catalogue fermé
        "var": {"type": "string"},
        "par": {"type": "string"},
        "contraste": {"type": "array", "minItems": 2, "maxItems": 2,
                      "items": {"type": "string"}},
        "par_temps": {"type": "string"},
        "marge": {"type": "number", "minimum": 0},
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
