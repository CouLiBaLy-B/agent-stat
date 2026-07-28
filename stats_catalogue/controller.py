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
    """Journal embarqué dans les artefacts = STRICTEMENT déterministe.

    Les durées (métriques wall-clock, volatiles par nature) sont séparées dans
    `durees_ms` (observabilité de l'appelant, jamais hashée) : un rejeu (reprise
    après gate) doit reproduire le MÊME contenu adressé par SHA-256.
    """

    def __init__(self, seed: int):
        self.seed = seed
        self.journal: list[dict] = []
        self.durees_ms: dict[str, float] = {}        # observabilité hors hash

    def executer(self, op: Callable[..., dict], nom_op: str, version: str,
                 **params) -> dict:
        t0 = time.perf_counter()
        # UNICITÉ DE LA SORTIE EXÉCUTABLE (verrou toctou) : pour un même
        # (run, op, entrées), la PREMIÈRE sortie est la seule possible —
        # toute demande ultérieure est RÉ-EXÉCUTÉE puis comparée : concordance
        # bit-à-bit ⇒ on sert la première sortie (jamais une nouvelle,
        # `exec_rejoue` tracé) ; divergence ⇒ toctou détecté ⇒ blocage dur.
        # Chaque première sortie porte un TICKET séquentiel monotone du run
        # (`exec_seq`) : preuve P2 que ce sont les premières sorties.
        if getattr(self, "_memo", None) is None:
            self._memo = {}
            self._seq = 0
        clef_exec = (nom_op, empreinte(params))
        memo = self._memo.get(clef_exec)
        if memo is not None:
            r3 = op(seed=self.seed, **params)
            if empreinte(r3) != memo["_execution"]["sha256"]:
                from core.exceptions import ErreurLogique
                raise ErreurLogique(
                    f"ré-exécution divergente sur {nom_op} (mêmes entrées, "
                    "sortie différente) — toctou détecté : blocage "
                    "reproductibilité")
            self.durees_ms[nom_op] = round(
                (time.perf_counter() - t0) * 1000, 2)
            return {**memo, "_execution": {**memo["_execution"],
                                           "exec_rejoue": True},
                    "_copie_memoire": True}
        self._seq += 1
        seq = self._seq
        r1 = op(seed=self.seed, **params)
        r2 = op(seed=self.seed, **params)              # double exécution de contrôle
        h1, h2 = empreinte(r1), empreinte(r2)
        if h1 != h2:
            from core.exceptions import ErreurLogique
            raise ErreurLogique(
                f"double exécution divergente sur {nom_op} : blocage reproductibilité")
        trace = {"op": nom_op, "version": version, "sha256": h1,
                 "seed": self.seed, "verifiee": True, "exec_seq": seq,
                 "ticket_unicite": True}
        self.journal.append(trace)
        self.durees_ms[nom_op] = round((time.perf_counter() - t0) * 1000, 2)
        sortie = {**r1, "_execution": trace}
        self._memo[clef_exec] = dict(sortie)
        return sortie
