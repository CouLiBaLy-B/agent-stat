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


OPS_ASSOCIATION = {"odds_ratio_cas_temoins", "or_apparie",
                   "risque_relatif_cohorte", "km_logrank_hr"}

GRANDEUR_ASSOCIATION = {"odds_ratio_cas_temoins": "l'OR",
                        "or_apparie": "l'OR apparié",
                        "risque_relatif_cohorte": "le RR",
                        "km_logrank_hr": "le HR"}


def _faits_op_association(op: str, r: dict) -> str:
    """Ligne de FAITS sourcée pour une opération d'association — mesure
    d'association + IC95 % + p, JAMAIS la p seule, lexique non causal."""
    if op == "odds_ratio_cas_temoins":
        ic = r.get("ic95_or") or [None, None]
        corr = (" (correction de Haldane-Anscombe +0,5 déclarée)"
                if r.get("correction_haldane_anscombe") else "")
        return (f"OR de cas-témoins = {_fmt(r['odds_ratio'])} "
                f"(IC95 % [{_fmt(ic[0])} ; {_fmt(ic[1])}]), "
                f"p exacte de Fisher {_fmt_p(r.get('p_valeur'))}{corr} ; "
                f"exposés : cas {_fmt(100 * (r.get('exposes_cas') or 0), 1)} % vs "
                f"témoins {_fmt(100 * (r.get('exposes_temoins') or 0), 1)} %")
    if op == "or_apparie":
        ic = r.get("ic95_or") or [None, None]
        corr = (" (correction +0,5 : discordance nulle d'un côté — déclarée)"
                if r.get("correction_zero_discordant") else "")
        return ("OR apparié conditionnel = "
                f"{_fmt(r['odds_ratio'])} (IC95 % [{_fmt(ic[0])} ; {_fmt(ic[1])}]), "
                f"p exacte de McNemar {_fmt_p(r.get('p_valeur'))}{corr} ; "
                f"paires discordantes {r['paires_bc_cas_expose_temoin_non']}/"
                f"{r['paires_cb_cas_non_temoin_expose']} sur "
                f"{r.get('paires_appariees', 'n/d')} appariées "
                f"({r.get('paires_concordantes', 'n/d')} concordantes)")
    if op == "risque_relatif_cohorte":
        ic = r.get("ic95_rr") or [None, None]
        icd = r.get("ic95_difference_risques") or [None, None]
        corr = (" (correction de Haldane-Anscombe +0,5 déclarée)"
                if r.get("correction_haldane_anscombe") else "")
        return (f"RR = {_fmt(r['risque_relatif'])} "
                f"(IC95 % [{_fmt(ic[0])} ; {_fmt(ic[1])}]) ; "
                f"risques : exposés {_fmt(100 * r['risque_expose'], 1)} % vs "
                f"non-exposés {_fmt(100 * r['risque_non_expose'], 1)} % ; "
                f"différence de risques {_fmt(100 * r['difference_risques'], 1)} "
                f"points (IC95 % de Newcombe [{_fmt(100 * icd[0], 1)} ; "
                f"{_fmt(100 * icd[1], 1)}]) ; "
                f"p exacte de Fisher {_fmt_p(r.get('p_valeur'))}{corr}")
    if op == "km_logrank_hr":
        ic = r.get("ic95_hr") or [None, None]
        return (f"HR (estimateur de Peto / log-rang) = {_fmt(r['hr'])} "
                f"(IC95 % [{_fmt(ic[0])} ; {_fmt(ic[1])}]), "
                f"p log-rang {_fmt_p(r.get('p_valeur'))} ; "
                f"médianes de survie {_fmt(r['mediane_survie_g1'], 1)} vs "
                f"{_fmt(r['mediane_survie_g2'], 1)} mois/ut ; "
                f"événements {r['evenements1']}/{r['n1']} vs "
                f"{r['evenements2']}/{r['n2']} ; risques relatifs constants "
                "supposés (à confirmer)")
    return ""


def _faits_ajustement(op: str, r: dict, entipes: dict) -> str:
    """Ligne de FAITS pour l'analyse d'ajustement multivariée A3 — mesure
    ajustée + IC95 % pour CHAQUE coefficient, jamais la p seule ; le rappel
    « association ajustée ≠ causalité » reste dans le même fait."""
    coefs = r.get("coefficients", [])
    if not coefs:
        return ""
    mesure = "OR" if op == "regression_logistique" else "HR"
    cle_m = "odds_ratio" if op == "regression_logistique" else "hazard_ratio"
    cle_ic = "ic95_or" if op == "regression_logistique" else "ic95_hr"
    morceaux = []
    for c in coefs:
        ic = c.get(cle_ic) or [None, None]
        morceaux.append(
            f"`{c['covariable']}` {mesure} ajusté = {_fmt(c[cle_m])} "
            f"(IC95 % [{_fmt(ic[0])} ; {_fmt(ic[1])}]), "
            f"p de Wald {_fmt_p(c['p_valeur'])}")
    modele = ("régression logistique binaire (IRLS)"
              if op == "regression_logistique" else
              "modèle de Cox à risques proportionnels (Breslow)")
    extra = (f"pseudo-R² de McFadden {_fmt(r.get('pseudo_r2_mcfadden'))}, "
             if op == "regression_logistique" else "")
    return (f"Association AJUSTÉE ({modele}, SAP verrouillé G3, cas complets "
            f"n={r.get('n')}, EPV={r.get('epv')} ≥ {r.get('seuil_epv'):g}) : "
            + " ; ".join(morceaux)
            + f" ; modèle global : χ²({_fmt(r.get('ddl_modele'), 0)} ddl) p "
            + _fmt_p(r.get("p_valeur_modele")) + f" ; {extra}"
            + "mesures AJUSTÉES aux covariables pré-déclarées — restent "
            "associatives (confusion non mesurée)")


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
        op1 = res.get("A1", {}).get("op_retenue", "")

        # ---------- FAITS ---------------------------------------------------
        faits: list[str] = []
        a1 = res.get("A1", {}).get("resultat", {})
        # ops d'association (observationnel) : rendu dédié, par rôle
        for aid, ana in res.items():
            op_r = ana.get("op_retenue")
            if op_r not in OPS_ASSOCIATION:
                continue
            r = ana.get("resultat", {})
            etiqu = {"primaire": "Association primaire",
                     "secondaire": "Association secondaire"}.get(
                         ana.get("role"), ana.get("role", "Association"))
            if not r.get("interpretable"):
                faits.append(f"- {etiqu} `{aid}` ({op_r}) : non interprétable"
                             f" — {r.get('motif')} {_src(src_res)}")
                continue
            faits.append(f"- {etiqu} : {_faits_op_association(op_r, r)} "
                         f"{_src(src_res)}")
        # prévalences descriptives (Wilson par groupe)
        for aid, ana in res.items():
            if ana.get("op_retenue") != "proportion_wilson":
                continue
            ent = ana.get("entrees", {})
            for g, r in ana.get("par_groupe", {}).items():
                ic = r.get("ic95") or [None, None]
                faits.append(
                    f"- Prévalence `{ent.get('var')}` ({g}) : "
                    f"{r['succes']}/{r['total']} ({_fmt(100 * r['proportion'], 1)} %, "
                    f"IC95 % de Wilson [{_fmt(100 * ic[0], 1)} ; "
                    f"{_fmt(100 * ic[1], 1)}]) {_src(src_res)}")
        if a1.get("interpretable") and a1.get("test") == "tost_equivalence":
            ic90 = a1.get("ic90_difference") or [None, None]
            faits.append(
                f"- Équivalence (TOST, marge Δ={_fmt(a1.get('marge'))}) : "
                f"différence {_fmt(a1.get('difference'))} "
                f"(IC90 % [{_fmt(ic90[0])} ; {_fmt(ic90[1])}]), "
                f"p_TOST {_fmt_p(a1.get('p_tost'))} → "
                f"{a1.get('verdict', '').replace('_', ' ')} {_src(src_res)}")
        elif a1.get("interpretable") and op1 not in OPS_ASSOCIATION:
            faits.append(
                f"- Critère principal `{sap['endpoint_principal']['variable']}` : "
                f"différence {_fmt(a1.get('difference'))} "
                f"(IC95 % [{_fmt((a1.get('ic95_difference') or [None])[0])} ; "
                f"{_fmt((a1.get('ic95_difference') or [None, None])[1])}]), "
                f"test {a1.get('test')}, p {_fmt_p(a1.get('p_valeur'))}, "
                f"taille d'effet d={_fmt(a1.get('taille_effet_cohen_d'))} {_src(src_res)}")
        # ajustement multivarié A3 (observationnel, SAP verrouillé)
        a3_res = next((a for a in res.values()
                       if a.get("role") == "ajustement"), None)
        if a3_res is not None:
            r3 = a3_res.get("resultat", {})
            if r3.get("interpretable"):
                faits.append(f"- {_faits_ajustement(a3_res['op_retenue'], r3, a3_res.get('entrees', {}))} "
                             f"{_src(src_res)}")
            else:
                faits.append(
                    f"- Ajustement multivarié `{a3_res.get('op_retenue')}` "
                    f"(A3, SAP verrouillé) : NON interprétable — "
                    f"{r3.get('motif')} ; aucune mesure ajustée présentée "
                    f"(fail-closed) {_src(src_res)}")
        sens = e["resultats"].get("sensibilites", {}).get("A1_sensibilite_MI")
        if sens and sens.get("pooled", {}).get("interpretable"):
            p = sens["pooled"]
            faits.append(
                f"- Sensibilité (imputation multiple PMM m={sens['m']}, pooling "
                f"de Rubin) : effet poolé {_fmt(p['theta_pooled'])} "
                f"(IC95 % [{_fmt(p['ic95'][0])} ; {_fmt(p['ic95'][1])}]), "
                f"p {_fmt_p(p['p_valeur'])}, FMI {_fmt(100*p['fraction_info_manquante'],1)} %, "
                f"tipping δ={sens.get('delta_flip')} {_src(src_res)}")
        for aid, ana in res.items():
            if ana.get("op_retenue") != "tendance_lineaire":
                continue
            r = ana["resultat"]
            if r.get("interpretable"):
                bornes = (e["spec"].get("bornes_acceptation", {})
                          .get(r.get("var")))
                faits.append(
                    f"- Stabilité `{r.get('var')}` : pente {_fmt(r['pente'],4)}/temps "
                    f"(IC95 % [{_fmt(r['ic95_pente'][0],4)} ; {_fmt(r['ic95_pente'][1],4)}]), "
                    f"moyenne à {r['temps_max']} = {_fmt(r['moyenne_tmax'],2)} "
                    f"(bornes {bornes}) {_src(src_res)}")
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
        if op1 in OPS_ASSOCIATION and a1.get("interpretable"):
            if cc >= 0.4:
                a3_ok = (a3_res is not None
                         and (a3_res.get("resultat") or {}).get("interpretable"))
                if a3_ok:
                    # mesure d'intérêt PRE-DÉCLARÉE = association ajustée (A3) ;
                    # la mesure brute (A1) reste rapportée en FAITS.
                    ce = a3_res["resultat"]["coefficients"][0]   # exposition imposée
                    sig = (ce.get("p_valeur") or 1.0) < 0.05
                    mesure = ("OR" if a3_res["op_retenue"]
                              == "regression_logistique" else "HR")
                    marqueur = "est" if sig else "n'est PAS"
                    inferences.append(
                        f"- Au seuil 5 %, l'exposition {marqueur} "
                        "statistiquement associée à l'issue APRES AJUSTEMENT "
                        f"sur les covariables pré-déclarées ({mesure} ajusté "
                        "et son IC95 % en section FAITS) — mesure d'intérêt "
                        "verrouillée au SAP ; la mesure brute (univariée) "
                        "reste rapportée à titre descriptif.")
                    conclusion_directionnelle = (
                        "association_ajustee_significative" if sig else
                        "association_ajustee_non_significative")
                else:
                    sig = (a1.get("p_valeur") or 1.0) < 0.05
                    grandeur = GRANDEUR_ASSOCIATION[op1]
                    marqueur = "est" if sig else "n'est PAS"
                    inferences.append(
                        f"- Au seuil 5 %, l'exposition {marqueur} "
                        f"statistiquement associée à l'issue ({grandeur} et "
                        "son IC95 % en section FAITS) ; la lecture est "
                        "strictement relationnelle et l'ampleur plausible est "
                        "bornée par l'intervalle.")
                    conclusion_directionnelle = (
                        "association_significative" if sig
                        else "association_non_significative")
                if not sig:
                    inferences.append(
                        "- Absence de significativité ≠ absence d'association : "
                        "l'IC95 % peut rester compatible avec des ampleurs "
                        "cliniquement importantes (puissance).")
            else:
                inferences.append(
                    "- Confiance de conclusion insuffisante (CC < 0,40) : "
                    "aucune conclusion d'association n'est autorisée.")
            inferences.append(
                "- Association ≠ causalité : ce design observationnel "
                "n'autorise aucune lecture causale (confusion non mesurée, "
                "biais de sélection et d'information non exclus).")
            if a3_res is not None and (a3_res.get("resultat") or {}).get(
                    "interpretable"):
                a3_sap = next((a for a in sap.get("analyses", [])
                               if a.get("role") == "ajustement"), {})
                cov_aj = list(a3_sap.get("covariables") or [])
                inferences.append(
                    "- L'association est rapportée AJUSTÉE sur les covariables "
                    f"pré-déclarées au SAP ({', '.join(cov_aj) or 'n/d'}) — "
                    "voir FAITS ; cette mesure multivariée reste une "
                    "association : la confusion non mesurée et les hypothèses "
                    "du modèle (forme fonctionnelle, risques proportionnels "
                    "le cas échéant) restent à la charge du statisticien (G3).")
            elif a3_res is not None:
                inferences.append(
                    "- L'ajustement multivarié pré-déclaré (A3) n'était PAS "
                    "interprétable (voir FAITS) : la seule mesure disponible "
                    "est l'association univariée, exploratoire par nature — "
                    "revue biostatistique exigée avant toute poursuite.")
            else:
                inferences.append(
                    "- Analyse non ajustée (univariée) : l'ampleur rapportée "
                    "reste exploratoire ; tout ajustement multivarié "
                    "(régression logistique, Cox…) relève d'une décision "
                    "biostatistique pré-spécifiée au SAP (G3), jamais pilotée "
                    "par les résultats.")
        if a1.get("test") == "tost_equivalence" and a1.get("interpretable"):
            ok_eq = a1.get("verdict") == "equivalence_demontree"
            inferences.append(
                f"- Lecture TOST : l'équivalence des moyennes est "
                f"{'démontrée' if ok_eq else 'NON démontrée'} au sens de la marge "
                f"Δ pré-définie ; seul le rejet conjoint des deux tests "
                f"unilatéraux autorise cette formulation.")
            conclusion_directionnelle = "tost_" + ("equivalence" if ok_eq
                                                   else "non_equivalence")
        elif (a1.get("interpretable") and cc >= 0.4
                and op1 not in OPS_ASSOCIATION):
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
        elif a1.get("interpretable") and op1 not in OPS_ASSOCIATION:
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
        a1 = res.get("A1", {}).get("resultat", {})
        if conf.get("verdict_conformite") == "NON_CONFORME_BLOQUANT":
            decision = "Ne pas poursuivre en l'état — non-conformité bloquante."
        elif any(s.get("grade", 0) >= 2 for s in safety.get("signaux", [])):
            decision = "Suspendre la conclusion — signaux de sécurité à trancher (G4)."
        elif cc < 0.4:
            decision = ("Données insuffisantes pour conclure — tests/compléments "
                        "requis avant réévaluation.")
        elif a1.get("test") == "tost_equivalence" and \
                a1.get("verdict") == "equivalence_demontree":
            decision = ("Équivalence démontrée selon la marge pré-définie — "
                        "soumis à validation humaine G6.")
        elif op1 in OPS_ASSOCIATION and a1.get("interpretable"):
            sig = (a1.get("p_valeur") or 1.0) < 0.05
            decision = ("Association statistiquement "
                        f"{'significative' if sig else 'non significative'} "
                        "rapportée — lecture strictement relationnelle, sans "
                        "portée causale ni décision automatique — soumise à "
                        "validation humaine G6.")
        else:
            decision = ("Avis favorable prudent proposé — soumis à validation "
                        "humaine G6.")

        limites = [f"[données] {a}" for a in e.get("dq_assumptions", [])] \
            + [f"[analyse] {a}" for a in e.get("analyse_assumptions", [])] \
            + [f"[observationnel:{k}] {v}" for k, v in
               (e["sap"].get("garde_fous_observationnel") or {}).items()] \
            + ([f"[ajustement] hypothèses du modèle {a3_res['op_retenue']} NON "
                "testées (linéarité du logit / risques proportionnels — "
                "diagnostics hors MVP, déclarées et assumées à G3) ; analyse "
                "en cas complets (voir stratégie manquants du SAP)"]
               if a3_res is not None
               and (a3_res.get("resultat") or {}).get("interpretable")
               else []) \
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
