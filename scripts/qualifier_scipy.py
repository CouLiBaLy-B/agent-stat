#!/usr/bin/env python3
"""Générateur de la référence de qualification — OUTIL DÉVELOPPEUR UNIQUEMENT.

⚠ NÉCESSITE scipy (épinglé — voir docs/QUALIFICATION_SCIPY.md) ; JAMAIS
importé ni exécuté par le pipeline, les démos ou la CI des tests.
Le produit livré reste 100 % stdlib : ce script produit des VALEURS GELÉES
(`tests/qualification/reference_scipy.py`) contre lesquelles les tests
(stdlib) qualifient nos implémentations numériques.

Le script n'importe AUCUN module de stats_catalogue : les réponses de
référence sont calculées par scipy ou par des ré-implémentations indépendantes
des formules publiées (Woolf/Katz/Wilson/Newcombe, log-rang Mantel + Peto,
TOST, pooling de Rubin, OLS) adossées à scipy pour les primitives.

Usage :
    python3 -m venv /tmp/qualif && /tmp/qualif/bin/pip install "scipy==1.17.1"
    /tmp/qualif/bin/python scripts/qualifier_scipy.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

import numpy as np                                  # noqa: E402
import scipy                                        # noqa: E402
from scipy import special, stats                             # noqa: E402
from tests.qualification.jeux import jeux           # noqa: E402

SCIPY_EPINGLET = "1.17.1"
ALPHA = 0.05
J = jeux()


def _x(v: float) -> float:
    return float(v)


def réf_dist() -> dict:
    """Grilles de qualification de stats_catalogue/dist.py."""
    r: dict = {}
    xs = [-8.0, -6.0, -4.0, -3.5, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0,
          0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 6.0, 8.0]
    r["norm_cdf"] = [[x, _x(stats.norm.cdf(x))] for x in xs]
    r["norm_sf"] = [[x, _x(stats.norm.sf(x))] for x in xs]
    ps = [1e-6, 1e-4, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
          0.75, 0.9, 0.95, 0.975, 0.99, 0.995, 0.999, 0.9999, 0.999999]
    r["norm_ppf"] = [[p, _x(stats.norm.ppf(p))] for p in ps]
    ddls = [1.0, 2.0, 5.0, 10.0, 29.0, 60.0, 120.0, 500.0]
    r["t_cdf"] = [[t, d, _x(stats.t.cdf(t, d))]
                  for d in ddls for t in (-6.0, -2.5, -1.0, 0.0, 1.0, 2.5, 6.0)]
    r["t_sf"] = [[t, d, _x(stats.t.sf(t, d))] for d in ddls
                 for t in (0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0)]
    r["t_ppf"] = [[p, d, _x(stats.t.ppf(p, d))] for d in ddls
                  for p in (0.5, 0.75, 0.9, 0.95, 0.975, 0.99, 0.995, 0.999)]
    ddls_c = [1.0, 2.0, 4.0, 9.0, 25.0, 100.0]
    xs_c = [0.01, 0.1, 0.5, 1.0, 2.0, 3.84, 5.0, 10.0, 20.0, 50.0, 100.0]
    r["chi2_cdf"] = [[x, d, _x(stats.chi2.cdf(x, d))] for d in ddls_c
                     for x in xs_c]
    r["chi2_sf"] = [[x, d, _x(stats.chi2.sf(x, d))] for d in ddls_c
                    for x in xs_c]
    couples = [(1.0, 1.0), (2.0, 5.0), (5.0, 10.0), (3.0, 29.0),
               (10.0, 60.0), (24.0, 120.0)]
    r["f_cdf"] = [[x, d1, d2, _x(stats.f.cdf(x, d1, d2))] for d1, d2 in couples
                  for x in (0.05, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)]
    r["f_sf"] = [[x, d1, d2, _x(stats.f.sf(x, d1, d2))] for d1, d2 in couples
                 for x in (0.05, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)]
    binoms = [(0, 10, 0.3), (2, 10, 0.5), (5, 30, 0.2), (8, 29, 0.3),
              (15, 40, 0.5), (0, 20, 0.1), (45, 60, 0.75)]
    r["binom_cdf"] = [[k, n, p, _x(stats.binom.cdf(k, n, p))]
                      for k, n, p in binoms]
    betas = [(0.025, 3.0, 10.0), (0.975, 4.0, 10.0), (0.025, 1.0, 20.0),
             (0.975, 1.0, 30.0), (0.5, 5.0, 5.0), (0.05, 2.0, 30.0)]
    r["beta_ppf"] = [[p, a, b, _x(stats.beta.ppf(p, a, b))] for p, a, b in betas]
    incbs = [(3.0, 2.0, 0.4), (0.5, 1.5, 0.2), (5.0, 10.0, 0.7),
             (1.0, 1.0, 0.3), (0.2, 0.5, 0.05), (2.0, 0.5, 0.8)]
    r["incbeta"] = [[a, b, x, _x(special.betainc(a, b, x))] for a, b, x in incbs]
    gammas = [(1.0, 2.0), (0.5, 1.5), (2.0, 0.7), (5.0, 3.0), (0.5, 0.05),
              (12.5, 9.9)]
    r["gamma_p"] = [[a, x, _x(special.gammainc(a, x))] for a, x in gammas]
    r["gamma_q"] = [[a, x, _x(special.gammaincc(a, x))] for a, x in gammas]
    return r


def _sd(xs: list[float]) -> float:
    a = np.asarray(xs, dtype=float)
    return float(a.std(ddof=1))


def _wilson(k: int, n: int) -> tuple[float, float]:
    z = float(stats.norm.ppf(1 - ALPHA / 2))
    ph = k / n
    den = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / den
    marge = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - marge), min(1.0, centre + marge)


def _fisher_valeurs(a: int, b: int, c: int, d: int) -> dict:
    """Contre-implémentation indépendante OR/RR/CMH + Fisher (scipy)."""
    z = float(stats.norm.ppf(1 - ALPHA / 2))
    p_f = float(stats.fisher_exact([[a, b], [c, d]])[1])
    zero = min(a, b, c, d) == 0
    aa, bb, cc, dd = (x + 0.5 if zero else x for x in (a, b, c, d))
    or_ = (aa * dd) / (bb * cc)
    se_or = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    rr = (aa / (aa + bb)) / (cc / (cc + dd))
    se_rr = math.sqrt(1 / aa - 1 / (aa + bb) + 1 / cc - 1 / (cc + dd))
    r1, r0 = a / (a + b), c / (c + d)
    w1, w0 = _wilson(a, a + b), _wilson(c, c + d)
    diff = r1 - r0
    icd = (diff - math.sqrt((r1 - w1[0]) ** 2 + (w0[1] - r0) ** 2),
           diff + math.sqrt((w1[1] - r1) ** 2 + (r0 - w0[0]) ** 2))
    return {
        "fisher_p": p_f,
        "or": or_, "ic95_or": [math.exp(math.log(or_) - z * se_or),
                               math.exp(math.log(or_) + z * se_or)],
        "se_log_or": se_or,
        "rr": rr, "ic95_rr": [math.exp(math.log(rr) - z * se_rr),
                              math.exp(math.log(rr) + z * se_rr)],
        "se_log_rr": se_rr,
        "difference_risques": diff, "ic95_diff": [icd[0], icd[1]],
        "wilson_1": list(w1), "wilson_0": list(w0),
    }


def _km_oracle(t1, e1, t2, e2) -> dict:
    """Ré-implémentation indépendante (tabulate par dict) log-rang Mantel +
    prédiction Peto, primitives scipy pour les queues de loi."""
    def km(ts, es):
        pts, s = {}, 1.0
        for t in sorted(set(ts[i] for i in range(len(ts)) if es[i] == 1)):
            risque = sum(1 for x in ts if x >= t)
            survenus = sum(1 for x, e in zip(ts, es) if x == t and e == 1)
            s *= 1 - survenus / risque
            pts[t] = s
        med = next((t for t in sorted(pts) if pts[t] <= 0.5), None)
        return med, (pts[max(pts)] if pts else 1.0)
    med1, fin1 = km(t1, e1)
    med2, fin2 = km(t2, e2)
    temps_evt = sorted(set(t for t, e in zip(t1 + t2, e1 + e2) if e == 1))
    e_att, var = 0.0, 0.0
    for t in temps_evt:
        r1 = sum(1 for x in t1 if x >= t)
        r2 = sum(1 for x in t2 if x >= t)
        d1 = sum(1 for x, e in zip(t1, e1) if x == t and e == 1)
        d2 = sum(1 for x, e in zip(t2, e2) if x == t and e == 1)
        r, dtot = r1 + r2, d1 + d2
        e_att += dtot * r1 / r
        if r > 1:
            var += r1 * r2 * dtot * (r - dtot) / (r * r * (r - 1))
    o1 = sum(e1)
    ecart = o1 - e_att
    chi2 = ecart * ecart / var
    log_hr = ecart / var
    se = 1.0 / math.sqrt(var)
    z = float(stats.norm.ppf(1 - ALPHA / 2))
    return {"E1": e_att, "variance_logrank": var, "chi2_logrank": chi2,
            "p_valeur": _x(stats.chi2.sf(chi2, 1)),
            "hr": math.exp(log_hr),
            "ic95_hr": [math.exp(log_hr - z * se), math.exp(log_hr + z * se)],
            "mediane_g1": med1, "mediane_g2": med2,
            "survie_g1": fin1, "survie_g2": fin2}


def _logit_oracle(y: list, x: dict) -> dict:
    """Contre-implémentation INDÉPENDANTE de la logistique : maximisation
    directe de la log-vraisemblance par BFGS (scipy.optimize) avec jacobien
    exact numpy — aucun IRLS, partageant seulement les primitives de lois."""
    from scipy import optimize
    noms = list(x)
    yy = np.asarray(y, dtype=float)
    X = np.column_stack([np.ones(len(y))] +
                        [np.asarray(x[k], dtype=float) for k in noms])
    p = X.shape[1]

    def f(beta):
        eta = X @ beta
        return float(np.sum(np.logaddexp(0.0, eta)) - yy @ eta)

    def jac(beta):
        eta = X @ beta
        mu = 1.0 / (1.0 + np.exp(-eta))
        return X.T @ (mu - yy)

    res = optimize.minimize(f, np.zeros(p), jac=jac, method="BFGS",
                            options={"gtol": 1e-8, "maxiter": 2000})
    # la line-search de BFGS échoue parfois par "precision loss" alors que
    # l'optimum est atteint — critère propre : gradient final ~ 0.
    gn = float(np.max(np.abs(jac(res.x))))
    assert gn < 1e-5, f"BFGS logistique : gradients {gn:.3g}"
    beta = res.x
    eta = X @ beta
    mu = 1.0 / (1.0 + np.exp(-eta))
    w = mu * (1 - mu)
    info = (X * w[:, None]).T @ X
    cov = np.linalg.inv(info)
    se = np.sqrt(np.diag(cov))
    z95 = float(stats.norm.ppf(1 - ALPHA / 2))
    ll = -f(beta)
    p0 = float(np.clip(yy.mean(), 1e-6, 1 - 1e-6))
    ll0 = len(y) * (p0 * math.log(p0) + (1 - p0) * math.log(1 - p0))
    chi2 = 2 * (ll - ll0)
    coefs = []
    for j, nom in enumerate(noms, start=1):
        b, s = float(beta[j]), float(se[j])
        coefs.append({"covariable": nom, "beta": b, "se": s,
                      "p_valeur": _x(2 * stats.norm.sf(abs(b / s))),
                      "odds_ratio": math.exp(b),
                      "ic95_or": [math.exp(b - z95 * s),
                                  math.exp(b + z95 * s)]})
    return {"coefficients": coefs,
            "intercept_beta": float(beta[0]), "intercept_se": float(se[0]),
            "log_vraisemblance": ll, "ll_modele_nul": ll0,
            "chi2_modele": chi2, "p_valeur_modele": _x(stats.chi2.sf(chi2, len(noms))),
            "pseudo_r2_mcfadden": 1 - ll / ll0,
            "aic": -2 * ll + 2 * (len(noms) + 1)}


def _cox_oracle(temps: list, evenements: list, x: dict) -> dict:
    """Contre-implémentation INDÉPENDANTE du Cox-Breslow : BFGS sur la
    log-vraisemblance partielle négative numpy + jacobien exact ; information
    observée recalculée à l'optimum et inversée par numpy.linalg."""
    from scipy import optimize
    noms = list(x)
    n = len(temps)
    t = np.asarray(temps, dtype=float)
    e = np.asarray(evenements, dtype=float)
    # même centrage que l'implémentation qualifiée : invariant exact du modèle
    X = np.column_stack([np.asarray(x[k], dtype=float) - np.mean(x[k])
                         for k in noms])
    p = X.shape[1]
    t_evt = sorted(set(t[i] for i in range(n) if e[i] == 1.0))
    risques = [np.flatnonzero(t >= te) for te in t_evt]
    evts = [np.flatnonzero((t == te) & (e == 1.0)) for te in t_evt]

    def f(beta):
        eta = np.clip(X @ beta, -50, 50)
        w = np.exp(eta)
        ll = 0.0
        for ris, ev in zip(risques, evts):
            ll += float(eta[ev].sum()) - len(ev) * math.log(float(w[ris].sum()))
        return -ll

    def jac(beta):
        eta = np.clip(X @ beta, -50, 50)
        w = np.exp(eta)
        g = np.zeros(p)
        for ris, ev in zip(risques, evts):
            s0 = w[ris].sum()
            s1 = (w[ris, None] * X[ris]).sum(axis=0)
            g += X[ev].sum(axis=0) - len(ev) * s1 / s0
        return -g

    res = optimize.minimize(f, np.zeros(p), jac=jac, method="BFGS",
                            options={"gtol": 1e-8, "maxiter": 2000})
    # idem — critère de convergence propre sur le gradient final.
    gn = float(np.max(np.abs(jac(res.x))))
    assert gn < 1e-5, f"BFGS cox : gradients {gn:.3g}"
    beta = res.x
    eta = np.clip(X @ beta, -50, 50)
    w = np.exp(eta)
    info = np.zeros((p, p))
    for ris, ev in zip(risques, evts):
        d = len(ev)
        s0 = w[ris].sum()
        s1 = (w[ris, None] * X[ris]).sum(axis=0)
        xr = w[ris, None] * X[ris]
        s2 = xr.T @ X[ris]
        info += d * (s2 / s0 - np.outer(s1, s1) / (s0 * s0))
    cov = np.linalg.inv(info)
    se = np.sqrt(np.diag(cov))
    z95 = float(stats.norm.ppf(1 - ALPHA / 2))
    # test du score à beta = 0 (identique au log-rang si 1 covariable binaire)
    eta0 = np.zeros(n)
    w0 = np.ones(n)
    u0 = np.zeros(p)
    i0 = np.zeros((p, p))
    for ris, ev in zip(risques, evts):
        d = len(ev)
        s0 = w0[ris].sum()
        s1 = (w0[ris, None] * X[ris]).sum(axis=0)
        xr = w0[ris, None] * X[ris]
        s2 = xr.T @ X[ris]
        u0 += X[ev].sum(axis=0) - d * s1 / s0
        i0 += d * (s2 / s0 - np.outer(s1, s1) / (s0 * s0))
    score = float(u0 @ np.linalg.inv(i0) @ u0)
    coefs = []
    for j, nom in enumerate(noms):
        b, s = float(beta[j]), float(se[j])
        coefs.append({"covariable": nom, "beta": b, "se": s,
                      "p_valeur": _x(2 * stats.norm.sf(abs(b / s))),
                      "hazard_ratio": math.exp(b),
                      "ic95_hr": [math.exp(b - z95 * s),
                                  math.exp(b + z95 * s)]})
    # ll partielle au point optimum (sans clip pour la valeur gelée si sûr)
    eta_fin = X @ beta
    w_fin = np.exp(np.clip(eta_fin, -50, 50))
    ll = 0.0
    for ris, ev in zip(risques, evts):
        ll += float(eta_fin[ev].sum()) - len(ev) * math.log(float(w_fin[ris].sum()))
    return {"coefficients": coefs, "log_vraisemblance_partielle": ll,
            "chi2_score_modele": score,
            "p_valeur_modele": _x(stats.chi2.sf(score, p))}


def réf_ops() -> dict:
    """Valeurs de référence des opérations du catalogue (oracles scipy)."""
    r: dict = {}
    z95 = float(stats.norm.ppf(1 - ALPHA / 2))

    # --- t Welch -------------------------------------------------------------
    for nom in ("welch_egal", "welch_inegal"):
        g1, g2 = J[nom]["g1"], J[nom]["g2"]
        tstat, p = stats.ttest_ind(g1, g2, equal_var=False)
        n1, n2 = len(g1), len(g2)
        v1, v2 = _sd(g1) ** 2, _sd(g2) ** 2
        ddl = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1)
                                          + (v2 / n2) ** 2 / (n2 - 1))
        se = math.sqrt(v1 / n1 + v2 / n2)
        r[f"t_test_welch:{nom}"] = {
            "t": _x(tstat), "ddl": _x(ddl), "se": se,
            "p_valeur": _x(p),
            "tc95": _x(stats.t.ppf(1 - ALPHA / 2, ddl)),
            "moyenne1": float(np.mean(g1)), "moyenne2": float(np.mean(g2))}

    # --- TOST ------------------------------------------------------------------
    g1, g2, marge = J["tost"]["g1"], J["tost"]["g2"], J["tost"]["marge"]
    n1, n2 = len(g1), len(g2)
    v1, v2 = _sd(g1) ** 2, _sd(g2) ** 2
    se = math.sqrt(v1 / n1 + v2 / n2)
    diff = float(np.mean(g1)) - float(np.mean(g2))
    ddl = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1)
                                      + (v2 / n2) ** 2 / (n2 - 1))
    r["tost_equivalence"] = {
        "p_tost": max(_x(stats.t.sf((diff + marge) / se, ddl)),
                      _x(stats.t.sf((marge - diff) / se, ddl))),
        "tc90": _x(stats.t.ppf(1 - ALPHA, ddl)), "diff": diff, "se": se,
        "ddl": _x(ddl)}

    # --- Mann-Whitney (continuité + ex æquo, asymptotique) ----------------------
    for nom in ("mw_ties", "mw_continu"):
        g1, g2 = J[nom]["g1"], J[nom]["g2"]
        res = stats.mannwhitneyu(g1, g2, use_continuity=True,
                                 alternative="two-sided", method="asymptotic")
        r[f"mann_whitney:{nom}"] = {
            "U1": _x(res.statistic), "p_valeur": _x(res.pvalue)}

    # --- Kruskal-Wallis ----------------------------------------------------------
    kr = J["kruskal"]
    kw = stats.kruskal(kr["a"], kr["b"], kr["c"])
    r["kruskal_wallis:sans_ties"] = {"statistique_h": _x(kw.statistic),
                                     "p_valeur": _x(kw.pvalue)}
    krt = J["kruskal_ties"]                      # scipy NE corrige pas les ties
    xs = np.concatenate([krt["a"], krt["b"]])
    rangs = stats.rankdata(xs)
    n = len(xs)
    somme = sum(rangs[: len(krt["a"])]) ** 2 / len(krt["a"]) \
        + sum(rangs[len(krt["a"]):]) ** 2 / len(krt["b"])
    h = 12.0 / (n * (n + 1)) * somme - 3 * (n + 1)
    _, comptes = np.unique(xs, return_counts=True)
    tie = int(np.sum(comptes ** 3 - comptes))
    corr = 1 - tie / (n ** 3 - n)
    r["kruskal_wallis:ties_corriges"] = {
        "statistique_h": h / corr,
        "p_valeur": _x(stats.chi2.sf(h / corr, 1)), "correction": corr}

    # --- tableaux 2x2 + Fisher -----------------------------------------------------
    for nom in ("tab_2x2", "tab_2x2_zero", "tab_2x2_gros"):
        r[f"tableau:{nom}"] = _fisher_valeurs(*[int(x) for x in J[nom]])
    for nom in ("discordants", "discordants_zero"):
        b, c = J[nom]
        p = float(stats.binomtest(min(b, c), b + c, 0.5,
                                  alternative="two-sided").pvalue)
        corr = (b == 0 or c == 0)
        bb = b + (0.5 if corr else 0.0)
        cc = c + (0.5 if corr else 0.0)
        orc = bb / cc
        se = math.sqrt(1 / bb + 1 / cc)
        r[f"mcnemar:{nom}"] = {
            "p_valeur": p, "or": orc,
            "ic95_or": [math.exp(math.log(orc) - z95 * se),
                        math.exp(math.log(orc) + z95 * se)],
            "se_log_or": se}

    # --- chi2 indépendance ------------------------------------------------------------
    tab33 = np.asarray(J["tab_3x3"], dtype=float)
    chi2, p, ddl, _ = stats.chi2_contingency(tab33, correction=False)
    n = tab33.sum()
    r["chi2_independance:tab_3x3"] = {
        "statistique": _x(chi2), "ddl": int(ddl), "p_valeur": _x(p),
        "cramers_v": math.sqrt(chi2 / (n * 2))}

    # --- Clopper-Pearson / Wilson -------------------------------------------------------
    r["clopper_pearson"] = [
        {"k": k, "n": n,
         "lo": (0.0 if k == 0 else _x(stats.beta.ppf(ALPHA / 2, k, n - k + 1))),
         "hi": (1.0 if k == n else _x(stats.beta.ppf(1 - ALPHA / 2,
                                                     k + 1, n - k)))}
        for k, n in J["cp"]]
    r["wilson"] = [{"k": k, "n": n, "ic95": list(_wilson(k, n))}
                   for k, n in J["wilson"]]

    # --- tendance linéaire ------------------------------------------------------------------
    t, y = J["tendance"]["t"], J["tendance"]["y"]
    reg = stats.linregress(t, y)
    n = len(t)
    mx = float(np.mean(t))
    sxx = float(np.sum((np.asarray(t) - mx) ** 2))
    residus = np.asarray(y) - (reg.intercept + reg.slope * np.asarray(t))
    sd_r = float(np.sqrt(np.sum(residus ** 2) / (n - 2)))
    tc = float(stats.t.ppf(1 - ALPHA / 2, n - 2))
    tmax = max(t)
    pred = reg.intercept + reg.slope * tmax
    se_pred = sd_r * math.sqrt(1 / n + (tmax - mx) ** 2 / sxx)
    r["tendance_lineaire"] = {
        "pente": _x(reg.slope), "intercept": _x(reg.intercept),
        "se_pente": _x(reg.stderr), "p_valeur_pente": _x(reg.pvalue),
        "r2": _x(reg.rvalue ** 2), "tc": tc,
        "ic95_pente": [float(reg.slope - tc * reg.stderr),
                       float(reg.slope + tc * reg.stderr)],
        "residus_sd": sd_r,
        "prevision_tmax": float(pred),
        "ic95_prevision_tmax": [pred - tc * se_pred, pred + tc * se_pred]}

    # --- survie KM / log-rang / Peto ------------------------------------------------------
    for nom in ("km_simple", "km_censure"):
        k = J[nom]
        r[f"km_logrank_hr:{nom}"] = _km_oracle(
            k["t1"], k["e1"], k["t2"], k["e2"])

    # --- pooling Rubin ------------------------------------------------------------------------
    est = J["rubin"]
    m = len(est)
    thetas = [e["theta"] for e in est]
    ubar = float(np.mean([e["se"] ** 2 for e in est]))
    qbar = float(np.mean(thetas))
    b = float(np.var(thetas, ddof=1))
    tvar = ubar + (1 + 1 / m) * b
    r_r = (1 + 1 / m) * b / ubar
    lam = (1 + 1 / m) * b / tvar
    ddl = (m - 1) / (lam * lam)
    se = math.sqrt(tvar)
    fmi = (r_r + 2 / (ddl + 3)) / (1 + r_r)
    r["pooling_rubin"] = {
        "theta_pooled": qbar, "se_pooled": se, "variance_intra": ubar,
        "variance_inter": b, "augmentation_relative": r_r, "ddl": ddl,
        "fraction_info_manquante": fmi,
        "p_valeur": _x(2 * stats.t.sf(abs(qbar / se), ddl)),
        "tc95": _x(stats.t.ppf(1 - ALPHA / 2, ddl))}

    # --- descriptif / smd / normalité --------------------------------------------------------
    xs = J["descriptif"]
    r["descriptif_continu"] = {
        "moyenne": float(np.mean(xs)), "ecart_type": _sd(xs),
        "q1": _x(np.percentile(xs, 25)), "median": _x(np.percentile(xs, 50)),
        "q3": _x(np.percentile(xs, 75)),
        "se": _sd(xs) / math.sqrt(len(xs))}
    g1, g2 = J["smd"]["g1"], J["smd"]["g2"]
    sp = math.sqrt((_sd(g1) ** 2 + _sd(g2) ** 2) / 2)
    r["smd_groupes"] = {"smd": (float(np.mean(g1)) - float(np.mean(g2))) / sp}
    for nom in ("normalite_ok", "normalite_ko"):
        xs = J[nom]
        # scipy 1.17 : method='interpolate' exigé pour obtenir la p-value
        # (interpolée des tables précalculées, plafonnée 0,01 ≤ p ≤ 0,15) ;
        # la statistique retournée est la A² BRUTE (non corrigée Stephens).
        ad = stats.anderson(xs, "norm", method="interpolate")
        p_scipy = _x(ad.pvalue)
        r[f"test_normalite:{nom}"] = {
            "statistique_a2_brute": _x(ad.statistic),
            "pvalue_scipy_interpolee": p_scipy,
            "verdict_scipy": "normal_ok" if p_scipy >= ALPHA else "non_normal"}

    # --- ajustement multivarié (oracles BFGS indépendants) ------------------------
    lg = J["logis_simple"]
    r["regression_logistique:logis_simple"] = _logit_oracle(lg["y"], lg["x"])
    for nom in ("cox_simple", "cox_censure"):
        k = J[nom]
        r[f"cox_ph:{nom}"] = _cox_oracle(k["temps"], k["evenements"], k["x"])
    return r


def _py(obj):
    """Convertit récursivement les types numpy en types Python natifs
    (le module de référence reste stdlib — pas de np.float64 gelé)."""
    if isinstance(obj, dict):
        return {k: _py(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_py(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def main() -> int:
    assert scipy.__version__ == SCIPY_EPINGLET, (
        f"scipy épinglé attendu {SCIPY_EPINGLET}, trouvé {scipy.__version__}")
    ref = {"meta": {
        "scipy": SCIPY_EPINGLET, "numpy": np.__version__,
        "genere_le_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "generateur": "scripts/qualifier_scipy.py",
        "note": ("Oracle de qualification — valeurs calculées par scipy/"
                 "ré-implémentations indépendantes ; JAMAIS importé par le "
                 "pipeline ni la CI runtime. Régénération : cf. "
                 "docs/QUALIFICATION_SCIPY.md")},
        "dist": réf_dist(), "ops": réf_ops()}

    cible = RACINE / "tests" / "qualification" / "reference_scipy.py"
    lignes = ['"""Valeurs de référence de qualification — GÉNÉRÉ AUTOMATIQUEMENT',
              "par scripts/qualifier_scipy.py (scipy épinglé). NE PAS ÉDITER —",
              "régénérer avec l'outil (cf. docs/QUALIFICATION_SCIPY.md).",
              '"""']
    out = "\n".join(lignes) + "\nREFERENCE = " + repr(_py(ref)) + "\n"
    cible.write_text(out, encoding="utf-8")
    print(f"✔ référence écrite : {cible} ({len(out) // 1024} Ko, "
          f"{sum(len(v) for v in ref['dist'].values())} points dist, "
          f"{len(ref['ops'])} paquets d'ops)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
