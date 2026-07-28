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
from ui_gates.liaison import ecrire_decisions_liees          # noqa: E402


def ecrire_decisions(chemin_decisions: Path, templates: dict, etat: Etat,
                     donnees: dict, llm="env") -> dict:
    """Écrit les décisions LIÉES (pré-liaison par rejeu déterministe)."""
    return ecrire_decisions_liees(chemin_decisions, templates, etat, donnees,
                                  llm=llm)


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
