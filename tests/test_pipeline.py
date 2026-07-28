"""Tests d'intégration du pipeline : parcours nominal + scénarios de blocage.

Les décisions de gates sont pré-déposées LIÉES aux versions déterministes
(sig-2.0.0) via `tests.outillage` — la liaison est vérifiée mécaniquement
par l'orchestrateur à chaque gate.
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit import JournalAudit
from core.exceptions import PipelineBloque
from core.state import Etat, RegleBlocage
from demo.jeu_donnees import DECISIONS_OK, generer
from orchestration.pipeline import run_pipeline
from tests.outillage import environnement


def _environnement(tmp: str, decisions: dict, donnees: dict):
    return environnement(str(Path(tmp) / "rt"), Path(tmp) / "decisions.json",
                         decisions, _etat(), donnees)


def _etat() -> Etat:
    return Etat(run_id="run-2026-07-27-0002", study_id="COS-2026-015",
                domaine="cosmetique", seed=20260727)


class TestParcoursNominal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sys_ = _environnement(cls.tmp.name, DECISIONS_OK, generer())
        cls.etat = run_pipeline(_etat(), cls.sys_, generer())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_termine(self):
        self.assertEqual(self.etat.statut, "TERMINE")

    def test_qualification(self):
        self.assertEqual(self.etat.type_etude, "test_usage_controle")
        self.assertGreaterEqual(self.etat.confiance_qualification, 0.8)

    def test_scores(self):
        s = self.etat.scores
        self.assertGreaterEqual(s["fiabilite_donnees"], 0.6)
        self.assertIsNotNone(s["robustesse_analyse"])
        self.assertIsNotNone(s["confiance_conclusion"])
        self.assertIn(s["verbalisation_cc"],
                      ("elevee", "moderee", "faible", "insuffisante"))

    def test_verrou_sap_pose(self):
        self.assertIsNotNone(self.etat.verrous["sap_sha256"])
        self.assertIsNotNone(self.etat.verrous["dataset_sha256"])

    def test_artefacts_et_audit(self):
        self.assertGreaterEqual(len(self.etat.artefacts), 10)
        rt = Path(self.tmp.name) / "rt"
        ok, n, _ = JournalAudit.verifier(rt / "audit.jsonl")
        self.assertTrue(ok)
        self.assertGreaterEqual(n, 15)
        export = rt / "exports" / self.etat.study_id / "rapport_final.md"
        self.assertTrue(export.exists())
        md = export.read_text(encoding="utf-8")
        for section in ("Résumé exécutif", "FAITS", "Inférences",
                        "Actions recommandées", "Limites et hypothèses",
                        "Validation humaine requise"):
            self.assertIn(section, md)

    def test_resultats_reproductibles(self):
        res = self.sys_["store"].lire_json(
            self.sys_["store"].resoudre("results", self.etat.study_id,
                                        "results_inferential").ref)
        self.assertEqual(res["double_execution"], "hashes concordants")
        a1 = res["resultats"]["A1"]["resultat"]
        self.assertTrue(a1["interpretable"])
        self.assertIn("ic95_difference", a1)      # jamais de p-value seule


class TestBlocages(unittest.TestCase):
    def test_gate_g6_absent_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            dec = {k: v for k, v in DECISIONS_OK.items() if k != "G6"}
            sys_ = _environnement(d, dec, generer())
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, generer())
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.GATE_HUMAIN_NON_VALIDE)
            self.assertEqual(ctx.exception.etat.statut, "BLOQUE")

    def test_completude_endpoint_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer(manquants_endpoint=10)   # 10/60 ≈ 17 % manquants
            sys_ = _environnement(d, DECISIONS_OK, donnees)
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.COMPLETUDE_ENDPOINT_INSUFFISANTE)

    def test_substance_interdite_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["composition"] = list(donnees["composition"]) + [
                {"inci": "hydroquinone", "concentration_pct": 1.0}]
            sys_ = _environnement(d, DECISIONS_OK, donnees)
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)

    def test_signal_safety_gate_g4_requis(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            for r in donnees["datasets"]["rows"][:8]:   # 13 % de réactions ≥ 2
                r["reaction_grade"] = 2
            sys_ = _environnement(d, DECISIONS_OK, donnees)   # pas de G4
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(_etat(), sys_, donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.GATE_HUMAIN_NON_VALIDE)
            self.assertTrue(ctx.exception.etat.signaux)

    def test_signal_safety_gate_g4_valide_passe(self):
        with tempfile.TemporaryDirectory() as d:
            dec = dict(DECISIONS_OK)
            dec["G4"] = {"statut": "VALIDATED", "validateur_id": "u:tox-003",
                         "role": "toxicologue",
                         "motif": "Signal analysé : réactions irritatives réversibles, "
                                  "surveillance renforcée recommandée.",
                         "pieces_consultees": ["safety_report",
                                               "results_inferential"]}
            donnees = generer()
            for r in donnees["datasets"]["rows"][:8]:
                r["reaction_grade"] = 2
            sys_ = _environnement(d, dec, donnees)
            etat = run_pipeline(_etat(), sys_, donnees)
            self.assertEqual(etat.statut, "TERMINE")
            self.assertTrue(etat.signaux)     # signal conservé + tracé, pas masqué


class TestRelectureContreAnalyse(unittest.TestCase):
    def test_chiffre_falsifie_detecte(self):
        with tempfile.TemporaryDirectory() as d:
            sys_ = _environnement(d, DECISIONS_OK, generer())
            etat = run_pipeline(_etat(), sys_, generer())
            store = sys_["store"]
            ref_res = store.resoudre("results", etat.study_id,
                                     "results_inferential").ref
            contenu = store.lire_json(ref_res)
            falsifie = copy.deepcopy(contenu)
            falsifie["resultats"]["A1"]["resultat"]["p_valeur"] = 0.0001
            # la relecture recalcule depuis les entrées embarquées → objection
            agent = sys_["orchestrateur"].agents["agent.relecture"]
            sortie = agent(etat, {"rapport": {"lignes_faits": [],
                                              "texte_inferences": "",
                                              "limites": ["x"]},
                                  "resultats": falsifie,
                                  "scores": {"confiance_conclusion": None,
                                             "verbalisation_cc": None}})
            self.assertTrue(sortie["objections_bloquantes"])
            self.assertEqual(sortie["objections_bloquantes"][0]["type"],
                             "chiffre_non_reproduit")


if __name__ == "__main__":
    unittest.main()
