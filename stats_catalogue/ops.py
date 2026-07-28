"""
Catalogue d'opérations statistiques pré-approuvées (MVP).

Contrat de chaque opération :
- signature `op(..., seed: int) -> dict` — sortie pure, JSON-sérialisable ;
- AUCUNE entrée/sortie non déclarée, aucun I/O, aucune stochasticité non seedée ;
- versionnée (changement de logique = nouveau numéro de version → revalidation ciblée) ;
- estimation systématiquement accompagnée d'IC 95 % et/ou taille d'effet
  (les p-values seules sont refusées par la couche reporting — cf. ARCHITECTURE.md).

Phase 1 : réimplémentation sur scipy/statsmodels épinglés, contrats inchangés,
qualification par batterie de valeurs de référence (tests/test_stats.py).
"""
from __future__ import annotations

import math
from typing import Sequence

from stats_catalogue import dist

VERSION_CATALOGUE = "1.0.0"


# ------------------------------------------------------------------ helpers

def _moy(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _moy(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _quantiles(xs: Sequence[float]) -> tuple[float, float, float]:
    s = sorted(xs)
    n = len(s)

    def q(p: float) -> float:
        if n == 1:
            return s[0]
        pos = (n - 1) * p
        lo, frac = int(pos), pos - int(pos)
        return s[lo] if lo + 1 >= n else s[lo] + frac * (s[lo + 1] - s[lo])

    return q(0.25), q(0.5), q(0.75)


def _rangs(valeurs: Sequence[float]) -> tuple[list[float], dict[float, int]]:
    """Rangs moyens pour ex æquo + effectifs des groupes de ties."""
    idx = sorted(range(len(valeurs)), key=lambda i: valeurs[i])
    rangs = [0.0] * len(valeurs)
    ties: dict[float, int] = {}
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and valeurs[idx[j + 1]] == valeurs[idx[i]]:
            j += 1
        rang_moyen = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rangs[idx[k]] = rang_moyen
        if j > i:
            ties[valeurs[idx[i]]] = j - i + 1
        i = j + 1
    return rangs, ties


def _fusion(groups: dict[str, Sequence[float]]):
    valeurs, etiquettes = [], []
    for g, xs in groups.items():
        valeurs.extend(xs)
        etiquettes.extend([g] * len(xs))
    return valeurs, etiquettes


# ------------------------------------------------------------------ opérations

def descriptif_continu(valeurs: list[float], seed: int = 0) -> dict:
    n = len(valeurs)
    if n == 0:
        return {"n": 0, "interpretable": False}
    m, sd = _moy(valeurs), _sd(valeurs)
    q1, med, q3 = _quantiles(valeurs)
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    ic = ([m - dist.t_ppf(0.975, n - 1) * se, m + dist.t_ppf(0.975, n - 1) * se]
          if n >= 3 else None)
    return {"n": n, "moyenne": m, "ecart_type": sd, "se": se, "median": med,
            "q1": q1, "q3": q3, "iqr": q3 - q1, "min": min(valeurs),
            "max": max(valeurs), "ic95_moyenne": ic, "interpretable": True}


def proportion_wilson(succes: int, total: int, alpha: float = 0.05,
                      seed: int = 0) -> dict:
    """IC de Wilson — robuste aux petits effectifs (recommandé vs Wald)."""
    if total == 0:
        return {"interpretable": False, "motif": "total nul"}
    z = dist.norm_ppf(1.0 - alpha / 2.0)
    p = succes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    marge = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return {"succes": succes, "total": total, "proportion": p,
            "ic95": [max(0.0, centre - marge), min(1.0, centre + marge)],
            "methode": "wilson"}


def proportion_exacte(succes: int, total: int, alpha: float = 0.05,
                      seed: int = 0) -> dict:
    """IC de Clopper-Pearson — référence pour les incidences safety."""
    if total == 0:
        return {"interpretable": False, "motif": "total nul"}
    lo = 0.0 if succes == 0 else dist.beta_ppf(alpha / 2, succes, total - succes + 1)
    hi = 1.0 if succes == total else dist.beta_ppf(1 - alpha / 2, succes + 1,
                                                   total - succes)
    return {"succes": succes, "total": total, "proportion": succes / total,
            "ic95": [lo, hi], "methode": "clopper-pearson"}


def t_test_welch(g1: list[float], g2: list[float], alpha: float = 0.05,
                 seed: int = 0) -> dict:
    """Comparaison de 2 moyennes (Welch, robuste aux variances inégales).
    Précondition : résidus ≈ normaux OU n suffisant — vérifiée par agent.hypotheses."""
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return {"interpretable": False, "motif": "effectif < 2 par groupe"}
    m1, m2 = _moy(g1), _moy(g2)
    v1, v2 = _sd(g1) ** 2, _sd(g2) ** 2
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return {"interpretable": False, "motif": "variances nulles"}
    diff = m1 - m2
    t = diff / se
    ddl = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1)
                                      + (v2 / n2) ** 2 / (n2 - 1))
    p = 2 * dist.t_sf(abs(t), ddl)
    tc = dist.t_ppf(1 - alpha / 2, ddl)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    d = diff / sp if sp > 0 else None
    return {"interpretable": True, "test": "t_welch", "n1": n1, "n2": n2,
            "moyenne1": m1, "moyenne2": m2, "difference": diff, "se": se,
            "t": t, "ddl": ddl, "p_valeur": p,
            "ic95_difference": [diff - tc * se, diff + tc * se],
            "taille_effet_cohen_d": d}


def mann_whitney(g1: list[float], g2: list[float], seed: int = 0) -> dict:
    """Wilcoxon-Mann-Whitney — approximation normale avec corrections ex æquo
    et continuité. Fiabilité : n >= 10/groupe (signalé sinon).
    Interprétation recommandée : P(X1 > X2) (stochastic superiority),
    PAS 'différence de médianes' si distributions de formes différentes."""
    n1, n2 = len(g1), len(g2)
    if n1 < 3 or n2 < 3:
        return {"interpretable": False, "motif": "effectif < 3 par groupe"}
    valeurs, etiquettes = _fusion({"g1": g1, "g2": g2})
    rangs, ties = _rangs(valeurs)
    r1 = sum(r for r, e in zip(rangs, etiquettes) if e == "g1")
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    n = n1 + n2
    tie_term = sum(t ** 3 - t for t in ties.values())
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1)))
    if sigma2 <= 0:
        return {"interpretable": False, "motif": "variance des rangs nulle"}
    mu = n1 * n2 / 2.0
    corr = 0.5 if u1 > mu else (-0.5 if u1 < mu else 0.0)
    z = (u1 - mu - corr) / math.sqrt(sigma2)
    p = 2 * min(dist.norm_cdf(z), dist.norm_sf(z))
    return {"interpretable": True, "test": "mann_whitney", "n1": n1, "n2": n2,
            "U1": u1, "U2": u2, "z": z, "p_valeur": min(1.0, p),
            "probabilite_superiorite_g1": u1 / (n1 * n2),
            "approximation": "normale_corrigee",
            "avertissement": (None if min(n1, n2) >= 10
                              else "petit effectif : p approximative")}


def chi2_independance(tableau: list[list[int]], seed: int = 0) -> dict:
    """Tableau r×c. Fallback recommandé : Fisher exact si précondition
    de Cochran violée (effectif théorique < 5) — gérée par agent.hypotheses."""
    r, c = len(tableau), len(tableau[0])
    lignes = [sum(l) for l in tableau]
    cols = [sum(tableau[i][j] for i in range(r)) for j in range(c)]
    n = sum(lignes)
    if n == 0:
        return {"interpretable": False, "motif": "tableau vide"}
    stat, min_theorique = 0.0, float("inf")
    for i in range(r):
        for j in range(c):
            theo = lignes[i] * cols[j] / n
            min_theorique = min(min_theorique, theo)
            if theo > 0:
                stat += (tableau[i][j] - theo) ** 2 / theo
    ddl = (r - 1) * (c - 1)
    p = dist.chi2_sf(stat, ddl)
    k = min(r - 1, c - 1)
    v = math.sqrt(stat / (n * k)) if k > 0 and n > 0 else None
    return {"interpretable": True, "test": "chi2_independance",
            "statistique": stat, "ddl": ddl, "p_valeur": p,
            "cramers_v": v, "effectif_theorique_min": min_theorique,
            "cochran_ok": min_theorique >= 5}


def fisher_exact_2x2(a: int, b: int, c: int, d: int, seed: int = 0) -> dict:
    """Test exact de Fisher (2×2, bilatéral) — somme des tables de probabilité
    ≤ p(observée). Référence pour petits effectifs / endpoints safety."""
    n = a + b + c + d
    if n == 0:
        return {"interpretable": False, "motif": "tableau vide"}
    r1, c1 = a + b, a + c

    def lp(x: int) -> float:
        return (math.lgamma(r1 + 1) - math.lgamma(x + 1) - math.lgamma(r1 - x + 1)
                + math.lgamma(n - r1 + 1) - math.lgamma(c1 - x + 1)
                - math.lgamma(n - r1 - c1 + x + 1) - math.lgamma(n + 1)
                + math.lgamma(c1 + 1) + math.lgamma(n - c1 + 1))

    p_obs = math.exp(lp(a))
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    p_tot = sum(math.exp(lp(x)) for x in range(lo, hi + 1)
                if math.exp(lp(x)) <= p_obs * (1 + 1e-9))
    p1, p2 = a / r1 if r1 else 0.0, c / (c + d) if (c + d) else 0.0
    return {"interpretable": True, "test": "fisher_exact",
            "tableau": [[a, b], [c, d]], "p_valeur": min(1.0, p_tot),
            "proportion_g1": p1, "proportion_g2": p2,
            "risque_relatif": (p1 / p2) if p2 > 0 else None,
            "difference_risques": p1 - p2}


def mcnemar(discordant_bf: int, discordant_cf: int, seed: int = 0) -> dict:
    """McNemar exact (données appariées, 2×2) — seuls comptent les discordants."""
    b, c = discordant_bf, discordant_cf
    n = b + c
    if n == 0:
        return {"interpretable": False, "motif": "aucune paire discordante"}
    k = min(b, c)
    p = min(1.0, 2 * dist.binom_cdf(k, n, 0.5))
    return {"interpretable": True, "test": "mcnemar_exact",
            "discordants": n, "p_valeur": p}


def kruskal_wallis(groups: dict[str, list[float]], seed: int = 0) -> dict:
    """ANOVA sur rangs (k ≥ 2 groupes) — fallback non paramétrique des comparaisons
    multi-groupes. Post-hoc (Dunn) hors MVP : prévu au SAP si k > 2."""
    k = len(groups)
    if k < 2 or any(len(xs) < 2 for xs in groups.values()):
        return {"interpretable": False, "motif": "≥ 2 groupes de n ≥ 2 requis"}
    valeurs, etiquettes = _fusion(groups)
    rangs, ties = _rangs(valeurs)
    n = len(valeurs)
    somme = 0.0
    for g in groups:
        rg = sum(r for r, e in zip(rangs, etiquettes) if e == g)
        somme += rg * rg / len(groups[g])
    h = 12.0 / (n * (n + 1)) * somme - 3 * (n + 1)
    tie_term = sum(t ** 3 - t for t in ties.values())
    corr = 1.0 - tie_term / (n ** 3 - n) if n > 1 else 1.0
    h = h / corr if corr > 0 else h
    return {"interpretable": True, "test": "kruskal_wallis", "statistique_h": h,
            "ddl": k - 1, "p_valeur": dist.chi2_sf(h, k - 1),
            "n_total": n, "groupes": {g: len(xs) for g, xs in groups.items()}}


def test_normalite(valeurs: list[float], seed: int = 0) -> dict:
    """Anderson-Darling (normalité, paramètres estimés — correction de Stephens).
    Sert aux préconditions des tests paramétriques (agent.hypotheses)."""
    n = len(valeurs)
    if n < 8:
        return {"interpretable": False, "motif": "n < 8 : puissance illusoire",
                "verdict": "indetermine"}
    m, sd = _moy(valeurs), _sd(valeurs)
    if sd == 0:
        return {"interpretable": False, "verdict": "non_normal",
                "motif": "variance nulle"}
    zs = sorted((x - m) / sd for x in valeurs)
    eps = 1e-300
    s = 0.0
    for i, z in enumerate(zs, start=1):
        phi_i = min(1.0 - 1e-16, max(eps, dist.norm_cdf(z)))
        phi_j = min(1.0 - 1e-16, max(eps, dist.norm_cdf(zs[n - i])))
        s += (2 * i - 1) * (math.log(phi_i) + math.log1p(-phi_j))
    a2 = -n - s / n
    a2s = a2 * (1.0 + 0.75 / n + 2.25 / (n * n))
    if a2s < 0.2:
        p = 1.0 - math.exp(-13.436 + 101.14 * a2s - 223.73 * a2s ** 2)
    elif a2s < 0.34:
        p = 1.0 - math.exp(-8.318 + 42.796 * a2s - 59.938 * a2s ** 2)
    elif a2s < 0.6:
        p = math.exp(0.9177 - 4.279 * a2s - 1.38 * a2s ** 2)
    else:
        p = math.exp(1.2937 - 5.709 * a2s + 0.0186 * a2s ** 2)
    return {"interpretable": True, "test": "anderson_darling_normalite",
            "statistique_a2_corrige": a2s, "p_valeur": p,
            "verdict": "normal_ok" if p >= 0.05 else "non_normal"}


def smd_groupes(g1: list[float], g2: list[float], seed: int = 0) -> dict:
    """Différence standardisée (balance des covariables). Seuil usuel : |SMD| > 0,1
    = déséquilibre potentiel ; > 0,25 = sérieux (cf. volet biais)."""
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return {"interpretable": False}
    v1, v2 = _sd(g1) ** 2, _sd(g2) ** 2
    sp = math.sqrt((v1 + v2) / 2.0)
    smd = (_moy(g1) - _moy(g2)) / sp if sp > 0 else 0.0
    return {"interpretable": True, "smd": smd,
            "desequilibre": "fort" if abs(smd) > 0.25 else
                            ("potentiel" if abs(smd) > 0.1 else "negligeable")}


def mos_cosmetique(noael_mg_kg_j: float | None, sed_mg_kg_j: float,
                   seuil: float = 100.0, seed: int = 0) -> dict:
    """Margin of Safety = NOAEL / SED (réf. SCCS). Seuil par défaut 100.
    NOAEL manquant → zone d'incertitude étiquetée, PAS de verdict favorable."""
    if noael_mg_kg_j is None:
        return {"interpretable": False, "verdict": "INCERTAIN",
                "motif": "NOAEL manquant — revue toxicologue requise",
                "zone_incertitude": True}
    if sed_mg_kg_j <= 0:
        return {"interpretable": False, "verdict": "INCERTAIN",
                "motif": "SED nul ou négatif — données d'exposition à corriger"}
    mos = noael_mg_kg_j / sed_mg_kg_j
    return {"interpretable": True, "noael": noael_mg_kg_j, "sed": sed_mg_kg_j,
            "mos": mos, "seuil": seuil,
            "verdict": "OK" if mos >= seuil else "SIGNAL",
            "zone_incertitude": False}


def pooling_rubin(estimations: list[dict], alpha: float = 0.05,
                  seed: int = 0) -> dict:
    """Pooling des règles de Rubin après imputation multiple.
    estimations : [{"theta", "se"}] par jeu imputé (m ≥ 2).
    Retourne l'estimation poolée, son erreur, la variance intra/inter,
    la fraction d'information manquante (FMI) et l'IC/p poolés."""
    m = len(estimations)
    if m < 2:
        return {"interpretable": False, "motif": "m < 2 : pooling impossible"}
    thetas = [e["theta"] for e in estimations]
    ses = [e["se"] for e in estimations]
    qbar = _moy(thetas)
    ubar = _moy([s * s for s in ses])                    # variance intra
    b = (sum((t - qbar) ** 2 for t in thetas) / (m - 1))  # variance inter
    tvar = ubar + (1 + 1 / m) * b
    se = math.sqrt(tvar)
    if tvar == 0 or se == 0:
        return {"interpretable": False, "motif": "variances nulles"}
    r = (1 + 1 / m) * b / ubar if ubar > 0 else 0.0       # augmentation relative
    if b > 0 and ubar > 0:
        lam = (1 + 1 / m) * b / tvar
        ddl = max(1.0, (m - 1) / (lam * lam))
    else:
        ddl = float(m) * 10 ** 6
    fmi = ((r + 2 / (ddl + 3)) / (1 + r)) if ddl < 1e12 else r / (1 + r)
    tc = dist.t_ppf(1 - alpha / 2, ddl)
    t_stat = qbar / se
    return {"interpretable": True, "test": "pooling_rubin", "m": m,
            "theta_pooled": qbar, "se_pooled": se, "p_valeur":
            2 * dist.t_sf(abs(t_stat), ddl),
            "ic95": [qbar - tc * se, qbar + tc * se],
            "variance_intra": ubar, "variance_inter": b,
            "augmentation_relative": r, "fraction_info_manquante": fmi, "ddl": ddl}


def tost_equivalence(g1: list[float], g2: list[float], marge: float,
                     alpha: float = 0.05, seed: int = 0) -> dict:
    """TOST (two one-sided tests) d'équivalence de moyennes (approx. Welch).
    Exige une marge Δ PRÉ-DÉFINIE (SAP). p non significatif ≠ équivalence :
    seul le rejet conjoint des deux tests unilatéraux (équiv. IC (1-2α)
    ⊂ [-Δ, +Δ]) permet de conclure à l'équivalence."""
    if marge is None or marge <= 0:
        return {"interpretable": False,
                "motif": "marge d'équivalence > 0 exigée (pré-définie au SAP)"}
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return {"interpretable": False, "motif": "effectif < 2 par groupe"}
    m1, m2 = _moy(g1), _moy(g2)
    v1, v2 = _sd(g1) ** 2, _sd(g2) ** 2
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return {"interpretable": False, "motif": "variances nulles"}
    diff = m1 - m2
    ddl = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1)
                                      + (v2 / n2) ** 2 / (n2 - 1))
    t1, t2 = (diff + marge) / se, (marge - diff) / se
    p1 = dist.t_sf(t1, ddl)          # H0_1 : diff ≤ -Δ
    p2 = dist.t_sf(t2, ddl)          # H0_2 : diff ≥ +Δ
    p_tost = max(p1, p2)
    tc = dist.t_ppf(1 - alpha, ddl)
    ic90 = [diff - tc * se, diff + tc * se]
    equivalence = (p_tost < alpha) and (ic90[0] >= -marge) and (ic90[1] <= marge)
    return {"interpretable": True, "test": "tost_equivalence", "n1": n1, "n2": n2,
            "difference": diff, "se": se, "marge": marge, "ddl": ddl,
            "p_tost": p_tost, "p_infe": p1, "p_supe": p2,
            "ic90_difference": ic90,
            "verdict": "equivalence_demontree" if equivalence
            else "equivalence_non_demontree"}


def tendance_lineaire(temps: list[float], valeurs: list[float],
                      alpha: float = 0.05, seed: int = 0) -> dict:
    """Régression linéaire simple y ~ temps — parcours stabilité.
    Fournit pente + IC, prédiction à l'échéance observée + IC de prédiction,
    diagnostics résiduels. La règle métier (bornes d'acceptation) est
    évaluée en aval par le moteur de règles (R-STAB-01)."""
    n = len(temps)
    if n < 4 or len(set(temps)) < 2:
        return {"interpretable": False, "motif": "≥ 4 points et ≥ 2 temps uniques"}
    assert n == len(valeurs), "temps/valeurs désalignés"
    mx, my = _moy(temps), _moy(valeurs)
    sxx = sum((t - mx) ** 2 for t in temps)
    sxy = sum((t - mx) * (v - my) for t, v in zip(temps, valeurs))
    beta, alpha0 = sxy / sxx, my - (sxy / sxx) * mx
    fitted = [alpha0 + beta * t for t in temps]
    residus = [v - f for v, f in zip(valeurs, fitted)]
    s2 = sum(r * r for r in residus) / (n - 2)
    sd_r = math.sqrt(s2)
    se_b = sd_r / math.sqrt(sxx)
    tc = dist.t_ppf(1 - alpha / 2, n - 2)
    t_max, t_min = max(temps), min(temps)
    pred = alpha0 + beta * t_max
    se_pred = sd_r * math.sqrt(1 / n + (t_max - mx) ** 2 / sxx)
    ic_pred = [pred - tc * se_pred, pred + tc * se_pred]
    vals_tmax = [v for t, v in zip(temps, valeurs) if t == t_max]
    syy = sum((v - my) ** 2 for v in valeurs)
    r2 = 1 - (s2 * (n - 2)) / syy if syy > 0 else 1.0
    return {"interpretable": True, "test": "tendance_lineaire", "n": n,
            "pente": beta, "se_pente": se_b,
            "ic95_pente": [beta - tc * se_b, beta + tc * se_b],
            "p_valeur_pente": 2 * dist.t_sf(abs(beta / se_b) if se_b > 0
                                            else 0.0, n - 2),
            "intercept": alpha0, "r2": r2,
            "temps_min": t_min, "temps_max": t_max,
            "moyenne_tmax": _moy(vals_tmax),
            "prevision_tmax": pred, "ic95_prevision_tmax": ic_pred,
            "residus_sd": sd_r, "residus_max_abs": max(abs(r) for r in residus)}


# ------------------------------------------------------------------ registre

OPS: dict[str, dict] = {
    "descriptif_continu":    {"fn": descriptif_continu,   "version": "1.0.0"},
    "proportion_wilson":     {"fn": proportion_wilson,    "version": "1.0.0"},
    "proportion_exacte":     {"fn": proportion_exacte,    "version": "1.0.0"},
    "t_test_welch":          {"fn": t_test_welch,         "version": "1.0.0"},
    "mann_whitney":          {"fn": mann_whitney,         "version": "1.0.0"},
    "chi2_independance":     {"fn": chi2_independance,    "version": "1.0.0"},
    "fisher_exact_2x2":      {"fn": fisher_exact_2x2,     "version": "1.0.0"},
    "mcnemar":               {"fn": mcnemar,              "version": "1.0.0"},
    "kruskal_wallis":        {"fn": kruskal_wallis,       "version": "1.0.0"},
    "test_normalite":        {"fn": test_normalite,       "version": "1.0.0"},
    "smd_groupes":           {"fn": smd_groupes,          "version": "1.0.0"},
    "mos_cosmetique":        {"fn": mos_cosmetique,       "version": "1.0.0"},
    "pooling_rubin":         {"fn": pooling_rubin,        "version": "1.0.0"},
    "tost_equivalence":      {"fn": tost_equivalence,     "version": "1.0.0"},
    "tendance_lineaire":     {"fn": tendance_lineaire,    "version": "1.0.0"},
}


def executer(nom_op: str, seed: int, **params) -> dict:
    """Point d'entrée unique — l'agent inférentiel ne peut RIEN appeler hors catalogue."""
    if nom_op not in OPS:
        from core.exceptions import ErreurLogique
        raise ErreurLogique(f"op hors catalogue : {nom_op!r} (blocage SAP)")
    return OPS[nom_op]["fn"](seed=seed, **params)
