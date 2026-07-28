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
        if not r1 or not r2:
            art = depot(ctx, etat.study_id, "biais", "bias_report",
                        {"non_applicable": True,
                         "motif": "pas de variable de groupe — design sans "
                                  "comparaison de bras (stabilité, série)",
                         "checklist": ["selection", "mesure", "reporting"]})
            return sortie(confidence=0.9, artefacts=[art],
                          assumptions=["analyse de biais inter-bras non "
                                       "applicable"], biais_critiques=[],
                          biais_ref=art.ref)
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
        OPS_NORMALITE = {"t_test_welch", "tost_equivalence"}  # précondition gaussienne
        for ana in sap["analyses"]:
            op = ana["op"]
            if op not in OPS_NORMALITE:
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
        from stats_catalogue import imputation
        sap, spec = entrees["sap"], entrees["spec"]
        rows = entrees["datasets"]["rows"]
        ep = spec["endpoint_principal"]
        n = len(rows)
        cible = [r.get(ep) for r in rows]
        taux_ep = imputation.taux_manquants(cible)
        if taux_ep > 0.40:
            raise ErreurLogique(
                f">{40:.0%} de manquants sur l'endpoint — escalade data management")

        # mécanisme : indépendance(manquant, groupe) — écran MAR grossier
        mecanisme = {"verdict": "non testé (taux ≤ 5 %)"}
        ctrl = ControleurExecution(etat.seed)
        if taux_ep > 0.05:
            groupe = spec.get("variable_groupe")
            if groupe:
                mods = sorted({r.get(groupe) for r in rows})
                if len(mods) == 2:
                    a = sum(1 for r in rows if r.get(groupe) == mods[0]
                            and r.get(ep) in (None, ""))
                    b = sum(1 for r in rows if r.get(groupe) == mods[0]) - a
                    c = sum(1 for r in rows if r.get(groupe) == mods[1]
                            and r.get(ep) in (None, ""))
                    d = sum(1 for r in rows if r.get(groupe) == mods[1]) - c
                    f = ctrl.executer(catalogue.fisher_exact_2x2,
                                      "fisher_exact_2x2", "1.0.0", a=a, b=b,
                                      c=c, d=d)
                    mecanisme = {"test": "fisher manquants × groupe",
                                 "p_valeur": f["p_valeur"],
                                 "verdict": ("taux différentiel suspect (MNAR à "
                                             "documenter)" if f["p_valeur"] < 0.10
                                             else "pas de signal MNAR grossier")}

        datasets_completes, indices, m = None, [], 0
        if taux_ep > 0.05:
            m = max(20, int(round(taux_ep * 100)))     # m ≥ taux×100 (règle usuelle)
            pred_nom = (spec.get("covariables_baseline") or [None])[0]
            pred = [r.get(pred_nom) for r in rows] if pred_nom else None
            colonnes, indices = imputation.imputer_pmm(cible, pred, m, etat.seed)
            datasets_completes = []
            for k, col in enumerate(colonnes):
                copie = [dict(r) for r in rows]
                for i, v in enumerate(col):
                    copie[i][ep] = v
                datasets_completes.append(copie)

        strategie = ("cas complets (taux ≤ 5 %)" if taux_ep <= 0.05 else
                     f"imputation multiple PMM m={m} (prédicteur baseline "
                     "si complet, sinon hot-deck) ; sensibilité MNAR : "
                     "delta-adjustment / tipping point côté inferentiel")
        rapport = {"endpoint": ep, "n": n,
                   "taux_manquants_endpoint": round(taux_ep, 4),
                   "strategie_gabarit": sap["gestion_manquants"]["strategie"],
                   "strategie_appliquee": strategie, "m": m,
                   "indices_imputes": indices, "mecanisme": mecanisme,
                   "sensibilite_mnar": ("delta-adjustment tipping point (δ "
                                        "pénalisant produit)" if m else
                                        "non requise par le SAP à ce taux")}
        art = depot(ctx, etat.study_id, "missingness", "missingness", rapport,
                    utilisant=[entrees.get("sap_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=[strategie,
                                   f"mécanisme : {mecanisme.get('verdict')}",
                                   "PMM : donneurs réels, seed dérivée"],
                      datasets_completes=datasets_completes,
                      indices_imputes=indices, mecanisme=mecanisme,
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

def deduire_seuil_franchissement(moys: list[tuple[float, float]],
                                 lo: float, hi: float) -> tuple[float, str]:
    """Seuil et sens du franchissement d'une série de stabilité — DÉDUCTION
    DÉTERMINISTE (jamais à vue) depuis les moyennes de réplicats par temps
    `moys` triées [(mois, moyenne)] et les bornes d'acceptation [lo, hi] :

    - trajectoire descendante (pente des extrémités < 0) ⇒ borne basse,
      sens « inferieur » ; ascendante ⇒ borne haute, « superieur » ;
    - trajectoire strictement plate ⇒ borne la plus PROCHE du niveau
      final (premier franchissement plausible).
    """
    if len(moys) < 2:
        raise ErreurLogique("≥ 2 points de temps exigés pour déduire le "
                            "sens du franchissement")
    pente = (moys[-1][1] - moys[0][1]) / (moys[-1][0] - moys[0][0])
    if pente < 0:
        return lo, "inferieur"
    if pente > 0:
        return hi, "superieur"
    fin = moys[-1][1]
    return (lo, "inferieur") if (fin - lo) <= (hi - fin) else (hi, "superieur")


def fabriquer_inferentiel(ctx: Contexte):
    ctx.producteur = "agent.inferentiel"

    def agent(etat: Etat, entrees: dict) -> dict:
        sap, spec = entrees["sap"], entrees["spec"]
        verdicts = entrees["verdicts_hypotheses"]
        rows = entrees["datasets"]["rows"]
        groupe = spec.get("variable_groupe", "groupe")
        ctrl = ControleurExecution(etat.seed)
        resultats: dict = {}
        ajustement_refuses: list[str] = []
        sensibilite_refuses: list[str] = []
        datasets = entrees.get("datasets_completes")   # copies PMM (ou None)

        def _deux_groupes(ana, lignes):
            g1, g2 = ana.get("contraste", ["produit", "controle"])
            return (numeriques([r for r in lignes if r.get(groupe) == g1], ana["var"]),
                    numeriques([r for r in lignes if r.get(groupe) == g2], ana["var"]))

        def _modalite(var, souhaitee, defauts):
            """Modalité présente dans les données (déterministe)."""
            vals = sorted({r.get(var) for r in rows if r.get(var) is not None},
                          key=str)
            for c in ([souhaitee] if souhaitee is not None else []) + defauts:
                if c in vals:
                    return c
            raise ErreurLogique(
                f"{var!r} : modalité attendue introuvable parmi {vals!r}")

        def _tab22_association(ana):
            """Cellules 2×2 exposition × issue (cas-témoins non appariée)."""
            expo, issue = ana["var_exposition"], ana["var_issue"]
            cas = _modalite(issue, ana.get("modalite_cas"), ["cas", 1, True])
            tem = _modalite(issue, ana.get("modalite_temoin"),
                            ["temoins", "temoin", 0, False])
            exp = _modalite(expo, ana.get("modalite_expose"),
                            [1, True, "expose", "oui"])
            nxp = _modalite(expo, ana.get("modalite_non_expose"),
                            [0, False, "non_expose", "non"])
            a = sum(1 for r in rows if r.get(issue) == cas
                    and r.get(expo) == exp)
            b = sum(1 for r in rows if r.get(issue) == cas
                    and r.get(expo) == nxp)
            c = sum(1 for r in rows if r.get(issue) == tem
                    and r.get(expo) == exp)
            d = sum(1 for r in rows if r.get(issue) == tem
                    and r.get(expo) == nxp)
            return {"a": a, "b": b, "c": c, "d": d}

        for ana in sap["analyses"]:
            op = verdicts.get(ana["id"], {}).get("op_retenue", ana["op"])
            if op not in catalogue.OPS:
                raise ErreurLogique(f"{ana['id']} : op {op!r} hors catalogue")
            entrees_op: dict = {}
            if op in ("t_test_welch", "mann_whitney"):
                va, vb = _deux_groupes(ana, rows)
                entrees_op = {"g1": va, "g2": vb}
            elif op == "tost_equivalence":
                marge = ana.get("marge") or spec.get("marge_equivalence")
                if not marge:
                    raise ErreurLogique(f"{ana['id']} : marge d'équivalence "
                                        "exigée au SAP (Δ > 0 pré-définie)")
                va, vb = _deux_groupes(ana, rows)
                entrees_op = {"g1": va, "g2": vb, "marge": float(marge)}
            elif op == "tendance_lineaire":
                var_temps = ana.get("par_temps") or spec.get("var_temps", "mois")
                paires = sorted(
                    (float(r[var_temps]), float(r[ana["var"]])) for r in rows
                    if r.get(var_temps) is not None and r.get(ana["var"]) is not None)
                entrees_op = {"temps": [t for t, _ in paires],
                              "valeurs": [v for _, v in paires],
                              "var": ana["var"]}
            elif op == "descriptif_continu":
                entrees_op = {"valeurs": numeriques(rows, ana["var"])}
            elif op == "proportion_wilson":
                var, par = ana["var"], ana.get("par", groupe)
                mod = ana.get("modalite")
                if mod is None:
                    mod = _modalite(var, None, [1, True, "oui", "expose", "cas"])
                sous = {}
                for g in sorted({r.get(par) for r in rows}, key=str):
                    xs = [r for r in rows if r.get(par) == g
                          and r.get(var) not in (None, "")]
                    succes = sum(1 for r in xs if r[var] == mod)
                    sous[str(g)] = ctrl.executer(
                        catalogue.proportion_wilson, "proportion_wilson", "1.0.0",
                        succes=succes, total=len(xs))
                resultats[ana["id"]] = {
                    "op_retenue": op, "version": catalogue.OPS[op]["version"],
                    "role": ana["role"], "par_groupe": sous,
                    "entrees": {"var": var, "par": par, "modalite": mod}}
                continue
            elif op == "odds_ratio_cas_temoins":
                entrees_op = _tab22_association(ana)
            elif op == "or_apparie":
                expo, issue = ana["var_exposition"], ana["var_issue"]
                paire_v = ana["var_paire"]
                cas = _modalite(issue, ana.get("modalite_cas"), ["cas", 1, True])
                tem = _modalite(issue, ana.get("modalite_temoin"),
                                ["temoins", "temoin", 0, False])
                exp = _modalite(expo, ana.get("modalite_expose"),
                                [1, True, "expose", "oui"])
                nxp = _modalite(expo, ana.get("modalite_non_expose"),
                                [0, False, "non_expose", "non"])
                paires: dict = {}
                for r in rows:
                    if r.get(paire_v) is None:
                        continue
                    if r.get(issue) == cas:
                        paires.setdefault(r[paire_v], {})["cas"] = r.get(expo)
                    elif r.get(issue) == tem:
                        paires.setdefault(r[paire_v], {})["tem"] = r.get(expo)
                n_bc = n_cb = n_concor = n_incomplet = 0
                for m in paires.values():
                    ex_c, ex_t = m.get("cas"), m.get("tem")
                    if ex_c is None or ex_t is None:
                        n_incomplet += 1
                    elif ex_c == exp and ex_t == nxp:
                        n_bc += 1
                    elif ex_c == nxp and ex_t == exp:
                        n_cb += 1
                    else:
                        n_concor += 1
                entrees_op = {"paires_b": n_bc, "paires_c": n_cb}
                meta_apparie = {"paires_appariees": n_bc + n_cb + n_concor,
                                "paires_concordantes": n_concor,
                                "paires_incompletes_exclues": n_incomplet}
            elif op == "risque_relatif_cohorte":
                par = ana.get("par", groupe)
                g1, g2 = ana.get("contraste") or spec.get(
                    "contraste", ["expose", "non_expose"])
                ev = ana["var_evenement"]
                positif = ana.get("modalite_evenement", 1)
                entrees_op = {
                    "a": sum(1 for r in rows if r.get(par) == g1
                             and r.get(ev) == positif),
                    "b": sum(1 for r in rows if r.get(par) == g1
                             and r.get(ev) not in (None, "") and r.get(ev) != positif),
                    "c": sum(1 for r in rows if r.get(par) == g2
                             and r.get(ev) == positif),
                    "d": sum(1 for r in rows if r.get(par) == g2
                             and r.get(ev) not in (None, "") and r.get(ev) != positif)}
            elif op == "km_logrank_hr":
                par = ana.get("par", groupe)
                g1, g2 = ana.get("contraste") or spec.get(
                    "contraste", ["expose", "non_expose"])
                ev = ana["var_evenement"]
                tv = ana.get("var_temps_event") or spec.get("var_temps_event")
                if not tv:
                    raise ErreurLogique(f"{ana['id']} : 'var_temps_event' requis")
                positif = ana.get("modalite_evenement", 1)

                def _serie(g):
                    ts, es = [], []
                    for r in rows:
                        if r.get(par) != g:
                            continue
                        t, e = r.get(tv), r.get(ev)
                        if t is None or e is None:
                            continue
                        ts.append(float(t))
                        es.append(1 if e == positif else 0)
                    return ts, es

                t1, e1 = _serie(g1)
                t2, e2 = _serie(g2)
                entrees_op = {"temps1": t1, "evenements1": e1,
                              "temps2": t2, "evenements2": e2}
            elif op in ("regression_logistique", "cox_ph"):
                # A3 — ajustement multivarié du SAP (verrouillé G3) ; la matrice
                # X est construite de façon DÉTERMINISTE : exposition binaire en
                # 1re position, covariables continues en float, binaires 0/1 via
                # modalités déclarées ; cas complets (cohérent stratégie
                # manquants du SAP) ; k > 2 modalités ⇒ blocage (k−1 indicatrices
                # hors MVP → décision humaine G3).
                expo = ana["var_exposition"]
                exp = _modalite(expo, ana.get("modalite_expose"),
                                [1, True, "expose", "oui"])
                nxp = _modalite(expo, ana.get("modalite_non_expose"),
                                [0, False, "non_expose", "non"])
                covs = list(ana.get("covariables", []))
                requis = {expo, *covs}
                if op == "regression_logistique":
                    y_var = ana["var_issue"]
                    requis.add(y_var)
                else:
                    tv = ana["var_temps_event"]
                    y_var = ana["var_evenement"]
                    requis.update({tv, y_var})
                lignes = [r for r in rows
                          if all(r.get(v) is not None and r.get(v) != ""
                                 for v in requis)]
                if op == "regression_logistique":
                    pos = _modalite(y_var, ana.get("modalite_cas"),
                                    [1, True, "cas", "oui", "evenement"])
                    ent_y = [1 if r[y_var] == pos else 0 for r in lignes]
                else:
                    pos = ana.get("modalite_evenement", 1)
                    ent_y = [1 if r[y_var] == pos else 0 for r in lignes]
                ent_x = {expo: [1.0 if r.get(expo) == exp else 0.0
                                for r in lignes]}
                for cov in covs:
                    vspec = spec.get("variables", {}).get(cov, {})
                    if vspec.get("type") == "continue":
                        ent_x[cov] = [float(r[cov]) for r in lignes]
                    else:
                        mods = sorted({r.get(cov) for r in lignes}, key=str)
                        if len(mods) > 2:
                            raise ErreurLogique(
                                f"{ana['id']} : covariable {cov!r} à "
                                f"{len(mods)} modalités — codage en k−1 "
                                "indicatrices hors MVP → décision humaine G3")
                        ent_x[cov] = [1.0 if r.get(cov) == mods[-1] else 0.0
                                      for r in lignes]
                entrees_op = {"epv_min": float(ana.get("epv_min", 10))}
                if op == "regression_logistique":
                    entrees_op.update({"y": ent_y, "x": ent_x})
                else:
                    entrees_op.update({
                        "temps": [float(r[tv]) for r in lignes],
                        "evenements": ent_y, "x": ent_x})
                entrees_op["n_lignes_completes"] = len(lignes)
            elif op == "tipping_point_mnar_smd":
                # A4 — sensibilité MNAR δ-ajustée sur SMD (pré-déclarée au
                # SAP, G3) : copies PMM ALIGNÉES des 2 bras de la primaire.
                # Sans imputation (taux ≤ seuil MI) ou primaire hors
                # comparaison 2 groupes continus ⇒ sans objet : l'op rend
                # non interprétable ET la contradiction est tracée —
                # jamais de ruban de complaisance.
                g1n, g2n = ana.get("contraste") or spec.get(
                    "contraste", ["produit", "controle"])
                gm = ana.get("groupe_mnar")
                if gm == g1n:
                    groupe_ajuste = "g1"
                elif gm == g2n:
                    groupe_ajuste = "g2"
                else:
                    raise ErreurLogique(
                        f"{ana['id']} : groupe_mnar {gm!r} hors contraste "
                        f"{(g1n, g2n)} — incohérence SAP (le verrou G3 "
                        "n'aurait pas dû passer)")
                a1 = next((a for a in sap["analyses"]
                           if a.get("role") == "primaire"), None)
                ops_mi = {"t_test_welch", "mann_whitney"}
                applicable = (datasets is not None and a1 is not None
                              and a1.get("op") in ops_mi
                              and a1.get("var") == ana["var"])
                cols1, cols2 = [], []
                if datasets:
                    for rows_k in datasets:
                        cols1.append([float(r[ana["var"]]) for r in rows_k
                                      if r.get(groupe) == g1n])
                        cols2.append([float(r[ana["var"]]) for r in rows_k
                                      if r.get(groupe) == g2n])
                entrees_op = {
                    "colonnes_g1": cols1, "colonnes_g2": cols2,
                    "deltas": [float(d) for d in ana.get("deltas", [0.0])],
                    "groupe_ajuste": groupe_ajuste}
                if applicable:
                    op_mnar = catalogue.OPS[op]
                    res = ctrl.executer(op_mnar["fn"], op,
                                        op_mnar["version"], **entrees_op)
                else:
                    res = {"interpretable": False,
                           "test": "tipping_point_mnar_smd",
                           "motif": ("sans objet : imputation multiple non "
                                     "déclenchée (taux ≤ seuil) ou primaire "
                                     "hors comparaison 2 groupes continus — "
                                     "sensibilité MNAR documentée, aucune "
                                     "mesure produite")}
                if not res.get("interpretable"):
                    sensibilite_refuses.append(
                        f"{ana['id']} : sensibilité MNAR non interprétable "
                        f"({res.get('motif')}) — robustesse MNAR non "
                        "établie, à arbitrer en relecture (fail-closed)")
                resultats[ana["id"]] = {
                    "op_retenue": op, "version": catalogue.OPS[op]["version"],
                    "role": ana["role"], "var": ana.get("var"),
                    "groupe_mnar": gm,          # libellé humain (hors calcul)
                    "resultat": res, "entrees": entrees_op}
                continue
            elif op == "tendance_fenetre_glissante":
                # sensibilité « passage au grand mail » (stabilité) — seuil et
                # sens DÉDUITS DE FAÇON DÉTERMINISTE de la spécification si non
                # pré-déclarés dans l'item SAP : borne la plus menacée par la
                # droite des moyennes de réplicats par temps (première
                # franchie) ; jamais choisis à vue, cf. docs/SENSIBILITE.md
                var_temps = ana.get("par_temps") or spec.get("var_temps", "mois")
                pts = [{"mois": float(r[var_temps]),
                        "valeur": float(r[ana["var"]])}
                       for r in rows
                       if r.get(var_temps) is not None
                       and r.get(ana["var"]) is not None]
                seuil, direction = ana.get("spec_limite"), ana.get("direction")
                if seuil is None or direction not in ("inferieur", "superieur"):
                    bornes_v = spec.get("bornes_acceptation", {}).get(ana["var"])
                    if not bornes_v:
                        raise ErreurLogique(
                            f"{ana['id']} : sensibilité stabilité — pré-déclarer "
                            "spec_limite + direction au SAP ou "
                            "bornes_acceptation dans la spec (fail-closed)")
                    lo, hi = float(bornes_v[0]), float(bornes_v[1])
                    par_mois: dict = {}
                    for p in pts:
                        par_mois.setdefault(p["mois"], []).append(p["valeur"])
                    moys = sorted((m, sum(v) / len(v))
                                  for m, v in par_mois.items())
                    try:
                        seuil, direction = deduire_seuil_franchissement(
                            moys, lo, hi)
                    except ErreurLogique as e:
                        raise ErreurLogique(f"{ana['id']} : {e}") from e
                entrees_op = {"points": pts,
                              "fenetre_mois": float(ana.get("fenetre_mois",
                                                            12.0)),
                              "horizon_mois": float(ana.get("horizon_mois",
                                                            6.0)),
                              "spec_limite": float(seuil),
                              "direction": direction}
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
                    "entrees": {"definition": ana.get(
                        "definition", f">= {seuil} grade reaction")}}
                continue
            appel = {k: v for k, v in entrees_op.items()
                     if k not in ("var", "n_lignes_completes")}
            res = ctrl.executer(catalogue.OPS[op]["fn"], op,
                                catalogue.OPS[op]["version"], **appel)
            if op == "tendance_lineaire" and res.get("interpretable"):
                res["var"] = entrees_op["var"]
            if op == "or_apparie" and res.get("interpretable"):
                res.update(meta_apparie)
            if ana["role"] == "ajustement" and not res.get("interpretable"):
                ajustement_refuses.append(
                    f"{ana['id']} : ajustement multivarié non interprétable "
                    f"({res.get('motif')}) — revue statisticien exigée ; "
                    "AUCUNE mesure ajustée ne sera présentée (fail-closed)")
            resultats[ana["id"]] = {
                "op_retenue": op, "version": catalogue.OPS[op]["version"],
                "role": ana["role"], "var": ana.get("var"),
                "resultat": res, "entrees": entrees_op}

        # — sensibilité MI poolée + tipping point MNAR (données imputées aval) —
        # (`datasets` hissé avant la boucle — ruban SMD A4 réutilise les
        # mêmes copies PMM alignées)
        sensibilites: dict = {}
        a1 = next((a for a in sap["analyses"] if a.get("role") == "primaire"), None)
        OPS_SENSIBILITE_MI = {"t_test_welch", "mann_whitney"}
        if (datasets and a1 and a1["var"] == spec["endpoint_principal"]
                and a1.get("op") in OPS_SENSIBILITE_MI):
            g1n, g2n = a1.get("contraste", ["produit", "controle"])
            idx = set(entrees.get("indices_imputes", []))
            estimations, par_jeu = [], []
            for rows_k in datasets:
                va = [float(r[a1["var"]]) for r in rows_k if r.get(groupe) == g1n]
                vb = [float(r[a1["var"]]) for r in rows_k if r.get(groupe) == g2n]
                w = catalogue.t_test_welch(g1=va, g2=vb, seed=etat.seed)
                if w.get("interpretable"):
                    estimations.append({"theta": w["difference"], "se": w["se"]})
                    par_jeu.append(w)
            if estimations:
                pool = ctrl.executer(catalogue.pooling_rubin, "pooling_rubin",
                                     "1.0.0", estimations=estimations)
                tipping, flip = [], None
                for delta in (0.5, 1.0, 2.0, 3.0, 4.0):
                    est_d = []
                    for rows_k in datasets:
                        va = [float(r[a1["var"]]) - delta if (i in idx) else
                              float(r[a1["var"]])
                              for i, r in enumerate(rows_k)
                              if r.get(groupe) == g1n]
                        vb = [float(r[a1["var"]]) for r in rows_k
                              if r.get(groupe) == g2n]
                        w = catalogue.t_test_welch(g1=va, g2=vb, seed=etat.seed)
                        if w.get("interpretable"):
                            est_d.append({"theta": w["difference"], "se": w["se"]})
                    if est_d:
                        pd = catalogue.pooling_rubin(estimations=est_d,
                                                     seed=etat.seed)
                        if pd.get("interpretable"):
                            tipping.append({"delta": delta,
                                            "p_valeur": pd["p_valeur"]})
                            if flip is None and pd["p_valeur"] >= 0.05:
                                flip = delta
                p_prim = resultats.get("A1", {}).get("resultat", {}).get("p_valeur")
                sensibilites["A1_sensibilite_MI"] = {
                    "type": "imputation_multiple_PMM + delta_adjustment",
                    "m": len(estimations), "pooled": pool,
                    "delta_grid_penalise_produit": tipping,
                    "delta_flip": flip,
                    "concordante_primaire": (
                        p_prim is not None and pool.get("interpretable")
                        and (pool["p_valeur"] < 0.05) == (p_prim < 0.05)),
                    "nb_estimations": len(estimations)}
        art = depot(ctx, etat.study_id, "results", "results_inferential",
                    {"resultats": resultats, "sensibilites": sensibilites,
                     "journal_execution": ctrl.journal,
                     "double_execution": "hashes concordants"},
                    utilisant=[entrees.get("sap_ref"),
                               entrees.get("assumptions_ref")])
        assumptions = ["1:1 SAP↔opérations vérifié", "double exécution concordante"]
        if sensibilites:
            assumptions.append("sensibilité MI : PMM poolée Rubin + tipping point δ")
        if any(a.get("role") == "ajustement"
               for a in resultats.values()):
            assumptions.append("ajustement multivarié A3 exécuté exactement "
                               "comme verrouillé au SAP (cas complets)")
        if any(a.get("op_retenue") == "tendance_fenetre_glissante"
               for a in resultats.values()):
            assumptions.append(
                "sensibilité stabilité « passage au grand mail » : seuil et "
                "sens du franchissement déduits de façon DÉTERMINISTE de "
                "bornes_acceptation (borne la plus menacée par la droite des "
                "moyennes de réplicats) quand non pré-déclarés au SAP — "
                "jamais choisis à vue")
        if any(a.get("op_retenue") == "tipping_point_mnar_smd"
               for a in resultats.values()):
            assumptions.append(
                "sensibilité MNAR (ruban δ-ajusté SMD) exécutée exactement "
                "comme verrouillée au SAP : grille δ et groupe pénalisé "
                "pré-déclarés ; renversement d'effet marqué "
                "(delta_renversement) s'il survient")
        return sortie(confidence=0.92, artefacts=[art],
                      assumptions=assumptions,
                      contradictions=ajustement_refuses + sensibilite_refuses,
                      resultats=resultats, sensibilites=sensibilites,
                      results_ref=art.ref)
    return agent
