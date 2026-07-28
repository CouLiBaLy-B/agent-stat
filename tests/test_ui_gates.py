"""Tests de l'UI des gates : dossiers de preuves, signature fail-closed LIÉE
à la version d'artefact (sig-2.0.0), export automatique à l'attente, reprise
déterministe (idempotence du store), reproductibilité inter-runs (métriques
wall-clock hors contenu hashé), SLA mesuré en temps réel.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit import JournalAudit
from core.exceptions import PipelineBloque
from core.state import Etat
from core.store import StoreArtefacts
from demo.jeu_donnees import DECISIONS_OK, generer
from orchestration.pipeline import construire_systeme, run_pipeline
from orchestration.reprise import reprendre_pipeline
from tests import outillage
from tests.outillage import environnement
from ui_gates import cli, dossier as dossier_mod
from ui_gates.signature import RegleSignature, deposer_decision


def _env(tmp: str, decisions: dict, donnees: dict):
    r = Path(tmp)
    return environnement(str(r), r / "decisions.json", decisions, _etat(),
                         donnees)


def _etat(sid="COS-2026-040"):
    return Etat(run_id="run-2026-07-27-gt", study_id=sid,
                domaine="cosmetique", seed=20260727)


class TestDossierPreuves(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        donnees = generer()
        cls.sys_ = _env(cls.tmp.name, DECISIONS_OK, donnees)
        cls.etat = run_pipeline(_etat(), cls.sys_, donnees)
        cls.store = cls.sys_["store"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _dossier(self, gate, type_, name):
        art = self.store.resoudre(type_, self.etat.study_id, name)
        return dossier_mod.construire_dossier(self.store, self.etat, gate, art,
                                              sla_h=72, roles_requis=["x"])

    def test_dossier_g3_sap_et_scores(self):
        d = self._dossier("G3", "sap", "sap")
        self.assertEqual(d["resume_metier"]["endpoint_principal"]["variable"],
                         "delta_score")
        self.assertTrue(any(a["op"] == "t_test_welch"
                            for a in d["resume_metier"]["analyses"]))
        self.assertIn("dq", d["resume_metier"])
        self.assertTrue(any(p.startswith("art://sap/")
                            for p in d["preuves"]))
        md = dossier_mod.rendre_markdown(d)
        self.assertIn("Dossier de décision", md)
        self.assertIn("AUCUNE validation par défaut", md)
        self.assertIn("python3 -m ui_gates.cli sign", md)
        self.assertIn("--etat", md)          # la liaison passe par le checkpoint
        self.assertIn("sig-2.0.0", md)

    def test_dossier_g6_decision_et_conformite(self):
        d = self._dossier("G6", "report", "rapport_draft")
        self.assertTrue(d["resume_metier"]["decision_proposee"])
        self.assertEqual(d["resume_metier"]["verdict"], "CONFORME")
        self.assertEqual(d["resume_metier"]["signaux"], [])
        self.assertTrue(d["resume_metier"]["limites"])

    def test_preuves_citent_hash(self):
        d = self._dossier("G3", "sap", "sap")
        self.assertTrue(all("#sha256:" in p for p in d["preuves"]))


class TestSignatureFailClosed(unittest.TestCase):
    def _audit(self, tmp):
        return JournalAudit(Path(tmp) / "a.jsonl")

    # liaison présente (factice) : la recevabilité UI exige sa PRÉSENCE ;
    # la vérification contre la version réelle a lieu au gate, pas au dépôt.
    BASE = {"statut": "VALIDATED", "validateur_id": "u:bio-1",
            "role": "biostatisticien",
            "motif": "justification suffisamment longue",
            "pieces_consultees": ["sap"],
            "artefact_ref": "art://sap/COS-2026-040/sap/v1",
            "artefact_sha256": "ab" * 32}

    def test_statut_invalide(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RegleSignature):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 {**self.BASE, "statut": "OK_BOF"},
                                 ["biostatisticien"])

    def test_role_non_habilite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RegleSignature, "non habilité"):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 {**self.BASE, "role": "stagiaire"},
                                 ["biostatisticien"])

    def test_motif_court_et_pieces_vides(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RegleSignature, "motif"):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 {**self.BASE, "motif": "ok"},
                                 ["biostatisticien"])
            with self.assertRaisesRegex(RegleSignature, "pieces_consultees"):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 {**self.BASE, "pieces_consultees": []},
                                 ["biostatisticien"])

    def test_liaison_manquante_refusee_au_depot(self):
        with tempfile.TemporaryDirectory() as d:
            sans = {k: v for k, v in self.BASE.items()
                    if k not in ("artefact_ref", "artefact_sha256")}
            with self.assertRaisesRegex(RegleSignature, "liaison"):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 dict(sans), ["biostatisticien"])
            sans_sha = dict(self.BASE); sans_sha["artefact_sha256"] = ""
            with self.assertRaisesRegex(RegleSignature, "liaison"):
                deposer_decision(Path(d) / "dec.json", self._audit(d), "G3",
                                 sans_sha, ["biostatisticien"])

    def test_signature_produit_reference_preuve_et_audit(self):
        with tempfile.TemporaryDirectory() as d:
            audit = self._audit(d)
            ecrite = deposer_decision(Path(d) / "dec.json", audit, "G3",
                                      dict(self.BASE), ["biostatisticien"])
            self.assertTrue(ecrite["signature_ref"].startswith("sig:"))
            preuve = ecrite["preuve_signature"]
            self.assertEqual(preuve["format"], "sig-2.0.0")
            self.assertEqual(preuve["artefact_lie"]["ref"],
                             self.BASE["artefact_ref"])
            self.assertIn("eidas", preuve)
            self.assertIsNone(preuve["eidas"]["certificat"])  # réservé
            from core.signature import verifier_preuve
            self.assertTrue(verifier_preuve("G3", ecrite))
            reg = json.loads((Path(d) / "dec.json").read_text("utf-8"))
            self.assertEqual(reg["G3"]["statut"], "VALIDATED")
            self.assertTrue(reg["G3"]["depose_le"])
            ok, n, _ = JournalAudit.verifier(Path(d) / "a.jsonl")
            self.assertTrue(ok)
            self.assertEqual(n, 1)


class TestExportAutomatiqueAttente(unittest.TestCase):
    def test_dossier_exporte_au_blocage_g3(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            dec = {k: v for k, v in DECISIONS_OK.items() if k != "G3"}
            sys_ = _env(d, dec, donnees)
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, donnees)
            motif = ctx.exception.motif
            self.assertIn("dossier de preuves exporté", motif)
            p = Path(d) / "exports" / "gates" / "dossier_G3.md"
            self.assertTrue(p.exists())
            self.assertIn("Dossier de décision", p.read_text("utf-8"))
            lignes = [json.loads(l) for l in
                      (Path(d) / "audit.jsonl").read_text("utf-8").splitlines()]
            self.assertTrue(any(e["action"] == "DOSSIER_GATE_EXPORTE:G3"
                                for e in lignes))

    def test_dossier_g4_signaux_exportes(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            for r in donnees["datasets"]["rows"][:8]:
                r["reaction_grade"] = 2
            sys_ = _env(d, DECISIONS_OK, donnees)       # pas de G4
            with self.assertRaises(PipelineBloque):
                run_pipeline(_etat(), sys_, donnees)
            p = Path(d) / "exports" / "gates" / "dossier_G4.json"
            self.assertTrue(p.exists())
            doc = json.loads(p.read_text("utf-8"))
            self.assertTrue(doc["resume_metier"]["signaux"])

    def test_refus_gate_ne_regenere_pas_dossier_mais_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            dec = dict(DECISIONS_OK)
            dec["G3"] = {**dec["G3"], "statut": "REFUSED"}
            sys_ = _env(d, dec, donnees)
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, donnees)
            self.assertEqual(ctx.exception.regle.value, "GATE_HUMAIN_NON_VALIDE")
            self.assertIn("refus", ctx.exception.motif)


class TestCycleAttenteSignatureReprise(unittest.TestCase):
    def test_cycle_complet_et_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            sys_ = _env(d, {}, donnees)               # aucune décision
            with self.assertRaises(PipelineBloque) as c1:
                run_pipeline(_etat(), sys_, donnees)
            etat = c1.exception.etat

            # la signature EST la version présentée : on la résout du store
            sap = sys_["store"].resoudre("sap", etat.study_id, "sap")
            audit = sys_["audit"]
            deposer_decision(Path(d) / "decisions.json", audit, "G3",
                             {**TestSignatureFailClosed.BASE,
                              "artefact_ref": sap.ref,
                              "artefact_sha256": sap.sha256},
                             ["biostatisticien"])
            # reprise 1 → nouvelle attente G6 (fail-closed)
            with self.assertRaises(PipelineBloque) as c2:
                reprendre_pipeline(etat, d, str(Path(d) / "decisions.json"),
                                   donnees)
            etat = c2.exception.etat
            self.assertIn("G6", c2.exception.motif)

            # la reprise a écrit depuis SA propre instance : tout nouvel
            # écrivain ré-instancie le journal (rescelle), comme la CLI
            store = StoreArtefacts(Path(d) / "store")   # index rechargé
            rapport = store.resoudre("report", etat.study_id, "rapport_draft")
            audit = JournalAudit(Path(d) / "audit.jsonl")
            deposer_decision(Path(d) / "decisions.json", audit, "G6",
                             {**TestSignatureFailClosed.BASE,
                              "role": "responsable_etude",
                              "artefact_ref": rapport.ref,
                              "artefact_sha256": rapport.sha256},
                             ["biostatisticien", "responsable_etude",
                              "safety_assessor"])
            etat = reprendre_pipeline(etat, d, str(Path(d) / "decisions.json"),
                                      donnees)
            self.assertEqual(etat.statut, "TERMINE")

            store = StoreArtefacts(Path(d) / "store")
            for t, n in (("sap", "sap"), ("results", "results_inferential"),
                         ("report", "rapport_draft")):
                self.assertTrue(store.resoudre(t, etat.study_id, n)
                                .ref.endswith("/v1"),
                                f"rejeu déterministe dupliqué sur {t}/{n}")
            ok, _, _ = JournalAudit.verifier(Path(d) / "audit.jsonl")
            self.assertTrue(ok)

    def test_reprise_refusee_si_non_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            etat = _etat()
            with self.assertRaises(Exception):
                reprendre_pipeline(etat, d, str(Path(d) / "decisions.json"),
                                   generer())


class TestCLI(unittest.TestCase):
    def test_cli_cycle_sign_status_resume(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            donnees = generer()
            _env(d, {}, donnees)
            (racine / "donnees.json").write_text(
                json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(PipelineBloque) as c1:
                run_pipeline(_etat(), construire_systeme(
                    str(racine), str(racine / "decisions.json"),
                    backoff_base_s=0.0), donnees)
            ckpt = sorted((racine / "checkpoints").glob("ckpt_*.json"))[-1]
            outillage.creer_comptes_demo(racine)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["status", "--racine", str(racine),
                               "--etat", str(ckpt)])
            self.assertEqual(rc, 0)
            self.assertIn("BLOCAGE", buf.getvalue())

            rc = cli.main(["sign", "--racine", str(racine), "--gate", "G3",
                           "--etat", str(ckpt),
                           "--validateur", "u:bio-042",
                           *outillage.args_sign(racine, "u:bio-042"),
                           "--decision", "VALIDATED",
                           "--motif", "SAP relu, endpoint unique et verrou OK",
                           "--pieces", "sap", "dq_report"])
            self.assertEqual(rc, 0)
            reg = json.loads((racine / "decisions.json").read_text("utf-8"))
            self.assertTrue(reg["G3"]["artefact_ref"]
                            .startswith("art://sap/"))
            self.assertEqual(len(reg["G3"]["artefact_sha256"]), 64)

            # compte authentifié MAIS rôle interdit → rc 2, registre
            # inchangé pour G6 (rôle lu dans l'annuaire, pas auto-déclaré)
            rc = cli.main(["sign", "--racine", str(racine), "--gate", "G6",
                           "--etat", str(ckpt),
                           "--validateur", "u:stagiaire-9",
                           *outillage.args_sign(racine, "u:stagiaire-9"),
                           "--decision", "VALIDATED",
                           "--motif", "tentative non habilitée",
                           "--pieces", "rapport_draft"])
            self.assertEqual(rc, 2)
            reg = json.loads((racine / "decisions.json").read_text("utf-8"))
            self.assertNotIn("G6", reg)

            rc = cli.main(["resume", "--racine", str(racine),
                           "--etat", str(ckpt),
                           "--donnees", str(racine / "donnees.json")])
            self.assertEqual(rc, 3)          # nouvelle attente G6

            rc = cli.main(["sign", "--racine", str(racine), "--gate", "G6",
                           "--etat", str(ckpt),
                           "--validateur", "u:dir-007",
                           *outillage.args_sign(racine, "u:dir-007"),
                           "--decision", "VALIDATED",
                           "--motif", "rapport relu, limites et risques OK",
                           "--pieces", "rapport_draft", "critique"])
            self.assertEqual(rc, 0)
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc = cli.main(["resume", "--racine", str(racine),
                               "--etat", str(ckpt),
                               "--donnees", str(racine / "donnees.json")])
            self.assertEqual(rc, 0)
            self.assertIn("TERMINE", buf2.getvalue())

    def test_cli_status_affiche_les_liaisons(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            donnees = generer()
            _env(d, {}, donnees)
            with self.assertRaises(PipelineBloque):
                run_pipeline(_etat(), construire_systeme(
                    str(racine), str(racine / "decisions.json"),
                    backoff_base_s=0.0), donnees)
            ckpt = sorted((racine / "checkpoints").glob("ckpt_*.json"))[-1]
            outillage.creer_comptes_demo(racine)
            rc = cli.main(["sign", "--racine", str(racine), "--gate", "G3",
                           "--etat", str(ckpt),
                           "--validateur", "u:bio-042",
                           *outillage.args_sign(racine, "u:bio-042"),
                           "--decision", "VALIDATED",
                           "--motif", "SAP relu, endpoint unique et verrou OK",
                           "--pieces", "sap", "dq_report"])
            self.assertEqual(rc, 0)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["status", "--racine", str(racine),
                          "--etat", str(ckpt)])
            self.assertIn("art://sap/", buf.getvalue())   # liaison visible


class TestReproductibiliteInterRuns(unittest.TestCase):
    def test_meme_sha256_entre_deux_runs(self):
        """Les métriques wall-clock sont hors contenu adressé : 2 runs
        indépendants (mêmes données/graine) ⇒ MÊME sha256 des résultats."""
        shas = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                donnees = generer()
                sys_ = _env(d, DECISIONS_OK, donnees)
                etat = run_pipeline(_etat(), sys_, donnees)
                res = sys_["store"].resoudre(
                    "results", etat.study_id, "results_inferential")
                shas.append(res.sha256)
        self.assertEqual(shas[0], shas[1])


if __name__ == "__main__":
    unittest.main()
