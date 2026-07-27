"""Agents d'analyse : EDA · biais · hypothèses · manquants · anomalies · inférentiel.

Règles d'orche (cf. registry.yaml) implémentées ici :
- EDA : aucune op inférentielle (n'importe QUE descriptif/proportions) ;
- hypothèses : fallback uniquement dans l'arbre pré-spécifié du SAP ;
- inférentiel : 1:1 entre opérations exécutées et SAP (via verdicts d'hypothèses),
  double exécution contrôlée, entrées embarquées dans l'artefact pour recalcul
  indépendant par la Relecture.
"""
from __future__ import annotations

from agents_impl.base import (Contexte, depot, numeriques, sha256_obj, sortie,
                              valeurs)
from core.exceptions import ErreurLogique
from core.state import Etat
from stats_catalogue.controller import ControleurExecution
from stats_catalogue import ops as catalogue


# ------------------------------------------------------------------ EDA

def fabriquer_eda(ctx: Contexte):
    ctx.producteur = "agent.eda"

    def agent(etat: Etat, entrees: dict) -> dict:
        rows, spec = entrees["datasets"]["rows"], entrees["spec"]
        ctrl = ControleurExecution(etat.seed)
        profil = {}
        for var, vspec in spec["variables"].items():
            if vspec.get("type") == "continue":
                profil[var] = ctrl.executer(
                    catalogue.descriptif_continu, "descriptif_continu", "1.0.0",
                    valeurs=numeriques(rows, var))
            else:
                vals = [r.get(var) for r in rows if r.get(var) not in (None, "")]
                comptes = {}
                for v in vals:
                    comptes[str(v)] = comptes.get(str(v), 0) + 1
                profil[var] = {"n": len(vals), "modalites": comptes}
        rapport = {"profils": profil, "n": len(rows),
                   "garantie": "strictement descriptif — aucune inférence"}
        art = depot(ctx, etat.study_id, "eda", "eda_report", rapport)
        return sortie(confidence=0.95, artefacts=[art], eda_ref=art.ref)
    return agent


# ------------------------------------------------------------------ biais

def fabriquer_biais(ctx: Contexte):
    ctx.producteur = "agent.biais"

    def agent(etat: Etat, entrees: dict) -> dict:
        rows, spec = entrees["datasets"]["rows"], entrees["spec"]
        groupe = spec.get("variable_groupe", "groupe")
        g1, g2 = spec.get("contraste", ["produit", "controle"])
        r1 = [r for r in rows if r.get(groupe) == g1]
        r2 = [r for r in rows if r.get(groupe) == g2]
        ctrl = ControleurExecution(etat.seed)
        mesures, graves = [], []
        for cov in spec.get("covariables_baseline", []):
            res = ctrl.executer(catalogue.smd_groupes, "smd_groupes", "1.0.0",
                                g1=numeriques(r1, cov), g2=numeriques(r2, cov))
            mesures.append({"covariable": cov, **res})
            if res.get("desequilibre") == "fort":
                graves.append(cov)
        # attrition différentielle sur le critère principal
        ep = spec["endpoint_principal"]
        t1 = 1 - len(numeriques(r1, ep)) / max(1, len(r1))
        t2 = 1 - len(numeriques(r2, ep)) / max(1, len(r2))
        if abs(t1 - t2) >= 0.05:
            graves.append(f"attrition_diff>{abs(t1 - t2):.1%}")
            mesures.append({"covariable": f"manquants_{ep}",
                            "taux_g1": t1, "taux_g2": t2,
                            "desequilibre": "potentiel"})
        rapport = {"mesures": mesures, "biais_critiques": graves,
                   "checklist": ["selection", "mesure", "reporting", "attrition"]}
        art = depot(ctx, etat.study_id, "biais", "bias_report", rapport)
        return sortie(confidence=0.85, artefacts=[art],
                      contradictions=[f"déséquilibre baseline fort : {graves}"]
                      if graves else [],
                      biais_ref=art.ref, biais_critiques=graves)
    return agent


# ------------------------------------------------------------------ hypothèses

def fabriquer_hypotheses(ctx: Contexte):
    ctx.producteur = "agent.hypotheses"

    def agent(etat: Etat, entrees: dict) -> dict:
        sap, spec = entrees["sap"], entrees["spec"]
        rows = entrees["datasets"]["rows"]
        groupe = spec.get("variable_groupe", "groupe")
        ctrl = ControleurExecution(etat.seed)
        verdicts = {}
        for ana in sap["analyses"]:
            op = ana["op"]
            if op != "t_test_welch":
                verdicts[ana["id"]] = {"op_prevue": op, "op_retenue": op,
                                       "motif": "pas de précondition testée"}
                continue
            g1, g2 = ana.get("contraste", ["produit", "controle"])
            xs1 = numeriques([r for r in rows if r.get(groupe) == g1], ana["var"])
            xs2 = numeriques([r for r in rows if r.get(groupe) == g2], ana["var"])
            n1 = ctrl.executer(catalogue.test_normalite, "test_normalite", "1.0.0",
                               valeurs=xs1)
            n2 = ctrl.executer(catalogue.test_normalite, "test_normalite", "1.0.0",
                               valeurs=xs2)
            non_normal = any(v.get("verdict") == "non_normal" for v in (n1, n2))
            if non_normal:
                fb = ana.get("fallback", {})
                if fb.get("si") != "non_normal" or "op" not in fb:
                    raise ErreurLogique(   # → déviation SAP → G3 (bloquant)
                        f"{ana['id']} : hypothèse violée sans fallback pré-spécifié")
                verdicts[ana["id"]] = {
                    "op_prevue": op, "op_retenue": fb["op"],
                    "motif": "normalité rejetée (Anderson-Darling)",
                    "detail": {"g1": n1.get("verdict"), "g2": n2.get("verdict"),
                               "p": [n1.get("p_valeur"), n2.get("p_valeur")]}}
            else:
                verdicts[ana["id"]] = {
                    "op_prevue": op, "op_retenue": op,
                    "motif": "préconditions satisfaites",
                    "detail": {"g1": n1.get("verdict"), "g2": n2.get("verdict"),
                               "p": [n1.get("p_valeur"), n2.get("p_valeur")]}}
        art = depot(ctx, etat.study_id, "assumptions", "assumptions", verdicts,
                    utilisant=[entrees.get("sap_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=["fallbacks pris uniquement dans l'arbre du SAP"],
                      verdicts=verdicts, assumptions_ref=art.ref)
    return agent


# ------------------------------------------------------------------ manquants

def fabriquer_manquants(ctx: Contexte):
    ctx.producteur = "agent.manquants"

    def agent(etat: Etat, entrees: dict) -> dict:
        sap, spec = entrees["sap"], entrees["spec"]
        rows = entrees["datasets"]["rows"]
        ep = spec["endpoint_principal"]
        n = len(rows)
        taux_ep = 1 - len(numeriques(rows, ep)) / max(1, n)
        if taux_ep > 0.40:
            raise ErreurLogique(
                f">{40:.0%} de manquants sur l'endpoint — escalade data management")
        if taux_ep > 0.05:
            raise ErreurLogique(   # stratégie SAP inapplicable en MVP → escalade
                f"taux manquants {taux_ep:.1%} > 5 % : imputation multiple requise"
                " (hors MVP) → escalade biostatisticien")
        rapport = {"endpoint": ep, "taux_manquants_endpoint": round(taux_ep, 4),
                   "strategie_gabarit": sap["gestion_manquants"]["strategie"],
                   "strategie_appliquee": "cas complets (taux ≤ 5 %)",
                   "mecanisme": "non testé (taux négligeable)",
                   "sensibilite_mnar": "non requise par le SAP à ce taux"}
        art = depot(ctx, etat.study_id, "missingness", "missingness", rapport,
                    utilisant=[entrees.get("sap_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=["cas complets : taux ≤ 5 % conforme au SAP"],
                      manquants_ref=art.ref)
    return agent


# ------------------------------------------------------------------ anomalies

def fabriquer_anomalies(ctx: Contexte):
    ctx.producteur = "agent.anomalies"

    def agent(etat: Etat, entrees: dict) -> dict:
        dq = entrees["dq"]
        det = [a for a in dq["anomalies"] if a["regle"] == "outlier_critique"]
        doublons_serie = 0
        vus = set()
        for r in entrees["datasets"]["rows"]:
            cle = sha256_obj(r)[:16]
            doublons_serie += 1 if cle in vus else 0
            vus.add(cle)
        rapport = {
            "outliers_critiques": det, "nb": len(det),
            "doublons_lignes_exactes": doublons_serie,
            "règle": "détection seule — aucune exclusion sans règle pré-spécifiée",
            "statut": "expliquees" if len(det) == 0 else "a_examiner",
        }
        art = depot(ctx, etat.study_id, "anomalies", "anomalies", rapport,
                    utilisant=[entrees.get("dq_ref")])
        suspect = doublons_serie / max(1, len(entrees["datasets"]["rows"])) > 0.02
        return sortie(confidence=0.9, artefacts=[art],
                      contradictions=["suspicion de duplication de séries"]
                      if suspect else [],
                      suspicion_manipulation=suspect, anomalies_ref=art.ref)
    return agent


# ------------------------------------------------------------------ inférentiel

def fabriquer_inferentiel(ctx: Contexte):
    ctx.producteur = "agent.inferentiel"

    def agent(etat: Etat, entrees: dict) -> dict:
        sap, spec = entrees["sap"], entrees["spec"]
        verdicts = entrees["verdicts_hypotheses"]
        rows = entrees["datasets"]["rows"]
        groupe = spec.get("variable_groupe", "groupe")
        ctrl = ControleurExecution(etat.seed)
        resultats = {}
        for ana in sap["analyses"]:
            op = verdicts.get(ana["id"], {}).get("op_retenue", ana["op"])
            if op not in catalogue.OPS:
                raise ErreurLogique(f"{ana['id']} : op {op!r} hors catalogue")
            entrees_op: dict = {}
            if op == "t_test_welch" or op == "mann_whitney":
                g1, g2 = ana.get("contraste", ["produit", "controle"])
                entrees_op = {
                    "g1": numeriques([r for r in rows if r.get(groupe) == g1],
                                     ana["var"]),
                    "g2": numeriques([r for r in rows if r.get(groupe) == g2],
                                     ana["var"])}
            elif op == "descriptif_continu":
                entrees_op = {"valeurs": numeriques(rows, ana["var"])}
            elif op == "proportion_exacte":
                seuil = spec.get("seuil_grade_reaction", 2)
                var, par = ana["var"], ana.get("par", groupe)
                sous = {}
                for g in sorted({r.get(par) for r in rows}):
                    xs = [r for r in rows if r.get(par) == g]
                    succes = sum(1 for r in xs
                                 if isinstance(r.get(var), (int, float))
                                 and r[var] >= seuil)
                    sous[g] = ctrl.executer(
                        catalogue.proportion_exacte, "proportion_exacte", "1.0.0",
                        succes=succes, total=len(xs))
                resultats[ana["id"]] = {
                    "op_retenue": op, "version": catalogue.OPS[op]["version"],
                    "role": ana["role"], "par_groupe": sous,
                    "entrees": {"definition": ana["definition"]}}
                continue
            res = ctrl.executer(catalogue.OPS[op]["fn"], op,
                                catalogue.OPS[op]["version"], **entrees_op)
            resultats[ana["id"]] = {
                "op_retenue": op, "version": catalogue.OPS[op]["version"],
                "role": ana["role"], "resultat": res, "entrees": entrees_op}
        art = depot(ctx, etat.study_id, "results", "results_inferential",
                    {"resultats": resultats, "journal_execution": ctrl.journal,
                     "double_execution": "hashes concordants"},
                    utilisant=[entrees.get("sap_ref"),
                               entrees.get("assumptions_ref")])
        return sortie(confidence=0.92, artefacts=[art],
                      assumptions=["1:1 SAP↔opérations vérifié",
                                   "double exécution concordante"],
                      resultats=resultats, results_ref=art.ref)
    return agent
