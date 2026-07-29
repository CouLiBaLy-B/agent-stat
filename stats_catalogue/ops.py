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
            "statistique_a2_brute": a2, "statistique_a2_corrige": a2s,
            "p_valeur": p,
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


# ------------------------------------------------------------------ observationnel

def _haldane(a: float, b: float, c: float, d: float):
    """Correction de Haldane-Anscombe (+0,5 aux 4 cellules) si une cellule
    est nulle — rend OR/RR calculables au prix d'un léger biais conservateur.
    TOUJOURS déclarée dans la sortie de l'op appelante."""
    if min(a, b, c, d) == 0:
        return a + 0.5, b + 0.5, c + 0.5, d + 0.5, True
    return float(a), float(b), float(c), float(d), False


def odds_ratio_cas_temoins(a: int, b: int, c: int, d: int,
                           alpha: float = 0.05, seed: int = 0) -> dict:
    """Odds ratio d'association exposition ↔ issue (cas-témoins NON appariée).
    a = exposés parmi les cas, b = non-exposés parmi les cas,
    c = exposés parmi les témoins, d = non-exposés parmi les témoins ;
    OR = (a·d)/(b·c), IC95 % de Woolf (sur log OR).
    p : test exact de Fisher 2×2 (référence petits effectifs).
    L'OR mesure une ASSOCIATION — jamais une causalité."""
    if min(a, b, c, d) < 0 or a + b + c + d == 0:
        return {"interpretable": False,
                "motif": "comptes négatifs ou tableau vide"}
    aa, bb, cc, dd, correction = _haldane(a, b, c, d)
    ratio = (aa * dd) / (bb * cc)
    log_or = math.log(ratio)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    z = dist.norm_ppf(1 - alpha / 2)
    p = fisher_exact_2x2(a, b, c, d)["p_valeur"]
    return {"interpretable": True, "test": "odds_ratio_cas_temoins",
            "tableau": [[a, b], [c, d]],
            "exposes_cas": a / (a + b) if (a + b) else None,
            "exposes_temoins": c / (c + d) if (c + d) else None,
            "odds_ratio": ratio, "log_or": log_or, "se_log_or": se,
            "ic95_or": [math.exp(log_or - z * se),
                        math.exp(log_or + z * se)],
            "p_valeur": p, "correction_haldane_anscombe": correction,
            "lecture": ("association (non causale) : OR > 1 = exposition plus "
                        "fréquente chez les cas que chez les témoins")}


def or_apparie(paires_b: int, paires_c: int, alpha: float = 0.05,
               seed: int = 0) -> dict:
    """OR conditionnel pour cas-témoins appariée 1:1 = b/c — seules les paires
    DISCORDANTES informent (principe McNemar) :
    b = paires (cas exposé, témoin non exposé) ;
    c = paires (cas non exposé, témoin exposé).
    p : McNemar exact sur les discordants ; IC95 % : log(b′/c′) ± z·√(1/b′+1/c′).
    Discordance nulle d'un côté → correction +0,5 déclarée."""
    if paires_b < 0 or paires_c < 0:
        return {"interpretable": False, "motif": "comptes négatifs"}
    if paires_b + paires_c == 0:
        return {"interpretable": False,
                "motif": "aucune paire discordante — puissance nulle"}
    correction = paires_b == 0 or paires_c == 0
    bb = paires_b + (0.5 if correction else 0.0)
    cc = paires_c + (0.5 if correction else 0.0)
    ratio = bb / cc
    log_or = math.log(ratio)
    se = math.sqrt(1 / bb + 1 / cc)
    z = dist.norm_ppf(1 - alpha / 2)
    p = mcnemar(paires_b, paires_c)["p_valeur"]
    return {"interpretable": True, "test": "or_apparie",
            "paires_bc_cas_expose_temoin_non": paires_b,
            "paires_cb_cas_non_temoin_expose": paires_c,
            "paires_discordantes": paires_b + paires_c,
            "odds_ratio": ratio, "log_or": log_or, "se_log_or": se,
            "ic95_or": [math.exp(log_or - z * se),
                        math.exp(log_or + z * se)],
            "p_valeur": p, "correction_zero_discordant": correction,
            "lecture": ("association conditionnelle (non causale) : OR > 1 = "
                        "exposition plus fréquente chez le cas de la paire")}


def risque_relatif_cohorte(a: int, b: int, c: int, d: int,
                           alpha: float = 0.05, seed: int = 0) -> dict:
    """Risque relatif (cohorte) : RR = R1/R0 avec R1 = a/(a+b) (exposés) et
    R0 = c/(c+d). IC95 % du RR : méthode log de Katz.
    Différence de risques : IC95 % de Newcombe (score, méthode 10 —
    combinaison des bornes de Wilson, sans correction de continuité).
    p : test exact de Fisher. +0,5 Haldane-Anscombe sur le RR si cellule
    nulle — déclarée. RR mesure une ASSOCIATION temporellement ordonnée,
    pas une causalité."""
    n1, n0 = a + b, c + d
    if min(a, b, c, d) < 0 or n1 == 0 or n0 == 0:
        return {"interpretable": False,
                "motif": "comptes négatifs ou bras vide"}
    r1, r0 = a / n1, c / n0
    w1 = proportion_wilson(a, n1, alpha)["ic95"]
    w0 = proportion_wilson(c, n0, alpha)["ic95"]
    diff = r1 - r0
    ic_diff = [diff - math.sqrt((r1 - w1[0]) ** 2 + (w0[1] - r0) ** 2),
               diff + math.sqrt((w1[1] - r1) ** 2 + (r0 - w0[0]) ** 2)]
    aa, bb, cc, dd, correction = _haldane(a, b, c, d)
    rr = (aa / (aa + bb)) / (cc / (cc + dd))
    log_rr = math.log(rr)
    se = math.sqrt(1 / aa - 1 / (aa + bb) + 1 / cc - 1 / (cc + dd))
    z = dist.norm_ppf(1 - alpha / 2)
    p = fisher_exact_2x2(a, b, c, d)["p_valeur"]
    return {"interpretable": True, "test": "risque_relatif_cohorte",
            "tableau": [[a, b], [c, d]], "risque_expose": r1,
            "risque_non_expose": r0, "risque_relatif": rr,
            "log_rr": log_rr, "se_log_rr": se,
            "ic95_rr": [math.exp(log_rr - z * se),
                        math.exp(log_rr + z * se)],
            "difference_risques": diff, "ic95_difference_risques": ic_diff,
            "p_valeur": p, "correction_haldane_anscombe": correction,
            "lecture": ("association (non causale) : RR > 1 = incidence plus "
                        "élevée chez les exposés")}


def km_logrank_hr(temps1: list[float], evenements1: list[int],
                  temps2: list[float], evenements2: list[int],
                  alpha: float = 0.05, seed: int = 0) -> dict:
    """Survie à 2 groupes : Kaplan-Meier par groupe + test du log-rang (Mantel)
    + HR estimé par résumé de Peto : log HR = (O1−E1)/V, SE = 1/√V.
    HR > 1 ⇒ risque accru dans le groupe 1. Hypothèse de risques relatifs
    CONSTANTS (proportionnels) — non vérifiable avec ce seul estimateur :
    à confirmer par le biostatisticien (G3) et l'épidémiologiste (G6)."""
    n1, n2 = len(temps1), len(temps2)
    if (n1 < 2 or n2 < 2 or len(evenements1) != n1
            or len(evenements2) != n2):
        return {"interpretable": False,
                "motif": "longueurs temps/événements incohérentes ou < 2"}
    t1 = [float(t) for t in temps1]
    t2 = [float(t) for t in temps2]
    e1 = [int(e) for e in evenements1]
    e2 = [int(e) for e in evenements2]
    if (any(t < 0 for t in t1 + t2)
            or any(e not in (0, 1) for e in e1 + e2)):
        return {"interpretable": False,
                "motif": "temps négatif ou événement hors {0,1}"}

    def km(ts: list[float], es: list[int]) -> dict:
        pts, s = [], 1.0
        for t in sorted({x for x, e in zip(ts, es) if e == 1}):
            risque = sum(1 for x in ts if x >= t)
            survenus = sum(1 for x, e in zip(ts, es) if x == t and e == 1)
            s *= 1 - survenus / risque
            pts.append({"t": t, "survie": s})
        med = next((p["t"] for p in pts if p["survie"] <= 0.5), None)
        return {"points": pts, "mediane": med,
                "survie_finale": pts[-1]["survie"] if pts else 1.0}

    k1, k2 = km(t1, e1), km(t2, e2)
    o1, o2 = sum(e1), sum(e2)
    if o1 == 0 or o2 == 0:
        return {"interpretable": False,
                "motif": "un groupe sans événement — HR non estimable"}
    e_attendu1, variance = 0.0, 0.0
    temps_evt = sorted({t for t, e in zip(t1 + t2, e1 + e2) if e == 1})
    for t in temps_evt:
        r1 = sum(1 for x in t1 if x >= t)
        r2 = sum(1 for x in t2 if x >= t)
        d1 = sum(1 for x, e in zip(t1, e1) if x == t and e == 1)
        d2 = sum(1 for x, e in zip(t2, e2) if x == t and e == 1)
        r, dtot = r1 + r2, d1 + d2
        e_attendu1 += dtot * r1 / r
        if r > 1:
            variance += r1 * r2 * dtot * (r - dtot) / (r * r * (r - 1))
    if variance <= 0:
        return {"interpretable": False, "motif": "variance log-rang nulle"}
    ecart = o1 - e_attendu1
    chi2 = ecart * ecart / variance
    log_hr = ecart / variance
    se = 1.0 / math.sqrt(variance)
    z = dist.norm_ppf(1 - alpha / 2)
    return {"interpretable": True, "test": "km_logrank_hr",
            "n1": n1, "n2": n2, "evenements1": o1, "evenements2": o2,
            "mediane_survie_g1": k1["mediane"], "mediane_survie_g2": k2["mediane"],
            "survie_finale_g1": k1["survie_finale"],
            "survie_finale_g2": k2["survie_finale"],
            "O1": o1, "E1": e_attendu1, "variance_logrank": variance,
            "chi2_logrank": chi2, "p_valeur": dist.chi2_sf(chi2, 1),
            "hr": math.exp(log_hr), "log_hr": log_hr, "se_log_hr": se,
            "ic95_hr": [math.exp(log_hr - z * se),
                        math.exp(log_hr + z * se)],
            "estimateur": "Peto (log-rank summary) — approximation documentée",
            "hypothese": ("risques relatifs constants (proportionnels) — À "
                          "CONFIRMER par le biostatisticien"),
            "lecture": ("association temporellement ordonnée (non causale) : "
                        "HR > 1 = survenue plus rapide dans le groupe 1")}


# ------------------------------------------------------- sensibilité (P1)

def tendance_fenetre_glissante(points: list[dict], fenetre_mois: float,
                               horizon_mois: float, spec_limite: float,
                               direction: str, alpha: float = 0.05,
                               seed: int = 0) -> dict:
    """Sensibilité « passage au grand mail » par fenêtre glissante.

    Pour chaque origine t0 observée : OLS local sur les points de
    [t0, t0 + fenetre_mois], prévision à t0 + fenetre_mois + horizon_mois avec
    IC95 — on consigne la PIRE borne (inf si direction="inferieur", sup si
    "superieur") et le franchissement de `spec_limite`. Le premier mois où la
    détection devient possible est le délai mesurable de robustesse.

    Usage : stabilité / série temporelle réglementaire — modèle LOCAL
    (extrapolation linéaire courte), à pré-déclarer au SAP ; ce n'est pas un
    modèle mécaniste de dégradation, la lecture reste une robustesse.
    """
    if direction not in ("inferieur", "superieur"):
        return {"interpretable": False, "test": "tendance_fenetre_glissante",
                "motif": "direction ∈ {inferieur, superieur} exigée "
                         "(sens du franchissement de seuil)"}
    propres = sorted(((float(p["mois"]), float(p["valeur"])) for p in points),
                     key=lambda z: z[0])
    if len(propres) < 6 or fenetre_mois <= 0 or horizon_mois <= 0 or horizon_mois > 12:
        return {"interpretable": False, "test": "tendance_fenetre_glissante",
                "motif": "≥ 6 points, fenetre > 0, 0 < horizon ≤ 12 mois exigés"}
    origines = sorted({m for m, _ in propres})
    fenetres = []
    for t0 in origines:
        local = [(m, v) for m, v in propres if t0 <= m <= t0 + fenetre_mois]
        if len(local) < 4 or len({m for m, _ in local}) < 2:
            continue                        # fenêtre non couverte : sautée
        t_obs = [m for m, _ in local]
        v_obs = [v for _, v in local]
        nn = len(local)
        mx, my = _moy(t_obs), _moy(v_obs)
        sxx = sum((t - mx) ** 2 for t in t_obs)
        beta = sum((t - mx) * (v - my) for t, v in local) / sxx
        a0 = my - beta * mx
        residus = [v - (a0 + beta * t) for t, v in local]
        sd_r = math.sqrt(sum(r * r for r in residus) / (nn - 2))
        t_pred = t0 + fenetre_mois + horizon_mois
        # extrapolation STRICTEMENT bornée : la prévision ne dépasse jamais
        # la fin de fenêtre que de l'horizon déclaré (≤ 12 mois, garde en tête)
        pred = a0 + beta * t_pred
        tc = dist.t_ppf(1 - alpha / 2, nn - 2)
        se_pr = sd_r * math.sqrt(1 / nn + (t_pred - mx) ** 2 / sxx)
        ic_lo, ic_hi = pred - tc * se_pr, pred + tc * se_pr
        # pire borne dans le sens du franchissement
        borne = ic_lo if direction == "inferieur" else ic_hi
        franchit = borne <= spec_limite if direction == "inferieur" \
            else borne >= spec_limite
        fenetres.append({"t0": t0, "pente": beta,
                         "prevision": pred, "borne_pessime": borne,
                         "demi_ic": (ic_hi - ic_lo) / 2.0,
                         "franchit": franchit,
                         "mois_franchissement_prevu": t0 + fenetre_mois
                         + horizon_mois})
    if not fenetres:
        return {"interpretable": False, "test": "tendance_fenetre_glissante",
                "motif": "aucune fenêtre d'au moins 4 points couverte "
                         "par la série"}
    franchies = [f for f in fenetres if f["franchit"]]
    premiere = franchies[0] if franchies else None
    return {"interpretable": True, "test": "tendance_fenetre_glissante",
            "n_points": len(propres), "fenetre_mois": fenetre_mois,
            "horizon_mois": horizon_mois, "spec_limite": spec_limite,
            "direction": direction, "n_fenetres": len(fenetres),
            "fenetres": fenetres,
            "premier_t0_franchissement": (premiere["t0"]
                                          if premiere else None),
            "premier_mois_franchissement_prevu": (
                premiere["mois_franchissement_prevu"] if premiere else None),
            "verdict": (f"franchissement détectable dès t0="
                        f"{premiere['t0']:g} (prévu à "
                        f"{premiere['mois_franchissement_prevu']:g})"
                        if premiere
                        else "aucun franchissement de la pire borne IC95 "
                             "sur l'horizon balayé"),
            "hypothese": ("extrapolation LINÉAIRE LOCALE (OLS par fenêtre) — "
                          "robustesse de détection, pas un modèle mécaniste ; "
                          "à pré-déclarer au SAP (fenêtre, horizon, seuil)")}


# ------------------------------------------------------- ajustement multivarié
#
# Régression logistique (IRLS) et Cox à risques proportionnels (Newton sur la
# vraisemblance partielle de Breslow) — 100 % stdlib, déterministes, qualifiées
# contre oracle indépendant (BFGS scipy, cf. docs/QUALIFICATION_SCIPY.md).
# CADRE : uniquement derrière SAP verrouillé (G3) avec covariables et EPV_min
# pré-déclarés — jamais d'ajustement post-hoc. Tous les garde-fous rendent
# `interpretable: False` (fail-closed) : aucun OR/HR aberrant n'est émis.

MAX_COVARIABLES = 14    # + intercept ≤ 15 paramètres — borne dure anti sur-ajustement
SEP_COEF = 15.0         # |β| > 15 ⇒ OR/HR > 3e6 : quasi-séparation évidente
CONV_ITER = 60          # budget d'itérations IRLS/Newton


def _gauss_resoudre(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Résout a·x = b (élimination de Gauss, pivot partiel). None si un pivot
    < 1e-12 : matrice singulière ⇒ colinéarité exacte, fail-closed."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _gauss_inverser(a: list[list[float]]) -> list[list[float]] | None:
    """Inverse (Gauss-Jordan, pivot partiel). None si singulière."""
    n = len(a)
    m = [a[i][:] + [1.0 if j == i else 0.0 for j in range(n)]
         for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                if f:
                    for c in range(2 * n):
                        m[r][c] -= f * m[col][c]
    return [[m[r][n + c] / m[r][r] for c in range(n)] for r in range(n)]


def _sigmoide(eta: float) -> float:
    return 1.0 / (1.0 + math.exp(-eta)) if eta >= 0 else (
        math.exp(eta) / (1.0 + math.exp(eta)))


def _controles_communs(n: int, x: dict, p: int, evenements: int,
                       epv_min: float, nom_op: str) -> dict | None:
    """Garde-fous partagés des modèles multivariés — dict d'échec ou None."""
    if p < 1 or p > MAX_COVARIABLES:
        return {"interpretable": False, "test": nom_op,
                "motif": f"{p} covariables hors borne [1 ; {MAX_COVARIABLES}]"}
    longueurs = {len(v) for v in x.values()}
    if n < 20 or longueurs != {n}:
        return {"interpretable": False, "test": nom_op,
                "motif": "n < 20 ou covariables désalignées"}
    if evenements < 1:
        return {"interpretable": False, "test": nom_op,
                "motif": "aucun événement observé (issue constante)"}
    epv = evenements / p
    if epv < epv_min:
        return {"interpretable": False, "test": nom_op,
                "motif": (f"EPV insuffisant : {epv:.1f} < {epv_min:g} "
                          f"(événements/covariable — sur-ajustement certain)"),
                "epv": round(epv, 3)}
    for nom, col in x.items():
        if min(col) == max(col):   # exact et sans unité : colonne constante
            return {"interpretable": False, "test": nom_op,
                    "motif": f"covariable {nom!r} constante (variance nulle)"}
    return None


def _coefs_sortie(beta: list[float], cov: list[list[float]], noms: list[str],
                  alpha: float, mesure: str) -> list[dict]:
    z = dist.norm_ppf(1 - alpha / 2)
    out = []
    for j, nom in enumerate(noms):
        b = beta[j]
        se = math.sqrt(cov[j][j])
        zw = b / se if se > 0 else 0.0
        out.append({"covariable": nom, "beta": b, "se": se, "z": zw,
                    "p_valeur": min(1.0, 2 * dist.norm_sf(abs(zw))),
                    mesure: math.exp(b),
                    f"ic95_{'or' if mesure == 'odds_ratio' else 'hr'}":
                        [math.exp(b - z * se), math.exp(b + z * se)]})
    return out


def regression_logistique(y: list[int], x: dict[str, list[float]],
                          epv_min: float = 10.0, max_iter: int = CONV_ITER,
                          tol: float = 1e-9, alpha: float = 0.05,
                          seed: int = 0) -> dict:
    """Régression logistique binaire par IRLS (ré-estimation itérative des
    moindres carrés pondérés = Newton-Raphson).

    CADRE D'EMPLOI : ajustement multivarié PRÉ-DÉCLARÉ au SAP (G3) pour les
    designs observationnels — l'OR « ajusté » reste une ASSOCIATION (confusion
    non mesurée possible), le lexique causal demeure interdit.
    Garde-fous fail-closed : EPV (événements par variable) < seuil, covariable
    constante, colinéarité exacte, quasi-séparation ou non-convergence ⇒
    `interpretable: False` (revue statisticien — aucun OR aberrant émis).
    """
    noms = list(x)
    p, n = len(noms), len(y)
    ens = set(y)
    if ens - {0, 1}:
        return {"interpretable": False, "test": "regression_logistique",
                "motif": "issue non binaire (0/1 exigé)"}
    ev = min(sum(y), n - sum(y))
    refuse = _controles_communs(n, x, p, ev, epv_min, "regression_logistique")
    if refuse:
        return refuse

    X = [[1.0] + [float(x[nom][i]) for nom in noms] for i in range(n)]
    p0 = min(max(sum(y) / n, 1e-6), 1 - 1e-6)
    beta = [math.log(p0 / (1 - p0))] + [0.0] * p

    converge, it = False, 0
    for it in range(1, max_iter + 1):
        eta = [sum(beta[j] * X[i][j] for j in range(p + 1)) for i in range(n)]
        mu = [_sigmoide(e) for e in eta]
        w = [max(m_ * (1 - m_), 1e-9) for m_ in mu]
        z = [eta[i] + (y[i] - mu[i]) / w[i] for i in range(n)]
        A = [[sum(w[i] * X[i][j] * X[i][k] for i in range(n))
              for k in range(p + 1)] for j in range(p + 1)]
        b = [sum(w[i] * X[i][j] * z[i] for i in range(n))
             for j in range(p + 1)]
        nouveau = _gauss_resoudre(A, b)
        if nouveau is None:
            return {"interpretable": False, "test": "regression_logistique",
                    "motif": "colinéarité exacte entre covariables (matrice "
                             "d'information singulière)"}
        beta, delta = nouveau, max(abs(nouveau[j] - beta[j])
                                   for j in range(p + 1))
        if max(abs(bj) for bj in beta) > SEP_COEF:
            return {"interpretable": False, "test": "regression_logistique",
                    "motif": ("quasi-séparation (|β| > "
                              f"{SEP_COEF:g}) : modèle non identifiable — "
                              "revue statisticien exigée")}
        if delta < tol:
            converge = True
            break
    if not converge:
        return {"interpretable": False, "test": "regression_logistique",
                "motif": f"non-convergence après {max_iter} itérations IRLS"}

    eta = [sum(beta[j] * X[i][j] for j in range(p + 1)) for i in range(n)]
    mu = [_sigmoide(e) for e in eta]
    ll = sum(y[i] * math.log(max(mu[i], 1e-300))
             + (1 - y[i]) * math.log(max(1 - mu[i], 1e-300)) for i in range(n))
    ll0 = n * (p0 * math.log(p0) + (1 - p0) * math.log(1 - p0))
    A = [[sum(mu[i] * (1 - mu[i]) * X[i][j] * X[i][k] for i in range(n))
          for k in range(p + 1)] for j in range(p + 1)]
    cov = _gauss_inverser(A)
    if cov is None:
        return {"interpretable": False, "test": "regression_logistique",
                "motif": "matrice de covariance singulière à l'optimum"}
    chi2 = 2 * (ll - ll0)
    z_int = dist.norm_ppf(1 - alpha / 2)
    se_int = math.sqrt(cov[0][0])
    return {
        "interpretable": True, "test": "regression_logistique",
        "n": n, "n_evenements_minoritaire": ev, "epv": round(ev / p, 3),
        "n_covariables": p,
        "coefficients": _coefs_sortie(beta[1:], [r[1:] for r in cov[1:]],
                                      noms, alpha, "odds_ratio"),
        "intercept": {"beta": beta[0], "se": se_int,
                      "p_valeur": min(1.0, 2 * dist.norm_sf(
                          abs(beta[0] / se_int) if se_int > 0 else 0.0))},
        "log_vraisemblance": ll, "ll_modele_nul": ll0,
        "chi2_modele": chi2, "ddl_modele": p,
        "p_valeur_modele": dist.chi2_sf(chi2, p),
        "pseudo_r2_mcfadden": 1 - ll / ll0 if ll0 else 0.0,
        "aic": -2 * ll + 2 * (p + 1),
        "iterations": it, "seuil_epv": epv_min,
        "hypothese": ("linéarité du logit pour les covariables continues — "
                      "non testée ici (diagnostics hors MVP, décision G3)"),
        "lecture": ("association AJUSTÉE sur les covariables du SAP — reste "
                    "non causale (confusion non mesurée possible) ; "
                    "OR = odds ratio conditionnel aux autres covariables"),
        "note_lexique": "tournures d'association uniquement (⇒ relecture)"}


def cox_ph(temps: list[float], evenements: list[int],
           x: dict[str, list[float]], epv_min: float = 10.0,
           max_iter: int = CONV_ITER, tol: float = 1e-9,
           alpha: float = 0.05, seed: int = 0) -> dict:
    """Cox à hasards proportionnels — vraisemblance partielle de Breslow
    (ex æquo), itérations de Newton avec gradient et information exacts.

    CADRE D'EMPLOI : ajustement multivarié PRÉ-DÉCLARÉ au SAP (G3). L'hypothèse
    de risques PROPORTIONNELS n'est PAS testée ici (résidus de Schoenfeld hors
    MVP) : elle est déclarée à l'humain au G3 et reprise en section limites.
    Mêmes garde-fous fail-closed que la logistique (EPV, colinéarité,
    quasi-séparation, non-convergence).
    """
    noms = list(x)
    p, n = len(noms), len(temps)
    refuse = _controles_communs(n, x, p, sum(evenements), epv_min, "cox_ph")
    if refuse:
        return refuse
    if set(evenements) - {0, 1}:
        return {"interpretable": False, "test": "cox_ph",
                "motif": "indicatrice d'événement non binaire (0/1 exigée)"}
    if any(t <= 0 for t in temps):
        return {"interpretable": False, "test": "cox_ph",
                "motif": "temps strictement positifs exigés"}
    ev = sum(evenements)

    # centrage des covariables : invariant exact du modèle (la constante se
    # simplifie dans chaque contraste de la vraisemblance partielle) et stabilise
    # exp(η) — documenté pour la qualification bit-à-bit vs oracle.
    moy = {nom: _moy(x[nom]) for nom in noms}
    X = [[float(x[nom][i]) - moy[nom] for nom in noms] for i in range(n)]
    t_evt = sorted({temps[i] for i in range(n) if evenements[i] == 1})

    def _score_info(beta):
        eta = [min(50.0, max(-50.0, sum(beta[j] * X[i][j] for j in range(p))))
               for i in range(n)]
        w = [math.exp(e) for e in eta]
        ll = u = None
        U = [0.0] * p
        I = [[0.0] * p for _ in range(p)]
        ll = 0.0
        for t in t_evt:
            ris = [i for i in range(n) if temps[i] >= t]
            d = sum(evenements[i] for i in ris if temps[i] == t)
            s0 = sum(w[i] for i in ris)
            s1 = [sum(w[i] * X[i][j] for i in ris) for j in range(p)]
            s2 = [[sum(w[i] * X[i][j] * X[i][k] for i in ris)
                   for k in range(p)] for j in range(p)]
            for i in range(n):
                if temps[i] == t and evenements[i] == 1:
                    ll += eta[i] - math.log(s0)
                    for j in range(p):
                        U[j] += X[i][j] - s1[j] / s0
            for j in range(p):
                for k in range(p):
                    I[j][k] += d * (s2[j][k] / s0
                                    - s1[j] * s1[k] / (s0 * s0))
        return ll, U, I

    beta = [0.0] * p
    ll0, u0, i0 = _score_info(beta)
    converge, it = False, 0
    for it in range(1, max_iter + 1):
        _, U, I = _score_info(beta)
        delta = _gauss_resoudre(I, U)
        if delta is None:
            return {"interpretable": False, "test": "cox_ph",
                    "motif": "colinéarité exacte entre covariables (information "
                             "singulière)"}
        beta = [beta[j] + delta[j] for j in range(p)]
        if max(abs(bj) for bj in beta) > SEP_COEF:
            return {"interpretable": False, "test": "cox_ph",
                    "motif": ("quasi-séparation (|β| > "
                              f"{SEP_COEF:g}) : modèle non identifiable — "
                              "revue statisticien exigée")}
        if max(abs(dj) for dj in delta) < tol:
            converge = True
            break
    if not converge:
        return {"interpretable": False, "test": "cox_ph",
                "motif": f"non-convergence après {max_iter} itérations Newton"}

    ll, _, I = _score_info(beta)
    cov = _gauss_inverser(I)
    if cov is None:
        return {"interpretable": False, "test": "cox_ph",
                "motif": "matrice de covariance singulière à l'optimum"}
    i0i = _gauss_inverser(i0)
    score = (sum(u0[j] * i0i[j][k] * u0[k] for j in range(p) for k in range(p))
             if i0i is not None else None)
    return {
        "interpretable": True, "test": "cox_ph", "n": n,
        "n_evenements": ev, "epv": round(ev / p, 3), "n_covariables": p,
        "coefficients": _coefs_sortie(beta, cov, noms, alpha, "hazard_ratio"),
        "log_vraisemblance_partielle": ll, "ll_partielle_nulle": ll0,
        "chi2_score_modele": score, "ddl_modele": p,
        "p_valeur_modele": (dist.chi2_sf(score, p)
                            if score is not None else None),
        "iterations": it, "seuil_epv": epv_min, "methode_ties": "Breslow",
        "hypothese": ("risques relatifs PROPORTIONNELS supposés — NON testée "
                      "ici (Schoenfeld hors MVP) : confirmée à G3, reprise en "
                      "limites du rapport"),
        "lecture": ("association AJUSTÉE (HR conditionnel aux covariables du "
                    "SAP) — reste non causale ; HR > 1 = survenue plus rapide"),
        "note_lexique": "tournures d'association uniquement (⇒ relecture)"}


# ------------------------------------------------------------------
# Ajustement de multiplicité (Holm-Bonferroni) — pour secondaires
# confirmatoires PRÉ-DÉCLARÉS au SAP.
# Implémentation 100 % stdlib, déterministe, FWER contrôlé.
# Utilisé uniquement quand endpoints_secondaires confirmatoires déclarés
# (gabarit ou LLM) — jamais post-hoc. L'op est appelé depuis l'inférentiel
# sur les p des analyses secondaires confirmatoires.
# ------------------------------------------------------------------

def holm(pvals: list[float], alpha: float = 0.05, seed: int = 0) -> dict:
    """Ajustement Holm-Bonferroni (step-down) pour contrôle FWER.

    pvals : liste des p-valeurs brutes des tests secondaires confirmatoires
            (dans l'ordre de déclaration au SAP).
    Retourne p ajustés (monotones), rejets à alpha, etc.
    Si aucun p ou p<0 ou >1 : non interprétable (fail-closed).
    """
    if not pvals or not all(isinstance(p, (int, float)) and 0.0 <= float(p) <= 1.0
                            for p in pvals):
        return {"interpretable": False, "test": "holm",
                "motif": "liste p-valeurs non vide de floats dans [0,1] exigée"}
    k = len(pvals)
    if k == 0:
        return {"interpretable": False, "test": "holm", "motif": "k=0"}
    # indices originaux, triés par p croissant
    idx_sorted = sorted(range(k), key=lambda i: float(pvals[i]))
    p_adj = [0.0] * k
    for rank, orig_i in enumerate(idx_sorted):
        m = k - rank
        p_adj[orig_i] = min(1.0, float(pvals[orig_i]) * m)
    # rendre monotone non-décroissant dans l'ordre trié (step-down)
    for r in range(1, k):
        prev_i = idx_sorted[r - 1]
        curr_i = idx_sorted[r]
        p_adj[curr_i] = max(p_adj[curr_i], p_adj[prev_i])
    rej = [pa <= alpha for pa in p_adj]
    return {
        "interpretable": True,
        "test": "holm",
        "k": k,
        "p_original": [float(p) for p in pvals],
        "p_adjusted": p_adj,
        "rejected_at_alpha": rej,
        "alpha": alpha,
        "methode": "holm-bonferroni-stepdown",
        "hypothese": ("contrôle FWER fort (family-wise error rate) pour "
                      "secondaires confirmatoires pré-déclarés — Holm plus "
                      "puissant que Bonferroni ; lecture : rejet si p_adj <= alpha")
    }


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
    "pooling_rubin":         {"fn": pooling_rubin,         "version": "1.0.0"},
    "tost_equivalence":      {"fn": tost_equivalence,      "version": "1.0.0"},
    "tendance_lineaire":     {"fn": tendance_lineaire,     "version": "1.0.0"},
    "odds_ratio_cas_temoins": {"fn": odds_ratio_cas_temoins, "version": "1.0.0"},
    "or_apparie":            {"fn": or_apparie,            "version": "1.0.0"},
    "risque_relatif_cohorte": {"fn": risque_relatif_cohorte, "version": "1.0.0"},
    "km_logrank_hr":         {"fn": km_logrank_hr,         "version": "1.0.0"},
    "regression_logistique": {"fn": regression_logistique, "version": "1.0.0"},
    "cox_ph":                {"fn": cox_ph,                "version": "1.0.0"},
    "tendance_fenetre_glissante": {"fn": tendance_fenetre_glissante,
                                   "version": "1.0.0"},
    # 1.1.0 : ajout ADDITIF du marquage de renversement de l'effet
    # (`renverse` par δ, `delta_renversement`, verdict explicitant l'artefact
    # de « re-significativité » au-delà de la traversée de zéro) — calculs
    # θ/se/p/ddl et bascule INVARIANTS (qualifiés, cf. docs/QUALIFICATION)
    "tipping_point_mnar_smd": {"fn": lambda *a, **kw: __import__(
        "stats_catalogue.imputation", fromlist=["tipping_point_mnar_smd"]
    ).tipping_point_mnar_smd(*a, **kw), "version": "1.1.0"},
    "holm": {"fn": holm, "version": "1.0.0"},
}


def executer(nom_op: str, seed: int, **params) -> dict:
    """Point d'entrée unique — l'agent inférentiel ne peut RIEN appeler hors catalogue."""
    if nom_op not in OPS:
        from core.exceptions import ErreurLogique
        raise ErreurLogique(f"op hors catalogue : {nom_op!r} (blocage SAP)")
    return OPS[nom_op]["fn"](seed=seed, **params)
