"""Reprise d'un pipeline bloqué (typiquement : gate humain sans décision).

Sémantique : REJEU DÉTERMINISTE COMPLET depuis l'ingestion.
- mêmes `run_id` / données d'entrée (contrat) → mêmes octets d'artefacts ;
- le store est idempotent par contenu : AUCUNE version dupliquée ;
- le journal d'audit rescelle automatiquement (événement REPRISE ajouté) ;
- le verrou SAP est recalculé à l'identique (hash de contenu stable).

En mode déterministe ou llm-simule (provider scripté), la reprise est donc
bit-à-bit reproductible. Avec un LLM HTTP réel, un contenu d'artefact peut
différer → le pipeline déposera alors une NOUVELLE version et repassera les
gates concernés : c'est le comportement fail-closed voulu (jamais de réutilisation
silencieuse d'une validation).
"""
from __future__ import annotations

from core.audit import JournalAudit
from core.exceptions import ErreurLogique
from core.state import Etat
from orchestration.pipeline import construire_systeme, run_pipeline
from pathlib import Path


def reprendre_pipeline(etat_bloque: Etat, racine_runtime: str,
                       decisions_path: str, donnees: dict,
                       backoff_base_s: float = 0.0, llm: str = "off") -> Etat:
    """Rejoue le pipeline d'un état BLOQUE avec les mêmes données d'entrée.

    `llm="off"` (défaut) impose le rejeu strictement déterministe : la reprise
    est alors totalement reproductible. `"env"` résout la configuration
    d'environnement (développement connecté).
    """
    if etat_bloque.statut != "BLOQUE":
        raise ErreurLogique(
            f"reprise demandée sur un statut {etat_bloque.statut!r} — "
            "seul un pipeline BLOQUE est reprenable")
    blocage = etat_bloque.blocage or {}
    audit = JournalAudit(Path(racine_runtime) / "audit.jsonl")
    audit.log("orchestrateur", "REPRISE_PIPELINE", {
        "run_id": etat_bloque.run_id,
        "regle_blocage": blocage.get("regle_blocage"),
        "bloque_depuis": blocage.get("depuis_le"),
        "mode": "rejeu_deterministe" if llm == "off" else f"llm={llm}"})

    sys_ = construire_systeme(racine_runtime, decisions_path,
                              backoff_base_s=backoff_base_s,
                              llm=None if llm == "off" else llm)
    neuf = Etat(run_id=etat_bloque.run_id, study_id=etat_bloque.study_id,
                domaine=etat_bloque.domaine, seed=etat_bloque.seed,
                type_etude=etat_bloque.type_etude)
    neuf.sorties_demandes = list(etat_bloque.sorties_demandes)
    return run_pipeline(neuf, sys_, donnees)
