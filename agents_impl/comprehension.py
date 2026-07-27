"""Agent Compréhension du besoin — qualification déterministe par règles (MVP).

Phase 1 : le classifieur à mots-clés est remplacé par un LLM contraint par
schéma ; le contrat de sortie `intention.json` reste identique.
"""
from __future__ import annotations

import re

from agents_impl.base import Contexte, depot, sortie
from core.state import Etat

# (type_etude, motifs_regex, poids) — table de qualification versionnée
REGLES_QUALIFICATION: dict[str, list[tuple[str, float]]] = {
    "test_usage_controle": [
        (r"test d[' ]usage", 0.55), (r"sous contr[ôo]le (dermatologique|ophtalmologique|gyn[ée]cologique)", 0.35),
        (r"produit cosm[ée]tique", 0.15), (r"tol[ée]rance", 0.15)],
    "tolerance_cutanee": [
        (r"tol[ée]rance cutan[ée]e", 0.6), (r"patch", 0.3), (r"[ée]ryth[èe]me", 0.2)],
    "essai_randomise": [
        (r"randomis[ée]", 0.6), (r"essai clinique", 0.3), (r"double insu|aveugle", 0.25)],
    "cas_temoins": [(r"cas[- ]t[ée]moins?", 0.8)],
    "cohorte_prospective": [(r"cohorte", 0.4), (r"prospective", 0.4)],
    "cohorte_retrospective": [(r"cohorte", 0.4), (r"r[ée]trospective", 0.4)],
    "observationnelle_transversale": [
        (r"observationnelle?", 0.4), (r"transversale?", 0.4), (r"registre", 0.2)],
    "innocuite_cosmetique": [(r"innocuit[ée]", 0.7), (r"margin of safety|mos", 0.3)],
    "stabilite": [(r"stabilit[ée]", 0.7)],

}

CONFIANCE_MAX = 0.97


def qualifier(texte: str) -> tuple[str, float, list[str]]:
    t = texte.lower()
    scores: dict[str, float] = {}
    traces: list[str] = []
    for type_etude, motifs in REGLES_QUALIFICATION.items():
        total = sum(poids for motif, poids in motifs if re.search(motif, t))
        if total:
            scores[type_etude] = total
            traces.append(f"{type_etude}: {total:.2f}")
    if not scores:
        return "indetermine", 0.0, ["aucun motif reconnu"]
    best = max(scores, key=scores.get)
    confiance = min(CONFIANCE_MAX, 0.45 + scores[best] * 0.5)
    return best, round(confiance, 3), traces


def fabriquer(ctx: Contexte):
    ctx.producteur = "agent.comprehension"

    def agent(etat: Etat, entrees: dict) -> dict:
        meta = entrees["raw"]["metadata"]
        texte = " ".join(str(meta.get(k, "")) for k in
                         ("objectif", "design_indice", "contexte", "type_pressenti"))
        type_etude, confiance, traces = qualifier(texte)
        intention = {
            "study_id": etat.study_id, "domaine": etat.domaine,
            "objectif": meta.get("objectif", ""),
            "type_etude_pressenti": type_etude,
            "confiance": confiance,
            "traces_qualification": traces,
            "endpoints_pressentis": meta.get("endpoints", []),
            "contraintes": meta.get("contraintes", []),
            "livrables": etat.sorties_demandes,
            "pieces_recues": entrees["raw"].get("pieces", []),
        }
        art = depot(ctx, etat.study_id, "intention", "intention", intention)
        besoin_humain = confiance < 0.8 or type_etude == "indetermine"
        return sortie(confidence=confiance, artefacts=[art],
                      assumptions=["classification par règles lexicales v1"],
                      contradictions=[] if not besoin_humain else
                      ["qualification incertaine — G1 requis"],
                      type_etude=type_etude, needs_human=besoin_humain)
    return agent
