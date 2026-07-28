"""Générateur déterministe du cas d'usage stabilité (cosmétique).

4 échantillons × 6 points de mesure (t0 → 12 mois) sur pH et viscosité.
`derive_ph` pilote le scénario : nominal (dans les bornes) ou rupture.
Données SYNTHÉTIQUES (RNG seedé).
"""
from __future__ import annotations

import random

TEMPS = [0, 1, 3, 6, 9, 12]


def generer_stabilite(seed: int = 20260727, derive_ph: float = -0.018,
                      n_echantillons: int = 4) -> dict:
    rng = random.Random(seed)
    rows = []
    for e in range(1, n_echantillons + 1):
        biais_lot = rng.gauss(0, 0.02)
        for t in TEMPS:
            ph = 6.0 + biais_lot + derive_ph * t + rng.gauss(0, 0.03)
            visco = 12000 + rng.gauss(0, 150) - 18 * t + rng.gauss(0, 120)
            rows.append({"echantillon": f"E{e:02d}", "mois": t,
                         "date_mesure": f"2025-{min(12, 1 + t):02d}-15",
                         "ph": round(ph, 3), "viscosite": round(visco, 0)})
    return {
        "raw": {"metadata": {
            "objectif": ("Étude de stabilité accélérée et temps réel d'une crème "
                         "cosmétique : suivi pH et viscosité sur 12 mois, "
                         "vérification des bornes d'acceptation"),
            "design_indice": "étude de stabilité multi-temps",
            "endpoints": ["ph", "viscosite"], "pieces": ["protocole_stab_v1.pdf"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "echantillon", "endpoint_principal": "ph",
            "var_temps": "mois", "points_temps": TEMPS,
            "bornes_acceptation": {"ph": [5.0, 7.0],
                                   "viscosite": [9000, 13500]},
            "population": "4 échantillons, 6 points (T0→12 mois)",
            "visite_ordre": [],
            "endpoints_secondaires": ["viscosite"],
            "variables": {
                "echantillon": {"type": "id"},
                "mois":        {"type": "continue", "domaine": [0, 24]},
                "date_mesure": {"type": "date"},
                "ph":          {"type": "continue", "critique": True,
                                "domaine": [3.0, 9.0]},
                "viscosite":   {"type": "continue", "critique": True,
                                "domaine": [5000, 16000]},
            }},
        "produit": {"application": "leave_on", "usage": "creme_visage",
                    "zone": "visage", "population_cible": "adulte"},
        "ingredients": [],
        "composition": [
            {"inci": "aqua", "concentration_pct": 72.0},
            {"inci": "glycerin", "concentration_pct": 8.0},
            {"inci": "niacinamide", "concentration_pct": 4.0},
        ],
        "methodes_test": ["suivi physico-chimique multi-temps (pH, viscosité)"],
    }
