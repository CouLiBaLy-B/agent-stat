"""Branchement PSCE eIDAS — service de cachets de signature et d'horodatage.

ARCHITECTURE POSTE-DE-MARCHE (comme llm/) :
- `ServicePSCE` = contrat abstrait (certificat_du + fabrique_eidas) ;
- `PSCESimulation` = mise en œuvre 100 % stdlib, cachets recalculables
  publiquement (core/eidas) — **PAS de la cryptographie**, câblage complet
  et traçable en attendant le PSCQ réel (PKCS#11 / API trust-service, RFC
  3161 pour la TSA qualifiée) qui implémentera le même contrat ;
- mode `"off"` : architecture historique (SES référence chaînée) — la
  console laisse alors le bloc `eidas` à None (rétrocompatibilité des
  décisions existantes, modes démo/tests non PSCE).

Résolution : `AGENT_STAT_PSCE_MODE ∈ {off, simulateur, real}` (défaut off).
- "real" : stub qui lève PSCEIndisponible clair ; le vrai PSCQ (RSA/ECDSA qualifié + TSA RFC 3161, IdP OIDC derrière authentifier) doit implémenter **exactement** le contrat ServicePSCE (production).

FAIL-CLOSED : en mode `simulateur`, un validateur SANS certificat enregistré
ne peut pas obtenir de cachet — la console refuse le dépôt (aucune
dégradation silencieuse vers SES).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Protocol

from core import eidas as moteur

MODES = ("off", "simulateur", "real")
NIVEAU_REQUIS_GATES = {           # niveau minimum du cachet à la VALIDATION
    "G1": "AES_SIMULE", "G2": "AES_SIMULE", "G3": "AES_SIMULE",
    "G4": "AES_SIMULE", "G5": "AES_SIMULE", "G6": "AES_SIMULE",
}


class PSCEIndisponible(Exception):
    """Cachet impossible pour ce validateur (fail-closed)."""


class ServicePSCE(Protocol):
    """Contrat du prestataire (simulation aujourd'hui, PSCQ réel ensuite)."""

    mode: str

    def certificat_du(self, validateur_id: str) -> dict: ...
    def fabrique_eidas(self, validateur_id: str, empreinte: str,
                       gate_id: str) -> dict: ...


class PSCESimulation:
    """Registre simulé : certificats par validateur (qualifiés simulés,
    niveaux AES_SIMULE/QES_SIMULE), compteur de jetons TSA simulés."""

    mode = "simulateur"

    def __init__(self, certificats: dict[str, dict], nom: str = "PSCE-DEMO"):
        self.certificats = certificats
        self.nom = nom
        self._serial = 0

    def certificat_du(self, validateur_id: str) -> dict:
        cert = self.certificats.get(validateur_id)
        if cert is None:
            raise PSCEIndisponible(
                f"aucun certificat PSCE enregistré pour {validateur_id!r} — "
                "impossible d'émettre un cachet (fail-closed)")
        return cert

    def fabrique_eidas(self, validateur_id: str, empreinte: str,
                       gate_id: str) -> dict:
        cert = self.certificat_du(validateur_id)
        niveau = cert.get("niveau", "SES")
        requis = NIVEAU_REQUIS_GATES.get(gate_id, "AES_SIMULE")
        if not moteur.niveau_au_moins(niveau, requis):
            raise PSCEIndisponible(
                f"certificat {validateur_id!r} niveau {niveau} < {requis} "
                f"exigé au gate {gate_id}")
        self._serial += 1
        return {
            "niveau_actuel": niveau,
            "niveaux_cibles_production": ["AES", "QES"],
            "certificat": cert,
            "cachet_signature": moteur.fabriquer_cachet(empreinte, cert),
            "horodatage_qualifie": moteur.fabriquer_jeton_tsa(
                empreinte, serial=self._serial),
            "prestataire_confiance": {"nom": self.nom, "mode": self.mode,
                                      "type": "SIMULATION (démo)"},
            "note": ("cachets structuraux simulés (std-lib, recalculables "
                     "publiquement) — PAS de la cryptographie ; branchement "
                     "PSCQ/RFC-3161 réel derrière ce contrat en production"),
        }


def resoudre_psce(mode: str | None = None,
                  simulateur: PSCESimulation | None = None
                  ) -> ServicePSCE | None:
    """Factory branchée env : off → None ; simulateur → moteur simulé ;
    real → stub qui exige le PSCQ réel (RSA/ECDSA + TSA RFC 3161)
    branché derrière le MÊME contrat ServicePSCE (production).
    """
    mode = mode if mode is not None else os.environ.get(
        "AGENT_STAT_PSCE_MODE", "off")
    if mode == "off":
        return None
    if mode == "simulateur":
        return simulateur or psce_demo()
    if mode == "real":
        # Stub : le vrai PSCQ (certificats qualifiés, RSA/ECDSA, RFC 3161 TSA)
        # doit implémenter ServicePSCE. Le stub fail-closed avec message clair
        # pour forcer le câblage production (pas de dégradation silencieuse).
        class PSCERealStub:
            mode = "real"
            def certificat_du(self, validateur_id: str) -> dict:
                raise PSCEIndisponible(
                    "PSCQ réel (mode 'real') non câblé — "
                    "implémentez ServicePSCE derrière RSA/ECDSA + "
                    "TSA RFC 3161 (IdP OIDC pour l'auth) ; "
                    "même contrat qu'en simulateur (production uniquement)")
            def fabrique_eidas(self, validateur_id: str, empreinte: str,
                               gate_id: str) -> dict:
                raise PSCEIndisponible(
                    "PSCQ réel (mode 'real') non câblé — "
                    "implémentez ServicePSCE derrière RSA/ECDSA + "
                    "TSA RFC 3161 (IdP OIDC pour l'auth) ; "
                    "même contrat qu'en simulateur (production uniquement)")
        return PSCERealStub()
    raise PSCEIndisponible(f"AGENT_STAT_PSCE_MODE invalide : {mode!r} "
                           f"(attendu dans {MODES})")


def psce_demo() -> PSCESimulation:
    """PSCE simulé de démonstration, certificats alignés COMPTES_DEMO."""
    from demo.jeu_donnees import CERTIFICATS_PSCE_DEMO
    return PSCESimulation(CERTIFICATS_PSCE_DEMO)
