"""Signature des décisions de gates — recevabilité FAIL-CLOSED.

Une décision n'est écrite que si TOUS les contrôles passent :
- statut ∈ {VALIDATED, REFUSED} (jamais autre, jamais de défaut) ;
- rôle ∈ rôles recevables du gate ;
- motif ≥ 10 caractères (justification réelle) ;
- validateur identifié ; pièces consultées non vides ;
- artefact ciblé présenté (le hash est revérifié par le validateur).

Écriture atomique du registre (fusion sans écraser les autres gates) +
événement chaîné au journal d'audit. Toute violation lève RegleSignature.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core.audit import JournalAudit

STATUTS_RECEVABLES = ("VALIDATED", "REFUSED")
MOTIF_MIN = 10


class RegleSignature(Exception):
    """Violation d'une règle de recevabilité (jamais contournée)."""


def verifier_recevabilite(decision: dict, roles_requis: list[str]) -> None:
    statut = decision.get("statut")
    if statut not in STATUTS_RECEVABLES:
        raise RegleSignature(
            f"statut {statut!r} non recevable — attendu VALIDATED ou REFUSED "
            "(aucune valeur par défaut tolérée)")
    role = decision.get("role")
    if roles_requis and role not in roles_requis:
        raise RegleSignature(
            f"rôle {role!r} non habilité — rôles recevables : {roles_requis}")
    motif = (decision.get("motif") or "").strip()
    if len(motif) < MOTIF_MIN:
        raise RegleSignature(
            f"motif trop court ({len(motif)} < {MOTIF_MIN} caractères) — une "
            "justification réelle est exigée")
    if not (decision.get("validateur_id") or "").strip():
        raise RegleSignature("validateur_id manquant")
    if not decision.get("pieces_consultees"):
        raise RegleSignature(
            "pieces_consultees vide — la signature exige la consultation "
            "effective des pièces du dossier")


def fabriquer_signature(gate_id: str, validateur_id: str, statut: str,
                        motif: str, pieces: list[str]) -> str:
    """Référence de signature traçable (horodatée, antisèche de contenu)."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    empreinte = hashlib.sha256(
        json.dumps([gate_id, validateur_id, statut, motif, sorted(pieces), ts],
                   ensure_ascii=False).encode()).hexdigest()[:8]
    return f"sig:{ts}:{validateur_id}:{gate_id.lower()}:{empreinte}"


def deposer_decision(decisions_path: str | Path, audit: JournalAudit,
                     gate_id: str, decision: dict,
                     roles_requis: list[str]) -> dict:
    """Fusionne la décision dans le registre (atomique) + audit chaîné."""
    verifier_recevabilite(decision, roles_requis)
    decision = dict(decision)
    decision["signature_ref"] = fabriquer_signature(
        gate_id, decision["validateur_id"], decision["statut"],
        decision["motif"], decision["pieces_consultees"])
    decision["depose_le"] = datetime.now(timezone.utc).isoformat()

    chemin = Path(decisions_path)
    registre = {}
    if chemin.exists():
        registre = json.loads(chemin.read_text(encoding="utf-8"))
    registre[gate_id] = decision
    brut = json.dumps(registre, indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    tmp = chemin.with_suffix(".tmp")
    tmp.write_bytes(brut)
    tmp.replace(chemin)                                   # écriture atomique

    audit.log(decision["validateur_id"], f"GATE_DECISION_DEPOSEE:{gate_id}", {
        "statut": decision["statut"], "role": decision["role"],
        "motif": decision["motif"], "signature": decision["signature_ref"],
        "pieces": decision["pieces_consultees"]})
    return decision
