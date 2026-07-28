"""Tests de câblage pipeline des analyses de sensibilité PRÉ-DÉCLARÉES.

- gabarits : item ST-SENS (stabilité) et A4 MNAR (usage 2 groupes) ajoutés
  au SAP uniquement quand la spec les déclare, avec gardes fail-closed ;
- schéma LLM ANALYSE_ITEM : rôle 'sensibilite' + paramètres bornés
  (fenetre/horizon/seuil/direction, deltas/groupe_mnar) ;
- contre-vérification métier : omission, déviation, sensibilité post-hoc
  ou rôle incohérent ⇒ rejet (repli gabarit, jamais de dérive) ;
- inférentiel : seuil/sens du franchissement DÉDUITS DE FAÇON DÉTERMINISTE
  des bornes d'acceptation ; ruban MNAR exécuté sur copies PMM alignées,
  « sans objet » fail-closed sans imputation ; relecture recalcule A4 ;
- renversement d'effet : marqué (`renverse`, `delta_renversement`) quand
  la pénalisation traverse zéro — jamais de « re-significativité » trompeuse.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_impl import analyses as ag_analyses            # noqa: E402
from agents_impl import biostat                            # noqa: E402
from core.exceptions import ErreurLogique                  # noqa: E402
from core.state import Etat                                # noqa: E402
from core.store import StoreArtefacts                      # noqa: E402
from demo.jeu_donnees import DECISIONS_OK                  # noqa: E402
from demo.jeu_donnees import generer as generer_usage      # noqa: E402
from demo.jeu_stabilite import generer_stabilite           # noqa: E402
from llm import schemas, validation                        # noqa: E402
from orchestration.pipeline import run_pipeline            # noqa: E402
from stats_catalogue import ops                            # noqa: E402
from tests.outillage import environnement                  # noqa: E402


def _etat(study: str, run_id: str = "run-2026-07-28-0300") -> Etat:
    return Etat(run_id=run_id, study_id=study,
                domaine="cosmetique", seed=20260728)


def _spec_stabilite(**extra):
    spec = {"endpoint_principal": "ph", "var_temps": "mois",
            "bornes_acceptation": {"ph": [5.0, 7.0]},
            "variables": {"ph": {"type": "continue"},
                          "mois": {"type": "continue"}}}
    spec.update(extra)
    return spec


def _spec_usage(**extra):
    donnees = generer_usage()
    spec = dict(donnees["spec"])
    spec.update(extra)
    return spec


# ----------------------------------------------------------------------- SAP


class TestGabaritStabiliteSensibilite(unittest.TestCase):
    def test_item_sens_present_et_parametres_par_defaut(self):
        ana = biostat._gabarit_stabilite(_spec_stabilite(), {})
        sens = [a for a in ana if a["role"] == "sensibilite"]
        self.assertEqual(len(sens), 1)
        self.assertEqual(sens[0]["id"], "ST-SENS-ph")
        self.assertEqual(sens[0]["op"], "tendance_fenetre_glissante")
        self.assertEqual(sens[0]["fenetre_mois"], 12.0)
        self.assertEqual(sens[0]["horizon_mois"], 6.0)
        self.assertEqual(sens[0]["par_temps"], "mois")
        # seuil/direction NON figés au gabarit — déduits à l'exécution
        self.assertNotIn("spec_limite", sens[0])
        self.assertNotIn("direction", sens[0])

    def test_parametres_surcharges_via_spec(self):
        ana = biostat._gabarit_stabilite(_spec_stabilite(
            sensibilite_stabilite={"fenetre_mois": 6, "horizon_mois": 3}), {})
        sens = [a for a in ana if a["role"] == "sensibilite"][0]
        self.assertEqual(sens["fenetre_mois"], 6.0)
        self.assertEqual(sens["horizon_mois"], 3.0)

    def test_gardes_fail_closed(self):
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_stabilite(_spec_stabilite(
                sensibilite_stabilite={"fenetre_mois": 0}), {})
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_stabilite(_spec_stabilite(
                sensibilite_stabilite={"horizon_mois": 18}), {})
        # régression : bornes/var_temps toujours exigés
        spec = _spec_stabilite()
        del spec["var_temps"]
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_stabilite(spec, {})


class TestGabaritMnarSensibilite(unittest.TestCase):
    def test_absent_sans_declaration(self):
        ana = biostat._gabarit_usage_cosmetique(_spec_usage(), {})
        self.assertNotIn("tipping_point_mnar_smd",
                         {a["op"] for a in ana})

    def test_present_quand_declare(self):
        ana = biostat._gabarit_usage_cosmetique(_spec_usage(
            sensibilite_mnar_smd={"deltas": [0.5, 0.0, 0.25],
                                  "groupe": "controle"}), {})
        a4 = [a for a in ana if a["role"] == "sensibilite"]
        self.assertEqual(len(a4), 1)
        self.assertEqual(a4[0]["id"], "A4")
        self.assertEqual(a4[0]["op"], "tipping_point_mnar_smd")
        self.assertEqual(a4[0]["deltas"], [0.0, 0.25, 0.5])   # triée
        self.assertEqual(a4[0]["groupe_mnar"], "controle")
        self.assertEqual(a4[0]["var"], "delta_score")

    def test_gardes_fail_closed(self):
        base = _spec_usage()
        for bloc, extrait in (
                ({"deltas": [], "groupe": "produit"}, "non vide"),
                ({"deltas": [0.0] * 17, "groupe": "produit"}, "≤ 16"),
                ({"deltas": [0.0, 5.5], "groupe": "produit"}, "[0, 5]"),
                ({"deltas": [-0.1, 0.0], "groupe": "produit"}, "[0, 5]"),
                ({"deltas": [0.25, 0.5], "groupe": "produit"}, "0.0"),
                ({"deltas": [0.0, 0.5], "groupe": "placebo"}, "contraste")):
            with self.assertRaises(ErreurLogique) as ctx:
                biostat._gabarit_usage_cosmetique(
                    {**base, "sensibilite_mnar_smd": bloc}, {})
            self.assertIn(extrait, str(ctx.exception))


# ------------------------------------------------------------------- schéma


class TestSchemaSensibilite(unittest.TestCase):
    def _item(self, **kw):
        it = {"id": "A4", "role": "sensibilite",
              "op": "tipping_point_mnar_smd", "var": "delta_score"}
        it.update(kw)
        return it

    def test_item_mnar_valide(self):
        obj = {"analyses": [self._item(deltas=[0.0, 0.5],
                                       groupe_mnar="produit")],
               "gestion_multiplicite": "gatekeeping primaire d'abord",
               "gestion_manquants_strategie": "MI PMM m≥20 + ruban MNAR"}
        self.assertEqual(validation.valider(schemas.PROPOSITION_SAP, obj), [])

    def test_item_stabilite_valide(self):
        obj = {"analyses": [
            {"id": "A3", "role": "sensibilite",
             "op": "tendance_fenetre_glissante", "var": "ph",
             "par_temps": "mois", "fenetre_mois": 12, "horizon_mois": 6}],
            "gestion_multiplicite": "gatekeeping primaire d'abord",
            "gestion_manquants_strategie": "cas complets (série exhaustive)"}
        self.assertEqual(validation.valider(schemas.PROPOSITION_SAP, obj), [])

    def test_bornes_schema(self):
        for champ, mauvais in (("horizon_mois", 13), ("deltas", [])):
            obj = {"analyses": [self._item(**{champ: mauvais})],
                   "gestion_multiplicite": "gatekeeping primaire d'abord",
                   "gestion_manquants_strategie": "stratégie quelconque ok"}
            self.assertTrue(validation.valider(schemas.PROPOSITION_SAP, obj),
                            f"{champ}={mauvais} aurait dû être rejeté")

    def test_cle_inconnue_toujours_rejetee(self):
        obj = {"analyses": [self._item(sigma_ref=5.0)],
               "gestion_multiplicite": "gatekeeping primaire d'abord",
               "gestion_manquants_strategie": "stratégie quelconque ok"}
        self.assertTrue(validation.valider(schemas.PROPOSITION_SAP, obj))


# -------------------------------------------------------- contre-vérification


class TestReglesMetierSensibilite(unittest.TestCase):
    def _prop(self, op, **kw):
        base = _spec_usage()
        a = {"id": "A4", "role": "sensibilite", "op": op,
             "var": "delta_score"}
        a.update(kw)
        return base, [{"id": "A1", "role": "primaire", "op": "t_test_welch",
                       "var": "delta_score",
                       "fallback": {"si": "non_normal", "op": "mann_whitney"}},
                      a]

    def test_mnar_sans_declaration_rejete(self):
        spec, prop = self._prop("tipping_point_mnar_smd",
                                deltas=[0.0, 0.5], groupe_mnar="produit")
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(prop, spec)

    def test_mnar_declaree_omise_rejete(self):
        spec = _spec_usage(sensibilite_mnar_smd={"deltas": [0.0, 0.5],
                                                 "groupe": "produit"})
        prop = [{"id": "A1", "role": "primaire", "op": "t_test_welch",
                 "var": "delta_score",
                 "fallback": {"si": "non_normal", "op": "mann_whitney"}}]
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(prop, spec)

    def test_mnar_declaree_deviee_rejete(self):
        spec, prop = self._prop("tipping_point_mnar_smd",
                                deltas=[0.0, 1.0], groupe_mnar="produit")
        spec["sensibilite_mnar_smd"] = {"deltas": [0.0, 0.5],
                                        "groupe": "produit"}
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(prop, spec)

    def test_mnar_declaree_conforme_acceptee(self):
        spec, prop = self._prop("tipping_point_mnar_smd",
                                deltas=[0.5, 0.0], groupe_mnar="produit")
        spec["sensibilite_mnar_smd"] = {"deltas": [0.0, 0.5],
                                        "groupe": "produit"}
        biostat._verifier_regles_metier(prop, spec)   # ne doit pas lever

    def test_role_sensibilite_incoherent_rejete(self):
        spec = _spec_usage()
        prop = [{"id": "A1", "role": "primaire", "op": "t_test_welch",
                 "var": "delta_score",
                 "fallback": {"si": "non_normal", "op": "mann_whitney"}},
                {"id": "A4", "role": "sensibilite", "op": "descriptif_continu",
                 "var": "delta_score"}]
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(prop, spec)
        prop[1] = {"id": "A4", "role": "primaire",  # 2e primaire déguisée
                   "op": "tipping_point_mnar_smd", "var": "delta_score",
                   "deltas": [0.0], "groupe_mnar": "produit"}
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(prop, spec)

    def test_stabilite_parametres_invalides_rejetes(self):
        spec = _spec_stabilite()
        for mauvais in ({"fenetre_mois": 0, "horizon_mois": 6},
                        {"fenetre_mois": 12, "horizon_mois": 18},
                        {"fenetre_mois": 12, "horizon_mois": 6,
                         "spec_limite": 5.0},          # direction absente
                        {"fenetre_mois": 12, "horizon_mois": 6,
                         "direction": "inferieur"}):    # seuil absent
            prop = [{"id": "A1", "role": "primaire", "op": "tendance_lineaire",
                     "var": "ph", "par_temps": "mois"},
                    dict({"id": "A2", "role": "sensibilite",
                          "op": "tendance_fenetre_glissante", "var": "ph",
                          "par_temps": "mois"}, **mauvais)]
            with self.assertRaises(ErreurLogique):
                biostat._verifier_regles_metier(prop, spec)


# --------------------------------------------------- déduction seuil / sens


class TestDeductionSeuil(unittest.TestCase):
    def test_descendante_plate_ascendante(self):
        d = ag_analyses.deduire_seuil_franchissement
        self.assertEqual(d([(0, 6.0), (12, 5.6)], 5.0, 7.0), (5.0, "inferieur"))
        self.assertEqual(d([(0, 6.0), (12, 6.4)], 5.0, 7.0), (7.0, "superieur"))
        self.assertEqual(d([(0, 6.2), (12, 6.2)], 5.0, 7.0), (7.0, "superieur"))
        self.assertEqual(d([(0, 5.4), (12, 5.4)], 5.0, 7.0), (5.0, "inferieur"))
        with self.assertRaises(ErreurLogique):
            d([(0, 6.0)], 5.0, 7.0)


# ---------------------------------------------------------------- pipeline


class TestInferentielSensibilite(unittest.TestCase):
    def _lance(self, donnees, study):
        """Pipeline complet + artefacts lus AVANT la fin du tempdir (les
        objets du store sont volatils par conception — tout est lu ici)."""
        with tempfile.TemporaryDirectory() as d:
            sys_ = environnement(str(Path(d) / "rt"), Path(d) / "dec.json",
                                 DECISIONS_OK, _etat(study), donnees)
            etat = run_pipeline(_etat(study), sys_, donnees)
            store = StoreArtefacts(Path(d) / "rt" / "store")
            res = store.lire_json(store.resoudre(
                "results", study, "results_inferential").ref)
            crit = store.lire_json(store.resoudre(
                "critique", study, "critique").ref)
            return etat, res, crit

    def test_stabilite_e2e_seuil_deduit_et_recalcul_relecture(self):
        donnees = generer_stabilite()            # dérive nominale −0,018/mois
        etat, res, crit = self._lance(donnees, "STAB-TEST-01")
        self.assertEqual(etat.statut, "TERMINE")
        sens = res["resultats"]["ST-SENS-ph"]["resultat"]
        self.assertTrue(sens["interpretable"])
        self.assertEqual(sens["direction"], "inferieur")   # déduit (non à vue)
        self.assertEqual(sens["spec_limite"], 5.0)
        # relecture sans objection bloquante (recalcul de l'op en whitelist)
        self.assertEqual(crit["verdict"], "OK")

    def test_stabilite_e2e_ascendante_deduit_superieur(self):
        donnees = generer_stabilite(derive_ph=0.05)   # monte, reste < 7 à 12 m
        etat, res, crit = self._lance(donnees, "STAB-TEST-02")
        sens = res["resultats"]["ST-SENS-ph"]["resultat"]
        self.assertEqual(sens["direction"], "superieur")
        self.assertEqual(sens["spec_limite"], 7.0)

    def test_mnar_e2e_ruban_sur_copies_pmm(self):
        donnees = generer_usage(manquants_endpoint=5)
        donnees["spec"]["sensibilite_mnar_smd"] = {
            "deltas": [0.0, 0.25, 0.5, 1.0], "groupe": "produit"}
        etat, res, crit = self._lance(donnees, "COS-TEST-03")
        self.assertEqual(etat.statut, "TERMINE")
        a4 = res["resultats"]["A4"]
        self.assertEqual(a4["role"], "sensibilite")
        self.assertEqual(a4["groupe_mnar"], "produit")
        self.assertEqual(a4["version"], "1.1.0")
        rb = a4["resultat"]
        self.assertTrue(rb["interpretable"])
        self.assertEqual(rb["m_imputations"], 20)
        self.assertEqual(rb["groupe_ajuste"], "g1")       # produit = contraste[0]
        self.assertEqual(len(rb["ruban"]), 4)
        self.assertEqual(rb["delta_bascule"], 0.25)
        self.assertEqual(rb["delta_renversement"], 1.0)
        self.assertAlmostEqual(rb["base"]["theta_pooled"],
                               rb["ruban"][0]["theta_pooled"], places=15)
        # conclusions primaires inchangées par la sensibilité
        self.assertTrue(res["resultats"]["A1"]["resultat"]["interpretable"])
        self.assertEqual(res["contradictions_inferentiel"]
                         if "contradictions_inferentiel" in res else [], [])

    def test_mnar_sans_objet_si_pas_d_imputation(self):
        donnees = generer_usage()                        # 0 manquant → pas de MI
        donnees["spec"]["sensibilite_mnar_smd"] = {
            "deltas": [0.0, 0.5], "groupe": "produit"}
        etat, res, crit = self._lance(donnees, "COS-TEST-04")
        rb = res["resultats"]["A4"]["resultat"]
        self.assertFalse(rb["interpretable"])
        self.assertIn("sans objet", rb["motif"])
        # relecture : aucune objection (champs numériques absents des 2 côtés)
        self.assertEqual(crit["verdict"], "OK")

    def test_op_directe_renversement_marque(self):
        """Niveau catalogue : au-delà de la traversée de zéro, renverse=True
        et delta_renversement renseigné — la « re-significativité » est
        explicitée comme artefact du renversement."""
        import random
        rng = random.Random(5)
        g1 = [round(rng.gauss(12, 4), 2) for _ in range(25)]
        g2 = [round(rng.gauss(9, 4), 2) for _ in range(25)]
        cols1 = [[v for v in g1] for _ in range(4)]
        cols2 = [[v + 0.1 * k for v in g2] for k in range(4)]
        rb = ops.executer("tipping_point_mnar_smd", 0,
                          colonnes_g1=cols1, colonnes_g2=cols2,
                          deltas=[0.0, 0.5, 1.0, 2.0], groupe_ajuste="g2")
        self.assertTrue(rb["interpretable"])
        renverses = [r for r in rb["ruban"] if r["renverse"]]
        if rb["delta_renversement"] is not None:
            self.assertTrue(renverses)
            self.assertEqual(rb["delta_renversement"],
                             renverses[0]["delta"])
            if rb["delta_bascule"] is not None:
                self.assertIn("RENVERSÉ", rb["verdict"])
        for r in rb["ruban"]:
            self.assertIn("renverse", r)


if __name__ == "__main__":
    unittest.main()
