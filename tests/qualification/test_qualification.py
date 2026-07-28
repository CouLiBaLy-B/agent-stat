"""Qualification numérique du catalogue contre scipy 1.17.1 (oracle gelé).

La référence `tests/qualification/reference_scipy.py` a été générée UNE FOIS
par `scripts/qualifier_scipy.py` (scipy 1.17.1 épinglé dans REFERENCE["meta"])
et gelée : scipy n'est importé NI par ce test NI par le pipeline — le produit
livré reste 100 % stdlib. Entrées : `tests/qualification/jeux.py` (partagées
avec le générateur ; toute modification ⇒ régénération de l'oracle).

Tolérances hybrides (écart ≤ max(ABS, REL × |référence|) ; mesurées puis
fixées avec marge — détail dans docs/QUALIFICATION_SCIPY.md) :
- DIST : REL 1e-10 / ABS 1e-12 — écarts mesurés ≤ 2,4e-12 relatif
  (inversions Newton ppf) ; les queues < 1e-14 ne sont qualifiées qu'en
  absolu (underflow du complément 1 − Φ, documenté) ;
- OPS : REL 1e-9 / ABS 1e-12 — écarts mesurés ≤ 5,6e-14, sauf p OLS
  (1,9e-12 absolue — couverte par ABS) et r2 OLS (3,2e-6 relatif — couvert)
  documentés ;
- NORMALITÉ : statistique A² brute quasi bit-à-bit (≤ 4e-14) ; les p-values
  ne sont VOLONTAIREMENT PAS comparées en valeur : scipy renvoie une valeur
  interpolée entre tableaux (plafonnée [0,01 ; 0,15]) quand l'op donne les
  formules de Marsaglia sur A²* corrigé (Stephens, n ≤ 200). Deux familles
  d'interpolation, deux échelles : on qualifie A² + VERDICT à 5 %
  (concordants sur les deux jeux), pas la valeur.

Les jeux de normalité doivent rester interprétables par les deux familles
(variance non nulle, queue modérée) : un jeu dégénéré donnerait « nan »
côté scipy — la garde `test_reference_sans_nan_inf` verrouille cela.

L'ajustement multivarié (régression logistique, Cox PH) est qualifié dans
`tests/test_ajustement.py` contre les paquets `regression_logistique:` et
`cox_ph:` de la même référence (oracles BFGS indépendants).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stats_catalogue import dist                      # noqa: E402
from stats_catalogue import ops                       # noqa: E402
from tests.qualification import reference_scipy as REF  # noqa: E402
from tests.qualification.jeux import jeux             # noqa: E402

DIST_REL, DIST_ABS = 1e-10, 1e-12
OPS_REL, OPS_ABS = 1e-9, 1e-12
A2_REL = 1e-10

J = jeux()


def proche(ref: float, nous: float, rel: float, absol: float) -> bool:
    """Règle hybride : |écart| ≤ max(absol, rel × |référence|)."""
    return abs(float(ref) - float(nous)) <= max(absol, rel * abs(float(ref)))


class TestReferenceSaine(unittest.TestCase):
    """La référence gelée doit être complète, épinglée et sans nan/inf."""

    def test_meta_epinglee(self):
        self.assertEqual(REF.REFERENCE["meta"]["scipy"], "1.17.1")
        self.assertEqual(REF.REFERENCE["meta"]["generateur"],
                         "scripts/qualifier_scipy.py")
        self.assertGreaterEqual(len(REF.REFERENCE["dist"]), 15)
        self.assertGreaterEqual(len(REF.REFERENCE["ops"]), 15)

    def test_reference_sans_nan_inf(self):
        def parcours(o, chemin="reference"):
            if isinstance(o, float):
                self.assertNotIn(str(o), ("nan", "inf", "-inf"),
                                 f"{chemin} = {o} — référence gelée invalide")
            elif isinstance(o, dict):
                for k, v in o.items():
                    parcours(v, f"{chemin}.{k}")
            elif isinstance(o, (list, tuple)):
                for i, v in enumerate(o):
                    parcours(v, f"{chemin}[{i}]")
        parcours({"dist": REF.REFERENCE["dist"],
                  "ops": REF.REFERENCE["ops"]})


class TestDistQualification(unittest.TestCase):
    def proche_dist(self, ref, nous, quoi):
        self.assertTrue(proche(ref, nous, DIST_REL, DIST_ABS),
                        f"{quoi}: ref={ref!r} vs nous={nous!r}")

    def test_norm_cdf_sf_ppf(self):
        for x, ref in REF.REFERENCE["dist"]["norm_cdf"]:
            self.proche_dist(ref, dist.norm_cdf(x), f"norm_cdf({x})")
        for x, ref in REF.REFERENCE["dist"]["norm_sf"]:
            self.proche_dist(ref, dist.norm_sf(x), f"norm_sf({x})")
        for p, ref in REF.REFERENCE["dist"]["norm_ppf"]:
            self.proche_dist(ref, dist.norm_ppf(p), f"norm_ppf({p})")

    def test_t_cdf_sf_ppf(self):
        for t, d, ref in REF.REFERENCE["dist"]["t_cdf"]:
            self.proche_dist(ref, dist.t_cdf(t, d), f"t_cdf({t}, {d})")
        for t, d, ref in REF.REFERENCE["dist"]["t_sf"]:
            self.proche_dist(ref, dist.t_sf(t, d), f"t_sf({t}, {d})")
        for p, d, ref in REF.REFERENCE["dist"]["t_ppf"]:
            self.proche_dist(ref, dist.t_ppf(p, d), f"t_ppf({p}, {d})")

    def test_chi2_cdf_sf(self):
        for x, d, ref in REF.REFERENCE["dist"]["chi2_cdf"]:
            self.proche_dist(ref, dist.chi2_cdf(x, d), f"chi2_cdf({x}, {d})")
        for x, d, ref in REF.REFERENCE["dist"]["chi2_sf"]:
            self.proche_dist(ref, dist.chi2_sf(x, d), f"chi2_sf({x}, {d})")

    def test_f_cdf_sf(self):
        for x, d1, d2, ref in REF.REFERENCE["dist"]["f_cdf"]:
            self.proche_dist(ref, dist.f_cdf(x, d1, d2),
                             f"f_cdf({x}, {d1}, {d2})")
        for x, d1, d2, ref in REF.REFERENCE["dist"]["f_sf"]:
            self.proche_dist(ref, dist.f_sf(x, d1, d2),
                             f"f_sf({x}, {d1}, {d2})")

    def test_binom_cdf(self):
        for k, n, p, ref in REF.REFERENCE["dist"]["binom_cdf"]:
            self.proche_dist(ref, dist.binom_cdf(k, n, p),
                             f"binom_cdf({k}, {n}, {p})")

    def test_beta_incomplete(self):
        for a, b, x, ref in REF.REFERENCE["dist"]["incbeta"]:
            self.proche_dist(ref, dist.incbeta(a, b, x),
                             f"incbeta({a}, {b}, {x})")
        for p, a, b, ref in REF.REFERENCE["dist"]["beta_ppf"]:
            self.proche_dist(ref, dist.beta_ppf(p, a, b),
                             f"beta_ppf({p}, {a}, {b})")

    def test_gamma_incomplete(self):
        for a, x, ref in REF.REFERENCE["dist"]["gamma_p"]:
            self.proche_dist(ref, dist.gamma_p(a, x), f"gamma_p({a}, {x})")
        for a, x, ref in REF.REFERENCE["dist"]["gamma_q"]:
            self.proche_dist(ref, dist.gamma_q(a, x), f"gamma_q({a}, {x})")


def proches_entrees(ref: dict, executable: dict,
                    correspondance: dict[str, str], quoi: str,
                    rel: float = OPS_REL, absol: float = OPS_ABS) -> list:
    """Compare des champs catalogue ↔ référence côte à côte ; liste d'écarts."""
    ecarts = []
    for clef_cat, clef_ref in correspondance.items():
        a, b = executable.get(clef_cat), ref.get(clef_ref)
        if a is None or b is None:
            raise AssertionError(f"{quoi}: champ manquant "
                                 f"({clef_cat!r}/{clef_ref!r})")
        if isinstance(a, (list, tuple)):
            for i, (x, y) in enumerate(zip(list(a), list(b))):
                if not proche(y, x, rel, absol):
                    ecarts.append((f"{quoi}.{clef_cat}[{i}]", y, x))
        elif not proche(b, a, rel, absol):
            ecarts.append((f"{quoi}.{clef_cat}", b, a))
    return ecarts


class TestOpsQualification(unittest.TestCase):
    def ops(self, clef):
        return REF.REFERENCE["ops"][clef]

    def test_t_test_welch(self):
        for jeu in ("welch_egal", "welch_inegal"):
            ref = self.ops(f"t_test_welch:{jeu}")
            nous = ops.t_test_welch(J[jeu]["g1"], J[jeu]["g2"])
            ecarts = proches_entrees(
                ref, nous,
                {"t": "t", "ddl": "ddl", "se": "se", "p_valeur": "p_valeur",
                 "moyenne1": "moyenne1", "moyenne2": "moyenne2"},
                f"welch_{jeu}")
            # IC95 recombiné depuis l'oracle (tc95 qualifié via t_ppf ci-dessus)
            diff = ref["moyenne1"] - ref["moyenne2"]
            for i, borne_ref in enumerate(
                    (diff - ref["tc95"] * ref["se"],
                     diff + ref["tc95"] * ref["se"])):
                if not proche(borne_ref, nous["ic95_difference"][i],
                              OPS_REL, OPS_ABS):
                    ecarts.append((f"welch_{jeu}.ic95[{i}]", borne_ref,
                                   nous["ic95_difference"][i]))
            self.assertEqual(ecarts, [])

    def test_tost_equivalence(self):
        ref = self.ops("tost_equivalence")
        nous = ops.tost_equivalence(J["tost"]["g1"], J["tost"]["g2"],
                                    J["tost"]["marge"])
        ecarts = proches_entrees(
            ref, nous,
            {"p_tost": "p_tost", "difference": "diff", "se": "se",
             "ddl": "ddl"}, "tost")
        for i, borne_ref in enumerate(
                (ref["diff"] - ref["tc90"] * ref["se"],
                 ref["diff"] + ref["tc90"] * ref["se"])):
            if not proche(borne_ref, nous["ic90_difference"][i],
                          OPS_REL, OPS_ABS):
                ecarts.append((f"tost.ic90[{i}]", borne_ref,
                               nous["ic90_difference"][i]))
        self.assertEqual(ecarts, [])

    def test_mann_whitney(self):
        for jeu in ("mw_ties", "mw_continu"):
            ref = self.ops(f"mann_whitney:{jeu}")
            nous = ops.mann_whitney(J[jeu]["g1"], J[jeu]["g2"])
            ecarts = proches_entrees(
                ref, nous, {"U1": "U1", "p_valeur": "p_valeur"}, jeu)
            self.assertEqual(ecarts, [])

    def test_kruskal_wallis(self):
        # l'op applique TOUJOURS la correction d'ex æquo (neutre sans ties)
        avec_ties = {"kruskal_wallis:sans_ties": False,
                     "kruskal_wallis:ties_corriges": True}
        for clef, ties in avec_ties.items():
            ref = self.ops(clef)
            jeu = "kruskal_ties" if ties else "kruskal"
            nous = ops.kruskal_wallis(J[jeu])
            ecarts = proches_entrees(
                ref, nous,
                {"statistique_h": "statistique_h", "p_valeur": "p_valeur"},
                clef)
            self.assertEqual(ecarts, [])

    def test_tableau_2x2(self):
        for jeu in ("tab_2x2", "tab_2x2_zero", "tab_2x2_gros"):
            a, b, c, d = J[jeu]
            ref = self.ops(f"tableau:{jeu}")
            or_cat = ops.odds_ratio_cas_temoins(a, b, c, d)
            ecarts = proches_entrees(
                ref, or_cat,
                {"odds_ratio": "or", "ic95_or": "ic95_or",
                 "se_log_or": "se_log_or", "p_valeur": "fisher_p"},
                f"{jeu}.or")
            rr_cat = ops.risque_relatif_cohorte(a, b, c, d)
            ecarts += proches_entrees(
                ref, rr_cat,
                {"risque_relatif": "rr", "ic95_rr": "ic95_rr",
                 "se_log_rr": "se_log_rr",
                 "difference_risques": "difference_risques",
                 "ic95_difference_risques": "ic95_diff",
                 "p_valeur": "fisher_p"},
                f"{jeu}.rr")
            f_cat = ops.fisher_exact_2x2(a, b, c, d)
            ecarts += proches_entrees(
                ref, f_cat, {"p_valeur": "fisher_p"}, f"{jeu}.fisher")
            w1 = ops.proportion_wilson(a, a + b)
            ecarts += proches_entrees(ref, w1, {"ic95": "wilson_1"},
                                      f"{jeu}.wilson_1")
            w0 = ops.proportion_wilson(c, c + d)
            ecarts += proches_entrees(ref, w0, {"ic95": "wilson_0"},
                                      f"{jeu}.wilson_0")
            self.assertEqual(ecarts, [])

    def test_mcnemar_et_or_apparie(self):
        for jeu in ("discordants", "discordants_zero"):
            b, c = J[jeu]
            ref = self.ops(f"mcnemar:{jeu}")
            m = ops.mcnemar(b, c)
            ecarts = proches_entrees(ref, m, {"p_valeur": "p_valeur"},
                                     f"{jeu}.mcnemar")
            o = ops.or_apparie(b, c)
            ecarts += proches_entrees(
                ref, o,
                {"odds_ratio": "or", "ic95_or": "ic95_or",
                 "se_log_or": "se_log_or", "p_valeur": "p_valeur"},
                f"{jeu}.or_apparie")
            self.assertEqual(ecarts, [])

    def test_chi2_independance(self):
        ref = self.ops("chi2_independance:tab_3x3")
        nous = ops.chi2_independance(J["tab_3x3"])
        ecarts = proches_entrees(
            ref, nous,
            {"statistique": "statistique", "ddl": "ddl",
             "p_valeur": "p_valeur", "cramers_v": "cramers_v"},
            "chi2_indep")
        self.assertEqual(ecarts, [])

    def test_proportions_exactes(self):
        for ligne in self.ops("clopper_pearson"):
            nous = ops.proportion_exacte(ligne["k"], ligne["n"])
            lo, hi = nous["ic95"]
            self.assertTrue(proche(ligne["lo"], lo, OPS_REL, OPS_ABS),
                            f"cp({ligne['k']},{ligne['n']}).lo : ref="
                            f"{ligne['lo']!r} vs nous={lo!r}")
            self.assertTrue(proche(ligne["hi"], hi, OPS_REL, OPS_ABS),
                            f"cp({ligne['k']},{ligne['n']}).hi : ref="
                            f"{ligne['hi']!r} vs nous={hi!r}")

    def test_proportions_wilson(self):
        for ligne in self.ops("wilson"):
            nous = ops.proportion_wilson(ligne["k"], ligne["n"])
            for i in (0, 1):
                self.assertTrue(proche(ligne["ic95"][i], nous["ic95"][i],
                                       OPS_REL, OPS_ABS),
                                f"wilson({ligne['k']},{ligne['n']}).ic95[{i}]"
                                f" : ref={ligne['ic95'][i]!r} vs "
                                f"nous={nous['ic95'][i]!r}")

    def test_tendance_lineaire_ols(self):
        ref = self.ops("tendance_lineaire")
        nous = ops.tendance_lineaire(J["tendance"]["t"], J["tendance"]["y"])
        ecarts = proches_entrees(
            ref, nous,
            {"pente": "pente", "intercept": "intercept",
             "se_pente": "se_pente", "p_valeur_pente": "p_valeur_pente",
             "r2": "r2", "ic95_pente": "ic95_pente",
             "residus_sd": "residus_sd",
             "prevision_tmax": "prevision_tmax",
             "ic95_prevision_tmax": "ic95_prevision_tmax"},
            "ols")
        self.assertEqual(ecarts, [])

    def test_km_logrank_hr(self):
        for jeu in ("km_simple", "km_censure"):
            ref = self.ops(f"km_logrank_hr:{jeu}")
            nous = ops.km_logrank_hr(J[jeu]["t1"], J[jeu]["e1"],
                                     J[jeu]["t2"], J[jeu]["e2"])
            ecarts = proches_entrees(
                ref, nous,
                {"E1": "E1", "variance_logrank": "variance_logrank",
                 "chi2_logrank": "chi2_logrank", "p_valeur": "p_valeur",
                 "hr": "hr", "ic95_hr": "ic95_hr",
                 "mediane_survie_g1": "mediane_g1",
                 "mediane_survie_g2": "mediane_g2",
                 "survie_finale_g1": "survie_g1",
                 "survie_finale_g2": "survie_g2"},
                jeu)
            self.assertEqual(ecarts, [])

    def test_pooling_rubin(self):
        ref = self.ops("pooling_rubin")
        nous = ops.pooling_rubin(J["rubin"])
        ecarts = proches_entrees(
            ref, nous,
            {"theta_pooled": "theta_pooled", "se_pooled": "se_pooled",
             "variance_intra": "variance_intra",
             "variance_inter": "variance_inter",
             "augmentation_relative": "augmentation_relative", "ddl": "ddl",
             "fraction_info_manquante": "fraction_info_manquante",
             "p_valeur": "p_valeur"},
            "rubin")
        for i, borne_ref in enumerate(
                (ref["theta_pooled"] - ref["tc95"] * ref["se_pooled"],
                 ref["theta_pooled"] + ref["tc95"] * ref["se_pooled"])):
            if not proche(borne_ref, nous["ic95"][i], OPS_REL, OPS_ABS):
                ecarts.append((f"rubin.ic95[{i}]", borne_ref,
                               nous["ic95"][i]))
        self.assertEqual(ecarts, [])

    def test_descriptif_continu(self):
        ref = self.ops("descriptif_continu")
        nous = ops.descriptif_continu(J["descriptif"])
        ecarts = proches_entrees(
            ref, nous,
            {"moyenne": "moyenne", "ecart_type": "ecart_type",
             "q1": "q1", "median": "median", "q3": "q3", "se": "se"},
            "descriptif")
        self.assertEqual(ecarts, [])

    def test_smd_groupes(self):
        ref = self.ops("smd_groupes")
        nous = ops.smd_groupes(J["smd"]["g1"], J["smd"]["g2"])
        ecarts = proches_entrees(ref, nous, {"smd": "smd"}, "smd")
        self.assertEqual(ecarts, [])


class TestNormaliteQualification(unittest.TestCase):
    """Famille à part : A² brute quasi bit-à-bit + VERDICTS concordants.

    La p-value n'est PAS comparée camp par camp (assumé, documenté) : scipy
    interpole entre tableaux (plafonnée [0,01 ; 0,15]) quand l'op donne les
    formules de Marsaglia sur A²* corrigé (Stephens).
    """

    def _assert_a2_et_verdict(self, jeu):
        ref = REF.REFERENCE["ops"][f"test_normalite:{jeu}"]
        nous = ops.test_normalite(J[jeu])
        self.assertTrue(proche(ref["statistique_a2_brute"],
                               nous["statistique_a2_brute"], A2_REL, 1e-12),
                        f"{jeu}: A² brute ref={ref['statistique_a2_brute']!r}"
                        f" vs nous={nous['statistique_a2_brute']!r}")
        self.assertEqual(ref["verdict_scipy"], nous["verdict"],
                         f"{jeu}: verdict scipy={ref['verdict_scipy']} vs "
                         f"nous={nous['verdict']} — discordance à 5 %")
        return nous

    def test_normalite_ok(self):
        nous = self._assert_a2_et_verdict("normalite_ok")
        self.assertEqual(nous["verdict"], "normal_ok")
        self.assertGreaterEqual(nous["p_valeur"], 0.05)

    def test_normalite_ko(self):
        nous = self._assert_a2_et_verdict("normalite_ko")
        self.assertEqual(nous["verdict"], "non_normal")
        self.assertLess(nous["p_valeur"], 0.05)


if __name__ == "__main__":
    unittest.main()
