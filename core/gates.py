"""
Gates humains — semantics fail-closed.

- Une décision n'est recevable que si : statut VALIDATED, rôle ∈ rôles requis,
  motif renseigné, signature présente, pièces consultées listées.
- Pas de décision / décision expirée → GateExpire → blocage (JAMAIS de validation
  par défaut).
- En production, `DecisionsProvider` est adossé à l'UI de gates + signature
  électronique ; ici, fournisseur par fichier JSON pour le MVP et les tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from core.audit import JournalAudit
from core.exceptions import GateExpire, GateRefuse

CHAMPS_DECISION = ("statut", "validateur_id", "role", "motif", "signature_ref")


class DecisionsProvider(Protocol):
    def decision(self, gate_id: str, artefact_ref: str) -> dict | None: ...


class FichierDecisionsProvider:
    """Lit `decisions.json` : {gate_id: {statut, validateur_id, role, motif, ...}}"""

    def __init__(self, chemin: str | Path):
        self.chemin = Path(chemin)

    def decision(self, gate_id: str, artefact_ref: str) -> dict | None:
        if not self.chemin.exists():
            return None
        return json.loads(self.chemin.read_text(encoding="utf-8")).get(gate_id)


class GestionnaireGates:
    def __init__(self, provider: DecisionsProvider, audit: JournalAudit,
                 regles_roles: dict[str, list[str]]):
        self.provider, self.audit, self.regles_roles = provider, audit, regles_roles

    def attendre_decision(self, gate_id: str, artefact_ref: str,
                          sla_h: int = 72) -> dict:
        roles_requis = self.regles_roles.get(gate_id, [])
        self.audit.log("orchestrateur", f"GATE_OUVERT:{gate_id}",
                       {"artefact": artefact_ref, "roles_requis": roles_requis,
                        "sla_h": sla_h})
        d = self.provider.decision(gate_id, artefact_ref)
        if d is None:
            raise GateExpire(gate_id, "aucune décision — fail-closed")
        for c in CHAMPS_DECISION:
            if not d.get(c):
                raise GateExpire(gate_id, f"décision incomplète ({c} manquant)")
        if d["statut"] == "REFUSED":
            self.audit.log(d["validateur_id"], f"GATE_REFUSE:{gate_id}",
                           {"motif": d["motif"], "signature": d["signature_ref"]})
            raise GateRefuse(gate_id, f"refus motivé : {d['motif']}")
        if d["statut"] != "VALIDATED":
            raise GateExpire(gate_id, f"statut {d['statut']} non franchissable")
        if roles_requis and d["role"] not in roles_requis:
            raise GateExpire(gate_id,
                             f"rôle '{d['role']}' ∉ {roles_requis}")
        self.audit.log(d["validateur_id"], f"GATE_VALIDE:{gate_id}", {
            "artefact": artefact_ref, "role": d["role"], "motif": d["motif"],
            "signature": d["signature_ref"],
            "pieces": d.get("pieces_consultees", [])})
        return d
