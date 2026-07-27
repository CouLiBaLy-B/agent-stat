"""
Génération contrainte : provider → validation schéma → rétroaction → repli.

Garanties (cf. docs/LLM_INTEGRATION.md) :
- la réponse n'est acceptée que si elle satisfait le schéma du contrat ;
- la rétroaction contient UNIQUEMENT les erreurs de validation (jamais de
  contenu libre réinterprété) ;
- échec après N tentatives ⇒ ErreurLLM ⇒ l'agent applique son REPLI
  DÉTERMINISTE (jamais de blocage silencieux, toujours journalisé) ;
- tout est tracé : identité provider, hash du prompt, hash de la réponse,
  nombre de tentatives, indicateur de repli.
"""
from __future__ import annotations

import hashlib
import json

from llm.exceptions import ErreurLLM
from llm.validation import valider


def _sha(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:16]


def generer_contraint(provider, *, tache: str, systeme: str, utilisateur: str,
                      schema: dict, tentatives: int = 2,
                      temperature: float = 0.0) -> tuple[dict, dict]:
    """Retourne (objet_validé, méta). Lève ErreurLLM après épuisement."""
    meta = {"tache": tache, "provider": provider.identite,
            "prompt_sha": _sha(systeme + "\n" + utilisateur),
            "reponse_sha": None, "tentatives": 0, "schema_ok": False}
    retour = ""
    for essai in range(1, tentatives + 1):
        meta["tentatives"] = essai
        prompt_u = utilisateur if not retour else (
            utilisateur + "\n\n# ERREURS DE SCHÉMA À CORRIGER (réponds à nouveau, "
            "corrigé, même consigne) :\n" + retour)
        try:
            obj = provider.generer_json(tache=tache, systeme=systeme,
                                        utilisateur=prompt_u, schema=schema,
                                        temperature=temperature)
        except ErreurLLM:
            raise
        except Exception as e:                      # provider défaillant
            raise ErreurLLM(f"provider {provider.identite} injoignable : {e}")
        meta["reponse_sha"] = _sha(json.dumps(obj, sort_keys=True,
                                              ensure_ascii=False))
        erreurs = valider(schema, obj)
        if not erreurs:
            meta["schema_ok"] = True
            return obj, meta
        retour = "\n".join(f"- {e}" for e in erreurs[:20])
    raise ErreurLLM(f"{tache} : schéma non satisfait après {tentatives} "
                    f"tentatives — dernières erreurs : {retour[:400]}")
