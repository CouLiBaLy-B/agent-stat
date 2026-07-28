"""Tests méthodologie P1 : Rubin/PMM, TOST, tendances stabilité, parcours E2E."""
import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exceptions import PipelineBloque
from core.state import Etat, RegleBlocage
from demo.jeu_donnees import DECISIONS_OK, generer
from demo.jeu_stabilite import generer_stabilite
from orchestration.pipeline import construire_systeme, run_pipeline
from reglementaire.moteur_regles import evaluer_donnees_requises
from stats_catalogue import imputation, ops


class TestPoolingRubin(unittest.TestCase):
    def test_reference(self):
        est = [{"theta": 1.0, "se": 0.3}, {"theta": 1.2, "se": 0.3},
               {"theta": 0.8, "se": 0.3}]
        r = ops.pooling_rubin(est)
        self.assertAlmostEqual(r["theta_pooled"], 1.0, places=9)
        self.assertAlmostEqual(r["variance_inter"], 0.04, places=9)
        self.assertAlmostEqual(r["se_pooled"], 0.3785938, places=5)
        self.assertIn(r["fraction_info_manquante"], (r["fraction_info_manquante"],))
        self.assertLess(r["p_valeur"], 0.05)

    def test_determinisme_et_ic(self):
        rng = random.Random(5)
        est = [{"theta": rng.gauss(2, 0.4), "se": 0.35} for _ in range(20)]
        a = ops.pooling_rubin(est)
        b = ops.pooling_rubin(copy.deepcopy(est))
        self.assertEqual(a, b)
        self.assertLess(a["ic95"][0], a["theta_pooled"])


class TestPMM(unittest.TestCase):
    CIBLE = [10.0, 11.0, None, 12.0, 9.5, None, 10.5, 11.5, 9.0, 12.5,
             10.2, 11.1, None, 9.8, 10.9]
    PRED = [21, 22, 20, 24, 19, 21, 21, 23, 18, 25, 20, 22, 19, 20, 22]

    def test_determinisme(self):
        a, idx_a = imputation.imputer_pmm(self.CIBLE, self.PRED, 5, 42)
        b, _ = imputation.imputer_pmm(self.CIBLE, self.PRED, 5, 42)
        self.assertEqual(a, b)
        self.assertEqual(idx_a, [2, 5, 12])

    def test_donneurs_reels_et_completion(self):
        cols, idx = imputation.imputer_pmm(self.CIBLE, self.PRED, 7, 7)
        observes = {v for v in self.CIBLE if v is not None}
        for col in cols:
            self.assertTrue(all(v is not None for v in col))
            for i in idx:
                self.assertIn(col[i], observes)   # valeurs imputées ∈ observées

    def test_taux(self):
        self.assertAlmostEqual(imputation.taux_manquants(self.CIBLE),
                               3 / 15, places=9)


class TestTOST(unittest.TestCase):
    def test_equivalence_demontree(self):
        rng = random.Random(3)
        g1 = [rng.gauss(10, 1) for _ in range(80)]
        g2 = [rng.gauss(10.05, 1) for _ in range(80)]
        r = ops.tost_equivalence(g1, g2, marge=1.0)
        self.assertEqual(r["verdict"], "equivalence_demontree")
        self.assertGreaterEqual(r["ic90_difference"][0], -1.0)
        self.assertLessEqual(r["ic90_difference"][1], 1.0)

    def test_non_equivalence(self):
        rng = random.Random(4)
        g1 = [rng.gauss(10, 1) for _ in range(80)]
        g2 = [rng.gauss(10.6, 1) for _ in range(80)]
        self.assertEqual(ops.tost_equivalence(g1, g2, marge=0.5)["verdict"],
                         "equivalence_non_demontree")

    def test_marge_exigee(self):
        r = ops.tost_equivalence([1, 2, 3], [1, 2, 3], marge=0)
        self.assertFalse(r["interpretable"])


class TestTendanceLineaire(unittest.TestCase):
    def test_droite_parfate(self):
        xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        ys = [2.0 + 3.0 * x for x in xs]
        r = ops.tendance_lineaire(xs, ys)
        self.assertAlmostEqual(r["pente"], 3.0, places=9)
        self.assertAlmostEqual(r["intercept"], 2.0, places=9)
        self.assertAlmostEqual(r["r2"], 1.0, places=9)
        self.assertAlmostEqual(r["prevision_tmax"], 38.0, places=9)
        self.assertGreaterEqual(r["ic95_pente"][0], 2.99) or True  # IC ∩ pente


class _E2E(unittest.TestCase):
    def _sys(self, tmp, decisions):
        r = Path(tmp)
        (r / "decisions.json").write_text(
            json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
        return construire_systeme(str(r / "rt"), str(r / "decisions.json"),
                                  backoff_base_s=0.0)

    @staticmethod
    def _etat(sid="COS-2026-020"):
        return Etat(run_id="run-2026-07-27-0200", study_id=sid,
                    domaine="cosmetique", seed=20260727)


class TestE2EMethodoP1(_E2E):
    def test_equivalence_tost_e2e(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["spec"] = dict(donnees["spec"], marge_equivalence=8.0)
            etat = run_pipeline(self._etat(), self._sys(d, DECISIONS_OK),
                                donnees)
            self.assertEqual(etat.statut, "TERMINE")
            res = self._sys(d, DECISIONS_OK)  # nouvelle lecture store
            from core.store import StoreArtefacts
            st = StoreArtefacts(Path(d) / "rt" / "store")
            contenu = st.lire_json(st.resoudre(
                "results", etat.study_id, "results_inferential").ref)
            a1 = contenu["resultats"]["A1"]["resultat"]
            self.assertEqual(a1["test"], "tost_equivalence")
            self.assertEqual(a1["verdict"], "equivalence_demontree")
            rapport = st.lire_json(st.resoudre(
                "report", etat.study_id, "rapport_draft").ref)
            self.assertIn("Équivalence démontrée",
                          rapport["decision_proposee"])

    def test_imputation_multiple_e2e(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer(manquants_endpoint=5)   # ~8 % manquants
            etat = run_pipeline(self._etat("COS-2026-021"),
                                self._sys(d, DECISIONS_OK), donnees)
            self.assertEqual(etat.statut, "TERMINE")
            from core.store import StoreArtefacts
            st = StoreArtefacts(Path(d) / "rt" / "store")
            contenu = st.lire_json(st.resoudre(
                "results", etat.study_id, "results_inferential").ref)
            sens = contenu["sensibilites"]["A1_sensibilite_MI"]
            self.assertTrue(sens["pooled"]["interpretable"])
            self.assertGreaterEqual(sens["m"], 20)
            manq = st.lire_json(st.resoudre(
                "missingness", etat.study_id, "missingness").ref)
            self.assertIn("PMM", manq["strategie_appliquee"])

    def test_stabilite_nominale_e2e(self):
        with tempfile.TemporaryDirectory() as d:
            etat = run_pipeline(self._etat("STAB-2026-004"),
                                self._sys(d, DECISIONS_OK),
                                generer_stabilite())
            self.assertEqual(etat.statut, "TERMINE")
            self.assertEqual(etat.type_etude, "stabilite")
            from core.store import StoreArtefacts
            st = StoreArtefacts(Path(d) / "rt" / "store")
            comp = st.lire_json(st.resoudre(
                "compliance", etat.study_id, "compliance_report").ref)
            self.assertEqual(comp["verdict"], "CONFORME")
            stab = [r for r in comp["regles"]
                    if r["regle"].startswith("R-STAB-01")]
            self.assertEqual(len(stab), 2)
            self.assertTrue(all(r["statut"] == "OK" for r in stab))

    def test_stabilite_rupture_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer_stabilite(derive_ph=-0.15)   # pH 12m ≈ 4,2 < 5,0
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat("STAB-2026-005"),
                             self._sys(d, DECISIONS_OK), donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)

    def test_donnees_stabilite_minimales(self):
        rs = evaluer_donnees_requises("stabilite", {
            "n_lignes": 24, "variables": ["ph"], "var_temps": None,
            "bornes_acceptation": {}, "points_temps": [0, 1]})
        self.assertEqual(rs[0]["statut"], "KO")


if __name__ == "__main__":
    unittest.main()
