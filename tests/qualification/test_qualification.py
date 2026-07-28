"""Qualification numérique du catalogue stats vs oracle scipy gelé.

La référence (`reference_scipy.py`, GÉNÉRÉE par `scripts/qualifier_scipy.py`)
contient des valeurs calculées par scipy 1.17.1 épinglé ou par des
ré-implémentations indépendantes des formules publiées (cf. en-tête du
générateur). Ces tests n'ont BESOIN QUE de la stdlib : scipy n'est jamais
importé ici — c'est l'oracle qui est gelé, pas la dépendance.

Tolérances (documentées dans docs/QUALIFICATION_SCIPY.md) — règle hybride :
    |mesuré − référence| ≤ max(TOL_ABS, TOL_REL × |référence|)
  • DIST_REL = 1e-10, DIST_ABS = 1e-12 : les écarts mesurés sur les grilles
    vont jusqu'à ~2,4e-12 en relatif (norm_ppf, Newton) et ~8e-14 en absolu
    dans les queues extrêmes (underflow du complément 1−cdf, p. ex.
    t_sf(30 ; 29) ≈ 1e-23 côté scipy, 0 côté stdlib) ;
  • OPS_REL = 1e-9, OPS_ABS = 1e-12 : écarts mesurés ≤ ~6e-14 en relatif ;
  • p-values D'ANDERSON-DARLING : NON comparées en valeur — scipy interpole
    des tables plafonnées à [0,01 ; 0,15], notre catalogue applique les
    formules de Marsaglia & Marsaglia sur la statistique corrigée de
    Stephens ; deux familles d'interpolation différentes. On qualifie donc :
    la statistique A² BRUTE (accord quasi bit-à-bit), le VERDICT à α = 5 %
    (identique à celui de scipy sur les deux jeux), et la cohérence interne
    verdict ↔ p de notre implémentation.
"""
from __future__ import annotations

import math
import unittest

from stats_catalogue import dist, ops
from tests.qualification.jeux import jeux
from tests.qualification.reference_scipy import REFERENCE

DIST_REL, DIST_ABS = 1e-10, 1e-12
OPS_REL, OPS_ABS = 1e-9, 1e-12
A2_REL = 1e-10

J = jeux()
D = REFERENCE["dist"]
O = REFERENCE["ops"]


def proche(mesure: float, reference: float, rel: float, tol_abs: float) -> bool:
    if isinstance(reference, bool) or isinstance(mesure, bool):
        return mesure is reference and mesure is not None
    return abs(mesure - reference) <= max(tol_abs, rel * abs(reference))


class TestReferenceSaine(unittest.TestCase):
    """Garde-fous sur l'oracle gelé lui-même (régression « nan gelé »)."""

    def _parcourir(self, obj, chemin="root"):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from self._parcourir(v, f"{chemin}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from self._parcourir(v, f"{chemin}[{i}]")
        elif isinstance(obj, float):
            yield chemin, obj

    def test_aucun_nan_ni_inf_gele(self):
        defauts = [c for c, v in self._parcourir(REFERENCE)
                   if math.isnan(v) or math.isinf(v)]
        self.assertEqual(defauts, [], f"nan/inf gelés : {defauts[:5]}")

    def test_meta_scipy_epinglee(self):
        self.assertEqual(REFERENCE["meta"]["scipy"], "1.17.1")

    def test_grilles_non_vides(self):
        self.assertEqual(len(D), 15)
        self.assertGreaterEqual(sum(len(v) for v in D.values()), 400)
        self.assertEqual(len(O), 23)


class TestDistQualification(unittest.TestCase):
    """Grille par grille, stats_catalogue/dist.py vs scipy."""

    def _grille(self, nom, notre, indices_args, indice_val, rel=DIST_REL,
                tol_abs=DIST_ABS):
        for i, point in enumerate(D[nom]):
            ref = point[indice_val]
            args = [point[j] for j in indices_args]
            mes = notre(*args)
            self.assertTrue(
                proche(mes, ref, rel, tol_abs),
                f"{nom}[{i}] {args} : mesuré={mes!r} réf={ref!r}")

    def test_norm_cdf(self):
        self._grille("norm_cdf", dist.norm_cdf, [0], 1)

    def test_norm_sf(self):
        self._grille("norm_sf", dist.norm_sf, [0], 1)

    def test_norm_ppf(self):
        self._grille("norm_ppf", dist.norm_ppf, [0], 1)

    def test_t_cdf(self):
        self._grille("t_cdf", dist.t_cdf, [0, 1], 2)

    def test_t_sf(self):
        self._grille("t_sf", dist.t_sf, [0, 1], 2)

    def test_t_ppf(self):
        self._grille("t_ppf", dist.t_ppf, [0, 1], 2)

    def test_chi2_cdf(self):
        self._grille("chi2_cdf", dist.chi2_cdf, [0, 1], 2)

    def test_chi2_sf(self):
        self._grille("chi2_sf", dist.chi2_sf, [0, 1], 2)

    def test_f_cdf(self):
        self._grille("f_cdf", dist.f_cdf, [0, 1, 2], 3)

    def test_f_sf(self):
        self._grille("f_sf", dist.f_sf, [0, 1, 2], 3)

    def test_binom_cdf(self):
        self._grille("binom_cdf", dist.binom_cdf, [0, 1, 2], 3)

    def test_beta_ppf(self):
        self._grille("beta_ppf", dist.beta_ppf, [0, 1, 2], 3)

    def test_incbeta(self):
        self._grille("incbeta", dist.incbeta, [0, 1, 2], 3)

    def test_gamma_p(self):
        self._grille("gamma_p", dist.gamma_p, [0, 1], 2)

    def test_gamma_q(self):
        self._grille("gamma_q", dist.gamma_q, [0, 1], 2)


class TestOpsQualification(unittest.TestCase):
    """Champ par champ, stats_catalogue/ops.py vs oracle (REPRIS du mesurage)."""

    def _champs(self, ref: dict, out: dict, paires, etiquette: str,
                rel=OPS_REL, tol_abs=OPS_ABS):
        for cle_ref, cle_out in paires:
            r, m = ref[cle_ref], out[cle_out]
            if isinstance(r, (list, tuple)):
                ms = list(m)
                self.assertEqual(len(ms), len(r), f"{etiquette}.{cle_out}")
                for j, (rj, mj) in enumerate(zip(r, ms)):
                    self.assertTrue(
                        proche(float(mj), float(rj), rel, tol_abs),
                        f"{etiquette}.{cle_out}[{j}] : mesuré={mj!r} "
                        f"réf={rj!r}")
            else:
                self.assertTrue(
                    proche(float(m), float(r), rel, tol_abs),
                    f"{etiquette}.{cle_out} : mesuré={m!r} réf={r!r}")

    def _ic(self, centre: float, tc: float, se: float, ic_mesure,
            etiquette: str, rel=OPS_REL, tol_abs=OPS_ABS):
        for borne, m in zip((centre - tc * se, centre + tc * se), ic_mesure):
            self.assertTrue(proche(float(m), borne, rel, tol_abs),
                            f"{etiquette} : mesuré={m!r} réf≈{borne!r}")

    # --- comparaisons de moyennes ------------------------------------------

    def test_t_welch(self):
        for nom in ("welch_egal", "welch_inegal"):
            ref = O[f"t_test_welch:{nom}"]
            out = ops.t_test_welch(J[nom]["g1"], J[nom]["g2"])
            self._champs(ref, out, [("t", "t"), ("ddl", "ddl"), ("se", "se"),
                                    ("p_valeur", "p_valeur"),
                                    ("moyenne1", "moyenne1"),
                                    ("moyenne2", "moyenne2")], nom)
            self._ic(ref["moyenne1"] - ref["moyenne2"], ref["tc95"],
                     ref["se"], out["ic95_difference"], f"{nom}.ic95")

    def test_tost(self):
        ref = O["tost_equivalence"]
        out = ops.tost_equivalence(J["tost"]["g1"], J["tost"]["g2"],
                                   J["tost"]["marge"])
        self._champs(ref, out, [("p_tost", "p_tost"), ("diff", "difference"),
                                ("se", "se"), ("ddl", "ddl")], "tost")
        self._ic(ref["diff"], ref["tc90"], ref["se"],
                 out["ic90_difference"], "tost.ic90")
        equivalence = ref["p_tost"] < 0.05
        self.assertEqual(out["verdict"] == "equivalence_demontree",
                         equivalence)

    # --- non paramétrique ----------------------------------------------------

    def test_mann_whitney(self):
        for nom in ("mw_ties", "mw_continu"):
            ref = O[f"mann_whitney:{nom}"]
            out = ops.mann_whitney(J[nom]["g1"], J[nom]["g2"])
            self._champs(ref, out, [("U1", "U1"), ("p_valeur", "p_valeur")],
                         nom)

    def test_kruskal_sans_ties(self):
        ref = O["kruskal_wallis:sans_ties"]
        out = ops.kruskal_wallis({"a": J["kruskal"]["a"],
                                  "b": J["kruskal"]["b"],
                                  "c": J["kruskal"]["c"]})
        self._champs(ref, out, [("statistique_h", "statistique_h"),
                                ("p_valeur", "p_valeur")], "kruskal")

    def test_kruskal_ties_corriges(self):
        ref = O["kruskal_wallis:ties_corriges"]
        out = ops.kruskal_wallis({"a": J["kruskal_ties"]["a"],
                                  "b": J["kruskal_ties"]["b"]})
        self._champs(ref, out, [("statistique_h", "statistique_h"),
                                ("p_valeur", "p_valeur")], "kruskal_ties")

    # --- tableaux 2x2 --------------------------------------------------------

    def test_tableaux_or_rr_rd_fisher(self):
        for nom in ("tab_2x2", "tab_2x2_zero", "tab_2x2_gros"):
            a, b, c, d = J[nom]
            ref = O[f"tableau:{nom}"]
            o = ops.odds_ratio_cas_temoins(a, b, c, d)
            self._champs(ref, o, [("or", "odds_ratio"),
                                  ("se_log_or", "se_log_or"),
                                  ("fisher_p", "p_valeur")], f"or:{nom}")
            self._champs({"ic": ref["ic95_or"]}, {"ic": o["ic95_or"]},
                         [("ic", "ic")], f"or_ic95:{nom}")
            r = ops.risque_relatif_cohorte(a, b, c, d)
            self._champs(ref, r, [("rr", "risque_relatif"),
                                  ("se_log_rr", "se_log_rr"),
                                  ("difference_risques", "difference_risques"),
                                  ("fisher_p", "p_valeur")], f"rr:{nom}")
            self._champs({"ic": ref["ic95_rr"]}, {"ic": r["ic95_rr"]},
                         [("ic", "ic")], f"rr_ic95:{nom}")
            self._champs({"ic": ref["ic95_diff"]},
                         {"ic": r["ic95_difference_risques"]},
                         [("ic", "ic")], f"rd_ic95:{nom}")
            f = ops.fisher_exact_2x2(a, b, c, d)
            self._champs(ref, f, [("fisher_p", "p_valeur")], f"fisher:{nom}")
            w1 = ops.proportion_wilson(a, a + b)
            w0 = ops.proportion_wilson(c, c + d)
            self._champs({"ic": ref["wilson_1"]}, {"ic": w1["ic95"]},
                         [("ic", "ic")], f"wilson1:{nom}")
            self._champs({"ic": ref["wilson_0"]}, {"ic": w0["ic95"]},
                         [("ic", "ic")], f"wilson0:{nom}")

    def test_mcnemar_et_or_apparie(self):
        for nom in ("discordants", "discordants_zero"):
            b, c = J[nom]
            ref = O[f"mcnemar:{nom}"]
            out = ops.mcnemar(b, c)
            self._champs(ref, out, [("p_valeur", "p_valeur")],
                         f"mcnemar:{nom}")
            oa = ops.or_apparie(b, c)
            self._champs(ref, oa, [("or", "odds_ratio"),
                                   ("se_log_or", "se_log_or"),
                                   ("p_valeur", "p_valeur")],
                         f"or_apparie:{nom}")
            self._champs({"ic": ref["ic95_or"]}, {"ic": oa["ic95_or"]},
                         [("ic", "ic")], f"or_app_ic95:{nom}")

    def test_chi2_independance(self):
        ref = O["chi2_independance:tab_3x3"]
        out = ops.chi2_independance(J["tab_3x3"])
        self.assertEqual(out["ddl"], ref["ddl"])
        self._champs(ref, out, [("statistique", "statistique"),
                                ("p_valeur", "p_valeur"),
                                ("cramers_v", "cramers_v")], "chi2_indep")

    # --- proportions ---------------------------------------------------------

    def test_clopper_pearson(self):
        for e in O["clopper_pearson"]:
            out = ops.proportion_exacte(e["k"], e["n"])
            self._champs({"lo": e["lo"], "hi": e["hi"]},
                         {"lo": out["ic95"][0], "hi": out["ic95"][1]},
                         [("lo", "lo"), ("hi", "hi")], f"cp({e['k']},{e['n']})")

    def test_wilson(self):
        for e in O["wilson"]:
            out = ops.proportion_wilson(e["k"], e["n"])
            self._champs({"lo": e["ic95"][0], "hi": e["ic95"][1]},
                         {"lo": out["ic95"][0], "hi": out["ic95"][1]},
                         [("lo", "lo"), ("hi", "hi")],
                         f"wilson({e['k']},{e['n']})")

    # --- tendance / stabilité --------------------------------------------------

    def test_tendance_lineaire(self):
        ref = O["tendance_lineaire"]
        out = ops.tendance_lineaire(J["tendance"]["t"], J["tendance"]["y"])
        self._champs(ref, out, [("pente", "pente"), ("intercept", "intercept"),
                                ("se_pente", "se_pente"),
                                ("p_valeur_pente", "p_valeur_pente"),
                                ("r2", "r2"), ("residus_sd", "residus_sd"),
                                ("prevision_tmax", "prevision_tmax")],
                     "tendance")
        self._champs({"ic": ref["ic95_pente"]}, {"ic": out["ic95_pente"]},
                     [("ic", "ic")], "tendance.ic95_pente")
        self._champs({"ic": ref["ic95_prevision_tmax"]},
                     {"ic": out["ic95_prevision_tmax"]},
                     [("ic", "ic")], "tendance.ic95_prev")

    # --- survie ------------------------------------------------------------------

    def test_km_logrank_hr(self):
        for nom in ("km_simple", "km_censure"):
            k, ref = J[nom], O[f"km_logrank_hr:{nom}"]
            out = ops.km_logrank_hr(k["t1"], k["e1"], k["t2"], k["e2"])
            self._champs(ref, out, [("E1", "E1"),
                                    ("variance_logrank", "variance_logrank"),
                                    ("chi2_logrank", "chi2_logrank"),
                                    ("p_valeur", "p_valeur"), ("hr", "hr")],
                         f"km:{nom}")
            self._champs({"ic": ref["ic95_hr"]}, {"ic": out["ic95_hr"]},
                         [("ic", "ic")], f"km_ic95:{nom}")
            self.assertEqual(float(out["mediane_survie_g1"]),
                             float(ref["mediane_g1"]))
            self.assertEqual(float(out["mediane_survie_g2"]),
                             float(ref["mediane_g2"]))
            self._champs({"s1": ref["survie_g1"], "s2": ref["survie_g2"]},
                         {"s1": out["survie_finale_g1"],
                          "s2": out["survie_finale_g2"]},
                         [("s1", "s1"), ("s2", "s2")], f"km_survie:{nom}")

    # --- imputation multiple -------------------------------------------------------

    def test_pooling_rubin(self):
        ref = O["pooling_rubin"]
        out = ops.pooling_rubin(J["rubin"])
        self._champs(ref, out, [("theta_pooled", "theta_pooled"),
                                ("se_pooled", "se_pooled"),
                                ("variance_intra", "variance_intra"),
                                ("variance_inter", "variance_inter"),
                                ("augmentation_relative",
                                 "augmentation_relative"),
                                ("ddl", "ddl"),
                                ("fraction_info_manquante",
                                 "fraction_info_manquante"),
                                ("p_valeur", "p_valeur")], "rubin")
        self._ic(ref["theta_pooled"], ref["tc95"], ref["se_pooled"],
                 out["ic95"], "rubin.ic95")

    # --- descriptif / smd ------------------------------------------------------------

    def test_descriptif(self):
        ref = O["descriptif_continu"]
        out = ops.descriptif_continu(J["descriptif"])
        self._champs(ref, out, [("moyenne", "moyenne"),
                                ("ecart_type", "ecart_type"), ("q1", "q1"),
                                ("median", "median"), ("q3", "q3"),
                                ("se", "se")], "descriptif")

    def test_smd(self):
        ref = O["smd_groupes"]
        out = ops.smd_groupes(J["smd"]["g1"], J["smd"]["g2"])
        self._champs(ref, out, [("smd", "smd")], "smd")

    # --- normalité (Anderson-Darling) — règles spécifiques, cf. docstring -------------

    def test_normalite_a2_brute_quasi_bit_a_bit(self):
        for nom in ("normalite_ok", "normalite_ko"):
            ref = O[f"test_normalite:{nom}"]
            out = ops.test_normalite(J[nom])
            self.assertTrue(
                proche(out["statistique_a2_brute"],
                       ref["statistique_a2_brute"], A2_REL, 1e-12),
                f"{nom} A² brute : {out['statistique_a2_brute']!r} vs "
                f"{ref['statistique_a2_brute']!r}")

    def test_normalite_verdicts_concordants_scipy(self):
        for nom in ("normalite_ok", "normalite_ko"):
            ref = O[f"test_normalite:{nom}"]
            out = ops.test_normalite(J[nom])
            self.assertEqual(out["verdict"], ref["verdict_scipy"], nom)

    def test_normalite_coherence_interne_p_verdict(self):
        for nom in ("normalite_ok", "normalite_ko"):
            out = ops.test_normalite(J[nom])
            self.assertTrue(0.0 < out["p_valeur"] < 1.0)
            self.assertEqual(out["verdict"] == "normal_ok",
                             out["p_valeur"] >= 0.05, nom)


if __name__ == "__main__":
    unittest.main()
