"""Tests de la couche LLM : validation schéma, rétroaction, repli, HTTP, E2E."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_impl.base import Contexte
from agents_impl import comprehension, biostat
from core.audit import JournalAudit
from core.state import Etat
from core.store import StoreArtefacts
from demo.jeu_donnees import DECISIONS_OK, generer
from llm import schemas
from llm.exceptions import ErreurLLM, ReponseNonJSON
from llm.generation import generer_contraint
from llm.provider import ProviderOpenAICompatible, ProviderSimule
from llm.simule import (repondre_json_invalide_puis_valide,
                        repondre_toujours_invalide)
from llm.validation import valider
from orchestration.pipeline import run_pipeline
from tests.outillage import environnement


def _ctx(tmp: str) -> Contexte:
    r = Path(tmp)
    return Contexte(store=StoreArtefacts(r / "s"),
                    audit=JournalAudit(r / "a.jsonl"),
                    checkpoints_dir=str(r / "c"))


def _etat() -> Etat:
    return Etat(run_id="run-2026-07-27-0003", study_id="COS-2026-016",
                domaine="cosmetique", seed=42)


INTENTION_OK = {"type_etude": "test_usage_controle", "confiance": 0.9,
                "justification": "design explicite dans le brief"}


class TestValidationSchema(unittest.TestCase):
    def test_valide(self):
        self.assertEqual(valider(schemas.INTENTION, INTENTION_OK), [])

    def test_champ_requis_absent(self):
        mauvais = {"type_etude": "stabilite"}
        self.assertTrue(any("confiance" in e for e in
                            valider(schemas.INTENTION, mauvais)))

    def test_enum_et_propriete_supplementaire(self):
        e1 = valider(schemas.INTENTION,
                     {**INTENTION_OK, "type_etude": "etude_magique"})
        e2 = valider(schemas.INTENTION, {**INTENTION_OK, "surprise": 1})
        self.assertTrue(any("enum" in e for e in e1))
        self.assertTrue(any("non autorisée" in e for e in e2))

    def test_enum_catalogue_ops(self):
        mauvaise = {"id": "A1", "role": "primaire", "op": "t_test_magique",
                    "var": "x"}
        errs = valider(schemas.ANALYSE_ITEM, mauvaise)
        self.assertTrue(any("enum" in e for e in errs))


class TestGenerationContrainte(unittest.TestCase):
    def test_retroaction_puis_succes(self):
        p = ProviderSimule({"qualification_etude":
                            repondre_json_invalide_puis_valide})
        obj, meta = generer_contraint(
            p, tache="qualification_etude", systeme="s",
            utilisateur="<donnees>{}</donnees>", schema=schemas.INTENTION,
            tentatives=3)
        self.assertTrue(meta["schema_ok"])
        self.assertEqual(meta["tentatives"], 2)     # 1 échec + rétroaction + succès

    def test_epuisement_leve_erreur(self):
        p = ProviderSimule({"qualification_etude": repondre_toujours_invalide})
        with self.assertRaises(ErreurLLM):
            generer_contraint(p, tache="qualification_etude", systeme="s",
                              utilisateur="u", schema=schemas.INTENTION,
                              tentatives=2)


class TestProviderHTTP(unittest.TestCase):
    class _Resp:
        def __init__(self, corps): self._corps = corps
        def read(self): return self._corps
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def test_appel_et_parsing(self):
        corps = json.dumps({"choices": [{"message": {
            "content": json.dumps(INTENTION_OK)}}]}).encode()
        with patch("urllib.request.urlopen", return_value=self._Resp(corps)):
            p = ProviderOpenAICompatible("http://local.test", "k", "m-1")
            obj = p.generer_json(tache="qualification_etude", systeme="s",
                                 utilisateur="u", schema=schemas.INTENTION)
        self.assertEqual(obj["type_etude"], "test_usage_controle")
        self.assertEqual(p.identite, "http:m-1")

    def test_contenu_non_json(self):
        corps = json.dumps({"choices": [{"message": {
            "content": "pas du json"}}]}).encode()
        with patch("urllib.request.urlopen", return_value=self._Resp(corps)):
            p = ProviderOpenAICompatible("http://local.test", "k", "m-1")
            with self.assertRaises(ReponseNonJSON):
                p.generer_json(tache="t", systeme="s", utilisateur="u",
                               schema=schemas.INTENTION)


class TestAgentsAvecLLM(unittest.TestCase):
    DONNEES = generer()

    def test_comprehension_llm(self):
        with tempfile.TemporaryDirectory() as d:
            from llm.simule import provider_simule_defaut
            agent = comprehension.fabriquer(_ctx(d), llm=provider_simule_defaut())
            s = agent(_etat(), {"raw": self.DONNEES["raw"]})
            self.assertEqual(s["type_etude"], "test_usage_controle")
            self.assertIn("mode llm", s["assumptions"][0])

    def test_comprehension_repli_deterministe(self):
        with tempfile.TemporaryDirectory() as d:
            p = ProviderSimule({"qualification_etude": repondre_toujours_invalide})
            agent = comprehension.fabriquer(_ctx(d), llm=p)
            s = agent(_etat(), {"raw": self.DONNEES["raw"]})
            self.assertEqual(s["type_etude"], "test_usage_controle")   # repli OK
            self.assertTrue(any("repli" in c for c in s["contradictions"]))

    def test_biostat_repli_si_proposition_hors_catalogue(self):
        with tempfile.TemporaryDirectory() as d:
            p = ProviderSimule({"proposition_sap": lambda utilisateur, appel: {
                "analyses": [{"id": "A1", "role": "primaire",
                              "op": "bootstrap_magique", "var": "delta_score"}],
                "gestion_multiplicite": "xxxxxxxxxxxx",
                "gestion_manquants_strategie": "yyyyyyyyyyyy"}})
            agent = biostat.fabriquer(_ctx(d), llm=p)
            etat = _etat(); etat.type_etude = "test_usage_controle"
            s = agent(etat, {"spec": self.DONNEES["spec"], "dq": {"score_dq": 1}})
            # repli gabarit : les ops du SAP restent dans le catalogue fermé
            from stats_catalogue.ops import OPS
            for a in s["sap"]["analyses"]:
                self.assertIn(a["op"], OPS)
            self.assertTrue(any("repli" in a for a in s["assumptions"]))

    def test_biostat_rejet_metier_deux_primaires(self):
        with tempfile.TemporaryDirectory() as d:
            deux = [{"id": f"A{i}", "role": "primaire", "op": "mann_whitney",
                     "var": "delta_score"} for i in (1, 2)]
            p = ProviderSimule({"proposition_sap": lambda utilisateur, appel: {
                "analyses": deux,
                "gestion_multiplicite": "xxxxxxxxxxxx",
                "gestion_manquants_strategie": "yyyyyyyyyyyy"}})
            agent = biostat.fabriquer(_ctx(d), llm=p)
            etat = _etat(); etat.type_etude = "test_usage_controle"
            s = agent(etat, {"spec": self.DONNEES["spec"], "dq": {"score_dq": 1}})
            primaires = [a for a in s["sap"]["analyses"]
                         if a["role"] == "primaire"]
            self.assertEqual(len(primaires), 1)     # garde-fou métier appliqué


class TestPipelineModeLLM(unittest.TestCase):
    def test_e2e_llm_simule(self):
        """Le provider simulé (scripté) est reproductible : la pré-liaison
        par rejeu retrouve donc les mêmes versions — chemin LLM intact."""
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            from llm.simule import provider_simule_defaut
            sys_ = environnement(str(r / "rt"), r / "decisions.json",
                                 DECISIONS_OK, _etat(), generer(),
                                 llm=provider_simule_defaut())
            etat = run_pipeline(_etat(), sys_, generer())
            self.assertEqual(etat.statut, "TERMINE")
            ok, _, _ = JournalAudit.verifier(r / "rt" / "audit.jsonl")
            self.assertTrue(ok)
            # le chemin LLM est tracé dans l'audit (GENERATION)
            brut = (r / "rt" / "audit.jsonl").read_text(encoding="utf-8")
            self.assertIn("GENERATION", brut)
            self.assertIn("LLM_MODE", brut)
            # le SAP produit via LLM respecte le contrat aval
            sap = sys_["store"].lire_json(sys_["store"].resoudre(
                "sap", etat.study_id, "sap").ref)
            self.assertEqual(sap["mode_proposition"], "llm")
            primaires = [a for a in sap["analyses"] if a["role"] == "primaire"]
            self.assertEqual(len(primaires), 1)


if __name__ == "__main__":
    unittest.main()
