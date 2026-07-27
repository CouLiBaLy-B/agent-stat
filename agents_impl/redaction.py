"""Agent Rédaction du rapport + Exporteur final.

Garanties mécaniques (cf. ARCHITECTURE.md §C.9 / §D.1) :
- séparation stricte FAITS / INFÉRENCES / RECOMMANDATIONS ;
- chaque ligne de FAITS chiffrée cite `art://…#sha256:…` ;
- la verbalisation du score CC borne la force de la conclusion ;
- section « Limites et hypothèses » obligatoire, non vide (ErreurLogique sinon) ;
- lexique contrôlé : hors essai randomisé, tournures d'association seulement.
"""
from __future__ import annotations

import json
from pathlib import Path

from agents_impl.base import Contexte, depot, sortie
from core.exceptions import ErreurLogique
from core.state import Etat


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "n/d"
    return "< 0,001" if p < 0.001 else f"= {p:.3f}".replace(".", ",")


def _fmt(x: float | None, nd: int = 2) -> str:
    return "n/d" if x is None else f"{x:.{nd}f}".replace(".", ",")


def _src(art) -> str:
    return f"(source : {art.ref} #sha256:{art.sha256[:12]}…)"


def fabriquer_redaction(ctx: Contexte):
    ctx.producteur = "agent.redaction"

    def agent(etat: Etat, e: dict) -> dict:
        sap, res = e["sap"], e["resultats"]["resultats"]
        dq, safety, conf = e["dq"], e["safety"], e["conformite"]
        scores = e["scores"]
        src_res, src_dq = e["results_art"], e["dq_art"]
        src_saf, src_conf = e["safety_art"], e["compliance_art"]
        experimental = "randomise" in etat.type_etude
        verbe = "montre un effet" if experimental else "est associée à"
        cc = scores.get("confiance_conclusion") or 0.0

        # ---------- FAITS ---------------------------------------------------
        faits: list[str] = []
        a1 = res.get("A1", {}).get("resultat", {})
        if a1.get("interpretable"):
            faits.append(
                f"- Critère principal `{sap['endpoint_principal']['variable']}` : "
                f"différence {_fmt(a1.get('difference'))} "
                f"(IC95 % [{_fmt((a1.get('ic95_difference') or [None])[0])} ; "
                f"{_fmt((a1.get('ic95_difference') or [None, None])[1])}]), "
                f"test {a1.get('test')}, p {_fmt_p(a1.get('p_valeur'))}, "
                f"taille d'effet d={_fmt(a1.get('taille_effet_cohen_d'))} {_src(src_res)}")
        a2 = res.get("A2", {})
        for g, r in a2.get("par_groupe", {}).items():
            ic = r.get("ic95") or [None, None]
            faits.append(
                f"- Tolérance ({g}) : {r['succes']}/{r['total']} réactions "
                f"≥ grade {e['spec'].get('seuil_grade_reaction', 2)} "
                f"({_fmt(100 * r['proportion'], 1)} %, IC95 % exact "
                f"[{_fmt(100 * ic[0], 1)} ; {_fmt(100 * ic[1], 1)}]) {_src(src_saf)}")
        faits.append(f"- Score qualité des données DQ = {dq['score_dq']:.2f} "
                     f"{_src(src_dq)}")
        if safety.get("mos_min") is not None:
            faits.append(f"- MoS minimale = {_fmt(safety['mos_min'], 0)} "
                         f"(seuil 100) {_src(src_saf)}")

        # ---------- INFÉRENCES ----------------------------------------------
        inferences: list[str] = []
        conclusion_directionnelle = None
        if a1.get("interpretable") and cc >= 0.4:
            sig = a1.get("p_valeur", 1.0) < 0.05
            inferences.append(
                f"- Au seuil 5 %, la différence observée sur le critère principal "
                f"{verbe} {'une différence statistiquement significative' if sig else 'aucune différence statistiquement significative'} "
                f"entre les groupes ; l'intervalle de confiance et la taille "
                f"d'effet bornent l'ampleur plausible.")
            if not sig:
                inferences.append(
                    "- Absence de significativité ≠ absence d'effet : aucune "
                    "équivalence ne peut être affirmée sans test pré-spécifié (TOST).")
            conclusion_directionnelle = "significatif" if sig else "non_significatif"
        elif a1.get("interpretable"):
            inferences.append("- Confiance de conclusion insuffisante (CC < 0,40) : "
                              "aucune conclusion directionnelle autorisée.")

        # ---------- RECOMMANDATIONS -----------------------------------------
        reco = list(e.get("actions", []))
        if conf.get("regles_ko"):
            reco.append("Lever les non-conformités bloquantes avant toute "
                        "poursuite (revue expert réglementaire).")
        if safety.get("signaux"):
            reco.append("Revue safety assessor/toxicologue des signaux (G4) "
                        "avant toute décision.")
        if not reco:
            reco.append("Aucune action corrective requise par les agents — "
                        "soumettre à validation humaine G6.")

        # ---------- décision proposée (borne la force) -----------------------
        if conf.get("verdict_conformite") == "NON_CONFORME_BLOQUANT":
            decision = "Ne pas poursuivre en l'état — non-conformité bloquante."
        elif any(s.get("grade", 0) >= 2 for s in safety.get("signaux", [])):
            decision = "Suspendre la conclusion — signaux de sécurité à trancher (G4)."
        elif cc < 0.4:
            decision = ("Données insuffisantes pour conclure — tests/compléments "
                        "requis avant réévaluation.")
        else:
            decision = ("Avis favorable prudent proposé — soumis à validation "
                        "humaine G6.")

        limites = [f"[données] {a}" for a in e.get("dq_assumptions", [])] \
            + [f"[analyse] {a}" for a in e.get("analyse_assumptions", [])] \
            + ["Verbalisation CC = "
               f"{scores.get('verbalisation_cc', 'n/a')} ; toute conclusion est "
               "bornée par ce score.",
               "Référentiel réglementaire : échantillon pédagogique (phase 1 : "
               "référentiel consolidé + revue experte)."]

        md = f"""# Rapport d'analyse — {etat.study_id}

**Type d'étude** : {etat.type_etude} · **Domaine** : {etat.domaine} · **Run** : {etat.run_id}

## Résumé exécutif
- **Décision proposée** : {decision}
- **Scores** : DQ (fiabilité données) = {scores.get('fiabilite_donnees')} ·
  RA (robustesse analyse) = {scores.get('robustesse_analyse')} ·
  CC (confiance conclusion) = {scores.get('confiance_conclusion')}
  (**{scores.get('verbalisation_cc', 'n/a')}**)
- **Conformité** : {conf.get('verdict_conformite')} · **Signaux safety** : {len(safety.get('signaux', []))}

## Méthodes (SAP verrouillé, {_src(e['sap_art'])})
- Endpoint principal : `{sap['endpoint_principal']['variable']}` (unique, pré-spécifié)
- Population : {sap['population_analyse']['definition']}
- Multiplicité : {sap['gestion_multiplicite']}
- Manquants : {sap['gestion_manquants']['strategie']}

## Résultats — FAITS (chiffres sourcés)
{chr(10).join(faits)}

## Inférences (interprétation contrôlée)
{chr(10).join(inferences)}

## Risques
- Signaux safety : {json.dumps(safety.get('signaux', []), ensure_ascii=False)}
- Règles conformité KO : {conf.get('regles_ko') or 'aucune'} · incertaines : {conf.get('regles_incertaines') or 'aucune'}

## Actions recommandées
{chr(10).join(f'- {r}' for r in reco)}

## Limites et hypothèses
{chr(10).join(f'- {l}' for l in limites)}

## Validation humaine requise
- **G6** : validation finale du présent rapport (rôles : biostatisticien,
  responsable d'étude{', safety assessor' if etat.domaine == 'cosmetique' else ''}).
- Toute utilisation avant G6 est interdite (fail-closed).
"""
        if not limites:
            raise ErreurLogique("section limites vide — interdit")
        rapport = {"markdown": md, "lignes_faits": faits,
                   "texte_inferences": "\n".join(inferences),
                   "conclusion_directionnelle": conclusion_directionnelle,
                   "decision_proposee": decision, "limites": limites}
        art = depot(ctx, etat.study_id, "report", "rapport_draft", rapport,
                    utilisant=[src_res.ref, src_dq.ref, src_saf.ref, src_conf.ref])
        return sortie(confidence=0.88, artefacts=[art],
                      assumptions=["gabarit v1 — séparation faits/inférences/reco "
                                   "mécanique"],
                      rapport=rapport, markdown=md, report_ref=art.ref)
    return agent


# ------------------------------------------------------------------ export

def fabriquer_exporteur(ctx: Contexte, racine_exports: str):
    ctx.producteur = "agent.exporteur"

    def agent(etat: Etat, e: dict) -> dict:
        dossier = Path(racine_exports) / etat.study_id
        dossier.mkdir(parents=True, exist_ok=True)
        md = e["rapport"]["markdown"]
        p_md = dossier / "rapport_final.md"
        p_md.write_text(md, encoding="utf-8")
        meta = {"study_id": etat.study_id, "run_id": etat.run_id,
                "type_etude": etat.type_etude, "scores": etat.scores,
                "gates": "tous validés (journal d'audit)",
                "fichier": str(p_md)}
        (dossier / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        art = depot(ctx, etat.study_id, "export", "rapport_final",
                    {"markdown": md, "chemin": str(p_md)}, statut="VALIDATED")
        return sortie(confidence=0.95, artefacts=[art],
                      export_chemin=str(p_md), export_ref=art.ref)
    return agent
