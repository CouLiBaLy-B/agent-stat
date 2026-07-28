"""
Imputation multiple — PMM (predictive mean matching) déterministe + pooling de Rubin.

PMM : pour chaque valeur manquante, on prédit la moyenne conditionnelle par
régression linéaire simple (si un prédicteur complet est disponible), puis on
tire un DONNEUR parmi les k=5 observations les plus proches de cette moyenne —
l'imputé est une valeur RÉELLEMENT observée (robuste, sans distribution supposée).
Sans prédicteur exploitable : hot-deck sur les donneurs observés.

Toute la stochasticité passe par random.Random(seed) dérivé par imputation —
double exécution concordante garantie.
"""
from __future__ import annotations

import random
from typing import Sequence

K_DONNEURS = 5


def _reg_simple(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx == 0:
        return 0.0, my
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    return beta, my - beta * mx


def imputer_pmm(cible: list, prédicteur: list | None, m: int,
                seed: int) -> tuple[list[list[float]], list[int]]:
    """Retourne (colonnes_complétées par imputation, indices imputés).
    `cible` : valeurs avec None/'' = manquant. """
    obs = [i for i, v in enumerate(cible) if v is not None and v != ""]
    mis = [i for i in range(len(cible)) if i not in obs]
    if not mis:
        return [[float(v) for v in cible] for _ in range(m)], []

    y_obs = [float(cible[i]) for i in obs]
    utiliser_pred = (prédicteur is not None
                     and all(prédicteur[i] is not None for i in range(len(cible)))
                     and len(set(float(prédicteur[i]) for i in obs)) > 1
                     and len(obs) >= 8)
    if utiliser_pred:
        beta, alpha = _reg_simple([float(prédicteur[i]) for i in obs], y_obs)
        pred = [alpha + beta * float(prédicteur[i]) for i in range(len(cible))]
    else:
        pred = None

    colonnes: list[list[float]] = []
    for k in range(m):
        rng = random.Random(seed * 100003 + k)
        colonne = [float(v) if v is not None and v != "" else None for v in cible]
        for i in mis:
            if pred is not None:
                proches = sorted(obs, key=lambda j: abs(pred[j] - pred[i]))[:K_DONNEURS]
                donneur = proches[rng.randrange(len(proches))]
            else:
                donneur = obs[rng.randrange(len(obs))]
            colonne[i] = float(cible[donneur])
        colonnes.append(colonne)
    return colonnes, mis


def taux_manquants(cible: list) -> float:
    if not cible:
        return 1.0
    return sum(1 for v in cible if v is None or v == "") / len(cible)
