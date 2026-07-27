"""Moteur de scores — formules versionnées (cf. ARCHITECTURE.md §C.6).

DQ : calculée par agent.dataqualite (score intrinsèque aux données).
RA : robustesse de l'analyse. CC : confiance de la conclusion, plafonnée.
Les pondérations sont des CONSTANTES VERSIONNÉES : toute modification =
décision au registre + revalidation des études en cours.
"""
from __future__ import annotations

FORMULE_VERSION = "scores-1.0.0"


def calculer_ra(hypotheses_ok: float, concordance_sensibilite: float,
                diagnostics: float, multiplicite_ok: bool,
                validation_interne: float,
                fallback_primaire_perdu: bool = False) -> float:
    ra = (0.30 * hypotheses_ok + 0.25 * concordance_sensibilite
          + 0.20 * diagnostics + 0.15 * (1.0 if multiplicite_ok else 0.0)
          + 0.10 * validation_interne)
    if fallback_primaire_perdu:
        ra = min(ra, 0.50)
    return round(ra, 4)


def calculer_cc(dq: float, ra: float, pre_enregistre: bool,
                analyse_post_hoc: bool = False,
                endpoint_pre_specifie: bool = True,
                convergence_signaux: float = 1.0) -> float:
    facteur = (1.0 if pre_enregistre else 0.8) * convergence_signaux
    cc = min(dq, ra) * facteur
    if dq < 0.60:
        cc = min(cc, 0.40)
    if analyse_post_hoc:
        cc = min(cc, 0.50)
    if not endpoint_pre_specifie:
        cc = min(cc, 0.60)
    return round(cc, 4)


def verbaliser_cc(cc: float | None) -> str | None:
    if cc is None:
        return None
    return ("elevee" if cc >= 0.8 else "moderee" if cc >= 0.6 else
            "faible" if cc >= 0.4 else "insuffisante")
