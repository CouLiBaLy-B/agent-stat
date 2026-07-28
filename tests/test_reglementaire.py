"""Tests du référentiel réglementaire structuré et du moteur de règles v2."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exceptions import PipelineBloque
from core.state import Etat, RegleBlocage
from demo.jeu_donnees import DECISIONS_OK, generer
from orchestration.pipeline import construire_systeme, run_pipeline
from reglementaire.moteur_regles import (evaluer_composition,
                                         evaluer_donnees_requises,
                                         evaluer_dossier, evaluer_methodes)
from reglementaire.referentiel import charger_referentiel, normaliser_inci

REF = charger_referentiel()
LEAVE_ON_VISAGE = {"application": "leave_on", "usage": "creme_visage",
                   "zone": "visage", "population_cible": "adulte"}


def _dossier(composition, produit=LEAVE_ON_VISAGE, methodes=None, **kw):
    d = {"type_etude": "test_usage_controle", "produit": produit,
         "composition": composition, "methodes_test": methodes or [],
         "n_lignes": 60, "variables": ["reaction_grade"],
         "var_reaction": "reaction_grade"}
    d.update(kw)
    return d


class TestNormalisation(unittest.TestCase):
    def test_casse_espaces_tirets(self):
        self.assertEqual(normaliser_inci("  MethylIsoThiazoLinone "), 
                         "methylisothiazolinone")
        self.assertEqual(REF.canonique("KATHON-CG"),
                         "methylchloroisothiazolinone")
        self.assertEqual(REF.canonique("Vitamine E"), "tocopherol")
        self.assertEqual(REF.canonique("Benzophenone-3"), "oxybenzone")

    def test_corpus_trace(self):
        self.assertEqual(len(REF.sha256), 64)
        self.assertTrue(REF.version)


class TestAnnexeII(unittest.TestCase):
    def test_interdite_sans_derogation(self):
        rs, _ = evaluer_composition(REF, LEAVE_ON_VISAGE,
                                    [{"inci": "formaldehyde",
                                      "concentration_pct": 0.01}])
        self.assertEqual(rs[0]["statut"], "KO")
        self.assertIn("annexe II", rs[0]["reference"])

    def test_derogation_contextuelle(self):
        ongles = {**LEAVE_ON_VISAGE, "usage": "ongles_artificiels"}
        ok, _ = evaluer_composition(REF, ongles, [
            {"inci": "hydroquinone", "concentration_pct": 0.01}])
        self.assertEqual(ok[0]["statut"], "OK")
        ko, _ = evaluer_composition(REF, ongles, [
            {"inci": "hydroquinone", "concentration_pct": 0.05}])
        self.assertEqual(ko[0]["statut"], "KO")
        creme, _ = evaluer_composition(REF, LEAVE_ON_VISAGE, [
            {"inci": "hydroquinone", "concentration_pct": 0.01}])
        self.assertEqual(creme[0]["statut"], "KO")   # dérogation inapplicable


class TestRestrictionsContextuelles(unittest.TestCase):
    def test_mit_leave_on_interdit(self):
        rs, _ = evaluer_composition(REF, LEAVE_ON_VISAGE,
                                    [{"inci": "mit",
                                      "concentration_pct": 0.001}])
        self.assertEqual(rs[0]["statut"], "KO")

    def test_mit_rince_seuil(self):
        rince = {**LEAVE_ON_VISAGE, "application": "rinse_off"}
        ok, _ = evaluer_composition(REF, rince,
                                    [{"inci": "methylisothiazolinone",
                                      "concentration_pct": 0.001}])
        self.assertEqual(ok[0]["statut"], "OK")
        ko, _ = evaluer_composition(REF, rince,
                                    [{"inci": "methylisothiazolinone",
                                      "concentration_pct": 0.002}])
        self.assertEqual(ko[0]["statut"], "KO")

    def test_phenoxyethanol(self):
        ko, _ = evaluer_composition(REF, LEAVE_ON_VISAGE, [
            {"inci": "phenoxyethanol", "concentration_pct": 1.2}])
        self.assertEqual(ko[0]["statut"], "KO")

    def test_salicylique_population(self):
        enfant = {**LEAVE_ON_VISAGE, "population_cible": "enfant"}
        rs, _ = evaluer_composition(REF, enfant, [
            {"inci": "salicylic acid", "concentration_pct": 1.0}])
        self.assertEqual(rs[0]["statut"], "KO")
        self.assertIn("3 ans", rs[0]["preuve"])

    def test_agregat_parabens(self):
        somme_ko, _ = evaluer_composition(REF, LEAVE_ON_VISAGE, [
            {"inci": "methylparaben", "concentration_pct": 0.35},
            {"inci": "ethylparaben", "concentration_pct": 0.35},
            {"inci": "propylparaben", "concentration_pct": 0.12}])
        ag = [r for r in somme_ko if "parabens_somme" in r["regle"]]
        self.assertEqual(ag[0]["statut"], "KO")       # 0,82 % > 0,8 %
        somme_ok, _ = evaluer_composition(REF, LEAVE_ON_VISAGE, [
            {"inci": "methylparaben", "concentration_pct": 0.2}])
        ag2 = [r for r in somme_ok if "parabens_somme" in r["regle"]]
        self.assertEqual(ag2[0]["statut"], "OK")

    def test_substance_inconnue_incertitude(self):
        rs, couv = evaluer_composition(REF, LEAVE_ON_VISAGE, [
            {"inci": "extrait de lichen rare", "concentration_pct": 1.0}])
        self.assertEqual(rs[0]["statut"], "INCERTAIN")
        self.assertEqual(couv["inconnues"], ["extrait de lichen rare"])


class TestMethodes(unittest.TestCase):
    def test_methode_animale_ko(self):
        rs = evaluer_methodes(REF, ["test de Draize (irritation oculaire)"])
        self.assertEqual(rs[0]["statut"], "KO")
        self.assertIn("art. 18", rs[0]["reference"])

    def test_ocde_alternative_reconnue(self):
        rs = evaluer_methodes(REF, ["in vitro OCDE 439 épiderme reconstruit"])
        self.assertEqual(rs[0]["statut"], "OK")

    def test_methode_inconnue_incertitude(self):
        rs = evaluer_methodes(REF, ["chlurbidométrie quantique"])
        self.assertEqual(rs[0]["statut"], "INCERTAIN")

    def test_methode_stabilite_reconnue(self):
        rs = evaluer_methodes(REF, ["suivi physico-chimique multi-temps "
                                    "(pH, viscosité)"])
        self.assertEqual(rs[0]["statut"], "OK")


class TestDossierComplet(unittest.TestCase):
    def test_verdict_et_trace_corpus(self):
        res = evaluer_dossier(REF, _dossier(generer()["composition"]))
        self.assertEqual(res["verdict"], "CONFORME")
        self.assertEqual(len(res["corpus"]["sha256"]), 64)
        self.assertEqual(res["couverture_inci"]["total"], 5)

    def test_verdict_bloquant(self):
        res = evaluer_dossier(REF, _dossier(
            [{"inci": "chloroform", "concentration_pct": 0.1}]))
        self.assertEqual(res["verdict"], "NON_CONFORME_BLOQUANT")

    def test_donnees_minimales(self):
        rs = evaluer_donnees_requises("test_usage_controle", _dossier(
            [], n_lignes=12, produit={"application": "leave_on"}))
        self.assertEqual(rs[0]["statut"], "KO")   # n<30, INCI, contexte incomplet


class TestPipelineAvecReferentiel(unittest.TestCase):
    def _sys(self, tmp, decisions):
        r = Path(tmp)
        (r / "decisions.json").write_text(
            json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
        return construire_systeme(str(r / "rt"), str(r / "decisions.json"),
                                  backoff_base_s=0.0)

    def _etat(self):
        return Etat(run_id="run-2026-07-27-0004", study_id="COS-2026-017",
                    domaine="cosmetique", seed=7)

    def test_nominal_conforme_corpus_v2(self):
        with tempfile.TemporaryDirectory() as d:
            etat = run_pipeline(self._etat(), self._sys(d, DECISIONS_OK),
                                generer())
            self.assertEqual(etat.statut, "TERMINE")
            art = self._sys(d, DECISIONS_OK)["store"]  # référence neuve inutile
            # le rapport de conformité cite le corpus
            import glob
            rapports = glob.glob(str(Path(d) / "rt" / "store" / "index.json"))
            index = json.loads(Path(rapports[0]).read_text(encoding="utf-8"))
            ref_comp = index["compliance/COS-2026-017/compliance_report"]["ref"]
            store_vide = None
            from core.store import StoreArtefacts
            st = StoreArtefacts(Path(d) / "rt" / "store")
            comp = st.lire_json(ref_comp)
            self.assertEqual(comp["verdict"], "CONFORME")
            self.assertEqual(comp["couverture_inci"]["resolues"],
                             comp["couverture_inci"]["total"])
            self.assertEqual(comp["corpus"]["sha256"], REF.sha256)

    def test_inci_inconnu_bloque_sans_g4(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["composition"].append(
                {"inci": "extrait de lichen rare", "concentration_pct": 1.0})
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat(), self._sys(d, DECISIONS_OK), donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.GATE_HUMAIN_NON_VALIDE)

    def test_inci_inconnu_valide_par_expert_g4(self):
        with tempfile.TemporaryDirectory() as d:
            dec = dict(DECISIONS_OK)
            dec["G4"] = {"statut": "VALIDATED", "validateur_id": "u:reg-021",
                         "role": "expert_reglementaire",
                         "motif": "Lichen identifié sans restriction UE — pièce "
                                  "toxicologique archivée au dossier.",
                         "signature_ref": "sig:2026-07-27:reg-021:g4"}
            donnees = generer()
            donnees["composition"].append(
                {"inci": "extrait de lichen rare", "concentration_pct": 1.0})
            etat = run_pipeline(self._etat(), self._sys(d, dec), donnees)
            self.assertEqual(etat.statut, "TERMINE")


if __name__ == "__main__":
    unittest.main()
