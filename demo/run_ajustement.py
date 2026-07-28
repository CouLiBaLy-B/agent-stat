"""Démonstration du parcours AJUSTEMENT MULTIVARIÉ de bout en bout.

Scénario 1 : cohorte prospective + temps d'événement → A3 = cox_ph
             (Cox/Breslow ajusté âge + tabac, pré-déclaré au SAP).
Scénario 2 : cohorte rétrospective, incidence binaire → A3 = regression_logistique

Points démontrés :
- la CONFUSION : mesure brute (HR / OR univarié) diluée vers le nul vs mesure
  AJUSTÉE retrouvant l'association de génération — l'ajustement n'existe que
  parce qu'il est verrouillé au SAP (G3), jamais post-hoc ;
- garde-fous fail-closed (EPV ≥ 10, colinéarité, quasi-séparation) ;
- CC plafonnée à 0,75 pour l'observationnel ajusté (≠ essai randomisé) ;
- lexique d'association maintenu (FAITS, INFÉRENCES, LIMITES sourcés) ;
- recalcul indépendant de l'ajustement à la relecture (aucune objection).

Exécution :  python3 demo/run_ajustement.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                                # noqa: E402
from core.state import Etat                                        # noqa: E402
from demo.jeu_donnees import DECISIONS_OK                          # noqa: E402
from demo.jeu_ajustement import (generer_cohorte_cox,              # noqa: E402
                                 generer_cohorte_logistique)
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees                    # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "ajustement"


def scenario(etiquette: str, study_id: str, fabrique, run: str) -> tuple[Etat, dict]:
    dossier = RUNTIME / etiquette
    if dossier.exists():
        shutil.rmtree(dossier)
    dossier.mkdir(parents=True)
    decisions = dossier / "decisions.json"
    etat = Etat(run_id=f"run-{run}-{etiquette}", study_id=study_id,
                domaine="medical", seed=20260729)
    donnees = fabrique()
    registre = ecrire_decisions_liees(decisions, DECISIONS_OK, etat, donnees,
                                      llm="env")
    assert all(d.get("artefact_ref") for d in registre.values())
    sys_ = construire_systeme(str(dossier), str(decisions), backoff_base_s=0.0)
    print(f"\n== Pipeline {etiquette.upper()} : {study_id} (A3 pré-déclarée) ==")
    etat = run_pipeline(etat, sys_, donnees)
    store = sys_["store"]
    assert etat.statut == "TERMINE", etat.statut
    print(f"Statut/type     : {etat.statut} · {etat.type_etude}")
    print(f"Scores          : DQ={etat.scores['fiabilite_donnees']} · "
          f"RA={etat.scores['robustesse_analyse']} · "
          f"CC={etat.scores['confiance_conclusion']} "
          f"({etat.scores['verbalisation_cc']})")
    res = store.lire_json(store.resoudre("results", etat.study_id,
                                         "results_inferential").ref)
    a1 = res["resultats"]["A1"]["resultat"]
    a3 = res["resultats"]["A3"]
    print(f"  BRUT   A1 {res['resultats']['A1']['op_retenue']:<22}"
          f"grandeur p={a1['p_valeur']:.3f}")
    if not a3["resultat"].get("interpretable"):
        raise SystemExit(f"A3 non interprétable : {a3['resultat'].get('motif')}")
    print(f"  AJUSTÉ A3 {a3['op_retenue']:<24} EPV={a3['resultat']['epv']} "
          f"cas complets n={a3['resultat']['n']}")
    for c in a3["resultat"]["coefficients"]:
        m = c.get("odds_ratio", c.get("hazard_ratio"))
        ic = c.get("ic95_or", c.get("ic95_hr"))
        tag = "OR" if "odds_ratio" in c else "HR"
        print(f"    {c['covariable']:<12} {tag}={m:.3f} "
              f"IC95=[{ic[0]:.3f};{ic[1]:.3f}] p={c['p_valeur']:.2e}")
    rapport = store.lire_json(store.resoudre("report", etat.study_id,
                                             "rapport_draft").ref)
    print(f"Conclusion      : {rapport['conclusion_directionnelle']}")
    ok, n, msg = JournalAudit.verifier(dossier / "audit.jsonl")
    print(f"Audit           : {n} entrées — {msg} [{ok}]")
    return etat, sys_


def main() -> int:
    co, sys_co = scenario("cox", "MED-AJ-2026-201", generer_cohorte_cox,
                          "2026-07-29")
    lg, sys_lg = scenario("logi", "MED-AJ-2026-202", generer_cohorte_logistique,
                          "2026-07-30")

    # --- vérifications démontrées ------------------------------------------
    for etat, sys_, op_attendu in ((co, sys_co, "cox_ph"),
                                   (lg, sys_lg, "regression_logistique")):
        store = sys_["store"]
        res = store.lire_json(store.resoudre("results", etat.study_id,
                                             "results_inferential").ref)
        a3 = res["resultats"]["A3"]
        assert a3["op_retenue"] == op_attendu
        assert a3["resultat"]["interpretable"]
        expo = a3["resultat"]["coefficients"][0]
        assert expo["covariable"] == "exposition"     # imposée en 1re position
        assert expo["p_valeur"] < 0.05
        m = expo.get("odds_ratio", expo.get("hazard_ratio"))
        assert m < 1.0                                # association protectrice
        assert etat.scores["confiance_conclusion"] <= 0.75
        assert etat.scores["confiance_conclusion"] > 0.0
        rapport = store.lire_json(store.resoudre("report", etat.study_id,
                                                 "rapport_draft").ref)
        assert rapport["conclusion_directionnelle"].startswith("association")
    md1 = (RUNTIME / "cox" / "exports" / co.study_id
           / "rapport_final.md").read_text(encoding="utf-8")
    md2 = (RUNTIME / "logi" / "exports" / lg.study_id
           / "rapport_final.md").read_text(encoding="utf-8")
    for md in (md1, md2):
        assert "Association AJUSTÉE" in md
        assert "Association ≠ causalité" in md
        assert "art://" in md
    print("\nAJUSTEMENT OK ✔ (2 scénarios brut dilué → ajusté protecteur, "
          "lexique d'association maintenu, CC ≤ 0,75)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
