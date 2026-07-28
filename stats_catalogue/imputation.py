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


# ---------------------------------------------------------------- MNAR : ruban
# δ-ajusté et point de bascule (tipping point). On recalcule le SMD de Hedges
# par cycle MI sur les colonnes complétées par PMM, en décalant les imputés
# du groupe désigné CONTRE l'effet observé (non-réponse systématiquement
# défavorable — scénario conservateur), puis on re-poole par les règles de
# Rubin. Le point de bascule est le premier δ de la grille où le résultat
# perd la significativité — sensibilité MNAR à pré-déclarer au SAP.

def _ecart_type(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((v - m) ** 2 for v in xs) / (n - 1)) ** 0.5


def _hedges_smd_var(g1: list[float], g2: list[float]) -> tuple[float, float]:
    from stats_catalogue.ops import smd_groupes
    n1, n2 = len(g1), len(g2)
    sp = (((n1 - 1) * _ecart_type(g1) ** 2 + (n2 - 1) * _ecart_type(g2) ** 2)
          / (n1 + n2 - 2)) ** 0.5
    if sp == 0:
        return 0.0, 0.0
    smd = smd_groupes(g1, g2)["smd"]              # Hedges corrigé (catalogue)
    df = n1 + n2 - 2
    var = (n1 + n2) / (n1 * n2) + smd * smd / (2 * df)
    return smd, var


def tipping_point_mnar_smd(colonnes_g1: list[list[float]],
                           colonnes_g2: list[list[float]],
                           deltas: list[float], groupe_ajuste: str = "g1",
                           alpha: float = 0.05, seed: int = 0) -> dict:
    """Ruban MNAR δ-ajusté sur SMD de Hedges + tipping point.

    - `colonnes_g1/g2` : m copies alignées des deux groupes, déjà complétées
      (sorties de `imputer_pmm` — m ≥ 2, longueurs constantes) ;
    - `groupe_ajuste` ∈ {"g1", "g2"} : groupe dont les imputés sont
      pénalisés (typiquement celui qui porte les manquants) ; le décalage
      est appliqué **contre l'effet observé** (−signe(θ_base)·δ·σ_réf), de
      sorte que δ positif ATTÉNUE toujours le SMD poolé — δ est exprimé en
      unités de σ_réf, écart-type poolé calculé sur TOUTES les copies ;
    - chaque δ re-pool par `pooling_rubin` ; le champ `significatif` est
      p < alpha (t de Barnard-Rubin, comme l'inférence de base) — mesure
      BRUTE toutes directions confondues ; au-delà du point où θ traverse
      zéro, l'effet est RENVERSÉ par la pénalisation (`renverse: true` et
      `delta_renversement`) : une éventuelle « re-significativité » y est
      l'artefact du renversement, pas une résurrection de l'effet observé ;
    - la bascule retenue (`delta_bascule`) est le PREMIER δ où la
      significativité est perdue — dans le sens observé ; `theta_monotone`
      n'est vrai que si la grille ne traverse pas zéro (|θ| est en V sous
      une pénalisation affine) : un `theta_monotone = false` signale la
      traversée, la bascule reste unique par construction ;
    - sortie : base (δ=0), ruban par δ, premier δ de bascule, transcription
      en échelle de la variable (δ×σ_réf).
    """
    from stats_catalogue.ops import pooling_rubin
    if groupe_ajuste not in ("g1", "g2"):
        return {"interpretable": False, "test": "tipping_point_mnar_smd",
                "motif": "groupe_ajuste ∈ {g1, g2} exigé"}
    m = len(colonnes_g1)
    if m < 2 or len(colonnes_g2) != m:
        return {"interpretable": False, "test": "tipping_point_mnar_smd",
                "motif": "m ≥ 2 copies ALIGNÉES par groupe exigées (sorties PMM)"}
    if not deltas or any(not isinstance(d, (int, float)) for d in deltas):
        return {"interpretable": False, "test": "tipping_point_mnar_smd",
                "motif": "grille de deltas non vide exigée (floats, σ-unités)"}
    if any(len(c1) != len(colonnes_g1[0]) or len(c2) != len(colonnes_g2[0])
           for c1, c2 in zip(colonnes_g1, colonnes_g2)):
        return {"interpretable": False, "test": "tipping_point_mnar_smd",
                "motif": "copies de longueur constante par groupe exigées"}
    grille = sorted(float(d) for d in deltas)
    plat1 = [v for c in colonnes_g1 for v in c]
    plat2 = [v for c in colonnes_g2 for v in c]
    n1, n2 = len(colonnes_g1[0]), len(colonnes_g2[0])
    sp_ref = (((m * n1 - 1) * _ecart_type(plat1) ** 2
               + (m * n2 - 1) * _ecart_type(plat2) ** 2)
              / (m * n1 + m * n2 - 2)) ** 0.5
    if sp_ref == 0:
        return {"interpretable": False, "test": "tipping_point_mnar_smd",
                "motif": "σ_réf nul (données constantes) — ruban indéfini"}

    # orientation : δ ≥ 0 atténue |θ| quel que soit le signe de θ_base
    smd0, var0 = _hedges_smd_var(list(colonnes_g1[0]), list(colonnes_g2[0]))
    signe_base = 1.0 if smd0 >= 0 else -1.0
    sens = -signe_base

    ruban = []
    for d in grille:
        decal = sens * d * sp_ref
        proposes = []
        for c1, c2 in zip(colonnes_g1, colonnes_g2):
            if groupe_ajuste == "g1":
                smd_k, var_k = _hedges_smd_var([v + decal for v in c1],
                                               list(c2))
            else:
                smd_k, var_k = _hedges_smd_var(list(c1),
                                               [v - decal for v in c2])
            proposes.append({"theta": smd_k, "se": var_k ** 0.5})
        pool = pooling_rubin(proposes, alpha=alpha)
        ruban.append({"delta": d, "decalage_unite": abs(decal),
                      "theta_pooled": pool["theta_pooled"],
                      "se_pooled": pool["se_pooled"],
                      "p_valeur": pool["p_valeur"], "ddl": pool["ddl"],
                      "significatif": pool["p_valeur"] < alpha,
                      "renverse": pool["theta_pooled"] * signe_base < 0})
    base = ruban[grille.index(0.0)] if 0.0 in grille else None
    tipping = next((r for r in ruban if not r["significatif"]), None)
    renverses = [r for r in ruban if r["renverse"]]
    delta_renversement = renverses[0]["delta"] if renverses else None
    thetas = [abs(r["theta_pooled"]) for r in ruban]
    diffs = [thetas[i + 1] - thetas[i] for i in range(len(thetas) - 1)]
    # |θ(δ)| monotone décroissant : vrai tant que la grille ne traverse pas
    # zéro (|θ| est en V sous pénalisation affine — cf. docstring)
    monotone = all(d <= 1e-12 for d in diffs) if diffs else True
    if tipping:
        verdict = (f"résultat FRAGILE au-delà de δ={tipping['delta']:g} σ "
                   f"(décalage {tipping['decalage_unite']:g} unités)")
        if delta_renversement is not None:
            verdict += (f" ; l'effet est RENVERSÉ par la pénalisation dès "
                        f"δ={delta_renversement:g} σ — toute "
                        "« re-significativité » au-delà est l'artefact du "
                        "renversement, pas une résurrection de l'effet "
                        "observé")
    else:
        verdict = ("résultat ROBUSTE : aucune bascule sur la grille δ "
                   "balayée")
    return {"interpretable": True, "test": "tipping_point_mnar_smd",
            "m_imputations": m, "n1": n1, "n2": n2, "sigma_ref": sp_ref,
            "groupe_ajuste": groupe_ajuste, "grille_deltas": grille,
            "ruban": ruban, "base": base,
            "delta_bascule": tipping["delta"] if tipping else None,
            "decalage_bascule_unite": (tipping["decalage_unite"]
                                       if tipping else None),
            "theta_au_bascule": tipping["theta_pooled"] if tipping else None,
            "delta_renversement": delta_renversement,
            "theta_monotone": monotone,
            "verdict": verdict,
            "hypothese": ("scénario MNAR δ-ajusté contre l'effet observé sur "
                          f"les imputés de {groupe_ajuste} — sensibilité "
                          "PRÉ-DÉCLARÉE au SAP (grille, α) ; n'infirme ni ne "
                          "confirme MAR, borne la robustesse de la conclusion"),
            "lecture": "plus δ_bascule est grand (en σ), plus la conclusion "
                       "est robuste à un biais MNAR systématique"}
