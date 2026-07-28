"""Démonstration de bout en bout : test d'usage cosmétique, parcours nominal.

Exécution :  python3 demo/run_demo.py
Produit : runtime/demo/exports/<study>/rapport_final.md + audit.jsonl vérifiable.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit            # noqa: E402
from core.state import Etat                    # noqa: E402
from demo.jeu_donnees import DECISIONS_OK, generer   # noqa: E402
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees  # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "demo"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    decisions = RUNTIME / "decisions.json"
    etat = Etat(run_id="run-2026-07-27-0001", study_id="COS-2026-014",
                domaine="cosmetique", seed=20260727)
    donnees = generer()

    import os
    print("== Pré-liaison des décisions humaines (rejeu déterministe) ==")
    registre = ecrire_decisions_liees(decisions, DECISIONS_OK, etat, donnees,
                                      llm="env")
    for g, d in sorted(registre.items()):
        assert d.get("artefact_ref"), f"{g} non liée"
        print(f"  {g} liée à {d['artefact_ref']} "
              f"(sha256:{d['artefact_sha256'][:12]}…)")

    sys_ = construire_systeme(str(RUNTIME), str(decisions), backoff_base_s=0.0)
    print("== Lancement du pipeline (test d'usage cosmétique démo) ==")
    print(f"Mode LLM : {os.environ.get('AGENT_STAT_LLM_MODE', 'off')}")
    etat = run_pipeline(etat, sys_, donnees)

    print(f"\nStatut final        : {etat.statut} (phase {etat.phase})")
    print(f"Type d'étude        : {etat.type_etude} "
          f"(confiance {etat.confiance_qualification})")
    print(f"Scores              : DQ={etat.scores['fiabilite_donnees']} · "
          f"RA={etat.scores['robustesse_analyse']} · "
          f"CC={etat.scores['confiance_conclusion']} "
          f"({etat.scores['verbalisation_cc']})")
    print(f"Artefacts produits  : {len(etat.artefacts)}")
    print(f"Signaux safety      : {len(etat.signaux)}")
    print(f"Décisions orchestr. : {len(etat.decisions)}")

    ok, n, msg = JournalAudit.verifier(RUNTIME / "audit.jsonl")
    print(f"Journal d'audit     : {n} entrées — {msg} [{ok}]")

    export = RUNTIME / "exports" / etat.study_id / "rapport_final.md"
    print(f"\nRapport exporté     : {export}")
    print("----- extrait (résumé exécutif) -----")
    lignes = export.read_text(encoding="utf-8").splitlines()
    print("\n".join(lignes[:12]))
    assert ok and etat.statut == "TERMINE" and export.exists()
    print("\nDÉMO OK ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
