"""Valeurs de référence du catalogue statistique (stdlib pure)."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_catalogue import dist, ops
from stats_catalogue.controller import ControleurExecution
from core.exceptions import ErreurLogique


class TestDistributions(unittest.TestCase):
    def test_norm_cdf(self):
        self.assertAlmostEqual(dist.norm_cdf(1.959963984540054), 0.975, places=6)
        self.assertAlmostEqual(dist.norm_cdf(0.0), 0.5, places=12)

    def test_incbeta_identite(self):
        for x in (0.1, 0.5, 0.9):
            self.assertAlmostEqual(dist.incbeta(1.0, 1.0, x), x, places=12)

    def test_t_cdf_et_quantile(self):
        self.assertAlmostEqual(dist.t_cdf(0.0, 10), 0.5, places=12)
        # t_0.975 avec ddl=10 ≈ 2,22814 (tables)
        self.assertAlmostEqual(dist.t_ppf(0.975, 10), 2.228139, places=4)
        # aller-retour CDF/PPF
        self.assertAlmostEqual(dist.t_cdf(dist.t_ppf(0.99, 25), 25), 0.99, places=9)

    def test_chi2(self):
        # χ² 0,95 à 1 ddl ≈ 3,84146
        self.assertAlmostEqual(dist.chi2_cdf(3.8414588, 1), 0.95, places=5)
        self.assertAlmostEqual(dist.chi2_sf(3.8414588, 1), 0.05, places=5)

    def test_f_cdf(self):
        # médiane de F(1,1) = 1
        self.assertAlmostEqual(dist.f_cdf(1.0, 1, 1), 0.5, places=6)

    def test_beta_ppf(self):
        self.assertAlmostEqual(dist.beta_ppf(0.5, 2, 2), 0.5, places=9)


class TestOps(unittest.TestCase):
    def test_wilson_reference(self):
        # 10/100 → IC95 Wilson [0,0552 ; 0,1744] (référence publiée)
        r = ops.proportion_wilson(10, 100)
        self.assertAlmostEqual(r["ic95"][0], 0.05522, places=4)
        self.assertAlmostEqual(r["ic95"][1], 0.17437, places=4)

    def test_fisher_degustation(self):
        # Table classique [[3,1],[1,3]] → p bilatéral = 34/70 ≈ 0,485714
        r = ops.fisher_exact_2x2(3, 1, 1, 3)
        self.assertAlmostEqual(r["p_valeur"], 34 / 70, places=6)

    def test_mann_whitney_reference(self):
        # g1=[1,2,3], g2=[4,5,6] : U1=0, z≈-1,746 → p≈0,0808 (approx. normale)
        r = ops.mann_whitney([1, 2, 3], [4, 5, 6])
        self.assertAlmostEqual(r["p_valeur"], 0.0808, places=3)
        self.assertEqual(r["probabilite_superiorite_g1"], 0.0)

    def test_welch_separation_nette(self):
        rng = random.Random(7)
        g1 = [rng.gauss(10, 1) for _ in range(40)]
        g2 = [rng.gauss(15, 1) for _ in range(40)]
        r = ops.t_test_welch(g1, g2)
        self.assertTrue(r["interpretable"])
        self.assertLess(r["p_valeur"], 0.001)
        self.assertLess(r["ic95_difference"][0], -3.0)

    def test_welch_symetrie_et_determinisme(self):
        rng = random.Random(11)
        g1 = [rng.gauss(0, 2) for _ in range(30)]
        g2 = [rng.gauss(0.4, 2) for _ in range(30)]
        a = ops.t_test_welch(g1, g2)
        b = ops.t_test_welch(g2, g1)
        self.assertAlmostEqual(a["p_valeur"], b["p_valeur"], places=12)
        self.assertAlmostEqual(a["t"], -b["t"], places=12)

    def test_chi2_et_cochran(self):
        r = ops.chi2_independance([[30, 10], [20, 40]])
        self.assertTrue(r["interpretable"])
        self.assertTrue(r["cochran_ok"])
        self.assertLess(r["p_valeur"], 0.01)
        # petits effectifs théoriques → fallback Fisher recommandé
        r2 = ops.chi2_independance([[2, 1], [1, 2]])
        self.assertFalse(r2["cochran_ok"])

    def test_mcnemar(self):
        self.assertAlmostEqual(ops.mcnemar(9, 1)["p_valeur"],
                               2 * dist.binom_cdf(1, 10, 0.5), places=12)

    def test_kruskal(self):
        r = ops.kruskal_wallis({"a": [1, 2, 3], "b": [7, 8, 9], "c": [4, 5, 6]})
        self.assertTrue(r["interpretable"])
        self.assertLess(r["p_valeur"], 0.1)

    def test_anderson_darling(self):
        rng = random.Random(42)
        normales = [rng.gauss(0, 1) for _ in range(60)]
        self.assertEqual(ops.test_normalite(normales)["verdict"], "normal_ok")
        bizarres = [0.0] * 30 + [5.0] * 30
        self.assertEqual(ops.test_normalite(bizarres)["verdict"], "non_normal")

    def test_mos(self):
        self.assertEqual(ops.mos_cosmetique(200.0, 1.0)["verdict"], "OK")
        self.assertEqual(ops.mos_cosmetique(50.0, 1.0)["verdict"], "SIGNAL")
        inc = ops.mos_cosmetique(None, 1.0)
        self.assertEqual(inc["verdict"], "INCERTAIN")
        self.assertTrue(inc["zone_incertitude"])

    def test_hors_catalogue_bloque(self):
        with self.assertRaises(ErreurLogique):
            ops.executer("test_inexistant", seed=1)


class TestDoubleExecution(unittest.TestCase):
    def test_concordance(self):
        ctrl = ControleurExecution(seed=99)
        r = ctrl.executer(ops.t_test_welch, "t_test_welch", "1.0.0",
                          g1=[1, 2, 3, 4], g2=[2, 3, 4, 5])
        self.assertTrue(r["_execution"]["verifiee"])

    def test_divergence_detectee(self):
        compteur = {"n": 0}

        def op_bugguee(seed: int = 0):
            compteur["n"] += 1
            return {"valeur": compteur["n"]}

        ctrl = ControleurExecution(seed=1)
        with self.assertRaises(ErreurLogique):
            ctrl.executer(op_bugguee, "op_bugguee", "0.0.0")


if __name__ == "__main__":
    unittest.main()
