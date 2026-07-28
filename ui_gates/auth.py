"""Annuaire des comptes validateurs — authentification durcie (FAIL-CLOSED).

Menace visée : la console CLI accepte aujourd'hui n'importe quel
`--validateur`/`--role` auto-déclaré. Désormais, signer exige de PROUVER
le compte (secret) : le rôle ne se déclare plus, il est lu dans l'annuaire.

Règles :
- secret stocké en PBKDF2-HMAC-SHA256 (iterations + sel par compte) — JAMAIS
  en clair, jamais journalisé ; vérification à temps constant ;
- compte inexistant, inactif, secret faux ou compte VERROUILLÉ ⇒ refus ;
- anti force brute : `MAX_ECHECS` échecs consécutifs ⇒ verrouillage
  `VERROU_MIN` minutes (persisté dans l'annuaire, pas de contournement en
  supprimant un fichier temporaire) ; remise à zéro au premier succès ;
- l'annuaire est écrit de façon atomique et ne peut pas être ré-initialisé
  par-dessus (effet "rm comptes.json" : la console REFUSE de signer, cf.
  console.py — jamais d'authentification par défaut dégradée).

Ce module ne remplace PAS un IdP d'entreprise (OIDC/SAML) : c'est la référence
traçable MVP ; le branchement production garde le même contrat
(`authentifier(chemin, ident, secret) -> compte`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets as secrets_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

FORMAT_COMPTES = "comptes-1.0"
PBKDF2_ITER_DEFAUT = 60_000
MAX_ECHECS = 5
VERROU_MIN = 15


class AuthRefusee(Exception):
    """Toute cause d'authentification invalide (fail-closed)."""


def _pbkdf2(secret: str, sel_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), bytes.fromhex(sel_hex),
        iterations).hex()


def _ecrire_atomique(chemin: Path, annuaire: dict) -> None:
    brut = json.dumps(annuaire, indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    tmp = chemin.with_suffix(".tmp")
    tmp.write_bytes(brut)
    tmp.replace(chemin)


def _charger(chemin: str | Path) -> dict:
    chemin = Path(chemin)
    if not chemin.exists():
        raise AuthRefusee(
            f"annuaire de comptes absent ({chemin}) — la console refuse de "
            "signer sans annuaire déclaré (pas d'authentification par défaut)")
    try:
        ann = json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AuthRefusee(f"annuaire de comptes illisible : {e}") from e
    if ann.get("format") != FORMAT_COMPTES:
        raise AuthRefusee(f"format d'annuaire inconnu : {ann.get('format')!r}")
    return ann


def initialiser(chemin: str | Path) -> dict:
    """Crée un annuaire VIDE — refuse d'écraser un annuaire existant
    (l'effacement des comptes = incident de sécurité, pas une option CLI)."""
    chemin = Path(chemin)
    if chemin.exists():
        raise AuthRefusee(
            f"annuaire déjà présent ({chemin}) — ré-initialisation refusée")
    ann = {"format": FORMAT_COMPTES, "cree_le": datetime.now(
        timezone.utc).isoformat(timespec="seconds"), "comptes": {}}
    _ecrire_atomique(chemin, ann)
    return ann


def ajouter_compte(chemin: str | Path, ident: str, roles: list[str],
                   secret: str, actif: bool = True,
                   iterations: int = PBKDF2_ITER_DEFAUT,
                   sel: str | None = None) -> dict:
    """Ajoute un compte. `sel` injectable pour des annuaires de test
    DÉTERMINISTES (défaut : secrets.token_hex — production)."""
    if not ident or not ident.strip():
        raise AuthRefusee("identifiant vide")
    if not roles:
        raise AuthRefusee("au moins un rôle exigé")
    if not secret:
        raise AuthRefusee("secret vide interdit")
    ann = _charger(chemin)
    if ident in ann["comptes"]:
        raise AuthRefusee(f"compte {ident!r} déjà présent — modifier "
                          "hors CLI via procédure admin tracée")
    sel = sel or secrets_mod.token_hex(16)
    ann["comptes"][ident] = {
        "roles": sorted(set(roles)),
        "actif": bool(actif),
        "pbkdf2_sha256": _pbkdf2(secret, sel, iterations),
        "iterations": iterations, "sel": sel,
        "echecs_consecutifs": 0, "verrouille_jusqu_a": None}
    _ecrire_atomique(Path(chemin), ann)
    return ann["comptes"][ident]


def authentifier(chemin: str | Path, ident: str, secret: str,
                 maintenant: datetime | None = None) -> dict:
    """Retourne la fiche compte si le secret est prouvé, sinon AuthRefusee.

    Toutes les causes d'échec partagent un message AMBIGU côté appelant
    (compte inexistant / secret faux / verrou) pour ne pas énumérer les
    identifiants ; le détail revient explicitement dans le message car la
    console est locale et les tests doivent distinguer — le compte n'est
    jamais confirmé au réseau (pas d'API distante dans le MVP).
    """
    now = maintenant or datetime.now(timezone.utc)
    ann = _charger(chemin)
    compte = ann["comptes"].get(ident)
    if compte is None:
        raise AuthRefusee(f"authentification refusée pour {ident!r}")
    if not compte.get("actif", False):
        raise AuthRefusee(f"compte {ident!r} désactivé")
    verrou_a = compte.get("verrouille_jusqu_a")
    if verrou_a:
        fin = datetime.fromisoformat(verrou_a)
        if now < fin:
            reste = (fin - now).total_seconds()
            raise AuthRefusee(
                f"compte {ident!r} verrouillé encore {reste / 60:.0f} min "
                f"(anti force brute après {MAX_ECHECS} échecs)")

    calc = _pbkdf2(secret or "", compte["sel"], compte["iterations"])
    ok = hmac.compare_digest(calc, compte["pbkdf2_sha256"])
    if ok:
        if compte.get("echecs_consecutifs"):
            compte["echecs_consecutifs"] = 0
            compte["verrouille_jusqu_a"] = None
            _ecrire_atomique(Path(chemin), ann)
        return compte

    compte["echecs_consecutifs"] = compte.get("echecs_consecutifs", 0) + 1
    if compte["echecs_consecutifs"] >= MAX_ECHECS:
        compte["verrouille_jusqu_a"] = (
            now + timedelta(minutes=VERROU_MIN)).isoformat(timespec="seconds")
    _ecrire_atomique(Path(chemin), ann)
    restant = MAX_ECHECS - compte["echecs_consecutifs"]
    raise AuthRefusee(
        f"authentification refusée pour {ident!r}"
        + ("" if restant > 0 else " — compte désormais VERROUILLÉ "
                                  f"{VERROU_MIN} min"))


def role_present(compte: dict, role: str) -> bool:
    return role in (compte.get("roles") or [])
