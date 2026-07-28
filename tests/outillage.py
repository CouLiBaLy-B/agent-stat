"""Outillage partagé des tests d'intégration.

Depuis le durcissement « liaison signature ↔ version » (sig-2.0.0), les
décisions de gates pré-déposées doivent être LIÉES à la version exacte
(ref + sha256) de l'artefact présenté, munies d'une preuve recalculable et
d'un horodatage de dépôt. `ecrire_decisions` calcule cette liaison par rejeu
déterministe (runtime jetable) — cf. ui_gates/liaison.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.state import Etat                                  # noqa: E402
from orchestration.pipeline import construire_systeme        # noqa: E402
from ui_gates import auth                                    # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees          # noqa: E402
from demo.jeu_donnees import COMPTES_DEMO                    # noqa: E402


def ecrire_decisions(chemin_decisions: Path, templates: dict, etat: Etat,
                     donnees: dict, llm="env") -> dict:
    """Écrit les décisions LIÉES (pré-liaison par rejeu déterministe)."""
    return ecrire_decisions_liees(chemin_decisions, templates, etat, donnees,
                                  llm=llm)


SECRET_DEMO = {ident: c["secret"] for ident, c in COMPTES_DEMO.items()}


def creer_comptes_demo(racine: Path, iterations: int = 10_000) -> Path:
    """Annuaire d'authentification de TEST (comptes démo, sels fixes,
    itérations réduites pour la vitesse) — la console refuse toute signature
    sans lui. Jamais utilisé hors tests/démos."""
    chemin = Path(racine) / "comptes.json"
    auth.initialiser(chemin)
    for ident, c in COMPTES_DEMO.items():
        auth.ajouter_compte(chemin, ident, c["roles"], c["secret"],
                            sel=c["sel"], iterations=iterations)
    return chemin


def args_sign(racine: Path, validateur: str) -> list[str]:
    """Arguments CLI `sign` de test : annuaire + preuve de compte (le rôle
    est lu dans l'annuaire — jamais auto-déclaré)."""
    return ["--comptes", str(Path(racine) / "comptes.json"),
            "--secret", SECRET_DEMO[validateur]]


def environnement(racine_rt: str, chemin_decisions: Path, templates: dict,
                  etat: Etat, donnees: dict, llm="env"):
    """Décisions liées + système construit — prêt pour run_pipeline.

    `llm` est propagé TEL QUEL à la pré-liaison ET au système : les artefacts
    liés doivent être bit-à-bit ceux du run réel (off / simulé scripté ok ;
    http réel ⇒ divergence possible ⇒ blocage fail-closed, comportement voulu).
    """
    ecrire_decisions(chemin_decisions, templates, etat, donnees, llm=llm)
    return construire_systeme(racine_rt, str(chemin_decisions),
                              backoff_base_s=0.0, llm=llm)
