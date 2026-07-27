"""Socle commun des agents MVP : contexte injecté + contrat de sortie.

Contrat (vérifié mécaniquement par l'orchestrateur) :
    confidence: float [0,1], assumptions: list[str], contradictions: list[str],
    artefacts: list[Artefact] — + charge utile métier libre.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from core.audit import JournalAudit
from core.store import Artefact, StoreArtefacts


@dataclass
class Contexte:
    store: StoreArtefacts
    audit: JournalAudit
    checkpoints_dir: str
    producteur: str = ""          # renseigné par la fabrique de chaque agent


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def sha256_obj(obj) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def sortie(confidence: float, artefacts: list[Artefact] | None = None,
           assumptions: list[str] | None = None,
           contradictions: list[str] | None = None, **payload) -> dict:
    return {"confidence": confidence,
            "assumptions": assumptions or [],
            "contradictions": contradictions or [],
            "artefacts": artefacts or [], **payload}


def depot(ctx: Contexte, study_id: str, type_: str, name: str, payload: dict,
          utilisant: list[str] | None = None, statut: str = "PROPOSED") -> Artefact:
    brut = json.dumps(payload, indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    return ctx.store.deposer(type_, study_id, name, brut,
                             producteur=ctx.producteur or "agent.inconnu",
                             prov_used=utilisant or [], statut=statut)


def valeurs(rows: list[dict], var: str) -> list:
    return [r.get(var) for r in rows]


def numeriques(rows: list[dict], var: str) -> list[float]:
    return [float(r[var]) for r in rows
            if r.get(var) is not None and r.get(var) != ""]
