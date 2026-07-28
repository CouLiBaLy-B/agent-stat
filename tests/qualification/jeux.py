"""Jeux de données de qualification — déterministes purs (stdlib).

Partagés entre le générateur d'oracle (`scripts/qualifier_scipy.py`,
nécessite scipy — outil développeur, JAMAIS exécuté en CI/runtime) et les
tests de qualification (`tests/qualification/test_qualification.py`,
stdlib pur). Toute modification ici change les données ⇒ régénérer la
référence avec le script (cf. docs/QUALIFICATION_SCIPY.md).
"""
from __future__ import annotations

import random

SEED = 20260728


def _gauss_jeu(seed: int, n: int, mu: float, sigma: float,
               arrondi: int | None = None) -> list[float]:
    rng = random.Random(seed)
    xs = [rng.gauss(mu, sigma) for _ in range(n)]
    if arrondi is not None:
        xs = [round(x, arrondi) for x in xs]      # arrondi ⇒ ex æquo
    return xs


def _gamma_jeu(seed: int, n: int, alpha: float, beta: float) -> list[float]:
    rng = random.Random(seed)
    return [rng.gammavariate(alpha, beta) for _ in range(n)]


def jeux() -> dict:
    return {
        # --- comparaisons de moyennes ---------------------------------------
        "welch_egal": {
            "g1": _gauss_jeu(SEED + 1, 30, 12.0, 5.0),
            "g2": _gauss_jeu(SEED + 2, 30, 8.0, 5.0)},
        "welch_inegal": {                       # variances très inégales, petits n
            "g1": _gauss_jeu(SEED + 3, 9, 3.0, 1.0),
            "g2": _gauss_jeu(SEED + 4, 14, 4.5, 4.0)},
        "tost": {
            "g1": _gauss_jeu(SEED + 5, 40, 10.0, 3.0),
            "g2": _gauss_jeu(SEED + 6, 40, 10.4, 3.0),
            "marge": 2.0},
        # --- non paramétrique -------------------------------------------------
        "mw_ties": {                            # arrondi ⇒ nombreux ex æquo
            "g1": _gauss_jeu(SEED + 7, 25, 5.0, 2.0, arrondi=0),
            "g2": _gauss_jeu(SEED + 8, 28, 4.2, 2.0, arrondi=0)},
        "mw_continu": {
            "g1": _gauss_jeu(SEED + 9, 32, 5.0, 2.0),
            "g2": _gauss_jeu(SEED + 10, 31, 4.0, 2.0)},
        "kruskal": {
            "a": _gauss_jeu(SEED + 11, 12, 0.0, 1.0),
            "b": _gauss_jeu(SEED + 12, 12, 0.6, 1.0),
            "c": _gauss_jeu(SEED + 13, 12, -0.4, 1.0)},
        "kruskal_ties": {
            "a": _gauss_jeu(SEED + 14, 12, 0.0, 1.0, arrondi=0),
            "b": _gauss_jeu(SEED + 15, 12, 1.0, 1.0, arrondi=0)},
        # --- tableaux ----------------------------------------------------------
        "tab_2x2": (14, 6, 10, 30),            # a, b, c, d ≈ démo observationnel
        "tab_2x2_zero": (0, 18, 3, 25),        # cellule nulle ⇒ Haldane
        "tab_2x2_gros": (90, 40, 60, 110),     # valeurs gelées historiques
        "tab_3x3": [[12, 8, 5], [9, 15, 7], [4, 6, 18]],
        "discordants": (40, 20),
        "discordants_zero": (17, 0),
        # --- proportions ---------------------------------------------------------
        "cp": [(0, 20), (1, 12), (3, 10), (8, 29), (15, 15), (5, 60), (2, 30)],
        "wilson": [(0, 20), (1, 12), (8, 29), (5, 60), (2, 30)],
        # --- régression / tendance ------------------------------------------------
        "tendance": {
            "t": [0, 0, 1, 1, 3, 3, 6, 6, 9, 9, 12, 12],
            "y": [10.02, 9.98, 9.91, 9.89, 9.70, 9.66, 9.40, 9.32,
                  9.05, 8.97, 8.72, 8.60]},
        # --- survie ------------------------------------------------------------------
        "km_simple": {
            "t1": [1, 2, 3, 4, 8], "e1": [1, 1, 1, 1, 1],
            "t2": [2, 3, 4, 5, 4], "e2": [1, 1, 1, 0, 1]},
        "km_censure": {
            "t1": [3, 5, 6, 8, 9, 10, 12, 15],
            "e1": [1, 1, 0, 1, 1, 0, 1, 1],
            "t2": [2, 4, 5, 7, 8, 11, 13, 16],
            "e2": [1, 1, 1, 0, 1, 1, 0, 1]},
        # --- divers --------------------------------------------------------------
        "rubin": [{"theta": 0.42, "se": 0.11}, {"theta": 0.50, "se": 0.13},
                  {"theta": 0.39, "se": 0.10}, {"theta": 0.55, "se": 0.12},
                  {"theta": 0.47, "se": 0.14}, {"theta": 0.44, "se": 0.11}],
        "descriptif": _gauss_jeu(SEED + 16, 37, 7.0, 2.5),
        "smd": {
            "g1": _gauss_jeu(SEED + 17, 22, 35.0, 8.0),
            "g2": _gauss_jeu(SEED + 18, 24, 31.0, 7.0)},
        # échantillon gaussien « conforme » : scipy.interpolate donne p = 0,15
        # (plafond de table) ; la retenir comme OK exige une graine choisie —
        # gauss(SEED+19) était rejetée par scipy (p = 0,0138 ; vérifié 2026).
        "normalite_ok": _gauss_jeu(20260800, 40, 0.0, 1.0),
        # gamma(1,5 ; 1,0) : nettement non normal ; scipy (method='interpolate')
        # donne stat ≈ 2,1783 / p = 0,01, sans avertissement numérique.
        "normalite_ko": _gamma_jeu(SEED + 20, 40, 1.5, 1.0),
    }
