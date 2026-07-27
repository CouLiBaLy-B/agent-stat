"""État partagé du pipeline : dataclass JSON-sérialisable + checkpoints atomiques."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


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


@dataclass
class Etat:
    run_id: str
    study_id: str
    domaine: str                     # medical | cosmetique
    seed: int
    type_etude: str = "indetermine"
    confiance_qualification: float | None = None
    phase: str = Phase.INGESTION.value
    statut: str = "EN_COURS"         # EN_COURS|EN_ATTENTE_HUMAIN|BLOQUE|TERMINE|ABANDONNE
    blocage: dict | None = None
    scores: dict = field(default_factory=lambda: {
        "fiabilite_donnees": None, "robustesse_analyse": None,
        "confiance_conclusion": None, "verbalisation_cc": None})
    verrous: dict = field(default_factory=lambda: {
        "sap_sha256": None, "dataset_sha256": None})
    artefacts: list[str] = field(default_factory=list)   # refs art://…
    signaux: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    sorties_demandes: list[str] = field(default_factory=lambda: [
        "resume_executif", "sap", "rapport_resultats", "liste_risques",
        "actions_recommandees", "limites_hypotheses", "validation_humaine_requise"])

    # -- persistance ----------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Etat":
        return cls(**d)

    @classmethod
    def charger(cls, chemin: str | Path) -> "Etat":
        return cls.from_dict(json.loads(Path(chemin).read_text(encoding="utf-8")))

    def sauvegarder(self, chemin: str | Path) -> None:
        p = Path(chemin); p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)                                    # atomique

    def checkpoint(self, dossier: str | Path, etiquette: str) -> Path:
        """Checkpoint par étape → reprise propre depuis n'importe quelle phase."""
        p = Path(dossier) / f"ckpt_{self.run_id}_{etiquette}.json"
        self.sauvegarder(p)
        return p

    # -- décisions qui changent la vie du pipeline ----------------------------
    def maintenant_bloque(self, regle: RegleBlocage, motif: str,
                          debloqueur: str) -> None:
        self.phase, self.statut = Phase.BLOQUE.value, "BLOQUE"
        self.blocage = {
            "motif": motif, "regle_blocage": regle.value,
            "debloqueur_requis": debloqueur,
            "depuis_le": datetime.now(timezone.utc).isoformat()}
