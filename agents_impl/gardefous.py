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
        if var not in spec.get("variables", {}):
            rapport = {"non_applicable": True,
                       "motif": "aucune variable de tolérance dans la spec — "
                                "type d'étude sans collecte de réactions",
                       "signaux": [], "mos_min": None, "grade_max_observe": 0,
                       "table_mos": [], "zones_incertitude": []}
            art = depot(ctx, etat.study_id, "safety", "safety_report", rapport)
            return sortie(confidence=0.95, artefacts=[art],
                          assumptions=["volet tolérance non applicable à ce type "
                                       "d'étude"], signaux=[], mos_min=None,
                          grade_max=0, safety_ref=art.ref)
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

def fabriquer_conformite(ctx: Contexte):
    """Évalue le dossier via le moteur de règles v2 (reglementaire/) :
    contexte produit (leave_on/rinse_off, usage, zone, population), dérogations,
    agrégats, couverture INCI. Chaque règle cite référence + version/hash du corpus."""
    ctx.producteur = "agent.conformite"

    def agent(etat: Etat, entrees: dict) -> dict:
        from reglementaire.moteur_regles import evaluer_dossier
        from reglementaire.referentiel import charger_referentiel
        ref = charger_referentiel()
        spec, dq = entrees["spec"], entrees["dq"]
        dossier = {
            "type_etude": etat.type_etude,
            "domaine": etat.domaine,
            "produit": entrees.get("produit", {}),
            "composition": entrees.get("composition", []),
            "methodes_test": entrees.get("methodes_test", []),
            "claims": entrees.get("claims", []),
            "etiquetage": entrees.get("etiquetage"),
            "n_lignes": dq["n_lignes"],
            "variables": list(spec.get("variables", {})),
            "var_reaction": spec.get("var_reaction", "reaction_grade"),
            "var_temps": spec.get("var_temps"),
            "bornes_acceptation": spec.get("bornes_acceptation", {}),
            "points_temps": spec.get("points_temps", []),
            "var_exposition": spec.get("var_exposition"),
            "var_issue": spec.get("var_issue"),
            "var_evenement": spec.get("var_evenement"),
            "var_temps_event": spec.get("var_temps_event"),
            "var_paire": spec.get("var_paire"),
            "appariement": spec.get("appariement"),
            "resultats": entrees.get("resultats"),
        }
        res = evaluer_dossier(ref, dossier)

        rapport = {**res,
                   "revues_expertes_requises":
                       (["expert_reglementaire"]
                        if res["regles_ko"] or res["regles_incertaines"] else []),
                   "avertissement": ("corpus de démonstration élargi v2 — la conformité "
                                     "réelle exige la base consolidée officielle et la "
                                     "revue d'un expert réglementaire")}
        art = depot(ctx, etat.study_id, "compliance", "compliance_report", rapport,
                    utilisant=[entrees.get("dq_ref")])
        return sortie(confidence=0.85, artefacts=[art],
                      assumptions=[f"corpus {res['corpus']['version']} "
                                   f"sha256:{res['corpus']['sha256'][:12]}…",
                                   "toute règle KO ou INCERTAIN est escaladée",
                                   f"couverture INCI {res['couverture_inci']['resolues']}"
                                   f"/{res['couverture_inci']['total']}"],
                      contradictions=[] if not res["regles_incertaines"] else
                      [f"règles incertaines : {res['regles_incertaines']}"],
                      verdict_conformite=res["verdict"],
                      regles_ko=res["regles_ko"],
                      regles_incertaines=res["regles_incertaines"],
                      couverture_inci=res["couverture_inci"],
                      compliance_ref=art.ref)
    return agent


# ------------------------------------------------------------------ relecture critique

TOURNURES_CAUSALES_INTERDITES = {
    "observationnel": ["réduit", "prévient", "guérit", "soigne", "élimine",
                       "provoque", "entraîne une amélioration",
                       "réduit le risque", "diminue le risque",
                       "augmente le risque", "cause",
                       "effet protecteur", "effet curatif",
                       "dû à l'exposition", "grâce à l'exposition"],
}
TYPES_ASSOCIATION = ("cas_temoins", "cohorte", "observationnelle")
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
            appel = {k: v for k, v in ent.items()
                     if k in ("g1", "g2", "valeurs", "temps", "marge",
                              "a", "b", "c", "d", "paires_b", "paires_c",
                              "temps1", "evenements1", "temps2", "evenements2",
                              # ops multivariées (A3 ajustement)
                              "y", "x", "evenements", "epv_min")}
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
        # 3b) conclusion bornée au lexique d'association (types observationnels)
        concl = rapport.get("conclusion_directionnelle")
        if (concl and "randomise" not in etat.type_etude
                and any(t in etat.type_etude for t in TYPES_ASSOCIATION)
                and not concl.startswith(("association", "tost_"))):
            objection("conclusion_hors_lexique_observationnel", concl,
                      "les designs observationnels ne concluent qu'en termes "
                      "d'association (labels 'association_*')")

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
