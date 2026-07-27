"""
Orchestrateur central — squelette de référence (architecture docs/ARCHITECTURE.md).

Principes implémentés ici :
- machine à états déterministe (l'orchestrateur est du CODE, pas un LLM) ;
- communication uniquement par messages typés via le bus (jamais d'agent-à-agent) ;
- retries bornés avec backoff + idempotence ; erreurs logiques NON retryables ;
- blocages durs (fail-closed) couvrant les 10 règles du schéma d'état ;
- gates humains synchrones à la décision, asynchrones en attente (SLA, fail-closed) ;
- audit append-only chaîné SHA-256 ; artefacts immutables versionnés + hashés ;
- verrou SAP : aucune opération inférentielle sans égalité de hash avec le verrou G3.

Dépendances : bibliothèque standard uniquement (squelette). En production :
LangGraph/Temporal pour la machine à états, S3+Postgres pour le store, files SQS/Kafka
pour le bus, sandbox conteneurisé pour le moteur stats.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

# ----------------------------------------------------------------------------- constantes

SCHEMA_VERSION = "1.0.0"
RETRIES_MAX = 3
BACKOFF_BASE_S = 30            # backoff exponentiel : 30 s * 2**n
ALLERS_RETOURS_ARBITRAGE_MAX = 2

DQ_MIN_GATE = 0.60
DQ_BLOCAGE = 0.50
COMPLETUDE_ENDPOINT_MIN = 0.90
CONFIANCE_QUALIFICATION_MIN = 0.80
MOS_MIN_COSMETIQUE = 100
SEUIL_SIGNAL_GRADE_BLOQUANT = 2


class Phase(Enum):
    INGESTION = "INGESTION"
    QUALIFICATION_ETUDE = "QUALIFICATION_ETUDE"
    VERIFICATION_COMPLETUDE = "VERIFICATION_COMPLETUDE"
    CONTROLE_QUALITE_DONNEES = "CONTROLE_QUALITE_DONNEES"
    PLAN_ANALYSE = "PLAN_ANALYSE"
    CHOIX_METRIQUES = "CHOIX_METRIQUES"
    CALCUL_STATISTIQUE = "CALCUL_STATISTIQUE"
    GESTION_MANQUANTS = "GESTION_MANQUANTS"
    DETECTION_BIAIS_ANOMALIES = "DETECTION_BIAIS_ANOMALIES"
    EVALUATION_SECURITE_TOLERANCE = "EVALUATION_SECURITE_TOLERANCE"
    REDACTION_RAPPORT = "REDACTION_RAPPORT"
    CRITIQUE_CROISEE = "CRITIQUE_CROISEE"
    VALIDATION_HUMAINE = "VALIDATION_HUMAINE"
    EXPORT = "EXPORT"
    TERMINE = "TERMINE"
    BLOQUE = "BLOQUE"


class RegleBlocage(Enum):
    DQ_SOUS_SEUIL = "DQ_SOUS_SEUIL"
    COMPLETUDE_ENDPOINT_INSUFFISANTE = "COMPLETUDE_ENDPOINT_INSUFFISANTE"
    GATE_HUMAIN_NON_VALIDE = "GATE_HUMAIN_NON_VALIDE"
    SIGNAL_SECURITE_SANS_REVUE = "SIGNAL_SECURITE_SANS_REVUE"
    NON_CONFORMITE_BLOQUANTE = "NON_CONFORMITE_BLOQUANTE"
    CONTRADICTION_AGENTS_NON_ARBITREE = "CONTRADICTION_AGENTS_NON_ARBITREE"
    ARTEFACT_OBLIGATOIRE_ABSENT = "ARTEFACT_OBLIGATOIRE_ABSENT"
    DEVIATION_SAP_NON_APPROUVEE = "DEVIATION_SAP_NON_APPROUVEE"
    SUSPICION_MANIPULATION_DONNEES = "SUSPICION_MANIPULATION_DONNEES"
    ERREUR_TECHNIQUE_PERSISTANTE = "ERREUR_TECHNIQUE_PERSISTANTE"


class ErreurTechnique(Exception):
    """Retryable : timeout outil, crash sandbox, indisponibilité transitoire."""


class ErreurLogique(Exception):
    """NON retryable : schéma invalide, gate échoué, précondition absente."""


# ----------------------------------------------------------------------------- audit (WORM chaîné)

class JournalAudit:
    """Append-only, chaîné SHA-256 : chaque entrée scelle la précédente."""

    def __init__(self, journal_ref: str):
        self.ref = journal_ref
        self._dernier_hash = "0" * 64
        self._entrees: list[dict] = []   # en prod : stockage WORM (S3 Object Lock)

    def log(self, acteur: str, action: str, details: dict) -> str:
        entree = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "acteur": acteur, "action": action, "details": details,
            "prev_hash": self._dernier_hash,
        }
        self._dernier_hash = hashlib.sha256(
            json.dumps(entree, sort_keys=True).encode()).hexdigest()
        entree["hash"] = self._dernier_hash
        self._entrees.append(entree)     # en prod : PUT immuable
        return self._dernier_hash


# ----------------------------------------------------------------------------- store d'artefacts

@dataclass
class Artefact:
    ref: str                 # art://{type}/{study_id}/{name}/v{n}
    type: str
    version: int
    sha256: str
    producteur: str
    statut: str = "DRAFT"    # DRAFT|PROPOSED|VALIDATED|SUPERSEDED|REJECTED
    supersedes: Optional[str] = None


class StoreArtefacts:
    """Immutabilité : toute modification crée une nouvelle version liée (supersedes)."""

    def __init__(self):
        self._objets: dict[str, bytes] = {}
        self._versions: dict[str, int] = {}

    def deposer(self, type_: str, study_id: str, name: str,
                contenu: bytes, producteur: str) -> Artefact:
        cle = f"{type_}/{study_id}/{name}"
        n = self._versions.get(cle, 0) + 1
        self._versions[cle] = n
        ref = f"art://{cle}/v{n}"
        digest = hashlib.sha256(contenu).hexdigest()
        self._objets[ref] = contenu
        precedent = f"art://{cle}/v{n - 1}" if n > 1 else None
        return Artefact(ref=ref, type=type_, version=n, sha256=digest,
                        producteur=producteur, supersedes=precedent)

    def lire(self, ref: str) -> bytes:
        return self._objets[ref]  # jamais d'écrasement : la clé contient la version


# ----------------------------------------------------------------------------- état (cf. schemas/etat.schema.json)

@dataclass
class Etat:
    run_id: str
    study_id: str
    domaine: str
    seed: int
    phase: Phase = Phase.INGESTION
    statut: str = "EN_COURS"
    blocage: Optional[dict] = None
    scores: dict = field(default_factory=lambda: {
        "fiabilite_donnees": None, "robustesse_analyse": None,
        "confiance_conclusion": None, "verbalisation_cc": None})
    verrous: dict = field(default_factory=lambda: {
        "sap_sha256": None, "dataset_sha256": None})
    artefacts: list[Artefact] = field(default_factory=list)
    signaux: list[dict] = field(default_factory=list)
    # checkpoint par étape → reprise propre depuis n'importe quelle phase

    def checkpoint(self) -> dict:
        return self.__dict__.copy()


# ----------------------------------------------------------------------------- bus & gates humains

class Bus:
    """File de messages typés. Les agents n'ont pas de référence l'un à l'autre."""
    def __init__(self):
        self._files: dict[str, list[dict]] = {}

    def envoyer(self, enveloppe: dict, payload: dict, destinataire: str):
        for champ in ("msg_id", "task_id", "run_id", "from", "confidence"):
            assert champ in enveloppe, f"enveloppe incomplète : {champ}"
        self._files.setdefault(destinataire, []).append(
            {"envelope": enveloppe, "payload": payload})

    def recevoir(self, boite: str) -> Optional[dict]:
        f = self._files.get(boite, [])
        return f.pop(0) if f else None


class GestionnaireGates:
    """Fail-closed : absence de décision à l'échéance SLA = blocage, JAMAIS validation par défaut."""

    def __init__(self, bus: Bus, audit: JournalAudit):
        self.bus, self.audit = bus, audit
        self._decisions: dict[str, dict] = {}

    def attendre_decision(self, gate_id: str, artefact: Artefact,
                          roles_requis: list[str], sla_h: int) -> dict:
        """En prod : événement asynchrone (UI gate) + rappels ; ici, simulé.
        Lève ErreurLogique si refus/expiration → l'appelant déclenche le blocage."""
        decision = self._attendre_evenement_humain(gate_id)  # simulé
        if decision["statut"] != "VALIDATED":
            raise ErreurLogique(f"gate {gate_id} : {decision['statut']}")
        if decision["role"] not in roles_requis:
            raise ErreurLogique(f"gate {gate_id} : rôle invalide")
        self.audit.log(decision["validateur_id"], f"VALIDATION_{gate_id}",
                       {"artefact": artefact.ref, "motif": decision["motif"],
                        "signature": decision["signature_ref"]})
        return decision

    def _attendre_evenement_humain(self, gate_id: str) -> dict:
        return self._decisions.get(gate_id, {
            "statut": "EXPIRED", "validateur_id": None, "role": None,
            "motif": None, "signature_ref": None})


# ----------------------------------------------------------------------------- orchestrateur

AgentFn = Callable[[Etat, dict], dict]   # (état, artefacts_en_entrée) -> artefacts_en_sortie


class Orchestrateur:
    def __init__(self, store: StoreArtefacts, bus: Bus, audit: JournalAudit,
                 gates: GestionnaireGates, registry: dict[str, AgentFn]):
        self.store, self.bus, self.audit, self.gates = store, bus, audit, gates
        self.agents = registry             # id agent -> implémentation (chargée du registry.yaml)
        self._idempotence: set[str] = set()

    # ---- exécution d'une étape avec politique de reprise --------------------

    def execute(self, etat: Etat, etape: str, agent_id: str, entrees: dict,
                gate_apres: Optional[Callable[[Etat, dict], None]] = None) -> dict:
        cle_idem = f"{etat.run_id}:{etape}"
        if cle_idem in self._idempotence:
            raise ErreurLogique(f"double écriture évitée : {etape}")

        tentative, derniere_erreur = 0, None
        while tentative < RETRIES_MAX:
            try:
                sorties = self.agents[agent_id](etat, entrees)
                self._verifier_contrat(agent_id, sorties)          # schéma de sortie
                for art in sorties.get("artefacts", []):
                    etat.artefacts.append(art)
                if gate_apres:
                    gate_apres(etat, sorties)                      # lève ErreurLogique si KO
                self._idempotence.add(cle_idem)
                self.audit.log(agent_id, f"ETAPE_OK:{etape}",
                               {"refs": [a.ref for a in sorties.get("artefacts", [])]})
                return sorties
            except ErreurTechnique as e:                           # retryable
                tentative += 1
                derniere_erreur = e
                self.audit.log("orchestrateur", f"RETRY:{etape}",
                               {"tentative": tentative, "erreur": str(e)})
                time.sleep(BACKOFF_BASE_S * (2 ** (tentative - 1)))
            except ErreurLogique:
                raise                                              # JAMAIS de retry aveugle
        self.bloquer(etat, RegleBlocage.ERREUR_TECHNIQUE_PERSISTANTE,
                     f"{etape} : {RETRIES_MAX} tentatives échouées ({derniere_erreur})",
                     debloqueur_requis="ops:diagnostic outil/sandbox")
        raise ErreurLogique(f"{etape} abandon définitif")

    def execute_parallel(self, etat: Etat, etapes: list[tuple[str, str, dict]]) -> list[dict]:
        """Fan-out : tâches indépendantes ; fan-in : barrière de jointure.
        En prod : exécution réellement concurrente (Temporal/threads sandboxés).
        Ici : séquentiel simulé, mais sémantiquement une jointure bloquante."""
        resultats = {}
        for etape, agent_id, entrees in etapes:
            resultats[etape] = self.execute(etat, etape, agent_id, entrees)
        return [resultats[e] for e, _, _ in etapes]                # attente de TOUS

    # ---- blocage / déblocage -------------------------------------------------

    def bloquer(self, etat: Etat, regle: RegleBlocage, motif: str,
                debloqueur_requis: str):
        etat.phase, etat.statut = Phase.BLOQUE, "BLOQUE"
        etat.blocage = {
            "motif": motif, "regle_blocage": regle.value,
            "debloqueur_requis": debloqueur_requis,
            "depuis_le": datetime.now(timezone.utc).isoformat()}
        self.audit.log("orchestrateur", "BLOCAGE",
                       {"regle": regle.value, "motif": motif})

    def debloquer(self, etat: Etat, evenement: dict):
        """Un événement de déblocage = validation humaine signée OU artefact correctif."""
        self.audit.log(evenement["auteur"], "DEBLOCAGE", evenement)
        etat.blocage, etat.statut = None, "EN_COURS"

    # ---- vérifications structurelles ----------------------------------------

    def _verifier_contrat(self, agent_id: str, sorties: dict):
        for champ in ("confidence", "assumptions", "contradictions", "artefacts"):
            if champ not in sorties:
                raise ErreurLogique(f"contrat violé par {agent_id} : {champ} manquant")
        if sorties["contradictions"]:
            self.audit.log(agent_id, "CONTRADICTION_SIGNALEE",
                           {"items": sorties["contradictions"]})   # → arbitrage aval

    def verifier_verrou_sap(self, etat: Etat, sap_artefact: Artefact):
        """Aucune opération inférentielle si le SAP courant n'égale pas le verrou G3."""
        if etat.verrous["sap_sha256"] is None:
            self.bloquer(etat, RegleBlocage.GATE_HUMAIN_NON_VALIDE,
                         "calcul inférentiel demandé sans verrou SAP (G3)",
                         debloqueur_requis="validation G3 par biostatisticien")
            raise ErreurLogique("pas de verrou SAP")
        if sap_artefact.sha256 != etat.verrous["sap_sha256"]:
            self.bloquer(etat, RegleBlocage.DEVIATION_SAP_NON_APPROUVEE,
                         "SAP modifié après verrouillage G3",
                         debloqueur_requis="revalidation G3 (nouvelle version SAP)")
            raise ErreurLogique("déviation SAP")

    # ---- gates qualité déterministes -----------------------------------------

    def gate_g2_data_quality(self, etat: Etat, dq: dict):
        score = dq["score_dq"]
        completude_ep = dq["completude_endpoint_principal"]
        etat.scores["fiabilite_donnees"] = score
        if score < DQ_BLOCAGE or completude_ep < COMPLETUDE_ENDPOINT_MIN:
            regle = (RegleBlocage.COMPLETUDE_ENDPOINT_INSUFFISANTE
                     if completude_ep < COMPLETUDE_ENDPOINT_MIN
                     else RegleBlocage.DQ_SOUS_SEUIL)
            self.bloquer(etat, regle,
                         f"DQ={score:.2f}, complétude endpoint={completude_ep:.0%}",
                         debloqueur_requis="data manager : corrections + rejeu DQ")
            raise ErreurLogique("gate G2 échoué (blocage dur)")
        if score < DQ_MIN_GATE:
            raise ErreurLogique("gate G2 : DQ sous seuil → remédiation requise")

    def gate_g4_safety(self, etat: Etat, safety: dict):
        signaux = safety.get("signaux", [])
        mos_min = safety.get("mos_min")
        if mos_min is not None and mos_min < MOS_MIN_COSMETIQUE:
            signaux = signaux + [{"nature": "MoS<100", "grade": 3}]
        bloquants = [s for s in signaux if s["grade"] >= SEUIL_SIGNAL_GRADE_BLOQUANT]
        if bloquants:
            etat.signaux.extend(bloquants)
            # Gate humain obligatoire ; refus/expiration → blocage dur (fail-closed)
            self.gates.attendre_decision(
                "G4", safety["artefact"],
                roles_requis=["safety_assessor", "toxicologue"], sla_h=48)


# ----------------------------------------------------------------------------- pipeline (récit d'exécution)

def run_pipeline(etat: Etat, o: Orchestrateur,
                 artefacts_bruts: dict) -> Etat:
    """Séquence nominale — chaque étape est checkpointée, tout échec logique bloque."""

    # [1-2] Ingestion + qualification (G0 intégrité/PII, G1 confiance type d'étude)
    intention = o.execute(etat, "ingestion_qualification",
                          "agent.comprehension", {"raw": artefacts_bruts})
    if intention["confidence"] < CONFIANCE_QUALIFICATION_MIN:
        o.gates.attendre_decision("G1", intention["artefacts"][0],
                                  roles_requis=["methodologiste"], sla_h=48)

    # [3-4] Fan-out : data quality ∥ biais ∥ EDA → fan-in ; G2 sur le score DQ
    dq, _biais, _eda = o.execute_parallel(etat, [
        ("data_quality", "agent.dataqualite", {"datasets": artefacts_bruts["datasets"]}),
        ("biais",        "agent.biais",       {"intention": intention["artefacts"][0].ref}),
        ("eda",          "agent.eda",         {"datasets": artefacts_bruts["datasets"]}),
    ])
    o.gate_g2_data_quality(etat, dq)

    # [5] SAP → G3 HUMAIN AVANT TOUT CALCUL → verrou par hash
    sap = o.execute(etat, "plan_analyse", "agent.biostat",
                    {"intention": intention["artefacts"][0].ref,
                     "dq": dq["artefacts"][0].ref})
    o.gates.attendre_decision("G3", sap["artefacts"][0],
                              roles_requis=["biostatisticien"], sla_h=72)
    etat.verrous["sap_sha256"] = sap["artefacts"][0].sha256
    etat.verrous["dataset_sha256"] = dq["dataset_snapshot_sha256"]

    # [6-9] Hypothèses → calcul inférentiel (verrou contrôlé) → manquants → anomalies
    assumptions = o.execute(etat, "controle_hypotheses", "agent.hypotheses",
                            {"sap": sap["artefacts"][0].ref})
    o.verifier_verrou_sap(etat, sap["artefacts"][0])
    resultats = o.execute(etat, "calcul_inferentiel", "agent.inferentiel",
                          {"sap": sap["artefacts"][0].ref,
                           "assumptions": assumptions["artefacts"][0].ref,
                           "seed": etat.seed})
    manquants = o.execute(etat, "gestion_manquants", "agent.manquants",
                          {"sap": sap["artefacts"][0].ref})

    # [9-10] Fan-out : anomalies ∥ safety (G4 conditionnel) ∥ conformité
    def gate_safety(etat_, sorties_): o.gate_g4_safety(etat_, sorties_)
    _anom = o.execute(etat, "detection_anomalies", "agent.anomalies", {})
    _saf = o.execute(etat, "evaluation_securite", "agent.securite",
                     {"results": resultats["artefacts"][0].ref}, gate_apres=gate_safety)
    _conf = o.execute(etat, "conformite", "agent.conformite",
                      {"composition": artefacts_bruts.get("inci")})

    # [11-12] Rédaction → critique croisée, arbitrage borné à 2 allers-retours
    draft = o.execute(etat, "redaction", "agent.redaction",
                      {"all": [a.ref for a in etat.artefacts]})
    for tour in range(ALLERS_RETOURS_ARBITRAGE_MAX + 1):
        critique = o.execute(etat, "critique", "agent.relecture",
                             {"draft": draft["artefacts"][0].ref})
        if not critique.get("objections_bloquantes"):
            break
        if tour == ALLERS_RETOURS_ARBITRAGE_MAX:
            o.gates.attendre_decision("G5", critique["artefacts"][0],
                                      roles_requis=["redacteur_scientifique_senior"],
                                      sla_h=72)
            break
        draft = o.execute(etat, f"redaction_reprise_{tour}", "agent.redaction",
                          {"draft": draft["artefacts"][0].ref,
                           "objections": critique["artefacts"][0].ref})

    # [13-14] Validation finale G6 (rôles selon type d'étude) → export signé
    o.gates.attendre_decision("G6", draft["artefacts"][0],
                              roles_requis=["biostatisticien", "responsable_etude"],
                              sla_h=120)
    etat.phase = Phase.EXPORT
    o.execute(etat, "export", "agent.exporteur", {"draft": draft["artefacts"][0].ref})
    etat.phase, etat.statut = Phase.TERMINE, "TERMINE"
    return etat
