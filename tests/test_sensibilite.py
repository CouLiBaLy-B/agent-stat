"""Tests des analyses de sensibilité déclaratives et du verrou toctou.

- `tendance_fenetre_glissante` : passage au grand mail (premier mois où la
  pire borne IC95 de la prévision d'un OLS local franchit le seuil de
  spécification) — modèle local pré-déclaré, extrapolation bornée ;
- `tipping_point_mnar_smd` : ruban MNAR δ-ajusté contre l'effet observé
  (SMD de Hedges par cycle PMM, re-pool Rubin), point de bascule unique
  (|θ(δ)| monotone décroissant) ;
- verrou d'unicité des sorties exécutables du contrôleur : une seule
  PREMIÈRE sortie par (run, op, entrées) ; toute ré-exécution divergente
  (toctou) bloque ; toute concordante sert la première sortie, tracée.
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exceptions import ErreurLogique            # noqa: E402
from stats_catalogue import controller, ops          # noqa: E402
from stats_catalogue import imputation as imp        # noqa: E402


def _points_descendants(seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    return [{"mois": m,
             "valeur": round(26 - 0.25 * m + rng.gauss(0, 0.08), 3)}
            for m in [0, 3, 6, 9, 12, 18, 24]]


def _jeux_mnar(seed: int = 11, m: int = 6):
    rng = random.Random(seed)
    g1 = [round(rng.gauss(12.0, 5.0), 2) for _ in range(30)]
    g2 = [round(rng.gauss(8.0, 5.0), 2) for _ in range(30)]
    cible = list(g2)
    for i in range(0, 30, 3):
        cible[i] = None                        # 10 manquants (MNAR simulé)
    cols2, _ = imp.imputer_pmm(cible, None, m, seed=0)
    return [list(g1) for _ in range(m)], cols2


class TestTendanceFenetreGlissante(unittest.TestCase):
    def _exec(self, **kw):
        base = dict(points=_points_descendants(), fenetre_mois=12,
                    horizon_mois=6, spec_limite=24.5, direction="inferieur")
        base.update(kw)
        return ops.executer("tendance_fenetre_glissante", 0, **base)

    def test_franchissement_detecte_et_champs(self):
        r = self._exec()
        self.assertTrue(r["interpretable"])
        self.assertEqual(r["test"], "tendance_fenetre_glissante")
        self.assertGreaterEqual(r["n_fenetres"], 1)
        self.assertIsNotNone(r["premier_t0_franchissement"])
        self.assertIsNotNone(r["premier_mois_franchissement_prevu"])
        self.assertGreaterEqual(r["premier_mois_franchissement_prevu"],
                                r["premier_t0_franchissement"])
        for f in r["fenetres"]:
            self.assertIn(f["franchit"], (True, False))
            self.assertGreaterEqual(f["borne_pessime"], -1e9)

    def test_serie_stable_aucun_franchissement(self):
        rng = random.Random(3)
        points = [{"mois": m,
                   "valeur": round(25 + rng.gauss(0, 0.05), 3)}
                  for m in [0, 3, 6, 9, 12, 18, 24]]
        # seuil très éloigné : une série stable ne peut pas le franchir
        r = self._exec(points=points, spec_limite=20.0)
        self.assertTrue(r["interpretable"])
        self.assertIsNone(r["premier_t0_franchissement"])
        self.assertIn("aucun franchissement", r["verdict"])

    def test_gardes_fail_closed(self):
        self.assertFalse(self._exec(direction="haut")["interpretable"])
        self.assertFalse(self._exec(points=_points_descendants()[:5])
                         ["interpretable"])
        self.assertFalse(self._exec(horizon_mois=18)["interpretable"])
        self.assertFalse(self._exec(fenetre_mois=0)["interpretable"])
        # série sans fenêtre d'au moins 4 points
        trous = [{"mois": m, "valeur": 25.0}
                 for m in [0, 2, 12, 14, 24, 26]]
        r = self._exec(points=trous, fenetre_mois=3)
        self.assertFalse(r["interpretable"])

    def test_determinisme_et_direction_superieure(self):
        rng = random.Random(9)
        points = [{"mois": m,
                   "valeur": round(20 + 0.3 * m + rng.gauss(0, 0.05), 3)}
                  for m in [0, 3, 6, 9, 12, 18, 24]]
        r = self._exec(points=points, spec_limite=26.0,direction="superieur")
        self.assertTrue(r["interpretable"])
        a = self._exec(points=points, spec_limite=26.0, direction="superieur")
        self.assertEqual(a, r)


class TestTippingPointMnar(unittest.TestCase):
    def test_ruban_monotone_et_bascule(self):
        cols1, cols2 = _jeux_mnar()
        t = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                         colonnes_g2=cols2,
                         deltas=[0.0, 0.25, 0.5, 0.75, 1.0],
                         groupe_ajuste="g2")
        self.assertTrue(t["interpretable"])
        self.assertEqual(t["m_imputations"], 6)
        self.assertAlmostEqual(t["base"]["theta_pooled"],
                               t["ruban"][0]["theta_pooled"], places=15)
        self.assertTrue(t["theta_monotone"])
        self.assertEqual(t["delta_bascule"], 0.75)
        self.assertAlmostEqual(t["decalage_bascule_unite"],
                               0.75 * t["sigma_ref"], places=12)
        thetas = [r["theta_pooled"] for r in t["ruban"]]
        self.assertTrue(all(thetas[i] > thetas[i + 1]
                            for i in range(len(thetas) - 1)))

    def test_orientation_contre_effet_negatif(self):
        cols1, cols2 = _jeux_mnar()
        t = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols2,
                         colonnes_g2=cols1, deltas=[0.0, 0.5, 1.0],
                         groupe_ajuste="g1")
        self.assertLess(t["base"]["theta_pooled"], 0.0)
        self.assertIsNotNone(t["delta_bascule"])
        # |θ| décroît avec δ quel que soit le signe de l'effet
        absol = [abs(r["theta_pooled"]) for r in t["ruban"]]
        self.assertTrue(all(absol[i] > absol[i + 1]
                            for i in range(len(absol) - 1)))

    def test_robuste_si_aucune_bascule(self):
        cols1, cols2 = _jeux_mnar()
        t = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                         colonnes_g2=cols2, deltas=[0.0, 0.05],
                         groupe_ajuste="g2")
        self.assertIsNone(t["delta_bascule"])
        self.assertIn("ROBUSTE", t["verdict"])

    def test_gardes_fail_closed(self):
        cols1, cols2 = _jeux_mnar()
        ko = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                          colonnes_g2=[cols2[0]], deltas=[0.0],
                          groupe_ajuste="g2")
        self.assertFalse(ko["interpretable"])          # m désaligné / < 2
        ko = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                          colonnes_g2=cols2, deltas=[], groupe_ajuste="g2")
        self.assertFalse(ko["interpretable"])
        ko = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                          colonnes_g2=cols2, deltas=[0.0],
                          groupe_ajuste="g3")
        self.assertFalse(ko["interpretable"])
        const = [[1.0] * 10 for _ in range(3)]
        ko = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=const,
                          colonnes_g2=const, deltas=[0.0],
                          groupe_ajuste="g2")
        self.assertFalse(ko["interpretable"])          # σ nul

    def test_determinisme(self):
        cols1, cols2 = _jeux_mnar()
        a = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                         colonnes_g2=cols2, deltas=[0.0, 0.5],
                         groupe_ajuste="g2")
        b = ops.executer("tipping_point_mnar_smd", 0, colonnes_g1=cols1,
                         colonnes_g2=cols2, deltas=[0.0, 0.5],
                         groupe_ajuste="g2")
        self.assertEqual(a, b)


class TestVerrouToctouControleur(unittest.TestCase):
    """Unicité des sorties exécutables : la PREMIÈRE sortie est la seule."""

    def _ctrl(self):
        return controller.ControleurExecution(seed=1)

    def test_ticket_sequentiel_et_reexecution_exacte(self):
        ctrl = self._ctrl()
        pts = _points_descendants()
        r1 = ctrl.executer(ops.tendance_fenetre_glissante,
                           "tendance_fenetre_glissante", "1.0.0",
                           points=pts, fenetre_mois=12, horizon_mois=6,
                           spec_limite=24.5, direction="inferieur")
        self.assertEqual(r1["_execution"]["exec_seq"], 1)
        self.assertNotIn("exec_rejoue", r1["_execution"])
        # même op, entrées différentes ⇒ nouveau ticket strictement croissant
        r2 = ctrl.executer(ops.tendance_fenetre_glissante,
                           "tendance_fenetre_glissante", "1.0.0",
                           points=pts, fenetre_mois=12, horizon_mois=6,
                           spec_limite=24.0, direction="inferieur")
        self.assertEqual(r2["_execution"]["exec_seq"], 2)
        self.assertEqual(
            [j["exec_seq"] for j in ctrl.journal], [1, 2])

    def test_meme_entrees_sert_la_premiere_sortie(self):
        ctrl = self._ctrl()
        pts = _points_descendants()
        kw = dict(points=pts, fenetre_mois=12, horizon_mois=6,
                  spec_limite=24.5, direction="inferieur")
        r1 = ctrl.executer(ops.tendance_fenetre_glissante,
                           "tendance_fenetre_glissante", "1.0.0", **kw)
        r2 = ctrl.executer(ops.tendance_fenetre_glissante,
                           "tendance_fenetre_glissante", "1.0.0", **kw)
        self.assertTrue(r2.get("_copie_memoire"))
        self.assertTrue(r2["_execution"]["exec_rejoue"])
        self.assertEqual(r2["_execution"]["exec_seq"],
                         r1["_execution"]["exec_seq"])
        # pas de nouvelle trace journalisée : une seule première sortie
        self.assertEqual(len(ctrl.journal), 1)
        nu = lambda r: {k: v for k, v in r.items()
                        if k not in ("_execution", "_copie_memoire")}
        self.assertEqual(nu(r2), nu(r1))

    def test_reexecution_divergente_detecte_toctou(self):
        ctrl = self._ctrl()
        source = {"base": 10.0}          # état externe mutable (toctou simulé)

        def op_toctou(seed: int = 0, **params):
            # op déterministe EN APPARENCE mais adossée à un état externe
            return {"interpretable": True,
                    "valeur": source["base"] + params["x"][0]}

        r1 = ctrl.executer(op_toctou, "op_mu", "0.0.1", x=[1.0])
        self.assertEqual(r1["valeur"], 11.0)
        source["base"] = 99.0            # modification ENTRE deux appels
        with self.assertRaisesRegex(ErreurLogique, "toctou"):
            ctrl.executer(op_toctou, "op_mu", "0.0.1", x=[1.0])

    def test_double_execution_divergente_bloque(self):
        ctrl = self._ctrl()
        etat = {"n": 0}

        def op_bavarde(seed: int = 0, **params):
            etat["n"] += 1
            return {"interpretable": True, "bruit": etat["n"]}

        with self.assertRaisesRegex(ErreurLogique, "double exécution"):
            ctrl.executer(op_bavarde, "op_mu2", "0.0.1", x=[1.0])


if __name__ == "__main__":
    unittest.main()
