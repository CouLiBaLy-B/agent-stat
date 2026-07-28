"""Signature des décisions de gates — recevabilité FAIL-CLOSED.

Une décision n'est écrite que si TOUS les contrôles passent :
- statut ∈ {VALIDATED, REFUSED} (jamais autre, jamais de défaut) ;
- rôle ∈ rôles recevables du gate ;
- motif ≥ 10 caractères (justification réelle) ;
- validateur identifié ; pièces consultées non vides ;
- LIAISON OBLIGATOIRE à la version d'artefact signée : `artefact_ref` +
  `artefact_sha256` doivent être fournis au dépôt (la CLI les résout
  automatiquement depuis le store). Une décision non liée n'est jamais
  écrite : on ne signe pas « un gate », on signe « CETTE version ».

À l'écriture : preuve `sig-2.0.0` empreintée sur (gate, validateur, statut,
motif, pièces triées, artefact_ref, artefact_sha256, horodatage) + bloc
eIDAS réservé — cf. core/signature.py. Écriture atomique du registre
(fusion sans écraser les autres gates) + événement chaîné au journal
d'audit. Toute violation lève RegleSignature.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core import signature as sig
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
    if not decision.get("artefact_ref") or not decision.get("artefact_sha256"):
        raise RegleSignature(
            "liaison artefact_ref + artefact_sha256 manquante — une décision "
            "n'est écrite que liée à la version exacte qu'elle valide "
            "(la CLI la résout automatiquement depuis le store)")


def fabriquer_signature(gate_id: str, validateur_id: str, statut: str,
                        motif: str, pieces: list[str], artefact_ref: str,
                        artefact_sha256: str) -> tuple[str, dict]:
    """Retourne (référence lisible, preuve sig-2.0.0) liée à la version."""
    preuve = sig.fabriquer_preuve(gate_id, validateur_id, statut, motif,
                                  pieces, artefact_ref, artefact_sha256)
    return (sig.signature_ref_depuis(preuve, gate_id, validateur_id),
            preuve)


def deposer_decision(decisions_path: str | Path, audit: JournalAudit,
                     gate_id: str, decision: dict,
                     roles_requis: list[str]) -> dict:
    """Fusionne la décision liée dans le registre (atomique) + audit chaîné."""
    verifier_recevabilite(decision, roles_requis)
    decision = dict(decision)
    decision["signature_ref"], decision["preuve_signature"] = \
        fabriquer_signature(
            gate_id, decision["validateur_id"], decision["statut"],
            decision["motif"], decision["pieces_consultees"],
            decision["artefact_ref"], decision["artefact_sha256"])
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
        "artefact_ref": decision["artefact_ref"],
        "artefact_sha256": decision["artefact_sha256"],
        "empreinte_preuve": decision["preuve_signature"]["empreinte"],
        "pieces": decision["pieces_consultees"]})
    return decision
