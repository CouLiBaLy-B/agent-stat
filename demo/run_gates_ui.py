"""Démonstration du cycle UI des gates : attente → dossier → signature → reprise.

1. Lancement SANS décisions pré-déposées : le pipeline bloque à G3 (fail-closed)
   ET exporte automatiquement le dossier de preuves (vue scores + SAP + hash).
2. Signature G3 via la CLI réelle (subprocess) : rôle recevable, motif, pièces —
   la CLI LIE la décision à la version courante (ref + sha256, preuve sig-2.0.0).
3. Durcissement démontré en live : retouche du registre (motif modifié après
   dépôt) → la reprise BLOQUE (empreinte ≠ recalculée) ; on restaure, ça passe.
4. Reprise déterministe : le pipeline avance jusqu'à G6, nouvel export, même
   signature liée par la CLI, reprise → TERMINE.
5. Vérifications : store SANS versions dupliquées (idempotence par contenu),
   chaîne d'audit intègre, signatures chaînées, SLA mesuré et respecté.

Exécution :  python3 demo/run_gates_ui.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                         # noqa: E402
from core.exceptions import PipelineBloque                  # noqa: E402
from core.state import Etat                                 # noqa: E402
from demo.jeu_donnees import COMPTES_DEMO, generer          # noqa: E402
from ui_gates import auth                                   # noqa: E402
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from orchestration.reprise import reprendre_pipeline        # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "gates"
CKPT_BLOQUE = RUNTIME / "checkpoints" / "ckpt_run-2026-07-27-gates_BLOQUE.json"


def _cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ui_gates.cli", *argv],
        cwd=RACINE_REPO, capture_output=True, text=True)


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    decisions = RUNTIME / "decisions.json"
    decisions.write_text("{}", encoding="utf-8")          # aucune décision
    # Annuaire d'authentification obligatoire : plus aucune signature
    # auto-déclarée — comptes démo (sels fixes, PBKDF2 réduit pour la démo).
    auth.initialiser(RUNTIME / "comptes.json")
    for ident, cpt in COMPTES_DEMO.items():
        auth.ajouter_compte(RUNTIME / "comptes.json", ident, cpt["roles"],
                            cpt["secret"], sel=cpt["sel"], iterations=10_000)
    print("annuaire validateurs créé :", ", ".join(sorted(COMPTES_DEMO)))
    donnees = generer()
    (RUNTIME / "donnees.json").write_text(
        json.dumps(donnees, ensure_ascii=False), encoding="utf-8")

    sys_ = construire_systeme(str(RUNTIME), str(decisions), backoff_base_s=0.0)
    etat = Etat(run_id="run-2026-07-27-gates", study_id="COS-2026-030",
                domaine="cosmetique", seed=20260727)

    print("== 1) lancement sans décision déposée ==")
    try:
        run_pipeline(etat, sys_, donnees)
        raise AssertionError("le pipeline aurait dû bloquer à G3")
    except PipelineBloque as e:
        etat = e.etat
        print(f"   bloqué : {e.regle.value}")
        p_md = RUNTIME / "exports" / "gates" / "dossier_G3.md"
        assert p_md.exists(), "dossier G3 non exporté automatiquement"
        texte = p_md.read_text(encoding="utf-8")
        assert "Dossier de décision" in texte and "sha256" in texte
        print(f"   dossier de preuves exporté ✔ ({p_md.name})")
    assert CKPT_BLOQUE.exists()

    print("== 2) mauvais secret → authentification refusée ==")
    r = _cli("sign", "--racine", str(RUNTIME), "--gate", "G3",
             "--etat", str(CKPT_BLOQUE), "--comptes", str(RUNTIME / "comptes.json"),
             "--validateur", "u:bio-042", "--secret", "pas-le-bon-secret",
             "--decision", "VALIDATED",
             "--motif", "validation sans preuve de compte",
             "--pieces", "sap")
    assert r.returncode == 2 and "authentification refusée" in r.stderr
    print(f"   refus fail-closed ✔ ({r.stderr.strip()[:70]}…)")

    print("== 2b) compte authentifié, rôle non habilité au gate ==")
    r = _cli("sign", "--racine", str(RUNTIME), "--gate", "G3",
             "--etat", str(CKPT_BLOQUE), "--comptes", str(RUNTIME / "comptes.json"),
             "--validateur", "u:stagiaire-9",
             "--secret", COMPTES_DEMO["u:stagiaire-9"]["secret"],
             "--decision", "VALIDATED", "--motif", "validation sans compétence",
             "--pieces", "sap")
    assert r.returncode == 2 and "habilit" in r.stderr
    print(f"   refus fail-closed ✔ ({r.stderr.strip()[:80]}…)")

    print("== 3) signature G3 par un biostatisticien (CLI, liaison auto) ==")
    r = _cli("sign", "--racine", str(RUNTIME), "--gate", "G3",
             "--etat", str(CKPT_BLOQUE), "--comptes", str(RUNTIME / "comptes.json"),
             "--validateur", "u:bio-042",
             "--secret", COMPTES_DEMO["u:bio-042"]["secret"],
             "--decision", "VALIDATED",
             "--motif", "SAP conforme ICH E9, endpoint unique, fallback pré-spécifié",
             "--pieces", "sap", "dq_report")
    assert r.returncode == 0, r.stderr
    print(f"   {r.stdout.strip().splitlines()[0]}")
    print(f"   {r.stdout.strip().splitlines()[1].strip()}")
    reg = json.loads(decisions.read_text(encoding="utf-8"))
    assert reg["G3"]["artefact_sha256"] and \
        reg["G3"]["preuve_signature"]["format"] == "sig-2.0.0"

    print("== 3b) falsification du registre APRÈS dépôt → blocage ==")
    brut_original = decisions.read_text(encoding="utf-8")
    trafique = json.loads(brut_original)
    # attaquant naïf : retouche le motif SANS recalculer l'empreinte sig-2.0.0
    trafique["G3"]["motif"] = "motif retouché a posteriori (falsification)"
    decisions.write_text(json.dumps(trafique, ensure_ascii=False),
                         encoding="utf-8")
    try:
        reprendre_pipeline(etat, str(RUNTIME), str(decisions),
                           json.loads((RUNTIME / "donnees.json")
                                      .read_text("utf-8")), llm="env")
        raise AssertionError("une décision falsifiée ne doit JAMAIS passer")
    except PipelineBloque as e:
        assert "non liée" in str(e) or "altéré" in str(e)
        print(f"   blocage fail-closed ✔ ({str(e)[:72]}…)")
    decisions.write_text(brut_original, encoding="utf-8")     # restauration

    print("== 4) statut + reprise jusqu'au point d'attente suivant (G6) ==")
    r = _cli("status", "--racine", str(RUNTIME), "--etat", str(CKPT_BLOQUE))
    assert r.returncode == 0
    print("   " + r.stdout.strip().splitlines()[2])
    # reprise dans le MÊME mode LLM que le run initial ("env") : en mode
    # déterministe ou llm-simule (scripté), le rejeu est bit-à-bit reproductible
    try:
        reprendre_pipeline(etat, str(RUNTIME), str(decisions),
                           json.loads((RUNTIME / "donnees.json")
                                      .read_text("utf-8")), llm="env")
        raise AssertionError("attente G6 attendue")
    except PipelineBloque as e:
        etat2 = e.etat
        assert (RUNTIME / "exports" / "gates" / "dossier_G6.md").exists()
        print(f"   nouvelle attente : {e.regle.value} (dossier G6 exporté ✔)")

    print("== 5) signature G6 + reprise finale ==")
    r = _cli("sign", "--racine", str(RUNTIME), "--gate", "G6",
             "--etat", str(CKPT_BLOQUE), "--comptes", str(RUNTIME / "comptes.json"),
             "--validateur", "u:dir-007",
             "--secret", COMPTES_DEMO["u:dir-007"]["secret"],
             "--decision", "VALIDATED",
             "--motif", "Résultats reproductibles, limites documentées, aucun signal",
             "--pieces", "rapport_draft", "critique", "safety_report",
             "compliance_report")
    assert r.returncode == 0, r.stderr
    etat3 = reprendre_pipeline(etat2, str(RUNTIME), str(decisions),
                               json.loads((RUNTIME / "donnees.json")
                                          .read_text("utf-8")),
                               llm="env")
    assert etat3.statut == "TERMINE"
    print(f"   TERMINE ✔ scores {etat3.scores}")

    print("== 6) contrôles d'intégrité ==")
    from core.store import StoreArtefacts
    store = StoreArtefacts(RUNTIME / "store")     # index rechargé (post-reprises)
    refs = [store.resoudre(t, etat3.study_id, n).ref for t, n in
            (("sap", "sap"), ("results", "results_inferential"),
             ("report", "rapport_draft"))]
    assert all(ref.endswith("/v1") for ref in refs), \
        f"versions dupliquées malgré le rejeu déterministe : {refs}"
    print("   store idempotent (toutes les refs en v1) ✔")
    ok, n, msg = JournalAudit.verifier(RUNTIME / "audit.jsonl")
    assert ok
    lignes = [json.loads(l) for l in
              (RUNTIME / "audit.jsonl").read_text("utf-8").splitlines()]
    sig = [e for e in lignes if e["action"].startswith("GATE_DECISION_DEPOSEE")]
    rep = [e for e in lignes if e["action"] == "REPRISE_PIPELINE"]
    alt = [e for e in lignes if "PREUVE_ALTEREE" in e["action"]
           or "LIAISON_INVALIDE" in e["action"]]
    sla = [e for e in lignes if e["action"].startswith("SLA_GATE_MESURE")]
    assert len(sig) == 2 and len(rep) >= 2
    assert alt, "la falsification aurait dû être journalisée"
    assert sla and all(e["details"]["dans_les_delais"] for e in sla), \
        "SLA attendu mesuré et respecté partout"
    attentes = {e["action"].split(":")[1]: e["details"]["attente_h"]
                for e in sla}
    print(f"   audit : {n} entrées intègres, {len(sig)} signatures chaînées, "
          f"{len(rep)} reprises tracées ✔")
    print(f"   falsification journalisée ✔ · SLA mesuré : "
          + ", ".join(f"{g}={h:.4f} h" for g, h in sorted(attentes.items())))
    print("\nUI DES GATES OK ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
