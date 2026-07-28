"""Agent Nettoyage / Qualité des données — référentiel de contrôles + score DQ.

Formule (versionnée au registre des décisions, cf. ARCHITECTURE.md §C.6) :
    DQ = 0,25·Complétude_w + 0,20·Cohérence + 0,10·(1−Doublons)
       + 0,15·(1−OutliersCrit) + 0,10·Temporalité + 0,20·ÉcartProtocole
Plafonds : DQ ≤ 0,50 si complétude endpoint principal < 90 % ;
           DQ ≤ 0,60 si doublons sujets > 2 %.
Détecte et documente — JAMAIS de correction silencieuse.
"""
from __future__ import annotations

from agents_impl.base import (Contexte, depot, numeriques, sha256_obj, sortie,
                              valeurs)
from core.state import Etat

IQR_COEF = 3.0
Z_MAD_SEUIL = 4.0


def _quartiles(xs: list[float]) -> tuple[float, float]:
    s = sorted(xs)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    moitie = s[: n // 2]
    q1 = (moitie[len(moitie) // 2] if len(moitie) % 2 else
          (moitie[len(moitie) // 2 - 1] + moitie[len(moitie) // 2]) / 2) if moitie else med
    return q1, med


def _est_manquant(v) -> bool:
    return v is None or v == ""


def fabriquer(ctx: Contexte):
    ctx.producteur = "agent.dataqualite"

    def agent(etat: Etat, entrees: dict) -> dict:
        spec = entrees["spec"]
        rows: list[dict] = entrees["datasets"]["rows"]
        n = len(rows)
        variables: dict = spec["variables"]
        endpoint = spec["endpoint_principal"]
        sujet = spec.get("sujet_id", "sujet")
        anomalies: list[dict] = []

        # -- complétude (pondérée par criticité) ------------------------------
        comp: dict[str, float] = {}
        num, den = 0.0, 0.0
        for var, vspec in variables.items():
            manquants = sum(1 for r in rows if _est_manquant(r.get(var)))
            c = 1.0 - (manquants / n if n else 1.0)
            comp[var] = round(c, 4)
            poids = 1.0 if vspec.get("critique") else 0.5
            num += poids * c
            den += poids
            if manquants and vspec.get("critique"):
                anomalies.append({"regle": "completude_critique", "variable": var,
                                  "manquants": manquants, "gravite": "majeure"})
        completude_w = num / den if den else 0.0
        completude_ep = comp.get(endpoint, 0.0)

        # -- cohérence (domaines) ----------------------------------------------
        tot_cases, viol = n * max(1, len(variables)), 0
        for var, vspec in variables.items():
            dom = vspec.get("domaine")
            if not dom:
                tot_cases -= n
                continue
            for r in rows:
                v = r.get(var)
                if _est_manquant(v):
                    continue
                ok = (dom[0] <= v <= dom[1]) if isinstance(v, (int, float)) \
                    else v in dom
                if not ok:
                    viol += 1
                    anomalies.append({"regle": "domaine_viole", "variable": var,
                                      "valeur": v, "sujet": r.get(sujet),
                                      "gravite": "majeure"})
        coherence = 1.0 - viol / max(1, tot_cases)

        # -- doublons sujets (design : mesures répétées → clé composite) --------
        cle_doublon = ([sujet, spec["var_temps"]] if spec.get("var_temps")
                       else [sujet])
        cles = [tuple(r.get(k) for k in cle_doublon) for r in rows]
        vus, dups = set(), 0
        for c in cles:
            dups += 1 if c in vus else 0
            vus.add(c)
        taux_doublons = dups / n if n else 0.0
        if dups:
            anomalies.append({"regle": "doublon_sujet", "nb": dups,
                              "cle": "+".join(cle_doublon),
                              "gravite": "critique" if taux_doublons > 0.02 else "majeure"})

        # -- valeurs aberrantes (IQR×3, z robuste MAD) — détection seule -------
        outliers_crit, outliers_tot = 0, 0
        for var, vspec in variables.items():
            if vspec.get("type") != "continue":
                continue
            xs = numeriques(rows, var)
            if len(xs) < 8:
                continue
            q1, med = _quartiles(xs)
            s = sorted(xs)
            q3 = s[(3 * len(s)) // 4]
            iqr = max(1e-12, q3 - q1)
            mad = sorted(abs(x - med) for x in xs)[len(xs) // 2] or 1e-12
            for r in rows:
                v = r.get(var)
                if _est_manquant(v) or not isinstance(v, (int, float)):
                    continue
                z_mad = 0.6745 * abs(v - med) / mad
                if v < q1 - IQR_COEF * iqr or v > q3 + IQR_COEF * iqr or z_mad > Z_MAD_SEUIL:
                    outliers_tot += 1
                    if vspec.get("critique"):
                        outliers_crit += 1
                        anomalies.append({"regle": "outlier_critique",
                                          "variable": var, "valeur": v,
                                          "sujet": r.get(sujet),
                                          "z_mad": round(z_mad, 2),
                                          "gravite": "majeure"})
        nb_cont_crit = max(1, sum(1 for v, vs in variables.items()
                                  if vs.get("type") == "continue" and vs.get("critique")))
        outliers_score = 1.0 - outliers_crit / (n * nb_cont_crit)

        # -- cohérence temporelle ------------------------------------------------
        ordre = spec.get("visite_ordre", [])
        bad_tempo = 0
        if len(ordre) >= 2:
            for r in rows:
                dates = [r.get(d) for d in ordre]
                dates = [d for d in dates if d]
                if len(dates) >= 2 and dates != sorted(dates):
                    bad_tempo += 1
                    anomalies.append({"regle": "ordre_temporel", "sujet": r.get(sujet),
                                      "gravite": "majeure"})
        temporalite = 1.0 - bad_tempo / n if n else 0.0

        # -- écart protocole/données ---------------------------------------------
        absentes = [v for v in variables if all(_est_manquant(r.get(v)) for r in rows)]
        ecart = 1.0 - len(absentes) / max(1, len(variables))
        for v in absentes:
            anomalies.append({"regle": "variable_attendue_absente", "variable": v,
                              "gravite": "critique"})

        # -- score + plafonds -----------------------------------------------------
        dq = (0.25 * completude_w + 0.20 * coherence + 0.10 * (1 - taux_doublons)
              + 0.15 * max(0.0, outliers_score) + 0.10 * temporalite + 0.20 * ecart)
        plafonds = []
        if completude_ep < 0.90:
            dq, plafonds = min(dq, 0.50), plafonds + ["endpoint<90%"]
        if taux_doublons > 0.02:
            dq, plafonds = min(dq, 0.60), plafonds + ["doublons>2%"]
        dq = round(dq, 4)

        rapport = {
            "version_formule": "dq-1.0.0", "n_lignes": n,
            "score_dq": dq, "plafonds_appliques": plafonds,
            "sous_scores": {"completude_w": round(completude_w, 4),
                            "completude_endpoint_principal": round(completude_ep, 4),
                            "coherence": round(coherence, 4),
                            "taux_doublons": round(taux_doublons, 4),
                            "outliers_critiques": outliers_crit,
                            "outliers_total": outliers_tot,
                            "temporalite": round(temporalite, 4),
                            "ecart_protocole": round(ecart, 4)},
            "completude_par_variable": comp, "anomalies": anomalies,
            "dataset_snapshot_sha256": sha256_obj(rows),
        }
        art = depot(ctx, etat.study_id, "dq", "dq_report", rapport)
        contradictions = (["variables attendues absentes du dataset"]
                          if absentes else [])
        return sortie(confidence=max(0.5, min(0.99, dq + 0.1)), artefacts=[art],
                      assumptions=["détection sans correction (journal vide)",
                                   "grille de criticité issue de la spec"],
                      contradictions=contradictions,
                      score_dq=dq,
                      completude_endpoint_principal=completude_ep,
                      dataset_snapshot_sha256=rapport["dataset_snapshot_sha256"],
                      dq_ref=art.ref)
    return agent
