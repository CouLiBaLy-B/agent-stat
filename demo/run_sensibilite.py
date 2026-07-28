"""Démonstration E2E des analyses de sensibilité PRÉ-DÉCLARÉES (catalogue).

Volet 1 — stabilité, dérive lente : le lot reste DANS les bornes à 12 mois
(conformité nominale), mais la sensibilité « passage au grand mail »
(tendance_fenetre_glissante, item ST-SENS-ph du SAP) détecte dès t0=0 le
mois de franchissement anticipé du seuil (extrapolation ICH Q1E bornée) —
le signal de robustesse est rendu mesurable AVANT d'être constaté.

Volet 2 — données manquantes : ruban MNAR δ-ajusté sur SMD de Hedges
(tipping_point_mnar_smd) PRÉ-DÉCLARÉ au SAP (item A4, verrou G3), exécuté
sur les copies PMM (m = 20) — l'ampleur de biais MNAR qui renverserait la
conclusion est quantifiée en unités σ, à côté du tipping point historique
sur différence brute ; le renversement d'effet est marqué s'il survient.

Et pour les deux : mêmes entrées, même SEED ⇒ même SHA-256 des artefacts
résultats entre deux exécutions isolées (reproductibilité inter-runs).

Exécution :  python3 demo/run_sensibilite.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                       # noqa: E402
from core.state import Etat                               # noqa: E402
from demo.jeu_donnees import DECISIONS_OK                 # noqa: E402
from demo.jeu_donnees import generer as generer_usage     # noqa: E402
from demo.jeu_stabilite import generer_stabilite          # noqa: E402
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from ui_gates.liaison import ecrire_decisions_liees       # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "sensibilite"


def _lancer(racine: Path, etat: Etat, donnees: dict):
    """Pipeline complet avec décisions humaines pré-liées (rejeu
    déterministe — cf. demo/run_gates_ui.py)."""
    racine.mkdir(parents=True)
    decisions = racine / "decisions.json"
    registre = ecrire_decisions_liees(decisions, DECISIONS_OK, etat, donnees,
                                      llm="env")
    assert all(d.get("artefact_ref") for d in registre.values())
    sys_ = construire_systeme(str(racine), str(decisions), backoff_base_s=0.0)
    etat = run_pipeline(etat, sys_, donnees)
    return etat, sys_


def _results(sys_, study_id: str) -> dict:
    return sys_["store"].lire_json(sys_["store"].resoudre(
        "results", study_id, "results_inferential").ref)


def _rapport(sys_, study_id: str) -> dict:
    return sys_["store"].lire_json(sys_["store"].resoudre(
        "report", study_id, "rapport_draft").ref)


def volet_stabilite() -> str:
    """Retourne le SHA-256 de l'artefact résultats (comparé entre runs)."""
    print("── VOLET 1 · stabilité, dérive lente −0,07/mois ──")
    etat0 = Etat(run_id="run-2026-07-28-0210", study_id="STAB-2026-011",
                 domaine="cosmetique", seed=20260728)
    donnees = generer_stabilite(seed=20260728, derive_ph=-0.07)
    etat, sys_ = _lancer(RUNTIME / "stab" / "run_a", etat0, donnees)
    res = _results(sys_, etat.study_id)
    comp = sys_["store"].lire_json(sys_["store"].resoudre(
        "compliance", etat.study_id, "compliance_report").ref)
    print(f"  statut {etat.statut} · conformité 12 mois : {comp['verdict']}")

    sap = sys_["store"].lire_json(sys_["store"].resoudre(
        "sap", etat.study_id, "sap").ref)
    sens_sap = next(a for a in sap["analyses"] if a["role"] == "sensibilite")
    print(f"  SAP (G3) : {sens_sap['id']} · {sens_sap['op']} · fenêtre "
          f"{sens_sap['fenetre_mois']:g} mois · horizon +{sens_sap['horizon_mois']:g} mois")

    r = res["resultats"][sens_sap["id"]]["resultat"]
    assert r["interpretable"], r.get("motif")
    print(f"  seuil déduit (déterministe) : {r['spec_limite']:g} sens "
          f"{r['direction']} · {r['n_fenetres']} fenêtres couvertes")
    print(f"  verdict : {r['verdict']}")
    assert etat.statut == "TERMINE" and comp["verdict"] == "CONFORME"
    assert r["direction"] == "inferieur" and r["spec_limite"] == 5.0
    assert r["premier_t0_franchissement"] == 0.0
    assert r["premier_mois_franchissement_prevu"] == 18.0

    rep = _rapport(sys_, etat.study_id)
    ligne = next(l for l in rep["lignes_faits"] if "grand mail" in l)
    assert "franchissement détectable" in ligne and "art://" in ligne
    print(f"  rapport : « {ligne[2:130]}… »")

    # reproductibilité inter-runs : mêmes entrées + seed ⇒ même SHA-256
    donnees_b = generer_stabilite(seed=20260728, derive_ph=-0.07)
    etat_b = Etat(run_id="run-2026-07-28-0210", study_id="STAB-2026-011",
                  domaine="cosmetique", seed=20260728)
    etat_b, sys_b = _lancer(RUNTIME / "stab" / "run_b", etat_b, donnees_b)
    res_b = _results(sys_b, etat_b.study_id)
    art_a = sys_["store"].resoudre("results", etat.study_id,
                                   "results_inferential")
    art_b = sys_b["store"].resoudre("results", etat_b.study_id,
                                    "results_inferential")
    assert art_a.sha256 == art_b.sha256, "reproductibilité inter-runs rompue"
    print(f"  reproductibilité : sha256 résultats identiques entre 2 runs "
          f"isolés ({art_a.sha256[:16]}…)")

    sens_b = res_b["resultats"][sens_sap["id"]]["resultat"]
    assert sens_b == r
    return art_a.sha256


def volet_manquants() -> None:
    print("\n── VOLET 2 · manquants 8 % → ruban MNAR sur SMD pré-déclaré au SAP ──")
    donnees = generer_usage(manquants_endpoint=5)
    # sensibilité MNAR PRÉ-DÉCLARÉE dans la spec (verrouillée à G3 avec le
    # reste du SAP) : grille δ, groupe pénalisé = celui qui porte les
    # manquants ("produit", 5 imputés)
    donnees["spec"]["sensibilite_mnar_smd"] = {
        "deltas": [0.0, 0.25, 0.5, 1.0], "groupe": "produit"}
    etat = Etat(run_id="run-2026-07-28-0211", study_id="COS-2026-061",
                domaine="cosmetique", seed=20260728)
    etat, sys_ = _lancer(RUNTIME / "usage", etat, donnees)
    res = _results(sys_, etat.study_id)

    sap = sys_["store"].lire_json(sys_["store"].resoudre(
        "sap", etat.study_id, "sap").ref)
    a4 = next(a for a in sap["analyses"] if a["role"] == "sensibilite")
    assert a4["op"] == "tipping_point_mnar_smd"
    print(f"  SAP (G3) : {a4['id']} · {a4['op']} · δ {a4['deltas']} σ · "
          f"groupe pénalisé « {a4['groupe_mnar']} »")

    sens = res["sensibilites"]["A1_sensibilite_MI"]
    pool = sens["pooled"]
    rb = res["resultats"][a4["id"]]["resultat"]
    print(f"  PMM m={sens['m']} · effet poolé {pool['theta_pooled']:.3f} "
          f"(p={pool['p_valeur']:.4f}, FMI "
          f"{100*pool['fraction_info_manquante']:.0f} %)")
    print(f"  ruban SMD MNAR : σ_ref={rb['sigma_ref']:.3f} · {rb['verdict']}")
    for e in rb["ruban"]:
        tag = ("significatif" if e["significatif"]
               else ("RENVERSÉ" if e["renverse"] else "PERDU"))
        print(f"    δ={e['delta']:>4.2f} σ → θ(SMD)={e['theta_pooled']:+.3f} "
              f"p={e['p_valeur']:.4f} · {tag}")
    assert etat.statut == "TERMINE"
    assert rb["interpretable"]
    assert rb["delta_bascule"] == 0.25         # bascule dès δ = 0,25 σ
    assert rb["delta_renversement"] == 1.0     # effet renversé à δ = 1 σ
    assert not rb["theta_monotone"]            # grille traversée (V) — tracé
    assert rb["base"]["theta_pooled"] > 0
    assert sens["concordante_primaire"]

    rep = _rapport(sys_, etat.study_id)
    ligne = next(l for l in rep["lignes_faits"] if "ruban MNAR" in l)
    assert "FRAGILE" in ligne and "art://" in ligne
    print(f"  rapport : « {ligne[2:140]}… »")


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    volet_stabilite()
    volet_manquants()
    for sous in ("stab/run_a", "stab/run_b", "usage"):
        ok, n, msg = JournalAudit.verifier(RUNTIME / sous / "audit.jsonl")
        assert ok, f"audit rompu ({sous}) : {msg}"
    print("\nJournaux d'audit  : 3/3 chaînes intègres ✔")
    print("\nSENSIBILITÉ E2E OK ✔ (franchissement anticipé détecté en stabilité "
          "· ruban MNAR quantifié en σ · reproductibilité inter-runs prouvée)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
