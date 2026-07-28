"""Tests unitaires stdlib : régression logistique & Cox — garde-fous
fail-closed, déterminisme, cohérences internes. La qualification numérique
contre oracle scipy/BFGS est dans tests/qualification/.
"""
from __future__ import annotations

import math
import random
import unittest

from stats_catalogue import ops


def _jeu(n=240, seed=7):
    rng = random.Random(seed)
    age = [rng.gauss(58, 12) for _ in range(n)]
    traite = [1.0 if rng.random() < 1 / (1 + math.exp(-(a - 55) / 6))
              else 0.0 for a in age]
    y = [1 if rng.random()
         < 1 / (1 + math.exp(-(-3.5 + 0.05 * age[i] - 0.9 * traite[i])))
         else 0 for i in range(n)]
    temps = [max(0.5, rng.gauss(18 + 0.05 * (a - 58), 5)) for i, a in
             enumerate(age)]
    evt = [1 if rng.random() < 0.5 else 0 for _ in range(n)]
    return age, traite, y, temps, evt


class TestLogistique(unittest.TestCase):
    def setUp(self):
        self.age, self.traite, self.y, _, _ = _jeu()
        self.x = {"traite": self.traite, "age": self.age}

    def test_nominal_interpretable(self):
        r = ops.regression_logistique(y=self.y, x=self.x)
        self.assertTrue(r["interpretable"], r.get("motif"))
        self.assertEqual(r["test"], "regression_logistique")
        self.assertEqual(r["n_covariables"], 2)
        self.assertGreaterEqual(r["epv"], 10)
        for c in r["coefficients"]:
            self.assertTrue(0.0 <= c["p_valeur"] <= 1.0)
            self.assertTrue(c["ic95_or"][0] <= c["odds_ratio"]
                            <= c["ic95_or"][1])
            self.assertTrue(math.isclose(
                math.log(c["odds_ratio"]), c["beta"], rel_tol=1e-12))
        self.assertGreater(r["chi2_modele"], 0)
        self.assertLess(r["p_valeur_modele"], 0.05)
        self.assertGreater(r["log_vraisemblance"], r["ll_modele_nul"])

    def test_p_jamais_seule_ic_et_or_toujours_presents(self):
        r = ops.regression_logistique(y=self.y, x=self.x)
        for c in r["coefficients"]:
            self.assertIn("ic95_or", c)
            self.assertIn("odds_ratio", c)
            self.assertIn("association", r["lecture"])
        self.assertIn("association", r["note_lexique"])

    def test_determinisme_bit_a_bit(self):
        r1 = ops.regression_logistique(y=self.y, x=self.x)
        r2 = ops.regression_logistique(y=self.y, x=self.x)
        self.assertEqual(r1, r2)

    def test_epv_insuffisant_refuse(self):
        # 2 covariables, 14 événements minoritaires ⇒ EPV 7 < 10
        y = [0] * 180 + [1] * 14
        rng = random.Random(3)
        x = {"a": [rng.gauss(0, 1) for _ in y],
             "b": [rng.gauss(0, 1) for _ in y]}
        r = ops.regression_logistique(y=y, x=x)
        self.assertFalse(r["interpretable"])
        self.assertIn("EPV", r["motif"])
        self.assertLess(r["epv"], r.get("seuil_epv", 10))

    def test_epv_seuil_parametrable(self):
        y = [0] * 180 + [1] * 14
        rng = random.Random(3)
        x = {"a": [rng.gauss(0, 1) for _ in y]}
        r = ops.regression_logistique(y=y, x=x, epv_min=10.0)
        self.assertTrue(r["interpretable"], r.get("motif"))   # EPV 14 ≥ 10
        r2 = ops.regression_logistique(y=y, x=x, epv_min=20.0)
        self.assertFalse(r2["interpretable"])                 # EPV 14 < 20
        r3 = ops.regression_logistique(y=y, x={"a": x["a"]}, epv_min=12.0)
        self.assertTrue(r3["interpretable"], r3.get("motif"))

    def test_separation_quasi_complete_refusee(self):
        # séparation parfaite : y = 1 ssi x > 0
        y = [0] * 60 + [1] * 60
        x = {"sep": [-1.0] * 60 + [1.0] * 60}
        r = ops.regression_logistique(y=y, x=x)
        self.assertFalse(r["interpretable"])
        self.assertIn("séparation", r["motif"])

    def test_covariable_constante_refusee(self):
        x = dict(self.x)
        x["const"] = [3.14] * len(self.y)
        r = ops.regression_logistique(y=self.y, x=x)
        self.assertFalse(r["interpretable"])
        self.assertIn("constante", r["motif"])

    def test_colinearite_exacte_refusee(self):
        x = dict(self.x)
        x["age_copie"] = list(self.age)              # copie exacte ⇒ singulière
        r = ops.regression_logistique(y=self.y, x=x)
        self.assertFalse(r["interpretable"])
        self.assertIn("colinéarité", r["motif"])

    def test_issue_non_binaire_refusee(self):
        r = ops.regression_logistique(y=[0, 1, 2] + [0] * 57 + [1] * 60,
                                      x={"v": [1.0] * 120})
        self.assertFalse(r["interpretable"])

    def test_issue_constante_refusee(self):
        r = ops.regression_logistique(y=[0] * len(self.y), x={"v": list(self.age)})
        self.assertFalse(r["interpretable"])

    def test_trop_de_covariables_refuse(self):
        rng = random.Random(9)
        x = {f"c{k}": [rng.gauss(0, 1) for _ in self.y] for k in range(15)}
        r = ops.regression_logistique(y=self.y, x=x)
        self.assertFalse(r["interpretable"])
        self.assertIn("covariables hors borne", r["motif"])

    def test_desalignement_refuse(self):
        r = ops.regression_logistique(y=self.y,
                                      x={"court": list(self.age)[: 100]})
        self.assertFalse(r["interpretable"])

    def test_n_trop_petit_refuse(self):
        r = ops.regression_logistique(y=[0, 1] * 9,
                                      x={"v": [float(i) for i in range(18)]})
        self.assertFalse(r["interpretable"])


class TestCoxPh(unittest.TestCase):
    def setUp(self):
        self.age, self.traite, _, self.temps, self.evt = _jeu()
        self.x = {"traite": self.traite, "age": self.age}

    def test_nominal_interpretable(self):
        r = ops.cox_ph(temps=self.temps, evenements=self.evt, x=self.x)
        self.assertTrue(r["interpretable"], r.get("motif"))
        self.assertEqual(r["test"], "cox_ph")
        self.assertEqual(r["methode_ties"], "Breslow")
        self.assertGreater(r["chi2_score_modele"], 0)
        for c in r["coefficients"]:
            self.assertTrue(0.0 <= c["p_valeur"] <= 1.0)
            self.assertTrue(c["ic95_hr"][0] <= c["hazard_ratio"]
                            <= c["ic95_hr"][1])
            self.assertTrue(math.isclose(
                math.log(c["hazard_ratio"]), c["beta"], rel_tol=1e-12))
        self.assertIn("PROPORTIONNELS", r["hypothese"])
        self.assertIn("association", r["lecture"])

    def test_determinisme_bit_a_bit(self):
        r1 = ops.cox_ph(temps=self.temps, evenements=self.evt, x=self.x)
        r2 = ops.cox_ph(temps=self.temps, evenements=self.evt, x=self.x)
        self.assertEqual(r1, r2)

    def test_invariance_au_decalage_de_temps_non(self):
        # centrage : décaler les covariables ne change pas beta/se/p
        r1 = ops.cox_ph(temps=self.temps, evenements=self.evt, x=self.x)
        r2 = ops.cox_ph(temps=self.temps, evenements=self.evt,
                        x={"traite": self.traite,
                           "age": [a + 100 for a in self.age]})
        for c1, c2 in zip(r1["coefficients"], r2["coefficients"]):
            self.assertTrue(math.isclose(c1["beta"], c2["beta"],
                                         rel_tol=1e-9, abs_tol=1e-9))
            self.assertTrue(math.isclose(c1["se"], c2["se"],
                                         rel_tol=1e-9, abs_tol=1e-9))

    def test_temps_non_positifs_refuses(self):
        r = ops.cox_ph(temps=[0.0] + self.temps[1:], evenements=self.evt,
                       x=self.x)
        self.assertFalse(r["interpretable"])

    def test_evenement_non_binaire_refuse(self):
        e = list(self.evt)
        e[0] = 2
        r = ops.cox_ph(temps=self.temps, evenements=e, x=self.x)
        self.assertFalse(r["interpretable"])

    def test_epv_insuffisant_refuse(self):
        e = [0] * 236 + [1] * 4                      # 4 événements / 2 cov = 2 < 10
        r = ops.cox_ph(temps=self.temps, evenements=e, x=self.x)
        self.assertFalse(r["interpretable"])
        self.assertIn("EPV", r["motif"])

    def test_colinearite_refusee(self):
        x = dict(self.x)
        x["age_copie"] = list(self.age)
        r = ops.cox_ph(temps=self.temps, evenements=self.evt, x=x)
        self.assertFalse(r["interpretable"])

    def test_censure_totale_refusee(self):
        r = ops.cox_ph(temps=self.temps, evenements=[0] * len(self.temps),
                       x=self.x)
        self.assertFalse(r["interpretable"])

    def test_p_jamais_seule_ic_et_hr_toujours_presents(self):
        r = ops.cox_ph(temps=self.temps, evenements=self.evt, x=self.x)
        for c in r["coefficients"]:
            self.assertIn("ic95_hr", c)
            self.assertIn("hazard_ratio", c)

    def test_catalogue_enregistre_les_deux_ops(self):
        self.assertIn("regression_logistique", ops.OPS)
        self.assertIn("cox_ph", ops.OPS)
        self.assertEqual(ops.OPS["cox_ph"]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
