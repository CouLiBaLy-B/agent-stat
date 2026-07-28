"""Générateurs déterministes du parcours OBSERVATIONNEL (domaine médical).

Cas 1 : étude cas-témoins appariée 1:1 — exposition binaire, OR attendu > 1.
Cas 2 : cohorte prospective avec temps d'événement — HR attendu ≈ 1,9.
Données 100 % SYNTHÉTIQUES (RNG seedé) — aucune donnée personnelle ni réelle.
"""
from __future__ import annotations

import math
import random


def _age(rng: random.Random, mu: float, sigma: float = 9.0) -> float:
    return round(min(80.0, max(19.0, rng.gauss(mu, sigma))), 1)


def generer_cas_temoins(seed: int = 20260727, n_paires: int = 180,
                        p_exp_cas: float = 0.50, p_exp_tem: float = 0.28) -> dict:
    """Cas-témoins appariée 1:1 (âge ± 3 ans par paire).

    Probabilités d'exposition ~ Bernoulli : cas p=0,50, témoins p=0,28
    ⇒ OR populationnel ≈ (0,50·0,72)/(0,50·0,28) ≈ 2,6, discordants ~90
    paires ⇒ signal McNemar très significatif (p attendue < 1e-3).
    """
    rng = random.Random(seed)
    rows = []
    for i in range(1, n_paires + 1):
        paire = f"P{i:03d}"
        rows.append({"sujet": f"{paire}-C", "paire": paire, "statut": "cas",
                     "exposition": 1 if rng.random() < p_exp_cas else 0,
                     "age": _age(rng, 45)})
        rows.append({"sujet": f"{paire}-T", "paire": paire, "statut": "temoins",
                     "exposition": 1 if rng.random() < p_exp_tem else 0,
                     "age": _age(rng, 46)})
    return {
        "raw": {"metadata": {
            "objectif": ("Étude cas-témoins appariée 1:1 (âge ± 3 ans) évaluant "
                         "l'association entre l'exposition professionnelle aux "
                         "vapeurs de solvants et la survenue d'une dermatite de "
                         "contact (registre dermato 2023-2026)"),
            "design_indice": "cas-témoins appariée, 180 paires",
            "endpoints": ["exposition"],
            "pieces": ["protocole_ct_v1.pdf", "registre_dermato_export.csv"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "exposition",
            "variable_groupe": "statut", "contraste": ["cas", "temoins"],
            "var_issue": "statut", "var_exposition": "exposition",
            "appariement": "1:1", "var_paire": "paire",
            "covariables_baseline": ["age"],
            "population": "180 paires appariées cas-témoins (âge ± 3 ans)",
            "visite_ordre": [], "endpoints_secondaires": [],
            "variables": {
                "sujet":      {"type": "id"},
                "paire":      {"type": "id"},
                "statut":     {"type": "categorielle", "critique": True,
                               "domaine": ["cas", "temoins"]},
                "exposition": {"type": "categorielle", "critique": True,
                               "domaine": [0, 1]},
                "age":        {"type": "continue", "domaine": [18, 90]},
            }},
        "produit": {}, "ingredients": [], "composition": [],
        "methodes_test": [],
    }


def generer_cohorte(seed: int = 20260727, n_par_groupe: int = 120,
                    hr: float = 1.9, suivi_max: float = 36.0) -> dict:
    """Cohorte prospective 2 bras d'exposition, temps d'événement en mois.

    Événements ~ Exp(λ) : λ_non_exposé calé pour P(événement à 36 m) ≈ 35 %,
    λ_exposé = λ·hr (HR populationnel exact = hr). Censure administrative à
    36 mois + sorties d'étude (15 %, uniformes) — non informatives.
    """
    rng = random.Random(seed)
    lam_non = -math.log(1.0 - 0.35) / suivi_max
    lam_exp = lam_non * hr
    rows = []
    for i in range(2 * n_par_groupe):
        expos = i < n_par_groupe
        lam = lam_exp if expos else lam_non
        t_event = rng.expovariate(lam)
        t_cens = (suivi_max if rng.random() > 0.15
                  else rng.uniform(3.0, suivi_max))
        t_obs, evt = min(t_event, t_cens), 1 if t_event <= t_cens else 0
        rows.append({
            "sujet": f"S{i + 1:03d}",
            "exposition": "expose" if expos else "non_expose",
            "evenement": evt, "temps_mois": round(t_obs, 2),
            "age": _age(rng, 52 if expos else 51, 10.0)})
    return {
        "raw": {"metadata": {
            "objectif": ("Cohorte prospective évaluant l'association entre "
                         "l'exposition professionnelle (poste avec vapeurs de "
                         "solvants) et la survenue d'un premier épisode "
                         "respiratoire — suivi de 36 mois"),
            "design_indice": ("cohorte prospective observationnelle, "
                              "2 groupes d'exposition, suivi 36 mois"),
            "endpoints": ["evenement"],
            "pieces": ["protocole_co_v2.pdf", "plan_suivi.pdf"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "evenement",
            "variable_groupe": "exposition", "contraste": ["expose", "non_expose"],
            "var_exposition": "exposition", "var_evenement": "evenement",
            "var_temps_event": "temps_mois", "modalite_evenement": 1,
            "covariables_baseline": ["age"],
            "population": ("240 sujets adultes, suivi 36 mois "
                           "(censure administrative au dernier contact)"),
            "visite_ordre": [], "endpoints_secondaires": [],
            "variables": {
                "sujet":      {"type": "id"},
                "exposition": {"type": "categorielle", "critique": True,
                               "domaine": ["expose", "non_expose"]},
                "evenement":  {"type": "categorielle", "critique": True,
                               "domaine": [0, 1]},
                "temps_mois": {"type": "continue", "domaine": [0, 48]},
                "age":        {"type": "continue", "domaine": [18, 90]},
            }},
        "produit": {}, "ingredients": [], "composition": [],
        "methodes_test": [],
    }
