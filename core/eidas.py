"""Cachets eIDAS et horodatage qualifié — moteur STRUCTUREL simulé (stdlib).

⚠ CE MODULE N'EST PAS DE LA CRYPTOGRAPHIE. Les cachets « AES_SIMULE » /
« QES_SIMULE » et les jetons d'horodatage « TSA_SIMULEE » sont des empreintes
SHA-256 composites **recalculables publiquement** : ils démontrent la
STRUCTURE d'une preuve eIDAS, l'intégrité des champs et le câblage complet
(console → PSCE → preuve → vérification au gate), mais n'opposent aucune
barrière à un attaquant. La cible production est un PSCQ réel (certificats
qualifiés, signature RSA/ECDSA, RFC 3161 pour l'horodatage) branché derrière
le MÊME contrat (`ui_gates/eidas.py`) sans changer le schéma sig-2.0.0.

Invariants du moteur :
- un cachet couvre EXACTEMENT l'empreinte de liaison sig-2.0.0 + la
  référence complète du certificat + l'instant d'émission ;
- un jeton d'horodatage qualifié couvre l'empreinte + gen_time + serial ;
- toute altération d'un champ couvert rend la vérification fausse
  (fail-closed au gate : GATE_CACHET_INVALIDE).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

NIVEAUX = ("SES", "AES_SIMULE", "QES_SIMULE")   # ordre croissant de force
FORMAT_CACHET = "psce-cachet-1.0"
FORMAT_JETON_TSA = "tsa-jeton-1.0"


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode()


def niveau_au_moins(niveau: str, requis: str) -> bool:
    """Ordre SES < AES_SIMULE < QES_SIMULE (fail-closed sur inconnu)."""
    try:
        return NIVEAUX.index(niveau) >= NIVEAUX.index(requis)
    except ValueError:
        return False


def fabriquer_cachet(empreinte: str, certificat: dict,
                     emis_le_utc: str | None = None) -> dict:
    """Cachet de signature simulé lié à l'empreinte sig-2.0.0 exacte.

    `certificat` est la fiche PUBLIQUE (sujet, émetteur, série, validité,
    niveau, qualifié) — jamais la clé. Le cachet simulé vaut :
        sha256("psce-cachet:" | canon(empreinte, certificat, emis_le))
    """
    ts = emis_le_utc or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    valeur = hashlib.sha256(
        b"psce-cachet:" + _canon({"empreinte": empreinte,
                                  "certificat": certificat,
                                  "emis_le_utc": ts})).hexdigest()
    return {"format": FORMAT_CACHET, "algorithme": "sha256-simule",
            "valeur": valeur, "emis_le_utc": ts,
            "certificat": dict(certificat), "empreinte_couverte": empreinte,
            "avertissement": ("cachet SIMULE recomputable publiquement — "
                              "PAS une signature cryptographique ; PSCQ réel "
                              "requis en production")}


def verifier_cachet(cachet: dict, empreinte: str) -> bool:
    """Recalcule le cachet attendu pour `empreinte` et compare (intégral)."""
    if not isinstance(cachet, dict) or cachet.get("format") != FORMAT_CACHET:
        return False
    if cachet.get("empreinte_couverte") != empreinte:
        return False
    attendu = hashlib.sha256(
        b"psce-cachet:" + _canon({"empreinte": empreinte,
                                  "certificat": cachet.get("certificat"),
                                  "emis_le_utc": cachet.get("emis_le_utc")}
                                 )).hexdigest()
    return hmac.compare_digest(attendu, cachet.get("valeur") or "")


def fabriquer_jeton_tsa(empreinte: str, serial: int,
                        gen_time_utc: str | None = None,
                        tsa_id: str = "TSA-SIMULATION-DEMO") -> dict:
    """Jeton d'horodatage qualifié simulé (structure type RFC 3161)."""
    ts = gen_time_utc or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    valeur = hashlib.sha256(
        b"tsa-jeton:" + _canon({"tsa_id": tsa_id, "gen_time_utc": ts,
                                "serial": serial,
                                "message_imprint": empreinte,
                                "digest_algo": "sha256"})).hexdigest()
    return {"format": FORMAT_JETON_TSA, "tsa_id": tsa_id,
            "gen_time_utc": ts, "serial": serial, "digest_algo": "sha256",
            "message_imprint": empreinte, "jeton": valeur,
            "avertissement": ("horodatage SIMULE — pas un jeton RFC 3161 "
                              "d'une TSA qualifiée ; branchement production "
                              "requis")}


def verifier_jeton_tsa(jeton: dict, empreinte: str) -> bool:
    """Recalcule le jeton attendu pour `empreinte` et compare (intégral)."""
    if not isinstance(jeton, dict) or jeton.get("format") != FORMAT_JETON_TSA:
        return False
    if jeton.get("message_imprint") != empreinte:
        return False
    attendu = hashlib.sha256(
        b"tsa-jeton:" + _canon({"tsa_id": jeton.get("tsa_id"),
                                "gen_time_utc": jeton.get("gen_time_utc"),
                                "serial": jeton.get("serial"),
                                "message_imprint": empreinte,
                                "digest_algo": jeton.get("digest_algo")}
                               )).hexdigest()
    return hmac.compare_digest(attendu, jeton.get("jeton") or "")
