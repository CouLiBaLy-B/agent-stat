"""Courtier d'exécution du catalogue stats : double exécution + contrôle de hash.

Reproductibilité mécanique (cf. ARCHITECTURE.md §C.5) :
chaque opération est exécutée DEUX fois dans le même processus — les sorties
canonisées doivent produire des SHA-256 identiques, sinon ErreurLogique.
En Phase 1 : les deux exécutions auront lieu dans deux sandboxes distincts.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable


def canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def empreinte(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


class ControleurExecution:
    def __init__(self, seed: int):
        self.seed = seed
        self.journal: list[dict] = []

    def executer(self, op: Callable[..., dict], nom_op: str, version: str,
                 **params) -> dict:
        t0 = time.perf_counter()
        r1 = op(seed=self.seed, **params)
        r2 = op(seed=self.seed, **params)              # double exécution de contrôle
        h1, h2 = empreinte(r1), empreinte(r2)
        if h1 != h2:
            from core.exceptions import ErreurLogique
            raise ErreurLogique(
                f"double exécution divergente sur {nom_op} : blocage reproductibilité")
        trace = {"op": nom_op, "version": version, "sha256": h1,
                 "duree_ms": round((time.perf_counter() - t0) * 1000, 2),
                 "seed": self.seed, "verifiee": True}
        self.journal.append(trace)
        return {**r1, "_execution": trace}
