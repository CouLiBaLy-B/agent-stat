"""Tests du parcours observationnel : ops d'association (valeurs de référence),
lexique causal de bout en bout, règles R-DON-03/R-OBS-01, E2E cas-témoins
appariée + cohorte HR, blocages, chemin LLM contraint.

Valeurs de référence : recalculées à la main (tables 2×2, McNemar exact,
log-rang de Mantel, estimateur HR de Peto) avant gel dans les assertions.
"""
import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_impl import biostat, comprehension
from agents_impl.base import Contexte
from core.audit import JournalAudit
from core.exceptions import PipelineBloque
from core.state import Etat, RegleBlocage
from core.store import StoreArtefacts
from demo.jeu_donnees import DECISIONS_OK
from demo.jeu_observationnel import generer_cas_temoins, generer_cohorte
from llm.provider import ProviderSimule
from orchestration.pipeline import construire_systeme, run_pipeline
from reglementaire.moteur_regles import evaluer_donnees_requises
from stats_catalogue import ops


# ------------------------------------------------------------------ ops unitaires

class TestOddsRatioCasTemoins(unittest.TestCase):
    def test_valeurs_reference(self):
        """Table (90,40,60,110) : OR = 9900/2400 = 4,125 exactement ;
        IC Woolf et p de Fisher gelés après recalcul manuel."""
        r = ops.odds_ratio_cas_temoins(a=90, b=40, c=60, d=110)
        self.assertAlmostEqual(r["odds_ratio"], 4.125, places=9)
        self.assertAlmostEqual(r["ic95_or"][0], 2.53338, places=5)
        self.assertAlmostEqual(r["ic95_or"][1], 6.71656, places=5)
        self.assertAlmostEqual(r["se_log_or"], 0.24873, places=5)
        self.assertLess(r["p_valeur"], 1e-7)
        self.assertFalse(r["correction_haldane_anscombe"])
        self.assertAlmostEqual(r["exposes_cas"], 90 / 130, places=9)
        self.assertAlmostEqual(r["exposes_temoins"], 60 / 170, places=9)

    def test_correction_zero_declaree(self):
        r = ops.odds_ratio_cas_temoins(a=0, b=10, c=5, d=20)
        self.assertTrue(r["correction_haldane_anscombe"])
        # (0,5·20,5)/(10,5·5,5) = 10,25/57,75
        self.assertAlmostEqual(r["odds_ratio"], 10.25 / 57.75, places=9)
        self.assertGreater(r["ic95_or"][0], 0.0)

    def test_non_interpretable(self):
        self.assertFalse(ops.odds_ratio_cas_temoins(0, 0, 0, 0)["interpretable"])
        self.assertFalse(ops.odds_ratio_cas_temoins(-1, 2, 3, 4)["interpretable"])

    def test_determinisme(self):
        a = ops.odds_ratio_cas_temoins(12, 30, 5, 40)
        b = ops.odds_ratio_cas_temoins(12, 30, 5, 40)
        self.assertEqual(a, b)


class TestOrApparie(unittest.TestCase):
    def test_valeurs_reference(self):
        """b=40, c=20 : OR = 2,0 ; se = √(1/40+1/20) ; p McNemar exact figée."""
        r = ops.or_apparie(40, 20)
        self.assertAlmostEqual(r["odds_ratio"], 2.0, places=9)
        self.assertAlmostEqual(r["se_log_or"], (1/40 + 1/20) ** 0.5, places=9)
        self.assertAlmostEqual(r["ic95_or"][0], 1.16928, places=5)
        self.assertAlmostEqual(r["ic95_or"][1], 3.42091, places=5)
        self.assertAlmostEqual(r["p_valeur"], 0.01349, places=5)
        self.assertEqual(r["paires_discordantes"], 60)

    def test_correction_discordance_nulle(self):
        r = ops.or_apparie(7, 0)
        self.assertTrue(r["correction_zero_discordant"])
        self.assertAlmostEqual(r["odds_ratio"], 7.5 / 0.5, places=9)

    def test_aucune_discordance(self):
        r = ops.or_apparie(0, 0)
        self.assertFalse(r["interpretable"])
        self.assertIn("discordante", r["motif"])

    def test_determinisme(self):
        self.assertEqual(ops.or_apparie(13, 5), ops.or_apparie(13, 5))


class TestRisqueRelatifCohorte(unittest.TestCase):
    def test_valeurs_reference(self):
        """(20,180,10,190) : RR = 0,10/0,05 = 2,0 ; RD = 0,05 ; IC de Katz et
        IC de Newcombe (méthode 10) gelés après recalcul manuel."""
        r = ops.risque_relatif_cohorte(a=20, b=180, c=10, d=190)
        self.assertAlmostEqual(r["risque_relatif"], 2.0, places=9)
        self.assertAlmostEqual(r["risque_expose"], 0.10, places=9)
        self.assertAlmostEqual(r["risque_non_expose"], 0.05, places=9)
        self.assertAlmostEqual(r["ic95_rr"][0], 0.96059, places=5)
        self.assertAlmostEqual(r["ic95_rr"][1], 4.16409, places=5)
        self.assertAlmostEqual(r["difference_risques"], 0.05, places=9)
        self.assertAlmostEqual(r["ic95_difference_risques"][0], -0.00239, places=5)
        self.assertAlmostEqual(r["ic95_difference_risques"][1], 0.10434, places=5)
        self.assertAlmostEqual(r["p_valeur"], 0.08610, places=4)
        self.assertFalse(r["correction_haldane_anscombe"])

    def test_bras_vide(self):
        self.assertFalse(ops.risque_relatif_cohorte(0, 0, 3, 7)["interpretable"])


class TestKmLogrankHr(unittest.TestCase):
    T1, E1 = [3, 5, 8, 10, 12], [1, 1, 1, 0, 0]
    T2, E2 = [1, 2, 4, 6, 7], [1, 1, 1, 0, 0]

    def test_valeurs_reference(self):
        """Log-rang recalculé à la main : E1 = 3,9187 ; V = 1,1984 ;
        χ²₁ = 0,7042 ; HR Peto = exp((O1−E1)/V) = 0,4646 ; médianes 8 / 4."""
        r = ops.km_logrank_hr(self.T1, self.E1, self.T2, self.E2)
        self.assertAlmostEqual(r["E1"], 3.918651, places=6)
        self.assertAlmostEqual(r["variance_logrank"], 1.198409, places=6)
        self.assertAlmostEqual(r["chi2_logrank"], 0.704200, places=6)
        self.assertAlmostEqual(r["p_valeur"], 0.401376, places=6)
        self.assertAlmostEqual(r["hr"], 0.464609, places=6)
        self.assertEqual(r["mediane_survie_g1"], 8.0)
        self.assertEqual(r["mediane_survie_g2"], 4.0)
        self.assertIn("constants", r["hypothese"])
        self.assertLess(r["ic95_hr"][0], 1.0)
        self.assertGreater(r["ic95_hr"][1], 1.0)

    def test_significatif_hr_positif(self):
        rng = random.Random(1)
        t1 = [min(rng.expovariate(0.08), 36) for _ in range(120)]
        t2 = [min(rng.expovariate(0.04), 36) for _ in range(120)]
        e1 = [1 if t < 36 else 0 for t in t1]
        e2 = [1 if t < 36 else 0 for t in t2]
        r = ops.km_logrank_hr(t1, e1, t2, e2)
        self.assertGreater(r["hr"], 1.0)
        self.assertLess(r["p_valeur"], 0.001)
        self.assertGreater(r["ic95_hr"][0], 1.0)

    def test_gardes(self):
        self.assertFalse(ops.km_logrank_hr([1, 2], [1], [1, 2], [1, 1])
                         ["interpretable"])
        self.assertFalse(ops.km_logrank_hr([1, 2], [1, 2], [3, 4], [1, 1])
                         ["interpretable"])
        r = ops.km_logrank_hr(self.T1, [1, 1, 1, 1, 1], self.T2, self.E2)
        self.assertTrue(r["interpretable"])           # toutes issues sont événements
        self.assertFalse(ops.km_logrank_hr([1, 2], [1, 1], [3, 4], [0, 0])
                         ["interpretable"])           # groupe sans événement

    def test_determinisme(self):
        a = ops.km_logrank_hr(self.T1, self.E1, self.T2, self.E2)
        b = ops.km_logrank_hr(list(self.T1), list(self.E1),
                              list(self.T2), list(self.E2))
        self.assertEqual(a, b)


# ------------------------------------------------------------------ gabarits & qualification

class TestGabaritsObservationnels(unittest.TestCase):
    def test_qualification(self):
        t, c, _ = comprehension.qualifier(
            "étude cas-témoins appariée 1:1 sur registre dermato")
        self.assertEqual(t, "cas_temoins")
        self.assertGreaterEqual(c, 0.8)
        t, c, _ = comprehension.qualifier(
            "cohorte prospective de suivi d'exposition professionnelle")
        self.assertEqual(t, "cohorte_prospective")
        self.assertGreaterEqual(c, 0.8)

    def test_gabarit_cas_temoins_apparie_vs_non(self):
        sap_ct = biostat._gabarit_cas_temoins(
            generer_cas_temoins()["spec"], {})
        self.assertEqual([a["op"] for a in sap_ct],
                         ["proportion_wilson", "or_apparie"])
        self.assertEqual(sap_ct[1]["var_paire"], "paire")
        sap_ctn = biostat._gabarit_cas_temoins(
            {**generer_cas_temoins()["spec"], "appariement": None}, {})
        self.assertEqual(sap_ctn[1]["op"], "odds_ratio_cas_temoins")

    def test_gabarit_cohorte_hr_puis_rr(self):
        sap_co = biostat._gabarit_cohorte(generer_cohorte()["spec"], {})
        self.assertEqual([a["op"] for a in sap_co],
                         ["proportion_wilson", "km_logrank_hr",
                          "risque_relatif_cohorte"])
        self.assertEqual(sum(1 for a in sap_co if a["role"] == "primaire"), 1)
        sap_co2 = biostat._gabarit_cohorte(
            {**generer_cohorte()["spec"], "var_temps_event": None}, {})
        self.assertEqual(sap_co2[1]["op"], "risque_relatif_cohorte")
        self.assertEqual(sap_co2[1]["role"], "primaire")

    def test_garde_fous_observationnel_dans_sap(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Contexte(store=StoreArtefacts(Path(d) / "s"),
                           audit=JournalAudit(Path(d) / "a.jsonl"),
                           checkpoints_dir=d)
            agent = biostat.fabriquer(ctx)
            etat = Etat(run_id="r", study_id="x", domaine="medical", seed=1)
            etat.type_etude = "cas_temoins"
            s = agent(etat, {"spec": generer_cas_temoins()["spec"],
                             "dq": {"score_dq": 1.0}})
            gfo = s["sap"].get("garde_fous_observationnel")
            self.assertIsNotNone(gfo)
            self.assertIn("association", gfo["lexique"])

    def test_verification_metier_rejette_or_apparie_incomplet(self):
        analyses = [{"id": "A1", "role": "primaire", "op": "or_apparie",
                     "var": "exposition", "var_exposition": "exposition",
                     "var_issue": "statut"}]               # var_paire manquant
        with self.assertRaisesRegex(Exception, "var_paire"):
            biostat._verifier_regles_metier(
                analyses, generer_cas_temoins()["spec"])

    def test_regles_donnees_observationnel(self):
        ok = evaluer_donnees_requises("cas_temoins", {
            "n_lignes": 360,
            "variables": ["exposition", "statut", "paire"],
            "var_exposition": "exposition", "var_issue": "statut",
            "appariement": "1:1", "var_paire": "paire"})
        self.assertEqual(ok[0]["statut"], "OK")
        self.assertTrue(any(r["regle"].startswith("R-OBS-01")
                            and r["statut"] == "INFO" for r in ok))
        ko = evaluer_donnees_requises("cohorte_prospective", {
            "n_lignes": 30, "variables": ["exposition"],
            "var_exposition": "exposition", "var_evenement": "evt"})
        self.assertEqual(ko[0]["statut"], "KO")
        self.assertIn("n=30", ko[0]["preuve"])


# ------------------------------------------------------------------ E2E

class _E2E(unittest.TestCase):
    def _sys(self, tmp, decisions, llm=None):
        r = Path(tmp)
        (r / "decisions.json").write_text(
            json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
        return construire_systeme(str(r / "rt"), str(r / "decisions.json"),
                                  backoff_base_s=0.0, llm=llm)

    @staticmethod
    def _etat(sid):
        return Etat(run_id=f"run-2026-07-27-{sid}", study_id=sid,
                    domaine="medical", seed=20260727)

    @staticmethod
    def _lire(tmp, type_, sid, name):
        st = StoreArtefacts(Path(tmp) / "rt" / "store")
        return st.lire_json(st.resoudre(type_, sid, name).ref)


class TestE2ECasTemoins(_E2E):
    SID = "MED-CT-2026-201"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sys_ = cls()._sys(cls.tmp.name, DECISIONS_OK)
        cls.etat = run_pipeline(cls()._etat(cls.SID), cls.sys_,
                                generer_cas_temoins())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_termine_et_qualifie(self):
        self.assertEqual(self.etat.statut, "TERMINE")
        self.assertEqual(self.etat.type_etude, "cas_temoins")
        self.assertGreaterEqual(self.etat.confiance_qualification, 0.8)

    def test_primaire_or_apparie(self):
        res = self._lire(self.tmp.name, "results", self.SID,
                         "results_inferential")
        a1 = res["resultats"]["A1"]
        self.assertEqual(a1["op_retenue"], "or_apparie")
        r = a1["resultat"]
        self.assertTrue(r["interpretable"])
        self.assertGreater(r["odds_ratio"], 1.0)
        self.assertLess(r["p_valeur"], 0.05)
        self.assertIn("ic95_or", r)          # p jamais seule
        self.assertGreater(r["paires_bc_cas_expose_temoin_non"],
                           r["paires_cb_cas_non_temoin_expose"])

    def test_lexique_association_bout_en_bout(self):
        rap = self._lire(self.tmp.name, "report", self.SID, "rapport_draft")
        self.assertTrue(rap["conclusion_directionnelle"]
                        .startswith("association"))
        self.assertIn("Association ≠ causalité", rap["markdown"])
        self.assertIn("non ajustée", rap["markdown"])
        critique = self._lire(self.tmp.name, "critique", self.SID, "critique")
        self.assertEqual(critique["verdict"], "OK")   # aucune objection levée

    def test_scores_plafonnes_observationnel(self):
        s = self.etat.scores
        self.assertLessEqual(s["confiance_conclusion"], 0.60)
        self.assertEqual(s["verbalisation_cc"], "moderee")

    def test_conformite_et_audit(self):
        comp = self._lire(self.tmp.name, "compliance", self.SID,
                          "compliance_report")
        self.assertEqual(comp["verdict"], "CONFORME")
        regles = {r["regle"]: r["statut"] for r in comp["regles"]}
        self.assertEqual(regles.get("R-DON-03-donnees-observationnel"), "OK")
        self.assertEqual(regles.get("R-OBS-01-lexique-association"), "INFO")
        ok, n, _ = JournalAudit.verifier(
            Path(self.tmp.name) / "rt" / "audit.jsonl")
        self.assertTrue(ok)
        self.assertGreaterEqual(n, 15)


class TestE2ECohorte(_E2E):
    SID = "MED-CO-2026-202"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.sys_ = cls()._sys(cls.tmp.name, DECISIONS_OK)
        cls.etat = run_pipeline(cls()._etat(cls.SID), cls.sys_, generer_cohorte())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_hr_primaire_et_rr_secondaire(self):
        res = self._lire(self.tmp.name, "results", self.SID,
                         "results_inferential")
        a1, a2 = res["resultats"]["A1"], res["resultats"]["A2"]
        self.assertEqual(a1["op_retenue"], "km_logrank_hr")
        self.assertEqual(a2["op_retenue"], "risque_relatif_cohorte")
        self.assertGreater(a1["resultat"]["hr"], 1.0)
        self.assertLess(a1["resultat"]["p_valeur"], 0.001)
        self.assertGreater(a1["resultat"]["ic95_hr"][0], 1.0)
        self.assertGreater(a2["resultat"]["risque_relatif"], 1.0)
        self.assertIn("ic95_difference_risques", a2["resultat"])

    def test_critique_ok_et_lexique(self):
        critique = self._lire(self.tmp.name, "critique", self.SID, "critique")
        self.assertEqual(critique["verdict"], "OK")
        rap = self._lire(self.tmp.name, "report", self.SID, "rapport_draft")
        self.assertEqual(self.etat.scores["verbalisation_cc"], "moderee")
        self.assertIn("risques relatifs constants", rap["markdown"])

    def test_audit(self):
        ok, _, _ = JournalAudit.verifier(Path(self.tmp.name) / "rt"
                                         / "audit.jsonl")
        self.assertTrue(ok)


class TestBlocagesEtLexique(_E2E):
    def test_donnees_minimales_bloquent(self):
        with tempfile.TemporaryDirectory() as d:
            sys_ = self._sys(d, DECISIONS_OK)
            # 36 lignes < 40 ⇒ R-DON-03 KO ⇒ blocage non-conformité
            donnees = generer_cas_temoins(n_paires=18)
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat("MED-CT-2026-203"), sys_, donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)
            self.assertIn("R-DON-03", ctx.exception.motif)

    def test_lexique_causal_rejete_a_la_relecture(self):
        """Un rapport « falsifié » en vocabulaire causal doit lever les
        objections tournure_causale + conclusion_hors_lexique."""
        with tempfile.TemporaryDirectory() as d:
            sys_ = self._sys(d, DECISIONS_OK)
            etat = run_pipeline(self._etat("MED-CO-2026-204"), sys_,
                                generer_cohorte())
            store = sys_["store"]
            res = store.lire_json(store.resoudre(
                "results", etat.study_id, "results_inferential").ref)
            agent = sys_["orchestrateur"].agents["agent.relecture"]
            sortie = agent(etat, {
                "rapport": {"lignes_faits": [],
                            "texte_inferences":
                                "L'exposition réduit le risque d'événement, "
                                "elle cause une protection nette.",
                            "conclusion_directionnelle": "significatif",
                            "limites": ["x"]},
                "resultats": res,
                "scores": etat.scores,
                "report_ref": "art://report/x/rapport_draft/v1",
                "results_ref": "art://results/x/results_inferential/v1"})
            types = {o["type"] for o in sortie["objections_bloquantes"]}
            self.assertIn("tournure_causale_non_autorisee", types)
            self.assertIn("conclusion_hors_lexique_observationnel", types)

    def test_cas_temoins_llm_simule_e2e(self):
        """Chemin LLM contraint (génération par schéma) — même contrat."""
        from llm.simule import provider_simule_defaut
        with tempfile.TemporaryDirectory() as d:
            sys_ = self._sys(d, DECISIONS_OK, llm=provider_simule_defaut())
            etat = run_pipeline(self._etat("MED-CT-2026-205"), sys_,
                                generer_cas_temoins())
            self.assertEqual(etat.statut, "TERMINE")
            self.assertEqual(etat.type_etude, "cas_temoins")
            sap = self._lire(d, "sap", etat.study_id, "sap")
            self.assertEqual(sap["mode_proposition"], "llm")
            ops_sap = {a["op"] for a in sap["analyses"]}
            self.assertIn("or_apparie", ops_sap)
            a1 = next(a for a in sap["analyses"] if a["op"] == "or_apparie")
            self.assertIn("var_paire", a1)   # clés métier conservées

    def test_repli_llm_sur_proposition_metier_invalide(self):
        """Le LLM oublie var_paire → contre-vérification métier → repli gabarit."""
        propo = {"analyses": [
            {"id": "A1", "role": "primaire", "op": "or_apparie",
             "var": "exposition", "var_exposition": "exposition",
             "var_issue": "statut"}],                      # var_paire absent
            "gestion_multiplicite": "aucune (primaire unique)",
            "gestion_manquants_strategie": "cas complets si taux ≤ 5 %"}
        p = ProviderSimule({"proposition_sap":
                            lambda utilisateur, appel: copy.deepcopy(propo)})
        with tempfile.TemporaryDirectory() as d:
            ctx = Contexte(store=StoreArtefacts(Path(d) / "s"),
                           audit=JournalAudit(Path(d) / "a.jsonl"),
                           checkpoints_dir=d)
            agent = biostat.fabriquer(ctx, llm=p)
            etat = Etat(run_id="r", study_id="x", domaine="medical", seed=1)
            etat.type_etude = "cas_temoins"
            s = agent(etat, {"spec": generer_cas_temoins()["spec"],
                             "dq": {"score_dq": 1.0}})
            a1 = [a for a in s["sap"]["analyses"] if a["role"] == "primaire"][0]
            self.assertIn("var_paire", a1)     # gabarit de repli complet
            self.assertTrue(any("repli" in a for a in s["assumptions"]))


if __name__ == "__main__":
    unittest.main()
