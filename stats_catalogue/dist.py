"""
Fonctions de distribution — implémentations pures (stdlib), déterministes.

Références algorithmiques : Numerical Recipes (betacf, gammp/gammq),
approximations d'Abramowitz & Stegun. Précision ~1e-10, suffisante pour le
catalogue d'ops du MVP. Phase 1 : remplacement par scipy épinglé sans changer
les contrats d'opérations.
"""
from __future__ import annotations

import math

FPMIN = 1e-300
EPS = 3e-14
MAXIT = 300
SQRT2 = math.sqrt(2.0)


# ---------------------------------------------------------------- normal

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_sf(x: float) -> float:
    return 0.5 * math.erfc(x / SQRT2)


def norm_ppf(p: float) -> float:
    """Quantile normal par bissection monotone (déterministe)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p hors ]0,1[")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -norm_ppf(1.0 - p)
    lo, hi = 0.0, 10.0
    for _ in range(120):
        mid = (lo + hi) / 2.0
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- beta incomplète régularisée

def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d, h = 1.0 / d, 1.0 / d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def incbeta(a: float, b: float, x: float) -> float:
    """I_x(a, b) — fonction bêta incomplète régularisée."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


# ---------------------------------------------------------------- gamma incomplète régularisée

def gammaincc_p(a: float, x: float) -> float:
    """P(a, x) par série — valable x < a + 1."""
    if x <= 0.0:
        return 0.0
    ap, s, delta = a, 1.0 / a, 1.0 / a
    for _ in range(MAXIT):
        ap += 1.0
        delta *= x / ap
        s += delta
        if abs(delta) < abs(s) * EPS:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def gammaincc_q(a: float, x: float) -> float:
    """Q(a, x) par fraction continue — valable x >= a + 1."""
    if x <= 0.0:
        return 1.0
    b, c = x + 1.0 - a, 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, MAXIT + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < FPMIN:
            d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def gamma_p(a: float, x: float) -> float:
    return gammaincc_p(a, x) if x < a + 1.0 else 1.0 - gammaincc_q(a, x)


def gamma_q(a: float, x: float) -> float:
    return gammaincc_q(a, x) if x >= a + 1.0 else 1.0 - gammaincc_p(a, x)


# ---------------------------------------------------------------- lois usuelles

def chi2_cdf(x: float, ddl: float) -> float:
    return gamma_p(ddl / 2.0, x / 2.0)


def chi2_sf(x: float, ddl: float) -> float:
    return gamma_q(ddl / 2.0, x / 2.0)


def t_cdf(t: float, ddl: float) -> float:
    """CDF de Student via la bêta incomplète."""
    if ddl <= 0:
        raise ValueError("ddl doit être > 0")
    x = ddl / (ddl + t * t)
    ib = incbeta(ddl / 2.0, 0.5, x)
    return 1.0 - 0.5 * ib if t >= 0 else 0.5 * ib


def t_sf(t: float, ddl: float) -> float:
    return 1.0 - t_cdf(t, ddl)


def t_ppf(p: float, ddl: float) -> float:
    """Quantile de Student par bissection (monotone, déterministe)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p hors ]0,1[")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_ppf(1.0 - p, ddl)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, ddl) < p:
        hi *= 2.0
        if hi > 1e9:
            break
    for _ in range(120):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, ddl) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def f_cdf(x: float, d1: float, d2: float) -> float:
    if x <= 0:
        return 0.0
    return incbeta(d1 / 2.0, d2 / 2.0, (d1 * x) / (d1 * x + d2))


def f_sf(x: float, d1: float, d2: float) -> float:
    return 1.0 - f_cdf(x, d1, d2)


# ---------------------------------------------------------------- divers exacts

def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k), X~Bin(n,p) — somme exacte en log-espace."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    for i in range(0, k + 1):
        lp = (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
              + i * math.log(p) + (n - i) * math.log1p(-p))
        total += math.exp(lp)
    return min(1.0, total)


def beta_ppf(p: float, a: float, b: float) -> float:
    """Quantile bêta par bissection — sert aux IC exacts de Clopper-Pearson."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if incbeta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
