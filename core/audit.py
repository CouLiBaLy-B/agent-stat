"""
Journal d'audit append-only, chaîné SHA-256 (fichier JSONL, modèle WORM).

Chaque entrée scelle la précédente : hash = SHA256(canonical_json(entree + prev_hash)).
`verifier()` rejoue la chaîne et détecte toute altération ou réécriture.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


class JournalAudit:
    def __init__(self, chemin: str | Path):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self._dernier_hash = GENESIS
        self._n = 0
        if self.chemin.exists():                       # reprise : resceller la queue
            lignes = self.chemin.read_text(encoding="utf-8").strip().splitlines()
            for lig in lignes:
                entree = json.loads(lig)
                calcule = hashlib.sha256(_canon({
                    k: v for k, v in entree.items() if k != "hash"})).hexdigest()
                if calcule != entree["hash"]:
                    raise ValueError(f"chaîne d'audit corrompue à l'entrée "
                                     f"{entree.get('seq')}")
                self._dernier_hash = calcule
                self._n = entree["seq"]

    @property
    def dernier_hash(self) -> str:
        return self._dernier_hash

    def log(self, acteur: str, action: str, details: dict) -> str:
        self._n += 1
        entree = {
            "seq": self._n,
            "ts": datetime.now(timezone.utc).isoformat(),
            "acteur": acteur, "action": action, "details": details,
            "prev_hash": self._dernier_hash,
        }
        entree["hash"] = hashlib.sha256(_canon(entree)).hexdigest()
        with self.chemin.open("a", encoding="utf-8") as f:   # append-only
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
        self._dernier_hash = entree["hash"]
        return entree["hash"]

    @staticmethod
    def verifier(chemin: str | Path) -> tuple[bool, int, str]:
        """Retourne (chaîne_intègre, nb_entrées, message)."""
        lignes = [l for l in Path(chemin).read_text(encoding="utf-8")
                  .strip().splitlines() if l.strip()]
        precedent = GENESIS
        for i, lig in enumerate(lignes, start=1):
            e = json.loads(lig)
            if e.get("prev_hash") != precedent:
                return False, i, f"rupture de chaîne à la seq {e.get('seq')}"
            calcule = hashlib.sha256(_canon(
                {k: v for k, v in e.items() if k != "hash"})).hexdigest()
            if calcule != e["hash"]:
                return False, i, (f"entrée altérée à la seq {e.get('seq')} "
                                  f"(hash recalculé ≠ hash enregistré)")
            precedent = e["hash"]
        return True, len(lignes), "chaîne intègre"
