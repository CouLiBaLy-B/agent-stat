"""Preuves de signature des décisions de gates — format `sig-2.0.0`.

Une décision humaine n'a de valeur que si elle désigne EXACTEMENT l'objet
validé. La preuve lie donc, dans une empreinte recalculable :

    gate_id · validateur · statut · motif · pièces (triées)
    · artefact_ref (art://…/vN) · artefact_sha256 · horodatage UTC.

Toute modification a posteriori du registre de décisions (motif retouché,
pièces gonflées, ref pointée ailleurs) est détectée par `verifier_preuve`.

Préparation eIDAS : le bloc `eidas` matérialise la cible production
(AES/QES via prestataire de confiance qualifié + horodatage qualifié).
En MVP, le niveau est « SES référence traçable » : signature électronique
simple dont l'intégrité repose sur l'empreinte + le journal d'audit chaîné ;
les champs `certificat` / `horodatage_qualifie` / `prestataire_confiance`
sont réservés (None) pour le branchement eIDAS sans changement de schéma.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

FORMAT_SIG = "sig-2.0.0"
NIVEAU_SIG = "SES_REFERENCE_TRACABLE"
CHAMPS_LIES = ("gate_id", "validateur_id", "statut", "motif", "pieces",
               "artefact_ref", "artefact_sha256", "horodatage_utc")


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def empreinte_liaison(gate_id: str, validateur_id: str, statut: str,
                      motif: str, pieces: list[str], artefact_ref: str,
                      artefact_sha256: str, horodatage_utc: str) -> str:
    """Empreinte antisèche de la liaison décision ↔ version d'artefact."""
    corps = {
        "gate_id": gate_id,
        "validateur_id": validateur_id,
        "statut": statut,
        "motif": motif,
        "pieces": sorted(pieces or []),
        "artefact_ref": artefact_ref,
        "artefact_sha256": artefact_sha256,
        "horodatage_utc": horodatage_utc,
    }
    return hashlib.sha256(_canon(corps)).hexdigest()


def fabriquer_preuve(gate_id: str, validateur_id: str, statut: str,
                     motif: str, pieces: list[str], artefact_ref: str,
                     artefact_sha256: str,
                     horodatage_utc: str | None = None) -> dict:
    """Construit la preuve `sig-2.0.0` (empreinte + méta + bloc eIDAS)."""
    ts = horodatage_utc or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    emp = empreinte_liaison(gate_id, validateur_id, statut, motif, pieces,
                            artefact_ref, artefact_sha256, ts)
    return {
        "format": FORMAT_SIG,
        "niveau": NIVEAU_SIG,
        "algorithme_empreinte": "sha256",
        "champs_lies": list(CHAMPS_LIES),
        "horodatage_utc": ts,
        "empreinte": emp,
        "artefact_lie": {"ref": artefact_ref, "sha256": artefact_sha256},
        "eidas": {
            "niveau_actuel": "SES (référence traçable chaînée à l'audit)",
            "niveaux_cibles_production": ["AES", "QES"],
            "certificat": None,
            "horodatage_qualifie": None,
            "prestataire_confiance": None,
            "note": ("champs réservés au branchement d'un prestataire de "
                     "confiance qualifié (Règlement (UE) n° 910/2014) — "
                     "aucune valeur par défaut"),
        },
    }


def signature_ref_depuis(preuve: dict, gate_id: str,
                         validateur_id: str) -> str:
    """Référence lisible traçable (compatible avec le format sig:… existant)."""
    return (f"sig:{preuve['horodatage_utc']}:{validateur_id}:"
            f"{gate_id.lower()}:{preuve['empreinte'][:8]}")


def verifier_preuve(gate_id: str, decision: dict) -> bool:
    """Recalcule l'empreinte depuis la décision et la compare à la preuve.

    Retourne True si la preuve est intègre ; False si le registre a été
    altéré après dépôt (motif, pièces, statut, ref ou horodatage modifiés).
    """
    preuve = decision.get("preuve_signature")
    if not isinstance(preuve, dict):
        return False
    if preuve.get("format") != FORMAT_SIG:
        return False
    lie = (decision.get("artefact_ref"), decision.get("artefact_sha256"))
    if not all(lie):
        return False
    calcule = empreinte_liaison(
        gate_id,
        decision.get("validateur_id") or "",
        decision.get("statut") or "",
        decision.get("motif") or "",
        decision.get("pieces_consultees") or [],
        decision["artefact_ref"],
        decision["artefact_sha256"],
        preuve.get("horodatage_utc") or "")
    return calcule == preuve.get("empreinte")
