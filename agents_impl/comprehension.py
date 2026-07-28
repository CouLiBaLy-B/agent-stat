"""Agent Compréhension du besoin — qualification du type d'étude.

Deux chemins, MÊME contrat de sortie `intention.json` :
- `llm` (si un provider est injecté) : génération contrainte par schéma +
  rétroaction ; toute défaillance ⇒ REPLI DÉTERMINISTE journalisé ;
- `deterministe` (défaut) : classifieur à règles lexicales versionné.
"""
from __future__ import annotations

import re

from agents_impl.base import Contexte, depot, sortie
from core.state import Etat
from llm import prompts, schemas
from llm.exceptions import ErreurLLM
from llm.generation import generer_contraint

# (type_etude, motifs_regex, poids) — table de qualification versionnée
REGLES_QUALIFICATION: dict[str, list[tuple[str, float]]] = {
    "test_usage_controle": [
        (r"test d[' ]usage", 0.55),
        (r"sous contr[ôo]le (dermatologique|ophtalmologique|gyn[ée]cologique)", 0.35),
        (r"produit cosm[ée]tique", 0.15), (r"tol[ée]rance", 0.15)],
    "tolerance_cutanee": [
        (r"tol[ée]rance cutan[ée]e", 0.6), (r"patch", 0.3), (r"[ée]ryth[èe]me", 0.2)],
    "essai_randomise": [
        (r"randomis[ée]", 0.6), (r"essai clinique", 0.3), (r"double insu|aveugle", 0.25)],
    "cas_temoins": [(r"cas[- ]t[ée]moins?", 0.8), (r"appari[ée]", 0.3)],
    "cohorte_prospective": [(r"cohorte", 0.4), (r"prospective", 0.4)],
    "cohorte_retrospective": [(r"cohorte", 0.4), (r"r[ée]trospective", 0.4)],
    "observationnelle_transversale": [
        (r"observationnelle?", 0.4), (r"transversale?", 0.4), (r"registre", 0.2)],
    "innocuite_cosmetique": [(r"innocuit[ée]", 0.7), (r"margin of safety|mos", 0.3)],
    "stabilite": [(r"stabilit[ée]", 0.7)],
}

CONFIANCE_MAX = 0.97


def qualifier(texte: str) -> tuple[str, float, list[str]]:
    """Classifieur déterministe de référence (et repli du chemin LLM)."""
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


def fabriquer(ctx: Contexte, llm=None):
    ctx.producteur = "agent.comprehension"

    def agent(etat: Etat, entrees: dict) -> dict:
        meta = entrees["raw"]["metadata"]
        pieces = entrees["raw"].get("pieces", [])
        texte = " ".join(str(meta.get(k, "")) for k in
                         ("objectif", "design_indice", "contexte", "type_pressenti"))

        # --- chemin déterministe (référence / repli) -------------------------
        type_etude, confiance, traces = qualifier(texte)
        justification = "correspondances lexicales : " + "; ".join(traces)
        endpoints, contraintes = meta.get("endpoints", []), meta.get("contraintes", [])
        contradictions: list[str] = []
        mode, meta_gen = "deterministe", None

        # --- chemin LLM contraint (si provider injecté) ----------------------
        if llm is not None:
            try:
                obj, meta_gen = generer_contraint(
                    llm, tache="qualification_etude",
                    systeme=prompts.systeme_comprehension(),
                    utilisateur=prompts.utilisateur_comprehension(meta, pieces),
                    schema=schemas.INTENTION)
                type_etude, confiance = obj["type_etude"], obj["confiance"]
                justification = obj["justification"]
                endpoints = obj.get("endpoints_pressentis", endpoints)
                contraintes = obj.get("contraintes", contraintes)
                contradictions = list(obj.get("contradictions", []))
                mode = "llm"
                ctx.audit.log("llm", "GENERATION", meta_gen)
            except ErreurLLM as e:
                contradictions = [f"sortie LLM non conforme ({e}) — repli "
                                  "déterministe appliqué"]
                ctx.audit.log("agent.comprehension", "REPLI_LLM",
                              {"erreur": str(e)[:300]})

        besoin_humain = (confiance < 0.8 or type_etude == "indetermine"
                         or bool(contradictions))
        intention = {
            "study_id": etat.study_id, "domaine": etat.domaine,
            "objectif": meta.get("objectif", ""),
            "type_etude_pressenti": type_etude, "confiance": confiance,
            "justification": justification,
            "mode_qualification": mode,
            "endpoints_pressentis": endpoints, "contraintes": contraintes,
            "livrables": etat.sorties_demandes, "pieces_recues": pieces,
        }
        art = depot(ctx, etat.study_id, "intention", "intention", intention)
        return sortie(confidence=confiance, artefacts=[art],
                      assumptions=[f"qualification en mode {mode}"],
                      contradictions=contradictions,
                      type_etude=type_etude, needs_human=besoin_humain)
    return agent
