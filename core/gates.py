"""Gates humains — semantics fail-closed, liaison signature ↔ version.

- Une décision n'est recevable que si : statut VALIDATED, rôle ∈ rôles requis,
  motif renseigné, signature présente, pièces consultées listées.
- Pas de décision / décision expirée → GateExpire → blocage (JAMAIS de
  validation par défaut).

LIAISON SIGNATURE ↔ VERSION D'ARTEFACT (sig-2.0.0)
  Quand l'appelant présente l'artefact validé (`artefact_sha256` fourni —
  l'orchestrateur le fait TOUJOURS), la décision DOIT :
    1. porter `artefact_ref` + `artefact_sha256` ÉGAUX à la version présentée
       (une signature obtenue sur une autre version ne vaut rien) ;
    2. porter `depose_le` (horodatage de dépôt) et une `preuve_signature`
       intègre (empreinte recalculée — toute retouche du registre de
       décisions après dépôt est détectée et bloque) ;
    2b. si la preuve porte des CACHETS eIDAS (mode PSCE), chacun doit être
        intègre (cachet de signature simulé et/ou jeton d'horodatage
        recalculé sur l'empreinte couverte — retouche ⇒ blocage
        `GATE_CACHET_INVALIDE:<G>`) ; absence de cachet : recevable ici
        (c'est la console qui décide d'en exiger via le mode PSCE) ;
  sinon → GateExpire (fail-closed). Appels sans sha256 (tests unitaires du
  gestionnaire seul) : contrôles de recevabilité classiques uniquement.

SLA MESURÉ EN TEMPS RÉEL
  L'attente est mesurée entre la PREMIÈRE ouverture du gate pour cette
  version d'artefact (relue dans le journal d'audit, tous runs confondus)
  et le `depose_le` de la décision. Dépassement du SLA → la décision est
  expirée → GateExpire (il faut re-signer : le dossier a pu évoluer).
  La mesure est journalisée (`SLA_GATE_MESURE:<G>`), dépassée ou non.

En production, `DecisionsProvider` est adossé à l'UI de gates + signature
électronique ; ici, fournisseur par fichier JSON pour le MVP et les tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from core.audit import JournalAudit
from core.exceptions import GateExpire, GateRefuse
from core import signature as sig

CHAMPS_DECISION = ("statut", "validateur_id", "role", "motif", "signature_ref")


class DecisionsProvider(Protocol):
    def decision(self, gate_id: str, artefact_ref: str,
                 artefact_sha256: str | None = None) -> dict | None: ...


class FichierDecisionsProvider:
    """Lit `decisions.json` : {gate_id: {statut, validateur_id, role, motif, ...}}"""

    def __init__(self, chemin: str | Path):
        self.chemin = Path(chemin)

    def decision(self, gate_id: str, artefact_ref: str,
                 artefact_sha256: str | None = None) -> dict | None:
        if not self.chemin.exists():
            return None
        return json.loads(self.chemin.read_text(encoding="utf-8")).get(gate_id)


def _parse_ts(brut: str) -> datetime | None:
    try:
        return datetime.fromisoformat(brut)
    except (TypeError, ValueError):
        return None


class GestionnaireGates:
    def __init__(self, provider: DecisionsProvider, audit: JournalAudit,
                 regles_roles: dict[str, list[str]]):
        self.provider, self.audit, self.regles_roles = provider, audit, regles_roles

    # ------------------------------------------------------------ lecture SLA

    def _premiere_ouverture(self, gate_id: str,
                            artefact_ref: str) -> datetime | None:
        """Premier horodatage GATE_OUVERT pour (gate, version) dans le journal
        — tous runs confondus : le SLA court depuis la première présentation
        de CETTE version au validateur, y compris à travers les reprises."""
        chemin = getattr(self.audit, "chemin", None)
        if not chemin or not Path(chemin).exists():
            return None
        ouvertures = []
        for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
            if not ligne.strip():
                continue
            try:
                e = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if e.get("action") != f"GATE_OUVERT:{gate_id}":
                continue
            if (e.get("details") or {}).get("artefact") != artefact_ref:
                continue
            ts = _parse_ts(e.get("ts") or "")
            if ts:
                ouvertures.append(ts)
        return min(ouvertures) if ouvertures else None

    # ---------------------------------------------------------------- décision

    def attendre_decision(self, gate_id: str, artefact_ref: str,
                          sla_h: int = 72,
                          artefact_sha256: str | None = None) -> dict:
        roles_requis = self.regles_roles.get(gate_id, [])
        self.audit.log("orchestrateur", f"GATE_OUVERT:{gate_id}",
                       {"artefact": artefact_ref,
                        "artefact_sha256": artefact_sha256,
                        "roles_requis": roles_requis,
                        "sla_h": sla_h})
        d = self.provider.decision(gate_id, artefact_ref,
                                   artefact_sha256=artefact_sha256)
        if d is None:
            raise GateExpire(gate_id, "aucune décision — fail-closed")
        for c in CHAMPS_DECISION:
            if not d.get(c):
                raise GateExpire(gate_id, f"décision incomplète ({c} manquant)")

        if artefact_sha256 is not None:
            self._verifier_liaison(gate_id, artefact_ref, artefact_sha256, d)
            self._mesurer_sla(gate_id, artefact_ref, sla_h, d)

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
            "artefact": artefact_ref,
            "artefact_sha256": artefact_sha256,
            "liee": artefact_sha256 is not None,
            "role": d["role"], "motif": d["motif"],
            "signature": d["signature_ref"],
            "preuve": (d.get("preuve_signature") or {}).get("empreinte"),
            "pieces": d.get("pieces_consultees", [])})
        return d

    # ---------------------------------------------------------------- interne

    def _verifier_liaison(self, gate_id: str, artefact_ref: str,
                          artefact_sha256: str, d: dict) -> None:
        """La décision signée doit désigner EXACTEMENT la version présentée,
        avec une preuve d'intégrité recalculable et un horodatage de dépôt."""
        manquants = [c for c in ("artefact_ref", "artefact_sha256",
                                 "depose_le", "preuve_signature")
                     if not d.get(c)]
        if manquants:
            raise GateExpire(
                gate_id,
                f"décision non liée à la version de l'artefact "
                f"({', '.join(manquants)} manquant(s)) — une validation "
                "humaine ne vaut que pour la version présentée ; signer via "
                "ui_gates.cli (liaison automatique)")
        if (d["artefact_ref"], d["artefact_sha256"]) != (artefact_ref,
                                                         artefact_sha256):
            self.audit.log("orchestrateur", f"GATE_LIAISON_INVALIDE:{gate_id}",
                           {"attendu": {"ref": artefact_ref,
                                        "sha256": artefact_sha256},
                            "signe": {"ref": d["artefact_ref"],
                                      "sha256": d["artefact_sha256"]}})
            raise GateExpire(
                gate_id,
                f"décision liée à {d['artefact_ref']} "
                f"(sha256:{str(d['artefact_sha256'])[:12]}…) ≠ version "
                f"présentée {artefact_ref} (sha256:{artefact_sha256[:12]}…) "
                "— re-signature exigée sur la version courante")
        if not sig.verifier_preuve(gate_id, d):
            self.audit.log("orchestrateur",
                           f"GATE_PREUVE_ALTEREE:{gate_id}",
                           {"signature": d.get("signature_ref")})
            raise GateExpire(
                gate_id,
                "registre de décisions altéré après dépôt : empreinte de la "
                "preuve sig-2.0.0 ≠ empreinte recalculée — fail-closed")
        if not sig.verifier_cachets_eidas(d.get("preuve_signature") or {}):
            self.audit.log("orchestrateur",
                           f"GATE_CACHET_INVALIDE:{gate_id}",
                           {"signature": d.get("signature_ref")})
            raise GateExpire(
                gate_id,
                "cachet eIDAS altéré (valeur, certificat ou horodatage "
                "retouché sans recalcul de l'empreinte couverte) — "
                "fail-closed")

    def _mesurer_sla(self, gate_id: str, artefact_ref: str, sla_h: int,
                     d: dict) -> None:
        """Mesure l'attente réelle (1re ouverture → dépôt) et fait échouer
        toute décision déposée HORS SLA (le dossier a pu évoluer entre-temps :
        la validation doit être refaite dans les délais)."""
        depose = _parse_ts(d.get("depose_le") or "")
        if depose is None:
            raise GateExpire(gate_id,
                             "depose_le non daté — SLA non mesurable")
        t0 = self._premiere_ouverture(gate_id, artefact_ref)
        if t0 is None:
            return                       # jamais ouvert avant : pas de fenêtre
        attente_h = (depose - t0).total_seconds() / 3600.0
        self.audit.log("orchestrateur", f"SLA_GATE_MESURE:{gate_id}", {
            "artefact": artefact_ref, "sla_h": sla_h,
            "attente_h": round(attente_h, 4),
            "premiere_ouverture": t0.isoformat(),
            "depose_le": depose.isoformat(),
            "dans_les_delais": attente_h <= sla_h})
        if attente_h > sla_h:
            raise GateExpire(
                gate_id,
                f"décision déposée hors SLA ({attente_h:.1f} h > {sla_h} h "
                f"depuis la première ouverture du gate) — re-signature "
                "exigée : la validité d'une validation humaine est bornée "
                "dans le temps")
