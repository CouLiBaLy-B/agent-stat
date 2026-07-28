"""Tests d'intégration pipeline : ajustement multivarié derrière SAP (G3).

Couvre : gabarits SAP (A3 selon type/modalités), contre-vérifications métier
fail-closed, E2E cohorte Cox + cohorte logistique avec confusion démontrée,
CC plafonnée à 0,75 (scores-1.1.0), lexique « association ajustée » maintenu
à la relecture, A3 non interprétable → contradiction + aucune mesure ajustée.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_impl import biostat
from core.exceptions import ErreurLogique
from core.state import Etat
from demo.jeu_ajustement import (generer_cohorte_cox,
                                 generer_cohorte_logistique)
from demo.jeu_donnees import DECISIONS_OK
from demo.jeu_observationnel import generer_cas_temoins, generer_cohorte
from orchestration import scores as moteur_scores
from orchestration.pipeline import run_pipeline
from tests.outillage import environnement


# ------------------------------------------------------------------ gabarits SAP

def _spec_cohorte(aj=None, avec_temps=True):
    spec = generer_cohorte()["spec"]
    if not avec_temps:
        spec.pop("var_temps_event", None)
    if aj is not None:
        spec["ajustement_multivarie"] = aj
    return spec


class TestGabaritsAjustement(unittest.TestCase):
    def test_a3_cox_pour_cohorte_avec_temps(self):
        analyses = biostat._gabarit_cohorte(_spec_cohorte(
            {"covariables": ["age"]}, avec_temps=True), {})
        a3 = [a for a in analyses if a.get("role") == "ajustement"]
        self.assertEqual(len(a3), 1)
        self.assertEqual(a3[0]["op"], "cox_ph")
        self.assertEqual(a3[0]["epv_min"], 10.0)
        self.assertEqual(a3[0]["covariables"], ["age"])
        prim = [a for a in analyses if a.get("role") == "primaire"]
        self.assertEqual(len(prim), 1)     # règle « UNE primaire » préservée

    def test_a3_logistique_pour_cohorte_sans_temps(self):
        analyses = biostat._gabarit_cohorte(_spec_cohorte(
            {"covariables": ["age"]}, avec_temps=False), {})
        a3 = [a for a in analyses if a.get("role") == "ajustement"][0]
        self.assertEqual(a3["op"], "regression_logistique")
        self.assertEqual(a3["var_issue"], "evenement")

    def test_a3_logistique_cas_temoins_non_appariee(self):
        spec = generer_cas_temoins()["spec"]
        spec.pop("appariement", None)
        spec.pop("var_paire", None)
        spec["ajustement_multivarie"] = {"covariables": ["age"], "epv_min": 8}
        analyses = biostat._gabarit_cas_temoins(spec, {})
        a3 = [a for a in analyses if a.get("role") == "ajustement"][0]
        self.assertEqual(a3["op"], "regression_logistique")

    def test_blocage_cas_temoins_appariee_avec_ajustement(self):
        spec = generer_cas_temoins()["spec"]
        spec["ajustement_multivarie"] = {"covariables": ["age"]}
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_cas_temoins(spec, {})

    def test_blocage_epv_min_trop_bas(self):
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_cohorte(_spec_cohorte(
                {"covariables": ["age"], "epv_min": 3}), {})

    def test_blocage_sans_covariables(self):
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_cohorte(_spec_cohorte({"covariables": []}), {})

    def test_blocage_exposition_absente(self):
        spec = _spec_cohorte({"covariables": ["age"]})
        spec.pop("var_exposition")
        with self.assertRaises(ErreurLogique):
            biostat._gabarit_cohorte(spec, {})

    def test_contre_verification_metier_covariable_absente(self):
        analyses = biostat._gabarit_cohorte(
            {"endpoint_principal": "evenement", "variable_groupe": "exposition",
             "var_exposition": "exposition", "var_evenement": "evenement",
             "ajustement_multivarie": {"covariables": ["age"]}}, {})
        spec_incomplete = {"variables": {"exposition": {}, "evenement": {},
                                         "endpoint": {}}}
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(analyses, spec_incomplete)

    def test_contre_verification_exposition_non_redoublee(self):
        analyses = biostat._gabarit_cohorte(_spec_cohorte(
            {"covariables": ["exposition"]}), {})
        spec = {"variables": {"exposition": {}, "evenement": {}, "age": {}}}
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(analyses, spec)

    # --- proposition LLM : ajustement pré-déclaré jamais omis/dévié ---------

    def _proposition_sans_a3(self):
        # proposition de type LLM : A0a + A1 sans le bloc d'ajustement
        return [a for a in biostat._gabarit_cohorte(_spec_cohorte(), {})
                if a.get("role") != "ajustement"]

    def test_llm_omission_a3_rejetee(self):
        spec = {"variables": {"exposition": {}, "evenement": {}, "age": {},
                              "temps_mois": {}},
                "ajustement_multivarie": {"covariables": ["age"]}}
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(self._proposition_sans_a3(), spec)

    def test_llm_deviation_a3_rejetee(self):
        spec = {"variables": {"exposition": {}, "evenement": {}, "age": {},
                              "temps_mois": {}},
                "ajustement_multivarie": {"covariables": ["age"],
                                          "epv_min": 10}}
        deviante = [{"id": "A1", "role": "primaire", "op": "km_logrank_hr",
                     "var": "evenement", "var_evenement": "evenement",
                     "var_temps_event": "temps_mois"},
                    {"id": "A3", "role": "ajustement", "op": "cox_ph",
                     "var": "exposition", "var_exposition": "exposition",
                     "var_evenement": "evenement",
                     "var_temps_event": "temps_mois",
                     "covariables": ["temps_mois"],   # ← dérive de la spec
                     "epv_min": 10}]
        with self.assertRaises(ErreurLogique):
            biostat._verifier_regles_metier(deviante, spec)

    def test_llm_a3_conforme_acceptee(self):
        spec_base = generer_cohorte()["spec"]
        spec_base["ajustement_multivarie"] = {"covariables": ["age"],
                                              "epv_min": 10}
        analyses = biostat._gabarit_cohorte(spec_base, {})
        # ne doit PAS lever : les op d'ajustement sont validées champ à champ
        biostat._verifier_regles_metier(analyses, spec_base)


# ------------------------------------------------------------------ scores

class TestScoresAjuste(unittest.TestCase):
    def test_plafond_075_association_ajustee(self):
        cc = moteur_scores.calculer_cc(dq=0.95, ra=0.90, pre_enregistre=True,
                                       observationnel_ajuste=True)
        self.assertEqual(cc, 0.75)
        self.assertEqual(moteur_scores.verbaliser_cc(cc), "moderee")

    def test_plafond_060_non_ajuste_inchange(self):
        cc = moteur_scores.calculer_cc(dq=0.95, ra=0.90, pre_enregistre=True,
                                       observationnel_non_ajuste=True)
        self.assertEqual(cc, 0.60)

    def test_version_formule(self):
        self.assertEqual(moteur_scores.FORMULE_VERSION, "scores-1.1.0")

    def test_autres_plafonds_priment(self):
        cc = moteur_scores.calculer_cc(dq=0.50, ra=0.90, pre_enregistre=True,
                                       observationnel_ajuste=True)
        self.assertLessEqual(cc, 0.40)      # dq < 0,60 reste plus dur


# ------------------------------------------------------------------ E2E

class TestPipelineAjustement(unittest.TestCase):
    def _run(self, etiquette, study_id, fabrique):
        with tempfile.TemporaryDirectory() as td:
            etat = Etat(run_id=f"run-t-{etiquette}", study_id=study_id,
                        domaine="medical", seed=20260729)
            donnees = fabrique()
            sys_ = environnement(str(Path(td)), Path(td) / "decisions.json",
                                 DECISIONS_OK, etat, donnees, llm=None)
            etat = run_pipeline(etat, sys_, donnees)
            store = sys_["store"]
            res = store.lire_json(store.resoudre(
                "results", study_id, "results_inferential").ref)
            rapport = store.lire_json(store.resoudre(
                "report", study_id, "rapport_draft").ref)
            relecture = store.lire_json(store.resoudre(
                "critique", study_id, "critique").ref)
            sap = store.lire_json(store.resoudre(
                "sap", study_id, "sap").ref)
            return etat, res, rapport, relecture, sap

    def test_e2e_cohorte_cox_confusion_levee(self):
        etat, res, rapport, relecture, sap = self._run(
            "cox", "T-AJ-1", generer_cohorte_cox)
        self.assertEqual(etat.statut, "TERMINE")
        a3 = res["resultats"]["A3"]
        self.assertEqual(a3["op_retenue"], "cox_ph")
        self.assertTrue(a3["resultat"]["interpretable"])
        # la mesure brute (A1 log-rang) est non significative (confusion d'âge)
        self.assertGreaterEqual(res["resultats"]["A1"]["resultat"]["p_valeur"],
                                0.05)
        # l'ajustée retrouve l'association protectrice
        expo = a3["resultat"]["coefficients"][0]
        self.assertEqual(expo["covariable"], "exposition")
        self.assertLess(expo["p_valeur"], 0.05)
        self.assertLess(expo["hazard_ratio"], 1.0)
        self.assertLess(expo["ic95_hr"][0], expo["hazard_ratio"])
        self.assertGreaterEqual(a3["resultat"]["epv"], 10)
        # conclusion suit la mesure d'intérêt pré-déclarée (ajustée)
        self.assertEqual(rapport["conclusion_directionnelle"],
                         "association_ajustee_significative")
        self.assertEqual(etat.scores["confiance_conclusion"], 0.75)
        self.assertEqual(etat.scores["verbalisation_cc"], "moderee")
        # faits sourcés + limites explicites
        faits_aj = [l for l in rapport["lignes_faits"] if "AJUSTÉE" in l]
        self.assertTrue(faits_aj and any("art://" in l for l in faits_aj),
                        rapport["lignes_faits"])
        self.assertTrue(any("PROPORTIONNELS" in l or "proportionnels" in l
                            for l in rapport["limites"]))
        # relecture : aucune objection bloquante (recalcul A3 inclus)
        bloq = [o for o in relecture["objections"] if o.get("bloquante")]
        self.assertEqual(bloq, [], bloq)
        # SAP : garde-fous ajustement verrouillé cités
        gf = sap["garde_fous_observationnel"]["ajustement"]
        self.assertIn("PRÉ-DÉCLARÉ", gf)
        self.assertIn("cox_ph", gf)
        self.assertIn("aucun ajustement post-hoc", gf)

    def test_e2e_cohorte_logistique_confusion_levee(self):
        etat, res, rapport, relecture, _ = self._run(
            "logi", "T-AJ-2", generer_cohorte_logistique)
        self.assertEqual(etat.statut, "TERMINE")
        a3 = res["resultats"]["A3"]
        self.assertEqual(a3["op_retenue"], "regression_logistique")
        expo = a3["resultat"]["coefficients"][0]
        self.assertLess(expo["p_valeur"], 0.05)
        self.assertLess(expo["odds_ratio"], 1.0)
        self.assertLess(expo["odds_ratio"],
                        res["resultats"]["A1"]["resultat"]["risque_relatif"])
        self.assertEqual(rapport["conclusion_directionnelle"],
                         "association_ajustee_significative")
        bloq = [o for o in relecture["objections"] if o.get("bloquante")]
        self.assertEqual(bloq, [], bloq)

    def test_e2e_a3_non_interpretable_fail_closed(self):
        """EPV insuffisant : pipeline se termine, A3 marqué non interprétable,
        contradiction tracée, aucun chiffre ajusté au rapport, conclusion
        rabattue sur la mesure univariée."""
        donnees = generer_cohorte_logistique()
        rows = donnees["datasets"]["rows"]
        gardes = [r for r in rows if r["evenement"] == 1][:6]  # 6 événements
        gardes += [r for r in rows if r["evenement"] == 0][:154]
        for r in gardes:
            r.setdefault("_", None)
        donnees["datasets"]["rows"] = gardes
        with tempfile.TemporaryDirectory() as td:
            etat = Etat(run_id="run-t-epv", study_id="T-AJ-3",
                        domaine="medical", seed=20260729)
            sys_ = environnement(str(Path(td)), Path(td) / "decisions.json",
                                 DECISIONS_OK, etat, donnees, llm=None)
            etat = run_pipeline(etat, sys_, donnees)
            store = sys_["store"]
            res = store.lire_json(store.resoudre(
                "results", "T-AJ-3", "results_inferential").ref)
            a3 = res["resultats"]["A3"]
            self.assertFalse(a3["resultat"]["interpretable"])
            self.assertIn("EPV", a3["resultat"]["motif"])
            rapport = store.lire_json(store.resoudre(
                "report", "T-AJ-3", "rapport_draft").ref)
            self.assertTrue(any("NON interprétable" in l
                                for l in rapport["lignes_faits"]))
            self.assertFalse(any("OR ajusté" in l
                                 for l in rapport["lignes_faits"]),
                             "aucune mesure ajustée ne doit être présentée")
            self.assertNotIn("ajustee",
                             rapport["conclusion_directionnelle"] or "")
            # contradiction tracée en décision d'ARBITRAGE (fail-closed documenté)
            arbitrages = [d for d in etat.decisions if d["type"] == "ARBITRAGE"]
            self.assertTrue(arbitrages, etat.decisions)
            self.assertTrue(any("ajustement multivarié non interprétable"
                                in d["motif"] for d in arbitrages),
                            arbitrages)

    def test_observationnel_sans_ajustement_inchange(self):
        etat, res, rapport, relecture, sap = self._run(
            "brute", "T-AJ-4", generer_cas_temoins)
        self.assertEqual(etat.statut, "TERMINE")
        self.assertLessEqual(etat.scores["confiance_conclusion"], 0.60)
        self.assertNotIn("A3", res["resultats"])
        self.assertIn("univariée", sap["garde_fous_observationnel"]["ajustement"])


if __name__ == "__main__":
    unittest.main()
