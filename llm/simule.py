"""Répondeurs du ProviderSimule : simulent un LLM fiable ET des défaillances.

`repondre_qualification` / `repondre_proposition_sap` rejouent la logique
déterministe de référence derrière l'interface LLM — objectif : vérifier la
PLONBERIE (contrats, rétroaction, repli, audit), pas l'intelligence.
Les variantes `defaillant_*` servent aux tests de robustesse.
"""
from __future__ import annotations

import json
import re

from agents_impl.biostat import GABARITS
from agents_impl.comprehension import qualifier
from llm.provider import ProviderSimule

MARQUEUR_RE = re.compile(r"<donnees>\s*(\{.*\})\s*</donnees>", re.S)


def _corpus(utilisateur: str) -> dict:
    m = MARQUEUR_RE.search(utilisateur)
    return json.loads(m.group(1)) if m else {}


def repondre_qualification(utilisateur: str, appel: int = 1) -> dict:
    corpus = _corpus(utilisateur)
    meta = corpus.get("metadata", {})
    texte = " ".join(str(meta.get(k, "")) for k in
                     ("objectif", "design_indice", "contexte", "type_pressenti"))
    type_etude, confiance, traces = qualifier(texte)
    return {
        "type_etude": type_etude,
        "confiance": confiance,
        "justification": "Correspondances lexicales majeures : "
                         + "; ".join(traces),
        "endpoints_pressentis": meta.get("endpoints", []),
        "contraintes": [],
        "contradictions": [] if confiance >= 0.8
        else ["qualification incertaine — revue humaine G1 recommandée"],
    }


def repondre_proposition_sap(utilisateur: str, appel: int = 1) -> dict:
    corpus = _corpus(utilisateur)
    spec_reconstruit = {
        "endpoint_principal": corpus.get("endpoint_principal"),
        "variable_groupe": corpus.get("variable_groupe", "groupe"),
        "contraste": corpus.get("contraste", ["produit", "controle"]),
        "var_reaction": corpus.get("var_reaction", "reaction_grade"),
    }
    fabrique = GABARITS.get(corpus.get("type_etude")) or GABARITS[
        "test_usage_controle"]
    analyses = fabrique(spec_reconstruit, {})
    for ana in analyses:                       # respecter le schéma LLM (clés limitées)
        for cle in ("hypotheses", "definition", "multiplicite", "bornes", "note"):
            ana.pop(cle, None)
    return {
        "analyses": analyses,
        "gestion_multiplicite": ("gatekeeping : primaire d'abord ; Holm si "
                                 "secondaires confirmatoires ; exploratoire "
                                 "étiqueté"),
        "gestion_manquants_strategie": ("cas complets si taux ≤ 5 % ; sinon MI "
                                        "requise → escalade biostatisticien"),
        "justification_globale": "gabarit aligné catalogue fermé (simulé)",
    }


def repondre_json_invalide_puis_valide(utilisateur: str, appel: int = 1) -> dict:
    """Simule un LLM qui se trompe puis se corrige sous rétroaction."""
    if appel == 1:
        return {"type_etude": "etude_magique", "confiance": 7}   # hors schéma
    return repondre_qualification(utilisateur, appel)


def repondre_toujours_invalide(utilisateur: str, appel: int = 1) -> dict:
    return {"inattendu": True}


def provider_simule_defaut() -> ProviderSimule:
    return ProviderSimule({
        "qualification_etude": repondre_qualification,
        "proposition_sap": repondre_proposition_sap,
    })
