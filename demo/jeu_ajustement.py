"""Générateurs déterministes du parcours AJUSTEMENT MULTIVARIÉ (domaine médical).

Deux cohortes SYNTHÉTIQUES à confusion d'âge volontaire :
- exposition plus fréquente chez les sujets âgés (et fumeurs) ;
- risque d'événement augmenté par l'âge / le tabac, DIMINUÉ par l'exposition ;
- ⇒ la mesure BRUTE (HR/RR univarié) est diluée vers le nul, voire renversée ;
  la mesure AJUSTÉE (Cox / logistique — A3 pré-déclarée au SAP) retrouve
  l'association protectrice causale-agnostique de génération.

Données 100 % synthétiques (RNG seedé) — aucune donnée personnelle ni réelle.
"""
from __future__ import annotations

import math
import random


def _sig(v: float) -> float:
    return 1.0 / (1.0 + math.exp(-v))


def generer_cohorte_cox(seed: int = 20260729, n: int = 300) -> dict:
    """Cohorte prospective avec temps d'événement → A3 = cox_ph.

    Vrais coefficients de génération : âge +0,045/an, fumeur +0,55,
    exposition −0,70 (HR ≈ 0,50 protecteur). Brut attendu dilué (HR ≈ 0,8-1).
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        age = round(min(85.0, max(25.0, rng.gauss(57.0, 13.0))), 1)
        fumeur = 1 if rng.random() < _sig(-1.6 - 0.03 * (age - 55.0)) else 0
        exposition = 1 if rng.random() < _sig(
            -1.3 + 0.09 * (age - 55.0) + 0.55 * fumeur) else 0
        lam = 0.055 * math.exp(0.045 * (age - 55.0) + 0.55 * fumeur
                               - 0.70 * exposition)
        t_evt = rng.expovariate(lam)
        cens = rng.uniform(20.0, 30.0) if rng.random() < 0.25 else 24.0
        temps = min(t_evt, cens)
        rows.append({
            "sujet": f"S{i+1:03d}", "age": age, "fumeur": fumeur,
            "exposition": "expose" if exposition else "non_expose",
            "evenement": 1 if t_evt <= temps else 0,
            "temps_mois": round(max(0.5, temps), 1)})
    return {
        "raw": {"metadata": {
            "objectif": ("Cohorte prospective évaluant l'association entre "
                         "l'exposition professionnelle et la survenue d'un "
                         "événement respiratoire (suivi 24 mois), avec "
                         "ajustement multivarié PRÉ-DÉCLARÉ (âge, tabac)"),
            "design_indice": "cohorte prospective 300 sujets, ajustement Cox",
            "endpoints": ["evenement"],
            "pieces": ["protocole_co_v2.pdf", "suivi_export.csv"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "evenement",
            "variable_groupe": "exposition",
            "contraste": ["expose", "non_expose"],
            "var_exposition": "exposition", "var_evenement": "evenement",
            "var_temps_event": "temps_mois",
            "covariables_baseline": ["age", "fumeur"],
            "ajustement_multivarie": {"covariables": ["age", "fumeur"],
                                      "epv_min": 10},
            "population": "300 sujets exposés ou non (registre 2026)",
            "visite_ordre": [], "endpoints_secondaires": [],
            "variables": {
                "sujet":      {"type": "id"},
                "exposition": {"type": "categorielle", "critique": True,
                               "domaine": ["expose", "non_expose"]},
                "age":        {"type": "continue", "domaine": [18, 90]},
                "fumeur":     {"type": "categorielle", "domaine": [0, 1]},
                "evenement":  {"type": "categorielle", "critique": True,
                               "domaine": [0, 1]},
                "temps_mois": {"type": "continue", "domaine": [0, 36]},
            }},
        "produit": {}, "ingredients": [], "composition": [],
        "methodes_test": [],
    }


def generer_cohorte_logistique(seed: int = 20260730, n: int = 380) -> dict:
    """Cohorte rétrospective SANS temps d'événement → A3 = logistique.

    Incidence binaire à 12 mois ; vrais β : âge +0,05/an, exposition −1,4
    (OR ≈ 0,25 protecteur dilué vers OR brut ≈ 0,6 par la confusion d'âge).
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        age = round(min(85.0, max(25.0, rng.gauss(56.0, 12.0))), 1)
        exposition = 1 if rng.random() < _sig(-1.05 + 0.085 * (age - 55.0)) \
            else 0
        risque = _sig(-1.1 + 0.05 * (age - 55.0) - 1.4 * exposition)
        rows.append({
            "sujet": f"R{i+1:03d}", "age": age,
            "exposition": "expose" if exposition else "non_expose",
            "evenement": 1 if rng.random() < risque else 0})
    return {
        "raw": {"metadata": {
            "objectif": ("Cohorte rétrospective évaluant l'association entre "
                         "l'exposition et l'incidence d'un événement "
                         "dermatologique à 12 mois, ajustement logistique "
                         "PRÉ-DÉCLARÉ (âge)"),
            "design_indice": "cohorte rétrospective 380 sujets",
            "endpoints": ["evenement"],
            "pieces": ["protocole_cr_v1.pdf", "registre_extract.csv"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "evenement",
            "variable_groupe": "exposition",
            "contraste": ["expose", "non_expose"],
            "var_exposition": "exposition", "var_evenement": "evenement",
            "covariables_baseline": ["age"],
            "ajustement_multivarie": {"covariables": ["age"], "epv_min": 10},
            "population": "380 dossiers rétrospectifs (2019-2025)",
            "visite_ordre": [], "endpoints_secondaires": [],
            "variables": {
                "sujet":      {"type": "id"},
                "exposition": {"type": "categorielle", "critique": True,
                               "domaine": ["expose", "non_expose"]},
                "age":        {"type": "continue", "domaine": [18, 90]},
                "evenement":  {"type": "categorielle", "critique": True,
                               "domaine": [0, 1]},
            }},
        "produit": {}, "ingredients": [], "composition": [],
        "methodes_test": [],
    }
