"""Agents garde-fous : Sécurité/Tolérance · Conformité réglementaire · Relecture critique."""
from __future__ import annotations

import math

from agents_impl.base import Contexte, depot, sortie
from core.exceptions import ErreurLogique
from core.state import Etat
from stats_catalogue import ops as catalogue
from stats_catalogue.controller import ControleurExecution

SEUIL_SIGNAL_GRADE = 2
MOS_MIN = 100.0


# ------------------------------------------------------------------ sécurité / tolérance

def fabriquer_securite(ctx: Contexte):
    ctx.producteur = "agent.securite"

    def agent(etat: Etat, entrees: dict) -> dict:
        rows, spec = entrees["datasets"]["rows"], entrees["spec"]
        var = spec.get("var_reaction", "reaction_grade")
        seuil_g = spec.get("seuil_grade_reaction", 2)
        seuil_pct = spec.get("seuil_reactions_pct", 5.0) / 100.0
        groupe = spec.get("variable_groupe", "groupe")
        ctrl = ControleurExecution(etat.seed)

        par_groupe, signaux = {}, []
        grade_max = 0
        for g in sorted({r.get(groupe) for r in rows}):
            xs = [r for r in rows if r.get(groupe) == g]
            grades = [r[var] for r in xs
                      if isinstance(r.get(var), (int, float))]
            grade_max = max(grade_max, max(grades, default=0))
            n_sig = sum(1 for x in grades if x >= seuil_g)
            res = ctrl.executer(catalogue.proportion_exacte,
                                "proportion_exacte", "1.0.0",
                                succes=n_sig, total=len(xs))
            par_groupe[g] = {**res, "grade_max": max(grades, default=0)}
            if res["proportion"] > seuil_pct:
                signaux.append({
                    "nature": f"taux de réactions grade≥{seuil_g} > {seuil_pct:.0%}",
                    "groupe": g, "grade": 2, "mesure": res["proportion"],
                    "ic95": res.get("ic95")})
        if grade_max >= 3:
            signaux.append({"nature": "réaction individuelle grade 3+",
                            "grade": 3})

        # MoS par ingrédient (volet cosmétique)
        mos_table, mos_min, incertitudes = [], None, []
        for ing in entrees.get("ingredients", []):
            r = ctrl.executer(catalogue.mos_cosmetique, "mos_cosmetique", "1.0.0",
                              noael_mg_kg_j=ing.get("noael_mg_kg_j"),
                              sed_mg_kg_j=ing.get("sed_mg_kg_j", 0.0))
            mos_table.append({"inci": ing["inci"], **r})
            if r.get("zone_incertitude"):
                incertitudes.append(ing["inci"])
            elif r.get("interpretable"):
                mos_min = r["mos"] if mos_min is None else min(mos_min, r["mos"])
                if r["verdict"] == "SIGNAL":
                    signaux.append({"nature": f"MoS < {MOS_MIN:.0f}",
                                    "inci": ing["inci"], "mos": r["mos"],
                                    "grade": 3})

        rapport = {"par_groupe": par_groupe, "grade_max_observe": grade_max,
                   "table_mos": mos_table,
                   "mos_min": mos_min, "zones_incertitude": incertitudes,
                   "signaux": signaux}
        art = depot(ctx, etat.study_id, "safety", "safety_report", rapport,
                    utilisant=[entrees.get("results_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=["seuils produit issus de la spec versionnée",
                                   "IC safety = Clopper-Pearson (exact)"],
                      contradictions=[f"MoS incertaine : {incertitudes}"]
                      if incertitudes else [],
                      signaux=signaux, mos_min=mos_min, grade_max=grade_max,
                      safety_ref=art.ref)
    return agent


# ------------------------------------------------------------------ conformité réglementaire

# ⚠ ÉCHANTILLON PÉDAGOGIQUE — remplacer en production par le référentiel
# structuré versionné (annexes II–VI consolidées). Toute règle cite sa source.
ANNEXE_II_ECHANTILLON = {"chloroform", "hydroquinone", "mercury", "arsenic"}
RESTREINTES_ECHANTILLON = {
    "formaldehyde": {"annexe": "V", "limite_pct": 0.2,
                     "role": "conservateur"},
}


def fabriquer_conformite(ctx: Contexte):
    ctx.producteur = "agent.conformite"

    def agent(etat: Etat, entrees: dict) -> dict:
        composition = entrees.get("composition", [])   # [{inci, concentration_pct}]
        methodes = [m.lower() for m in entrees.get("methodes_test", [])]
        spec, dq = entrees["spec"], entrees["dq"]
        regles: list[dict] = []
        bloquantes, incertaines = [], []

        def add(regle, statut, preuve, reference):
            regles.append({"regle": regle, "statut": statut,
                           "preuve": preuve, "reference": reference})
            if statut == "KO":
                bloquantes.append(regle)
            elif statut == "INCERTAIN":
                incertaines.append(regle)

        # R-REG-01 : annexe II (substances interdites)
        interdites = [c["inci"] for c in composition
                      if c["inci"].strip().lower() in ANNEXE_II_ECHANTILLON]
        add("R-REG-01-annexe-II",
            "KO" if interdites else "OK",
            f"substances interdites détectées : {interdites}" if interdites
            else f"{len(composition)} INCI contrôlés, aucun ∈ annexe II (échantillon)",
            "Règlement (CE) n°1223/2009, annexe II — référentiel pédagogique v1")

        # R-REG-02 : annexes III–VI (restrictions de concentration)
        for c in composition:
            cle = c["inci"].strip().lower()
            if cle in RESTREINTES_ECHANTILLON:
                r = RESTREINTES_ECHANTILLON[cle]
                conc = c.get("concentration_pct", 0.0)
                ok = conc <= r["limite_pct"]
                add(f"R-REG-02-{cle}",
                    "OK" if ok else "KO",
                    f"{cle} {conc}% vs limite {r['limite_pct']}% (annexe {r['annexe']})",
                    f"Règlement (CE) n°1223/2009, annexe {r['annexe']} — échantillon v1")

        # R-MET-01 : interdiction expérimentation animale → méthodes alternatives
        animales = [m for m in methodes if "animal" in m]
        add("R-MET-01-methodes-alternatives",
            "KO" if animales else "OK",
            f"méthodes animales détectées : {animales}" if animales
            else "méthodes déclarées compatibles alternatives validées",
            "Règlement (CE) n°1223/2009, art. 18 — priorité aux méthodes alternatives")

        # R-DON-01 : données minimales pour un test d'usage (verdict 'insuffisant')
        if etat.type_etude == "test_usage_controle":
            manques = []
            if dq["n_lignes"] < 30:
                manques.append(f"n={dq['n_lignes']} < 30 sujets")
            if spec.get("var_reaction", "reaction_grade") not in spec["variables"]:
                manques.append("données de tolérance (grades) absentes")
            if not composition:
                manques.append("composition INCI absente")
            add("R-DON-01-donnees-minimales",
                "KO" if manques else "OK",
                "; ".join(manques) if manques
                else f"n={dq['n_lignes']} ≥ 30, tolérance et INCI présents",
                "SCCS Notes of Guidance — évaluation de sécurité avant mise sur le marché")

        verdict = ("NON_CONFORME_BLOQUANT" if bloquantes else
                   "CONFORME_SOUS_RESERVE" if incertaines else
                   "CONFORME" if regles else "INSUFFISANT")
        rapport = {"verdict": verdict, "regles": regles,
                   "revues_expertes_requises":
                       (["expert_reglementaire"] if bloquantes or incertaines else [])
                       + (["toxicologue"] if any("MoS" in b for b in bloquantes) else []),
                   "avertissement": "référentiel échantillon — la conformité réelle "
                                    "exige le référentiel consolidé et la revue humaine"}
        art = depot(ctx, etat.study_id, "compliance", "compliance_report", rapport,
                    utilisant=[entrees.get("dq_ref")])
        return sortie(confidence=0.85, artefacts=[art],
                      assumptions=["référentiel pédagogique v1 — échantillon",
                                   "toute règle KO/KO-bloquante est escaladée"],
                      contradictions=[] if not incertaines else
                      [f"règles incertaines : {incertaines}"],
                      verdict_conformite=verdict,
                      regles_ko=bloquantes, regles_incertaines=incertaines,
                      compliance_ref=art.ref)
    return agent


# ------------------------------------------------------------------ relecture critique

TOURNURES_CAUSALES_INTERDITES = {
    "observationnel": ["réduit", "prévient", "guérit", "soigne", "élimine",
                       "provoque", "entraîne une amélioration"],
}
TOLERANCE_NUM = 1e-9


def verbaliser_cc(cc: float | None) -> str | None:
    if cc is None:
        return None
    return ("elevee" if cc >= 0.8 else "moderee" if cc >= 0.6 else
            "faible" if cc >= 0.4 else "insuffisante")


def fabriquer_relecture(ctx: Contexte):
    ctx.producteur = "agent.relecture"

    def agent(etat: Etat, entrees: dict) -> dict:
        rapport = entrees["rapport"]
        resultats = entrees["resultats"]["resultats"]
        scores = entrees["scores"]
        objections, n = [], 0

        def objection(type_, extrait, preuve, bloquante=True):
            nonlocal n
            n += 1
            objections.append({"id": f"OBJ-{n:02d}", "type": type_,
                               "extrait": extrait, "preuve": preuve,
                               "bloquante": bloquante})

        # 1) recalcul indépendant de chaque chiffre cité ----------------------
        ctrl = ControleurExecution(etat.seed)
        for aid, ana in resultats.items():
            op = ana["op_retenue"]
            ent = ana.get("entrees", {})
            if "par_groupe" in ana:                       # proportion_exacte ×k
                for g, res in ana["par_groupe"].items():
                    recalc = ctrl.executer(
                        catalogue.proportion_exacte, "proportion_exacte", "1.0.0",
                        succes=res["succes"], total=res["total"])
                    if abs(recalc["proportion"] - res["proportion"]) > TOLERANCE_NUM:
                        objection("chiffre_non_reproduit", f"{aid}/{g}",
                                  f"{res['proportion']} ≠ recalcul "
                                  f"{recalc['proportion']}")
                continue
            attendu = ana.get("resultat", {})
            appel = {k: v for k, v in ent.items() if k in ("g1", "g2", "valeurs")}
            if not appel:
                objection("entrees_absentes_du_recalcul", aid,
                          "l'artefact ne permet pas le recalcul indépendant")
                continue
            recalc = ctrl.executer(catalogue.OPS[op]["fn"], op,
                                   catalogue.OPS[op]["version"], **appel)
            for cle, v in attendu.items():
                if isinstance(v, (int, float)) and isinstance(recalc.get(cle), (int, float)):
                    if not (math.isclose(v, recalc[cle], abs_tol=TOLERANCE_NUM)
                            or (math.isnan(v) and math.isnan(recalc[cle]))):
                        objection("chiffre_non_reproduit", f"{aid}.{cle}",
                                  f"rapporté {v} ≠ recalculé {recalc[cle]}")

        # 2) section FAITS : chaque ligne chiffrée cite son artefact+hash -------
        for ligne in rapport.get("lignes_faits", []):
            if any(c.isdigit() for c in ligne) and "art://" not in ligne:
                objection("fait_sans_source", ligne[:80],
                          "aucune référence art:// sur une ligne chiffrée")

        # 3) lexique contrôlé : association ≠ causalité -------------------------
        interdites = TOURNURES_CAUSALES_INTERDITES.get(
            "observationnel" if "randomise" not in etat.type_etude else "", [])
        inf = rapport.get("texte_inferences", "").lower()
        for t in interdites:
            if t in inf:
                objection("tournure_causale_non_autorisee", t,
                          f"design {etat.type_etude} : lexique causal interdit")

        # 4) cohérence score CC / verbalisation / force de conclusion ----------
        if scores.get("verbalisation_cc") != verbaliser_cc(scores.get("confiance_conclusion")):
            objection("verbalisation_cc_incoherente",
                      str(scores.get("verbalisation_cc")),
                      f"attendu {verbaliser_cc(scores.get('confiance_conclusion'))}")
        cc = scores.get("confiance_conclusion")
        if (cc is not None and cc < 0.4
                and rapport.get("conclusion_directionnelle")):
            objection("conclusion_trop_forte", rapport["conclusion_directionnelle"],
                      f"CC={cc:.2f} < 0,40 : le gabarit interdit de conclure")

        # 5) limites et hypothèses ----------------------------------------------
        if not rapport.get("limites"):
            objection("limites_absentes", "", "section limites vide — obligatoire")

        bloquantes = [o for o in objections if o["bloquante"]]
        art = depot(ctx, etat.study_id, "critique", "critique",
                    {"objections": objections,
                     "nb_bloquantes": len(bloquantes),
                     "recalculs": ctrl.journal,
                     "verdict": "OK" if not bloquantes else "OBJECTIONS"},
                    utilisant=[entrees.get("report_ref"),
                               entrees.get("results_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      objections=objections,
                      objections_bloquantes=bloquantes,
                      verdict="OK" if not bloquantes else "OBJECTIONS",
                      critique_ref=art.ref)
    return agent
