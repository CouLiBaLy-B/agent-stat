"""
Store d'artefacts immutables versionnés (fichiers + index JSON).

- adresse : art://{type}/{study_id}/{name}/v{n}
- chaque dépôt = nouvelle version liée au précédent (`supersedes`) : JAMAIS d'écrasement
- chaque objet est adressé par son SHA-256 ; l'index matérialise la provenance minimale
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Artefact:
    ref: str
    type: str
    version: int
    sha256: str
    producteur: str
    statut: str = "DRAFT"          # DRAFT|PROPOSED|VALIDATED|SUPERSEDED|REJECTED
    supersedes: str | None = None
    prov_used: list[str] = field(default_factory=list)
    cree_le: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StoreArtefacts:
    def __init__(self, racine: str | Path):
        self.racine = Path(racine)
        (self.racine / "objets").mkdir(parents=True, exist_ok=True)
        self._index_path = self.racine / "index.json"
        self._index: dict[str, dict] = {}
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text(encoding="utf-8"))

    def _flush(self):
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, ensure_ascii=False,
                                  sort_keys=True), encoding="utf-8")
        tmp.replace(self._index_path)          # écriture atomique

    def deposer(self, type_: str, study_id: str, name: str, contenu: bytes,
                producteur: str, prov_used: list[str] | None = None,
                statut: str = "PROPOSED") -> Artefact:
        cle = f"{type_}/{study_id}/{name}"
        meta = self._index.get(cle)
        digest = hashlib.sha256(contenu).hexdigest()
        # idempotence par contenu : un rejeu déterministe (reprise après gate)
        # reproduit les MÊMES octets → l'artefact existant est renvoyé,
        # aucune version dupliquée n'est créée.
        if meta and digest == self._index[meta["ref"]]["sha256"]:
            art = self.get(meta["ref"])
            art.statut = meta.get("statut", art.statut)
            return art
        n = (meta["version"] + 1) if meta else 1
        ref = f"art://{cle}/v{n}"
        cible = self.racine / "objets" / f"{digest}.bin"
        if not cible.exists():
            cible.write_bytes(contenu)         # adressage par contenu = immutabilité
        if meta:
            self._index[meta["ref"]]["statut"] = "SUPERSEDED"
        art = Artefact(ref=ref, type=type_, version=n, sha256=digest,
                       producteur=producteur, statut=statut,
                       supersedes=meta["ref"] if meta else None,
                       prov_used=prov_used or [])
        self._index[ref] = {**asdict(art), "cle": cle}
        self._index[cle] = {"ref": ref, "version": n, "statut": statut}
        self._flush()
        return art

    def lire(self, ref: str) -> bytes:
        meta = self._index[ref]
        brut = (self.racine / "objets" / f"{meta['sha256']}.bin").read_bytes()
        assert hashlib.sha256(brut).hexdigest() == meta["sha256"], \
            f"artefact altéré : {ref}"
        return brut

    def lire_json(self, ref: str) -> dict:
        return json.loads(self.lire(ref).decode("utf-8"))

    def resoudre(self, type_: str, study_id: str, name: str) -> Artefact:
        """Dernière version d'un artefact logique."""
        meta = self._index[f"{type_}/{study_id}/{name}"]
        return self.get(meta["ref"])

    def get(self, ref: str) -> Artefact:
        m = dict(self._index[ref]); m.pop("cle", None)
        return Artefact(**m)
