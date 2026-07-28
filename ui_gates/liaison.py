"""Liaison des décisions pré-déposées ↔ versions déterministes d'artefacts.

Problème : une décision humaine n'a de valeur que liée à la version EXACTE
(ref + sha256) qu'elle valide. Les flux de développement/démonstration qui
pré-déposent des décisions AVANT l'exécution ne connaissent pas encore ces
versions.

Solution — PRÉ-LIAISON PAR REJEU DÉTERMINISTE :
1. le pipeline est rejoué en muet dans un runtime JETABLE (jamais celui de
   production), avec un provider « auto-lieur » qui capture, à chaque gate
   franchi, la version (ref + sha256) réellement présentée ;
2. les décisions gabarits (statut / validateur / rôle / motif fournis par
   l'utilisateur) sont alors LIÉES à ces versions et munies d'une preuve
   sig-2.0.0 et d'un horodatage de dépôt.

Conséquences (voulues, fail-closed) :
- en mode déterministe ou llm-simule (scripté), le run réel reproduit les
  MÊMES versions → la liaison est acceptée ;
- en mode LLM http (non déterministe), un run divergent produit une NOUVELLE
  version → la liaison ne colle plus → le gate bloque et réexporte le
  dossier : jamais de validation réutilisée sur un contenu différent ;
- un gate présent dans les gabarits mais jamais franchi au rejeu (ex. G4
  sans signal) reste NON lié : s'il était atteint au run réel (données
  différentes), il bloquerait — conforme au principe.

Traçabilité : chaque décision liée porte `origine_liaison` indiquant sa
nature technique (recalculée sur rejeu), distincte d'une signature humaine
interactive via la CLI.
"""
from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import PipelineBloque
from core.state import Etat
from orchestration.pipeline import ROLES_GATES, construire_systeme, run_pipeline
from ui_gates.signature import fabriquer_signature

ORIGINE_LIAISON = ("pre-liaison technique (rejeu deterministe, runtime "
                   "jetable) — version recoltee, decision gabarit fournie "
                   "par l'utilisateur ; distinct d'une signature humaine "
                   "interactive")


class ProviderAutoLieur:
    """Provider de rejeu : valide chaque gate en capturant la version
    présentée (ref + sha256). N'écrit RIEN en dehors du runtime jetable."""

    def __init__(self, regles_roles: dict[str, list[str]]):
        self.regles_roles = regles_roles
        self.liaisons: dict[str, dict] = {}

    def decision(self, gate_id: str, artefact_ref: str,
                 artefact_sha256: str | None = None) -> dict:
        sha = artefact_sha256 or ""
        self.liaisons[gate_id] = {"artefact_ref": artefact_ref,
                                  "artefact_sha256": sha}
        roles = self.regles_roles.get(gate_id) or ["systeme"]
        ref_sig, preuve = fabriquer_signature(
            gate_id, "u:auto-lieur", "VALIDATED",
            f"pre-liaison technique du gate {gate_id} (pas une validation "
            "metier)", ["rejeu_deterministe"], artefact_ref, sha)
        return {"statut": "VALIDATED", "validateur_id": "u:auto-lieur",
                "role": roles[0],
                "motif": f"pre-liaison technique du gate {gate_id} "
                         "(pas une validation metier)",
                "signature_ref": ref_sig, "preuve_signature": preuve,
                "pieces_consultees": ["rejeu_deterministe"],
                "artefact_ref": artefact_ref, "artefact_sha256": sha,
                "depose_le": datetime.now(timezone.utc).isoformat()}


def prelier_decisions(decisions_templates: dict, etat_modele: Etat,
                      donnees: dict, llm=None,
                      backoff_base_s: float = 0.0) -> dict:
    """Retourne un registre de décisions LIÉES aux versions déterministes.

    `llm` : même politique que le run réel visé (None = déterministe pur,
    provider simulé, ou "env"). Les gabarits `decisions_templates` conservent
    leur substance métier (statut, validateur, rôle, motif, pièces) ; seule
    la liaison (ref/sha256, preuve, horodatage) est calculée.
    """
    with tempfile.TemporaryDirectory() as tmp:
        sys_ = construire_systeme(tmp, str(Path(tmp) / "decisions.json"),
                                  backoff_base_s=backoff_base_s, llm=llm,
                                  exports_gates=None)
        auto = ProviderAutoLieur(ROLES_GATES)
        sys_["orchestrateur"].gates.provider = auto
        etat = Etat.from_dict(copy.deepcopy(etat_modele.to_dict()))
        try:
            run_pipeline(etat, sys_, copy.deepcopy(donnees))
        except PipelineBloque:
            # Blocage dur (DQ, conformité, gate sans gabarit…) : les gates
            # franchis AVANT le blocage ont tout de même été capturés.
            pass

    registre: dict[str, dict] = {}
    for gate_id, gabarit in decisions_templates.items():
        d = dict(gabarit)
        liaison = auto.liaisons.get(gate_id)
        if liaison:
            d.update(liaison)
            d["signature_ref"], d["preuve_signature"] = fabriquer_signature(
                gate_id, d["validateur_id"], d["statut"], d["motif"],
                d.get("pieces_consultees") or ["pieces_pre_deposees"],
                liaison["artefact_ref"], liaison["artefact_sha256"])
            d["depose_le"] = datetime.now(timezone.utc).isoformat()
            d["origine_liaison"] = ORIGINE_LIAISON
        registre[gate_id] = d
    return registre


def ecrire_decisions_liees(chemin: str | Path, decisions_templates: dict,
                           etat_modele: Etat, donnees: dict,
                           llm=None) -> dict:
    """Pré-lie puis écrit (atomique) le registre de décisions ; retourne le
    registre écrit (utile pour assertions : liaisons présentes ou non)."""
    registre = prelier_decisions(decisions_templates, etat_modele, donnees,
                                 llm=llm)
    p = Path(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(registre, indent=2, ensure_ascii=False,
                              sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return registre
