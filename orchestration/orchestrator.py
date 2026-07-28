"""
Orchestrateur central — machine à états déterministe (code, PAS un LLM).

- exécute les agents du registre avec contrat vérifié mécaniquement ;
- retries bornés (backoff exponentiel) sur erreurs TECHNIQUES uniquement ;
- erreurs logiques : jamais retentées → blocage fail-closed ou remontée ;
- gates humains via GestionnaireGates ; toute exception de gate → blocage ;
- checkpoints d'état après chaque étape (reprise propre) ; registre des décisions.
"""
from __future__ import annotations

import time
from typing import Callable

from core.audit import JournalAudit
from core.bus import Bus
from core.exceptions import (ErreurContrat, ErreurLogique, ErreurTechnique,
                             GateError, GateExpire, PipelineBloque)
from core.gates import GestionnaireGates
from core.state import Etat, Phase, RegleBlocage
from core.store import Artefact, StoreArtefacts

RETRIES_MAX = 3

DQ_MIN_GATE = 0.60
DQ_BLOCAGE = 0.50
COMPLETUDE_ENDPOINT_MIN = 0.90
SEUIL_SIGNAL_GRADE_BLOQUANT = 2
MOS_MIN_COSMETIQUE = 100.0

AgentFn = Callable[[Etat, dict], dict]


class Orchestrateur:
    def __init__(self, store: StoreArtefacts, bus: Bus, audit: JournalAudit,
                 gates: GestionnaireGates, registry: dict[str, AgentFn],
                 checkpoints_dir: str = "runtime/checkpoints",
                 backoff_base_s: float = 30.0,
                 exports_gates_dir: str | None = None):
        self.store, self.bus, self.audit = store, bus, audit
        self.gates, self.agents = gates, registry
        self.checkpoints_dir = checkpoints_dir
        self.backoff_base_s = backoff_base_s
        self.exports_gates_dir = exports_gates_dir
        self._idempotence: set[str] = set()

    # ---------------------------------------------------------------- exécution

    def execute(self, etat: Etat, etape: str, agent_id: str, entrees: dict,
                gate_apres: Callable[[Etat, dict], None] | None = None) -> dict:
        cle = f"{etat.run_id}:{etape}"
        if cle in self._idempotence:
            raise ErreurLogique(f"double exécution évitée (idempotence) : {etape}")
        self._decision(etat, "ROUTAGE", f"{etape} → {agent_id}")
        tentative, derniere = 0, None
        while tentative < RETRIES_MAX:
            try:
                sortie = self.agents[agent_id](etat, entrees)
                self._verifier_contrat(agent_id, sortie)
                for art in sortie.get("artefacts", []):
                    etat.artefacts.append(art.ref)
                if gate_apres:
                    gate_apres(etat, sortie)
                self._idempotence.add(cle)
                self.audit.log(agent_id, f"ETAPE_OK:{etape}", {
                    "refs": [a.ref for a in sortie.get("artefacts", [])],
                    "confidence": sortie["confidence"]})
                etat.checkpoint(self.checkpoints_dir, f"{len(self._idempotence):02d}_{etape}")
                if sortie.get("contradictions"):
                    self._decision(etat, "ARBITRAGE",
                                   f"contradictions signalées par {agent_id} : "
                                   f"{sortie['contradictions']}")
                return sortie
            except ErreurTechnique as e:
                tentative += 1
                derniere = e
                self.audit.log("orchestrateur", f"RETRY:{etape}",
                               {"tentative": tentative, "erreur": str(e)})
                time.sleep(self.backoff_base_s * (2 ** (tentative - 1)))
            except (ErreurLogique, GateError):
                raise                      # JAMAIS de retry aveugle sur le logique
        self.bloquer(etat, RegleBlocage.ERREUR_TECHNIQUE_PERSISTANTE,
                     f"{etape} : {RETRIES_MAX} échecs techniques ({derniere})",
                     "ops : diagnostic outil/sandbox puis rejeu")
        raise ErreurLogique(f"{etape} abandon définitif")  # unreachable, clarté

    def execute_parallel(self, etat: Etat,
                         etapes: list[tuple[str, str, dict]]) -> list[dict]:
        """Fan-out logique (MVP : séquentiel ordonné, jointure bloquante).
        Phase 1 : exécution concurrente réelle via Temporal — sémantique identique."""
        resultats = [self.execute(etat, etape, agent_id, entrees)
                     for etape, agent_id, entrees in etapes]
        self.audit.log("orchestrateur", "JOINTURE",
                       {"etapes": [e[0] for e in etapes]})
        return resultats

    # ---------------------------------------------------------------- gates

    def gate_humain(self, etat: Etat, gate_id: str, artefact: Artefact,
                    sla_h: int = 72) -> dict:
        try:
            return self.gates.attendre_decision(gate_id, artefact.ref,
                                                sla_h=sla_h)
        except GateExpire as e:
            chemin = self._exporter_dossier_attente(etat, gate_id, artefact,
                                                    sla_h)
            suffixe = (f" — dossier de preuves exporté : {chemin}. Signature : "
                       f"python3 -m ui_gates.cli sign ..." if chemin else "")
            self.bloquer(etat, RegleBlocage.GATE_HUMAIN_NON_VALIDE,
                         str(e) + suffixe,
                         f"décision humaine au gate {gate_id}")
            raise
        except GateError as e:
            self.bloquer(etat, RegleBlocage.GATE_HUMAIN_NON_VALIDE,
                         str(e), f"décision humaine au gate {gate_id}")
            raise

    def _exporter_dossier_attente(self, etat: Etat, gate_id: str,
                                  artefact: Artefact, sla_h: int) -> str | None:
        """À l'ouverture d'un gate sans décision : exporte la vue preuves +
        scores (best-effort, jamais masquée) pour le validateur humain."""
        if not self.exports_gates_dir:
            return None
        try:
            from ui_gates.dossier import exporter
            p_md, _ = exporter(self.store, self.exports_gates_dir, etat,
                               gate_id, artefact, sla_h,
                               self.gates.regles_roles.get(gate_id, []))
            self.audit.log("orchestrateur", f"DOSSIER_GATE_EXPORTE:{gate_id}",
                           {"chemin": str(p_md), "artefact": artefact.ref})
            return str(p_md)
        except Exception as e:  # l'export ne doit JAMAIS masquer l'attente
            self.audit.log("orchestrateur", "DOSSIER_GATE_ECHEC",
                           {"gate": gate_id, "erreur": str(e)[:200]})
            return None

    def gate_g2_data_quality(self, etat: Etat, dq_payload: dict) -> None:
        score = dq_payload["score_dq"]
        comp_ep = dq_payload["completude_endpoint_principal"]
        etat.scores["fiabilite_donnees"] = score
        etat.verrous["dataset_sha256"] = dq_payload["dataset_snapshot_sha256"]
        if score < DQ_BLOCAGE or comp_ep < COMPLETUDE_ENDPOINT_MIN:
            regle = (RegleBlocage.COMPLETUDE_ENDPOINT_INSUFFISANTE
                     if comp_ep < COMPLETUDE_ENDPOINT_MIN
                     else RegleBlocage.DQ_SOUS_SEUIL)
            self.bloquer(etat, regle,
                         f"DQ={score:.2f}, complétude endpoint={comp_ep:.0%}",
                         "data manager : corrections justifiées + rejeu DQ")
            raise ErreurLogique("G2 : blocage dur")
        if score < DQ_MIN_GATE:
            raise ErreurLogique(f"G2 : DQ={score:.2f} sous seuil {DQ_MIN_GATE} "
                                "→ remédiation requise")

    def gate_g4_safety(self, etat: Etat, safety_payload: dict,
                       safety_art: Artefact) -> None:
        signaux = list(safety_payload.get("signaux", []))
        mos_min = safety_payload.get("mos_min")
        if mos_min is not None and mos_min < MOS_MIN_COSMETIQUE \
                and not any(s["grade"] >= 3 for s in signaux):
            signaux.append({"nature": "MoS<100", "grade": 3})
        if any(s["grade"] >= SEUIL_SIGNAL_GRADE_BLOQUANT for s in signaux):
            etat.signaux.extend(signaux)
            self.audit.log("orchestrateur", "SIGNAUX_SECURITE",
                           {"signaux": signaux})
            self.gate_humain(etat, "G4", safety_art, sla_h=48)

    def gate_conformite(self, etat: Etat, conf_payload: dict,
                        compliance_art: Artefact) -> None:
        verdict = conf_payload.get("verdict_conformite")
        if verdict == "NON_CONFORME_BLOQUANT":
            self.bloquer(etat, RegleBlocage.NON_CONFORMITE_BLOQUANTE,
                         f"règles KO : {conf_payload.get('regles_ko')}",
                         "expert réglementaire : traitement des KO puis rejeu")
            raise ErreurLogique("conformité : blocage dur")
        if verdict == "CONFORME_SOUS_RESERVE":
            self.gate_humain(etat, "G4", compliance_art, sla_h=48)

    # ---------------------------------------------------------------- verrous

    def verifier_verrou_sap(self, etat: Etat, sap_sha256_courant: str) -> None:
        if etat.verrous["sap_sha256"] is None:
            self.bloquer(etat, RegleBlocage.GATE_HUMAIN_NON_VALIDE,
                         "calcul inférentiel demandé sans verrou SAP (G3)",
                         "validation G3 par biostatisticien")
            raise ErreurLogique("pas de verrou SAP")
        if sap_sha256_courant != etat.verrous["sap_sha256"]:
            self.bloquer(etat, RegleBlocage.DEVIATION_SAP_NON_APPROUVEE,
                         "SAP modifié après verrouillage G3",
                         "revalidation G3 (nouvelle version du SAP)")
            raise ErreurLogique("déviation SAP")

    # ---------------------------------------------------------------- blocage

    def bloquer(self, etat: Etat, regle: RegleBlocage, motif: str,
                debloqueur: str) -> None:
        etat.maintenant_bloque(regle, motif, debloqueur)
        self._decision(etat, "BLOCAGE", f"{regle.value} — {motif}")
        self.audit.log("orchestrateur", "BLOCAGE",
                       {"regle": regle.value, "motif": motif,
                        "debloqueur": debloqueur})
        try:  # état bloqué persistant → consommé par ui_gates status/resume
            etat.checkpoint(self.checkpoints_dir, "BLOQUE")
        except Exception:
            pass
        raise PipelineBloque(etat, regle, motif)

    # ---------------------------------------------------------------- interne

    def _verifier_contrat(self, agent_id: str, sortie: dict) -> None:
        for champ in ("confidence", "assumptions", "contradictions", "artefacts"):
            if champ not in sortie:
                raise ErreurContrat(f"{agent_id} : champ {champ!r} manquant")
        if not 0.0 <= sortie["confidence"] <= 1.0:
            raise ErreurContrat(f"{agent_id} : confidence hors [0,1]")

    def _decision(self, etat: Etat, type_: str, motif: str) -> None:
        etat.decisions.append({
            "id": f"DEC-{len(etat.decisions) + 1:03d}", "type": type_,
            "auteur": "orchestrateur", "motif": motif})
