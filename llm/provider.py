"""
Fournisseurs LLM derrière un contrat unique.

- `ProviderSimule` : deterministic, scripté — sert aux tests et à la démo
  (`AGENT_STAT_LLM_MODE=llm-simule`) ; simule un LLM parfait OU défaillant.
- `ProviderOpenAICompatible` : endpoint HTTP chat/completions (urllib, stdlib) —
  toute base compatible (OpenAI, Azure, vLLM, Ollama…) via variables d'environnement :
  `AGENT_STAT_LLM_BASE_URL`, `AGENT_STAT_LLM_API_KEY`, `AGENT_STAT_LLM_MODEL`.
- `provider_depuis_env()` : `AGENT_STAT_LLM_MODE ∈ {off, llm-simule, http}`.

Règle d'or (cf. ARCHITECTURE.md) : le LLM ne CALCULE jamais — ses sorties sont
des plans/classifications/narratifs, validés par schéma puis par les règles
métier déterministes.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Protocol

from llm.exceptions import ErreurLLM, ReponseNonJSON


class LLMProvider(Protocol):
    @property
    def identite(self) -> str: ...

    def generer_json(self, *, tache: str, systeme: str, utilisateur: str,
                     schema: dict, temperature: float = 0.0) -> dict: ...


# ------------------------------------------------------------------ simulé

Repondeur = Callable[..., dict]      # (utilisateur: str, appel: int) -> dict


class ProviderSimule:
    """Répondeurs scriptés par tâche ; `appel` permet de simuler des échecs
    successifs (tests de rétroaction / repli)."""

    def __init__(self, repondeurs: dict[str, Repondeur], identite: str = "simule:1.0.0"):
        self._repondeurs, self._identite = repondeurs, identite
        self.appels: list[dict] = []

    @property
    def identite(self) -> str:
        return self._identite

    def generer_json(self, *, tache: str, systeme: str, utilisateur: str,
                     schema: dict, temperature: float = 0.0) -> dict:
        n = sum(1 for a in self.appels if a["tache"] == tache) + 1
        self.appels.append({"tache": tache, "appel": n})
        fn = self._repondeurs.get(tache)
        if fn is None:
            raise ErreurLLM(f"pas de répondeur simulé pour la tâche {tache!r}")
        return fn(utilisateur=utilisateur, appel=n)


# ------------------------------------------------------------------ HTTP (OpenAI-compatible)

class ProviderOpenAICompatible:
    def __init__(self, base_url: str, api_key: str, modele: str,
                 timeout_s: float = 60.0):
        if not base_url or not modele:
            raise ErreurLLM("configuration HTTP incomplète (base_url, modele)")
        self.base_url = base_url.rstrip("/")
        self.api_key, self.modele, self.timeout_s = api_key, modele, timeout_s

    @property
    def identite(self) -> str:
        return f"http:{self.modele}"

    @classmethod
    def depuis_env(cls) -> "ProviderOpenAICompatible":
        return cls(base_url=os.environ.get("AGENT_STAT_LLM_BASE_URL", ""),
                   api_key=os.environ.get("AGENT_STAT_LLM_API_KEY", ""),
                   modele=os.environ.get("AGENT_STAT_LLM_MODEL", ""))

    def generer_json(self, *, tache: str, systeme: str, utilisateur: str,
                     schema: dict, temperature: float = 0.0) -> dict:
        payload = {
            "model": self.modele, "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": systeme},
                {"role": "user", "content":
                    utilisateur + "\n\nRéponds UNIQUEMENT par un JSON conforme à ce "
                    "schéma (aucune prose, aucun markdown) :\n"
                    + json.dumps(schema, ensure_ascii=False)}],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                corps = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise ErreurLLM(f"appel HTTP échoué ({tache}) : {e}")
        try:
            texte = corps["choices"][0]["message"]["content"]
            debut, fin = texte.find("{"), texte.rfind("}")
            return json.loads(texte[debut: fin + 1])
        except Exception as e:
            raise ReponseNonJSON(f"contenu non JSON ({tache}) : {e}")


# ------------------------------------------------------------------ fabrique d'environnement

def provider_depuis_env() -> LLMProvider | None:
    mode = os.environ.get("AGENT_STAT_LLM_MODE", "off").strip().lower()
    if mode in ("", "off"):
        return None
    if mode == "http":
        return ProviderOpenAICompatible.depuis_env()
    if mode == "llm-simule":
        from llm.simule import provider_simule_defaut
        return provider_simule_defaut()
    raise ErreurLLM(f"AGENT_STAT_LLM_MODE inconnu : {mode!r}")
