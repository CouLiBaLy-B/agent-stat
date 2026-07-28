"""Démonstration du parcours OBSERVATIONNEL médical de bout en bout.

Scénario 1 : cas-témoins appariée 1:1  → primaire or_apparie (McNemar).
Scénario 2 : cohorte prospective       → primaire km_logrank_hr (HR Peto).

Points démontrés : lexique d'association imposé de bout en bout (SAP →
rapport), règles R-DON-03 / R-OBS-01, recalcul indépendant à la relecture,
gates G3/G6 fail-closed, audit chaîné vérifiable.

Exécution :  python3 demo/run_observationnel.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                              # noqa: E402
from core.state import Etat                                      # noqa: E402
from demo.jeu_donnees import DECISIONS_OK                        # noqa: E402
from demo.jeu_observationnel import (generer_cas_temoins,        # noqa: E402
                                     generer_cohorte)
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees                  # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "obs"


def scenario(etiquette: str, study_id: str, fabrique) -> tuple[Etat, dict]:
    dossier = RUNTIME / etiquette
    if dossier.exists():
        shutil.rmtree(dossier)
    dossier.mkdir(parents=True)
    decisions = dossier / "decisions.json"
    etat = Etat(run_id=f"run-2026-07-27-{etiquette}", study_id=study_id,
                domaine="medical", seed=20260727)
    donnees = fabrique()
    registre = ecrire_decisions_liees(decisions, DECISIONS_OK, etat, donnees,
                                      llm="env")
    assert all(d.get("artefact_ref") for d in registre.values())
    sys_ = construire_systeme(str(dossier), str(decisions), backoff_base_s=0.0)
    print(f"\n== Pipeline {etiquette.upper()} : {study_id} "
          "(décisions pré-liées) ==")
    etat = run_pipeline(etat, sys_, donnees)
    print(f"Statut/type     : {etat.statut} · {etat.type_etude} "
          f"(confiance {etat.confiance_qualification})")
    print(f"Scores          : DQ={etat.scores['fiabilite_donnees']} · "
          f"RA={etat.scores['robustesse_analyse']} · "
          f"CC={etat.scores['confiance_conclusion']} "
          f"({etat.scores['verbalisation_cc']})")
    store = sys_["store"]
    res = store.lire_json(store.resoudre("results", etat.study_id,
                                         "results_inferential").ref)
    for aid, ana in res["resultats"].items():
        r = ana.get("resultat", {})
        if ana.get("op_retenue") in ("or_apparie", "odds_ratio_cas_temoins"):
            print(f"  {aid} {ana['op_retenue']:<22} OR={r['odds_ratio']:.3f} "
                  f"IC95=[{r['ic95_or'][0]:.3f};{r['ic95_or'][1]:.3f}] "
                  f"p={r['p_valeur']:.2e}")
        if ana.get("op_retenue") == "km_logrank_hr":
            print(f"  {aid} {ana['op_retenue']:<22} HR={r['hr']:.3f} "
                  f"IC95=[{r['ic95_hr'][0]:.3f};{r['ic95_hr'][1]:.3f}] "
                  f"p(log-rang)={r['p_valeur']:.2e}")
        if ana.get("op_retenue") == "risque_relatif_cohorte":
            print(f"  {aid} {ana['op_retenue']:<22} RR={r['risque_relatif']:.3f} "
                  f"IC95=[{r['ic95_rr'][0]:.3f};{r['ic95_rr'][1]:.3f}] "
                  f"p={r['p_valeur']:.2e}")
    comp = store.lire_json(store.resoudre("compliance", etat.study_id,
                                          "compliance_report").ref)
    print(f"Conformité      : {comp['verdict']} "
          f"({len(comp['regles_ko'])} KO, R-OBS-01 INFO présente)")
    rapport = store.lire_json(store.resoudre("report", etat.study_id,
                                             "rapport_draft").ref)
    print(f"Conclusion      : {rapport['conclusion_directionnelle']} · "
          f"décision : {rapport['decision_proposee'][:90]}…")
    ok, n, msg = JournalAudit.verifier(dossier / "audit.jsonl")
    print(f"Audit           : {n} entrées — {msg} [{ok}]")
    return etat, sys_


def main() -> int:
    ct, sys_ct = scenario("ct", "MED-CT-2026-101", generer_cas_temoins)
    co, sys_co = scenario("co", "MED-CO-2026-102", generer_cohorte)

    md = (RUNTIME / "ct" / "exports" / ct.study_id / "rapport_final.md") \
        .read_text(encoding="utf-8")
    assert ct.statut == "TERMINE" and co.statut == "TERMINE"
    assert "Association ≠ causalité" in md
    res = sys_ct["store"].lire_json(sys_ct["store"].resoudre(
        "results", ct.study_id, "results_inferential").ref)
    rap = sys_ct["store"].lire_json(sys_ct["store"].resoudre(
        "report", ct.study_id, "rapport_draft").ref)
    assert rap["conclusion_directionnelle"].startswith("association")
    assert res["resultats"]["A1"]["op_retenue"] == "or_apparie"
    assert res["resultats"]["A1"]["resultat"]["p_valeur"] < 0.05
    res2 = sys_co["store"].lire_json(sys_co["store"].resoudre(
        "results", co.study_id, "results_inferential").ref)
    assert res2["resultats"]["A1"]["op_retenue"] == "km_logrank_hr"
    assert res2["resultats"]["A1"]["resultat"]["p_valeur"] < 0.05
    print("\nOBSERVATIONNEL OK ✔ (2 scénarios, lexique d'association vérifié)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
