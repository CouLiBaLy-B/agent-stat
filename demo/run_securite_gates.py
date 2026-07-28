"""Démonstration du durcissement sécurité des gates : authentification + eIDAS.

1. ANNUAIRE : PBKDF2 salé par compte (jamais de secret en clair), anti force
   brute — 5 échecs consécutifs → verrouillage 15 min persisté dans
   l'annuaire, aucune dégradation par défaut (annuaire absent ⇒ refus).
2. PSCE SIMULATEUR (AGENT_STAT_PSCE_MODE=simulateur) : validateur SANS
   certificat enregistré ⇒ refus du dépôt ; certificat de niveau insuffisant
   ⇒ refus ; certificat qualifié ⇒ cachet + horodatage qualifié — SIMULÉS
   (recalculables publiquement, PAS de la cryptographie : PSCQ + RFC 3161
   réels derrière le même contrat en production).
3. INTÉGRITÉ AU GATE : altération d'un cachet (valeur retouchée) dans le
   registre ⇒ la preuve sig-2.0.0 reste intacte MAIS la vérification des
   cachets bloque (GATE_CACHET_INVALIDE) — fail-closed ; restauration ⇒ OK.

Exécution :  python3 demo/run_securite_gates.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                        # noqa: E402
from core.exceptions import PipelineBloque                  # noqa: E402
from core.signature import verifier_cachets_eidas           # noqa: E402
from core.state import Etat                                 # noqa: E402
from demo.jeu_donnees import COMPTES_DEMO, generer          # noqa: E402
from orchestration.pipeline import construire_systeme, run_pipeline  # noqa: E402
from orchestration.reprise import reprendre_pipeline        # noqa: E402
from ui_gates import auth                                   # noqa: E402

RUNTIME = RACINE_REPO / "runtime" / "securite_gates"
CKPT = RUNTIME / "checkpoints" / "ckpt_run-2026-07-28-securite_BLOQUE.json"
COMPTES = RUNTIME / "comptes.json"


def _cli(*argv: str, psce: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if psce is not None:
        env["AGENT_STAT_PSCE_MODE"] = psce
    return subprocess.run([sys.executable, "-m", "ui_gates.cli", *argv],
                          cwd=RACINE_REPO, capture_output=True, text=True,
                          env=env)


def _sign(gate: str, validateur: str, secret: str, motif: str,
          pieces: list[str], psce: str = "simulateur") -> subprocess.CompletedProcess:
    return _cli("sign", "--racine", str(RUNTIME), "--gate", gate,
                "--etat", str(CKPT), "--comptes", str(COMPTES),
                "--validateur", validateur, "--secret", secret,
                "--decision", "VALIDATED", "--motif", motif,
                "--pieces", *pieces, psce=psce)


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    donnees = generer()
    (RUNTIME / "donnees.json").write_text(
        json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    (RUNTIME / "decisions.json").write_text("{}", encoding="utf-8")
    etat = Etat(run_id="run-2026-07-28-securite", study_id="COS-2026-050",
                domaine="cosmetique", seed=20260728)
    sys_ = construire_systeme(str(RUNTIME), str(RUNTIME / "decisions.json"),
                              backoff_base_s=0.0)

    print("== 1) pipeline jusqu'à l'attente G3 ==")
    auth.initialiser(COMPTES)
    for ident, cpt in COMPTES_DEMO.items():
        auth.ajouter_compte(COMPTES, ident, cpt["roles"], cpt["secret"],
                            sel=cpt["sel"], iterations=10_000)
    try:
        run_pipeline(etat, sys_, donnees)
        raise AssertionError("le pipeline aurait dû bloquer à G3")
    except PipelineBloque as e:
        etat = e.etat
        print(f"   bloqué : {e.regle.value} (dossier exporté)")

    print("== 2) annuaire : force brute puis verrouillage persisté ==")
    for k in range(4):
        try:
            auth.authentifier(COMPTES, "u:bio-042", f"faux-{k}")
        except auth.AuthRefusee:
            pass
    print("   4 échecs consécutifs enregistrés (annuaire persisté)")
    r = _sign("G3", "u:bio-042", "faux-5", "ne doit jamais passer", ["sap"])
    assert r.returncode == 2 and "VERROUILLÉ" in r.stderr, r.stderr
    print(f"   5ᵉ échec → compte verrouillé, refus CLI ✔ "
          f"({r.stderr.strip()[:64]}…)")
    r = _sign("G3", "u:bio-042", COMPTES_DEMO["u:bio-042"]["secret"],
              "meme le BON secret est refuse pendant le verrou", ["sap"])
    assert r.returncode == 2 and "verrouill" in r.stderr
    print("   même le bon secret est refusé pendant le verrou ✔")
    # déverrouillage administrateur tracé (procédure hors console) :
    brut = json.loads(COMPTES.read_text(encoding="utf-8"))
    brut["comptes"]["u:bio-042"]["echecs_consecutifs"] = 0
    brut["comptes"]["u:bio-042"]["verrouille_jusqu_a"] = None
    COMPTES.write_text(json.dumps(brut, ensure_ascii=False), encoding="utf-8")

    print("== 3) PSCE simulateur : certificat ABSENT → refus du cachet ==")
    auth.ajouter_compte(COMPTES, "u:bio-777", ["biostatisticien"],
                        "bio7-demo-2026", sel="77aa11bb33cc42dd",
                        iterations=10_000)   # rôle OK, AUCUN certificat PSCE
    r = _sign("G3", "u:bio-777", "bio7-demo-2026",
              "SAP relu, mais aucun certificat PSCE enregistre", ["sap"])
    assert r.returncode == 2 and "aucun certificat PSCE" in r.stderr
    assert "G3" not in json.loads((RUNTIME / "decisions.json")
                                  .read_text("utf-8"))
    print(f"   refus fail-closed, RIEN d'écrit ✔ ({r.stderr.strip()[:64]}…)")

    print("== 4) PSCE simulateur : certificat SES insuffisant → refus ==")
    # u:tech-003 a un certificat SES (non qualifié) — on lui donne le RÔLE
    # biostatisticien côté annuaire : seul le niveau eIDAS fait écran.
    brut = json.loads(COMPTES.read_text(encoding="utf-8"))
    brut["comptes"]["u:tech-003"]["roles"] = ["biostatisticien"]
    COMPTES.write_text(json.dumps(brut, ensure_ascii=False), encoding="utf-8")
    r = _sign("G3", "u:tech-003", COMPTES_DEMO["u:tech-003"]["secret"],
              "SAP relu, mais certificat de niveau SES insuffisant", ["sap"])
    assert r.returncode == 2 and "niveau SES" in r.stderr
    print(f"   refus fail-closed ✔ ({r.stderr.strip()[:70]}…)")

    print("== 5) signature scellée G3 (certificat QES simulé) ==")
    r = _sign("G3", "u:bio-042", COMPTES_DEMO["u:bio-042"]["secret"],
              "SAP conforme ICH E9, endpoint unique, fallback pré-spécifié",
              ["sap", "dq_report"])
    assert r.returncode == 0, r.stderr
    print("   " + r.stdout.strip().splitlines()[2].strip())
    reg = json.loads((RUNTIME / "decisions.json").read_text("utf-8"))
    eidas = reg["G3"]["preuve_signature"]["eidas"]
    assert eidas["niveau_actuel"] == "QES_SIMULE"
    assert verifier_cachets_eidas(reg["G3"]["preuve_signature"])
    assert eidas["cachet_signature"]["empreinte_couverte"] == \
        reg["G3"]["preuve_signature"]["empreinte"]
    print(f"   cachet couvre l'empreinte sig-2.0.0 exacte ✔ "
          f"(jeton TSA serial {eidas['horodatage_qualifie']['serial']})")

    print("== 6) falsification du CACHET dans le registre → GATE_CACHET_INVALIDE ==")
    brut_original = (RUNTIME / "decisions.json").read_text("utf-8")
    trafique = json.loads(brut_original)
    v = trafique["G3"]["preuve_signature"]["eidas"]["cachet_signature"]
    v["valeur"] = "0" * 64                      # cachet retouché, empreinte OK
    (RUNTIME / "decisions.json").write_text(
        json.dumps(trafique, ensure_ascii=False), encoding="utf-8")
    try:
        reprendre_pipeline(etat, str(RUNTIME), str(RUNTIME / "decisions.json"),
                           json.loads((RUNTIME / "donnees.json")
                                      .read_text("utf-8")), llm="env")
        raise AssertionError("un cachet falsifié ne doit JAMAIS passer")
    except PipelineBloque as e:
        assert "cachet eIDAS altéré" in str(e)
        print(f"   blocage fail-closed ✔ ({str(e)[:64]}…)")
    (RUNTIME / "decisions.json").write_text(brut_original, encoding="utf-8")
    lignes = [json.loads(l) for l in
              (RUNTIME / "audit.jsonl").read_text("utf-8").splitlines()]
    assert any(e.get("action") == "GATE_CACHET_INVALIDE:G3" for e in lignes)
    print("   journal audit : GATE_CACHET_INVALIDE:G3 tracé ✔")

    print("== 7) falsification de l'HORODATAGE qualifié → idem ==")
    trafique = json.loads(brut_original)
    trafique["G3"]["preuve_signature"]["eidas"]["horodatage_qualifie"][
        "gen_time_utc"] = "2001-01-01T00:00:00+00:00"
    (RUNTIME / "decisions.json").write_text(
        json.dumps(trafique, ensure_ascii=False), encoding="utf-8")
    try:
        reprendre_pipeline(etat, str(RUNTIME), str(RUNTIME / "decisions.json"),
                           json.loads((RUNTIME / "donnees.json")
                                      .read_text("utf-8")), llm="env")
        raise AssertionError("un jeton falsifié ne doit JAMAIS passer")
    except PipelineBloque:
        print("   blocage fail-closed ✔ (gen_time retouché détecté)")
    (RUNTIME / "decisions.json").write_text(brut_original, encoding="utf-8")

    print("== 8) reprise avec cachets intègres ==")
    try:
        reprendre_pipeline(etat, str(RUNTIME), str(RUNTIME / "decisions.json"),
                           json.loads((RUNTIME / "donnees.json")
                                      .read_text("utf-8")), llm="env")
    except PipelineBloque as e:
        print(f"   décision acceptée, pipeline avance : {e.regle.value} ✔")

    ok, n, msg = JournalAudit.verifier(RUNTIME / "audit.jsonl")
    assert ok
    print(f"   audit : {n} entrées intègres ✔")
    print("\nSÉCURITÉ DES GATES OK ✔ (annuaire PBKDF2 + verrou, cachets "
          "eIDAS SIMULÉS — PSCQ/RFC-3161 réels derrière le même contrat "
          "en production)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
