"""Exceptions propres à la couche LLM."""


class ErreurLLM(Exception):
    """Échec de la chaîne LLM (provider injoignable, sortie invalide après
    rétroaction, quota…) — toujours suivie du repli déterministe ou d'un blocage."""


class ReponseNonJSON(ErreurLLM):
    """Le provider n'a pas retourné de JSON exploitable."""
