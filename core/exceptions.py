"""Exceptions transverses du pipeline."""


class ErreurTechnique(Exception):
    """Retryable : timeout outil, crash sandbox, indisponibilité transitoire."""


class ErreurLogique(Exception):
    """NON retryable : schéma invalide, gate échoué, précondition absente."""


class ErreurContrat(ErreurLogique):
    """Sortie d'agent non conforme au contrat (confidence/assumptions/...)."""


class GateError(ErreurLogique):
    """Base des erreurs de gate humain."""

    def __init__(self, gate_id: str, motif: str):
        super().__init__(f"gate {gate_id} : {motif}")
        self.gate_id, self.motif = gate_id, motif


class GateRefuse(GateError):
    """Décision humaine explicite de refus."""


class GateExpire(GateError):
    """Pas de décision exploitable à l'échéance — fail-closed."""


class PipelineBloque(ErreurLogique):
    """Levée quand le pipeline entre en état BLOQUE (fail-closed)."""

    def __init__(self, etat, regle, motif):
        super().__init__(f"[{regle.value}] {motif}")
        self.etat, self.regle, self.motif = etat, regle, motif
