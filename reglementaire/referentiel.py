"""
Chargement du référentiel réglementaire versionné + normalisation INCI.

- Corpus = fichiers JSON de `reglementaire/donnees/` (versionnés dans git) ;
- `Referentiel.sha256` = empreinte du corpus complet — citée dans chaque
  compliance_report (preuve de la version de droit appliquée) ;
- normalisation INCI : casse, espaces, tirets ; résolution des synonymes vers
  la forme canonique. Toute substance non trouvée est rapportée comme
  "non référencée" — JAMAIS considérée conforme par défaut.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DONNEES = Path(__file__).resolve().parent / "donnees"

_SEP = re.compile(r"[\s\-_/]+")


def normaliser_inci(nom: str) -> str:
    return _SEP.sub(" ", nom.strip().lower())


def _charger(nom_fichier: str) -> dict:
    return json.loads((DONNEES / nom_fichier).read_text(encoding="utf-8"))


@dataclass
class Referentiel:
    version: str
    sha256: str
    annexe_ii: dict[str, dict]           # inci canonique -> entrée
    restrictions: dict[str, dict]
    connues: dict[str, dict]
    methodes: dict[str, dict]            # tg OCDE -> entrée
    motifs_animaux: list[str]
    sccs: dict
    agregats: list[dict]
    claims: dict = field(default_factory=dict)   # UE 655/2013 + art. 19
    _synonymes: dict[str, str] = field(repr=False, default_factory=dict)

    def canonique(self, nom: str) -> str:
        n = normaliser_inci(nom)
        return self._synonymes.get(n, n)

    def chercher_substance(self, nom: str) -> dict | None:
        """Retourne {'type': ..., 'entree': ...} ou None si non référencée."""
        c = self.canonique(nom)
        if c in self.annexe_ii:
            return {"type": "annexe_ii", "entree": self.annexe_ii[c], "inci": c}
        if c in self.restrictions:
            return {"type": "restriction", "entree": self.restrictions[c],
                    "inci": c}
        if c in self.connues:
            return {"type": "connue", "entree": self.connues[c], "inci": c}
        return None

    def tg_valide(self, code: str) -> dict | None:
        return self.methodes.get(code)


_CACHE: Referentiel | None = None


def _indexer(entrees: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    index, synonymes = {}, {}
    for e in entrees:
        canonique = normaliser_inci(e["inci"])
        index[canonique] = e
        for syn in e.get("synonymes", []):
            synonymes[normaliser_inci(syn)] = canonique
    return index, synonymes


def charger_referentiel(refresh: bool = False) -> Referentiel:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    corp = {n: _charger(f"{n}.json")
            for n in ("annexe_ii", "restrictions", "substances_connues",
                      "methodes_alternatives_oecd", "sccs_params",
                      "claims_etiquetage")}
    empreinte = hashlib.sha256(json.dumps(
        corp, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    a2, s2 = _indexer(corp["annexe_ii"]["substances"])
    restr, sr = _indexer(corp["restrictions"]["substances"])
    conn, sc = _indexer(corp["substances_connues"]["substances"])
    methodes = {m["tg"]: m for m in
                corp["methodes_alternatives_oecd"]["methodes"]}
    synonymes = {**sc, **sr, **s2}      # priorité annexe II > restrictions
    versions = sorted({corp[k]["version"] for k in corp})
    _CACHE = Referentiel(
        version="+".join(versions), sha256=empreinte,
        annexe_ii=a2, restrictions=restr, connues=conn,
        methodes=methodes,
        motifs_animaux=corp["methodes_alternatives_oecd"]
                          ["motifs_animaux_interdits"],
        sccs=corp["sccs_params"], agregats=corp["restrictions"]["agregats"],
        claims=corp["claims_etiquetage"],
        _synonymes=synonymes)
    return _CACHE
