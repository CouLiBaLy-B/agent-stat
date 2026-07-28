"""Générateur déterministe du cas d'usage démo : test d'usage cosmétique.

60 sujets, 2 groupes (produit / contrôle), endpoint continu `delta_score`,
grades de réaction cutanée, composition INCI, méthodes alternatives déclarées.
Toutes les données sont SYNTHÉTIQUES (RNG seedé) — aucune donnée personnelle.
"""
from __future__ import annotations

import random


def generer(seed: int = 20260727, n_par_groupe: int = 30,
            manquants_endpoint: int = 0, age_desequilibre: float = 0.0) -> dict:
    rng = random.Random(seed)
    rows = []
    for i in range(2 * n_par_groupe):
        groupe = "produit" if i < n_par_groupe else "controle"
        mu = 12.0 if groupe == "produit" else 8.0
        rows.append({
            "sujet": f"S{i + 1:03d}",
            "groupe": groupe,
            "date_v1": "2026-03-02",
            "date_v2": "2026-03-30",
            "age": round(rng.gauss(35 + (age_desequilibre if groupe == "produit"
                                         else 0), 8), 1),
            "delta_score": round(rng.gauss(mu, 5), 2),
            "reaction_grade": 1 if rng.random() < 0.05 else 0,
        })
    for k in range(manquants_endpoint):           # scénarios de blocage G2
        rows[k]["delta_score"] = None

    return {
        "raw": {"metadata": {
            "objectif": ("Évaluation de la tolérance et de l'efficacité perçue "
                         "d'un produit cosmétique (crème) lors d'un test d'usage "
                         "sous contrôle dermatologique"),
            "design_indice": ("test d'usage sous contrôle dermatologique, "
                              "deux groupes parallèles, 4 semaines"),
            "endpoints": ["delta_score"], "pieces": ["protocole_v2.pdf"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "delta_score",
            "variable_groupe": "groupe", "contraste": ["produit", "controle"],
            "covariables_baseline": ["age"],
            "var_reaction": "reaction_grade", "seuil_grade_reaction": 2,
            "seuil_reactions_pct": 5.0,
            "population": "tous sujets avec mesure J28 du critère principal",
            "visite_ordre": ["date_v1", "date_v2"],
            "endpoints_secondaires": [],
            "variables": {
                "sujet":          {"type": "id"},
                "groupe":         {"type": "categorielle", "critique": True,
                                   "domaine": ["produit", "controle"]},
                "date_v1":        {"type": "date"},
                "date_v2":        {"type": "date"},
                "age":            {"type": "continue", "domaine": [18, 70]},
                "delta_score":    {"type": "continue", "critique": True,
                                   "domaine": [-10, 30]},
                "reaction_grade": {"type": "categorielle", "domaine": [0, 1, 2, 3]},
            }},
        "ingredients": [
            {"inci": "niacinamide", "noael_mg_kg_j": 200.0, "sed_mg_kg_j": 1.0},
            {"inci": "phenoxyethanol", "noael_mg_kg_j": 183.0, "sed_mg_kg_j": 0.88},
        ],
        "produit": {"application": "leave_on", "usage": "creme_visage",
                    "zone": "visage", "population_cible": "adulte"},
        "composition": [
            {"inci": "aqua", "concentration_pct": 72.0},
            {"inci": "glycerin", "concentration_pct": 8.0},
            {"inci": "niacinamide", "concentration_pct": 4.0},
            {"inci": "phenoxyethanol", "concentration_pct": 0.5},   # ≤ 1,0 % : OK
            {"inci": "dmdm hydantoin", "concentration_pct": 0.3},   # ≤ 0,6 % : OK
        ],
        "methodes_test": ["in vitro OCDE 439 (irritation cutanée, épiderme reconstruit)",
                          "test d'usage sous contrôle dermatologique"],
    }


DECISIONS_OK = {
    "G3": {"statut": "VALIDATED", "validateur_id": "u:bio-042",
           "role": "biostatisticien",
           "motif": "SAP conforme aux principes ICH E9, endpoint unique, "
                    "fallback pré-spécifié.",
           "signature_ref": "sig:2026-07-27:bio-042:g3",
           "pieces_consultees": ["sap", "dq_report"]},
    "G6": {"statut": "VALIDATED", "validateur_id": "u:dir-007",
           "role": "responsable_etude",
           "motif": "Résultats cohérents, limites documentées, aucun signal.",
           "signature_ref": "sig:2026-07-27:dir-007:g6",
           "pieces_consultees": ["rapport_draft", "critique", "safety_report",
                                 "compliance_report"]},
}
