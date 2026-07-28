"""Démonstration du parcours stabilité : tendances pH/viscosité + règles bornes.

Exécution :  python3 demo/run_stabilite.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                  # noqa: E402
from core.state import Etat                          # noqa: E402
from demo.jeu_donnees import DECISIONS_OK            # noqa: E402
from demo.jeu_stabilite import generer_stabilite     # noqa: E402
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees  # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "stab"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    decisions = RUNTIME / "decisions.json"
    etat = Etat(run_id="run-2026-07-27-0100", study_id="STAB-2026-003",
                domaine="cosmetique", seed=20260727)
    donnees = generer_stabilite()
    registre = ecrire_decisions_liees(decisions, DECISIONS_OK, etat, donnees,
                                      llm="env")
    assert all(d.get("artefact_ref") for d in registre.values())
    sys_ = construire_systeme(str(RUNTIME), str(decisions), backoff_base_s=0.0)

    print("== Lancement du pipeline STABILITÉ (décisions pré-liées) ==")
    etat = run_pipeline(etat, sys_, donnees)
    print(f"Statut final        : {etat.statut} · type {etat.type_etude} "
          f"(confiance {etat.confiance_qualification})")
    print(f"Scores              : DQ={etat.scores['fiabilite_donnees']} · "
          f"RA={etat.scores['robustesse_analyse']} · "
          f"CC={etat.scores['confiance_conclusion']}")
    comp = sys_["store"].lire_json(sys_["store"].resoudre(
        "compliance", etat.study_id, "compliance_report").ref)
    print(f"Conformité          : {comp['verdict']}")
    for r in comp["regles"]:
        if r["regle"].startswith("R-STAB"):
            print(f"  - {r['regle']} : {r['statut']} — {r['preuve']}")
    ok, n, msg = JournalAudit.verifier(RUNTIME / "audit.jsonl")
    print(f"Journal d'audit     : {n} entrées — {msg} [{ok}]")
    export = RUNTIME / "exports" / etat.study_id / "rapport_final.md"
    lignes = export.read_text(encoding="utf-8").splitlines()
    print("----- extrait (résultats) -----")
    print("\n".join(l for l in lignes if "Stabilité" in l or "Décision" in l))
    assert ok and etat.statut == "TERMINE" and comp["verdict"] == "CONFORME"
    print("\nSTABILITÉ OK ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
