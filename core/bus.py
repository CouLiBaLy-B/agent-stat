"""Bus de messages typés — seul canal de communication entre agents (via l'orchestrateur)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

CHAMPS_OBLIGATOIRES = ("msg_id", "task_id", "run_id", "from", "confidence")


def enveloppe(msg_id: str, task_id: str, run_id: str, emetteur: str,
              artefacts: list[str], confidence: float,
              assumptions: list[str] | None = None,
              contradictions: list[str] | None = None,
              needs_human: bool = False) -> dict:
    return {
        "msg_id": msg_id, "task_id": task_id, "run_id": run_id,
        "from": emetteur, "artifact_refs": artefacts,
        "confidence": confidence,
        "assumptions": assumptions or [],
        "contradictions": contradictions or [],
        "needs_human": needs_human,
        "schema_version": "1.0.0",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


class Bus:
    """Files par boîte aux lettres ; les agents n'ont aucune référence directe
    l'un à l'autre (anti-empiètement + auditabilité)."""

    def __init__(self):
        self._files: dict[str, list[dict]] = {}

    def envoyer(self, env: dict, payload: dict, destinataire: str) -> None:
        manquants = [c for c in CHAMPS_OBLIGATOIRES if c not in env]
        if manquants:
            raise ValueError(f"enveloppe incomplète : {manquants}")
        if not 0.0 <= env["confidence"] <= 1.0:
            raise ValueError("confidence hors [0,1]")
        self._files.setdefault(destinataire, []).append(
            {"envelope": env, "payload": json.loads(json.dumps(payload))})

    def recevoir(self, boite: str) -> Optional[dict]:
        f = self._files.get(boite, [])
        return f.pop(0) if f else None

    def en_attente(self, boite: str) -> int:
        return len(self._files.get(boite, []))
