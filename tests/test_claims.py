"""Tests du chantier « Claims UE 655/2013 + étiquetage art. 19 ».

Propriétés garanties :
- R-CLM-01 : toute allégation doit être étayée (justificatif obligatoire) ;
- R-CLM-02 : une allégation d'efficacité doit être CONCORDANTE avec un
  résultat mesuré (significatif, direction favorable) — contredite ⇒ KO
  (trompeuse, critère de sincérité) ; critère non mesuré ⇒ KO ;
- R-CLM-03 : motifs interdits (sanitaire/trompeur) ⇒ KO ; « sans X » avec
  X ∈ annexe II ⇒ KO ; autre « sans X » ⇒ INFO (risque de dénigrement C5) ;
- R-ETQ-01/02/03 : mentions art. 19 obligatoires, couverture INCI de
  l'étiquette, ordre décroissant > 1 % — violations ⇒ KO ;
- domaine médical : ces règles ne s'appliquent pas (inchangé) ;
- e2e : claim sanitaire ⇒ blocage NON_CONFORMITE_BLOQUANTE ; nominal avec
  claims conformes ⇒ TERMINE + verdict CONFORME.
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exceptions import PipelineBloque
from core.state import Etat, RegleBlocage
from core.store import StoreArtefacts
from demo.jeu_donnees import DECISIONS_OK, generer
from orchestration.pipeline import run_pipeline
from reglementaire.moteur_regles import (evaluer_claims, evaluer_etiquetage,
                                         evaluer_dossier)
from reglementaire.referentiel import charger_referentiel
from tests.outillage import environnement

REF = charger_referentiel()


def _statuts(regles):
    return {r["regle"].split("-", 2)[1][:3]: r["statut"] for r in regles}


def _regle(regles, prefixe):
    return next(r for r in regles if r["regle"].startswith(prefixe))


def _resultats_ok(var="delta_score", p=0.001, diff=4.2):
    return {"A1": {"role": "primaire", "var": var,
                   "op_retenue": "t_test_welch",
                   "resultat": {"interpretable": True, "p_valeur": p,
                                "difference": diff}}}


class TestClaimsEtayage(unittest.TestCase):
    CTX = {"resultats": _resultats_ok(), "variables": ["delta_score"]}

    def test_justificatif_requis_efficacite_et_tolerance(self):
        r = evaluer_claims(REF, [
            {"id": "A", "type": "efficacite", "texte": "améliore l'aspect",
             "endpoint": "delta_score"},
            {"id": "B", "type": "tolerance", "texte": "testée sous contrôle"},
            {"id": "C", "type": "marketing", "texte": "texture légère"}],
            self.CTX)
        self.assertEqual(_regle(r, "R-CLM-01-A")["statut"], "KO")
        self.assertEqual(_regle(r, "R-CLM-01-B")["statut"], "KO")
        self.assertEqual(_regle(r, "R-CLM-01-C")["statut"], "INCERTAIN")

    def test_justificatif_present_ok(self):
        r = evaluer_claims(REF, [
            {"id": "A", "type": "efficacite", "texte": "améliore l'aspect",
             "endpoint": "delta_score",
             "justificatif": "étude test d'usage 60 sujets 4 semaines"}],
            self.CTX)
        self.assertEqual(_regle(r, "R-CLM-01-A")["statut"], "OK")


class TestClaimsConcordance(unittest.TestCase):
    CLAIM = {"id": "A", "type": "efficacite", "texte": "améliore l'aspect",
             "endpoint": "delta_score", "justificatif": "étude 4 semaines"}

    def test_concordant_ok(self):
        r = evaluer_claims(REF, [dict(self.CLAIM)],
                           {"resultats": _resultats_ok(),
                            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "OK")

    def test_non_significatif_ko_trompeuse(self):
        r = evaluer_claims(REF, [dict(self.CLAIM)],
                           {"resultats": _resultats_ok(p=0.32),
                            "variables": ["delta_score"]})
        rg = _regle(r, "R-CLM-02-A")
        self.assertEqual(rg["statut"], "KO")
        self.assertIn("trompeuse", rg["preuve"])

    def test_direction_defavorable_ko(self):
        r = evaluer_claims(REF, [dict(self.CLAIM)],
                           {"resultats": _resultats_ok(diff=-4.2),
                            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "KO")

    def test_direction_favorable_moins_un(self):
        claim = {**self.CLAIM, "direction_favorable": "-1"}
        r = evaluer_claims(REF, [claim],
                           {"resultats": _resultats_ok(diff=-4.2),
                            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "OK")

    def test_endpoint_non_mesure_ko(self):
        r = evaluer_claims(REF, [{**self.CLAIM, "endpoint": "hydratation"}],
                           {"resultats": _resultats_ok(),
                            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "KO")

    def test_endpoint_absent_incert(self):
        claim = {k: v for k, v in self.CLAIM.items() if k != "endpoint"}
        r = evaluer_claims(REF, [claim],
                           {"resultats": _resultats_ok(),
                            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "INCERTAIN")

    def test_sans_resultats_incert(self):
        r = evaluer_claims(REF, [dict(self.CLAIM)],
                           {"resultats": {}, "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "INCERTAIN")

    def test_resultat_non_interpretable_ko(self):
        r = evaluer_claims(REF, [dict(self.CLAIM)], {
            "resultats": {"A1": {"role": "primaire", "var": "delta_score",
                                 "op_retenue": "t_test_welch",
                                 "resultat": {"interpretable": False,
                                              "p_valeur": None,
                                              "difference": None}}},
            "variables": ["delta_score"]})
        self.assertEqual(_regle(r, "R-CLM-02-A")["statut"], "KO")


class TestClaimsLexique(unittest.TestCase):
    CTX = {"resultats": {}, "variables": []}

    def test_sanitaire_interdit_ko(self):
        for texte in ("Guérit les irritations cutanées",
                      "Aide en cas de psoriasis léger",
                      "Crème qui cicatrise rapidement"):
            r = evaluer_claims(REF, [{"id": "A", "type": "efficacite",
                                      "texte": texte}], self.CTX)
            rg = _regle(r, "R-CLM-03-A")
            self.assertEqual(rg["statut"], "KO", texte)
            self.assertIn("sanitaire", rg["preuve"])

    def test_trompeur_absolu_ko(self):
        for texte in ("Produit sans danger pour la peau",
                      "Sans produit chimique",
                      "Efficace à 100 % dès la première application"):
            r = evaluer_claims(REF, [{"id": "A", "type": "marketing",
                                      "texte": texte}], self.CTX)
            self.assertEqual(_regle(r, "R-CLM-03-A")["statut"], "KO", texte)

    def test_sans_x_annexe_ii_ko(self):
        # hydroquinone ∈ annexe II du corpus : « sans hydroquinone » n'est
        # pas un avantage (C1) — alléguer le respect de la loi est interdit
        r = evaluer_claims(REF, [{"id": "A", "type": "marketing",
                                  "texte": "Soin sans hydroquinone"}], self.CTX)
        rg = _regle(r, "R-CLM-03-A")
        self.assertEqual(rg["statut"], "KO")

    def test_sans_x_autre_info_denigrement(self):
        r = evaluer_claims(REF, [{"id": "A", "type": "marketing",
                                  "texte": "Soin sans alcool ajouté"}], self.CTX)
        rg = _regle(r, "R-CLM-03-A")
        self.assertEqual(rg["statut"], "INFO")
        self.assertIn("dénigrement", rg["preuve"])

    def test_texte_conforme_ok_avec_prudence_lexique(self):
        r = evaluer_claims(REF, [{"id": "A", "type": "efficacite",
                                  "texte": "Améliore visiblement l'aspect de "
                                           "la peau"}], self.CTX)
        rg = _regle(r, "R-CLM-03-A")
        self.assertEqual(rg["statut"], "OK")
        self.assertIn("non exhaustif", rg["preuve"])     # la prudence reste


_ETIQ_OK = {"responsable_nom_adresse": "X SAS, Paris",
            "pays_origine": "France", "contenu_nominal": "50 ml",
            "pao_ou_dluo": "12M", "precautions": "usage externe",
            "numero_lot": "L1", "fonction_produit": "crème visage",
            "liste_inci": ["aqua", "glycerin", "niacinamide",
                           "phenoxyethanol", "dmdm hydantoin"]}
_COMP = [{"inci": "aqua", "concentration_pct": 72.0},
         {"inci": "glycerin", "concentration_pct": 8.0},
         {"inci": "niacinamide", "concentration_pct": 4.0},
         {"inci": "phenoxyethanol", "concentration_pct": 0.5},
         {"inci": "dmdm hydantoin", "concentration_pct": 0.3}]


class TestEtiquetage(unittest.TestCase):
    def test_conforme(self):
        r = evaluer_etiquetage(REF, dict(_ETIQ_OK), list(_COMP))
        self.assertEqual(_regle(r, "R-ETQ-01")["statut"], "OK")
        self.assertEqual(_regle(r, "R-ETQ-02")["statut"], "OK")
        self.assertEqual(_regle(r, "R-ETQ-03-")["statut"], "OK")

    def test_mentions_manquantes_ko(self):
        e = dict(_ETIQ_OK)
        del e["numero_lot"], e["precautions"]
        r = evaluer_etiquetage(REF, e, list(_COMP))
        rg = _regle(r, "R-ETQ-01")
        self.assertEqual(rg["statut"], "KO")
        self.assertIn("précautions", rg["preuve"])

    def test_couverture_inci_ko(self):
        e = dict(_ETIQ_OK,
                 liste_inci=["aqua", "glycerin", "niacinamide",
                             "phenoxyethanol"])
        r = evaluer_etiquetage(REF, e, list(_COMP))
        rg = _regle(r, "R-ETQ-02")
        self.assertEqual(rg["statut"], "KO")
        self.assertIn("dmdm hydantoin", rg["preuve"])

    def test_ordre_inci_ko(self):
        e = dict(_ETIQ_OK,
                 liste_inci=["glycerin", "aqua", "niacinamide",
                             "phenoxyethanol", "dmdm hydantoin"])
        r = evaluer_etiquetage(REF, e, list(_COMP))
        rg = _regle(r, "R-ETQ-03-")
        self.assertEqual(rg["statut"], "KO")

    def test_etiquette_avec_non_declare_info(self):
        e = dict(_ETIQ_OK, liste_inci=_ETIQ_OK["liste_inci"] + ["parfum"])
        r = evaluer_etiquetage(REF, e, list(_COMP))
        self.assertEqual(_regle(r, "R-ETQ-03b")["statut"], "INFO")


class TestDossierAgrege(unittest.TestCase):
    def test_claims_et_etiquette_alimentent_le_verdict(self):
        dossier = {
            "domaine": "cosmetique", "type_etude": "test_usage_controle",
            "produit": {"application": "leave_on", "usage": "creme_visage",
                        "zone": "visage", "population_cible": "adulte"},
            "composition": list(_COMP),
            "methodes_test": ["test d'usage sous contrôle"],
            "n_lignes": 60, "variables": ["sujet", "groupe", "delta_score"],
            "var_reaction": "reaction_grade",
            "claims": [{"id": "A", "type": "efficacite",
                        "texte": "Guérit les rougeurs"}],
            "etiquetage": dict(_ETIQ_OK),
            "resultats": _resultats_ok(),
        }
        res = evaluer_dossier(REF, dossier)
        self.assertEqual(res["verdict"], "NON_CONFORME_BLOQUANT")
        self.assertTrue(any(k.startswith("R-CLM-03")
                            for k in res["regles_ko"]))

    def test_domaine_medical_ne_declenche_pas_les_regles_claims(self):
        dossier = {"domaine": "medical", "type_etude": "cas_temoins",
                   "produit": {}, "composition": [], "methodes_test": [],
                   "n_lignes": 0, "variables": []}
        res = evaluer_dossier(REF, dossier)
        self.assertFalse(any(r["regle"].startswith(("R-CLM", "R-ETQ"))
                             for r in res["regles"]))

    def test_cosmetique_sans_claims_ni_etiquette_info_non_bloquant(self):
        dossier = {"domaine": "cosmetique", "type_etude": "stabilite",
                   "produit": {}, "composition": [], "methodes_test": [],
                   "n_lignes": 24, "variables": ["ph"],
                   "var_temps": "t", "bornes_acceptation": {"ph": [5, 8]},
                   "points_temps": [0, 1, 3, 6, 12], "resultats": {}}
        res = evaluer_dossier(REF, dossier)
        info = {r["regle"]: r["statut"] for r in res["regles"]}
        self.assertEqual(info["R-CLM-00-aucune-declaree"], "INFO")
        self.assertEqual(info["R-ETQ-00-non-fourni"], "INFO")
        self.assertNotEqual(res["verdict"], "NON_CONFORME_BLOQUANT")

    def test_corpus_claims_dans_le_hash(self):
        """Le bloc UE 655/2013 + art. 19 fait partie du corpus hashé/versionné."""
        import hashlib
        import json as _json
        from reglementaire import referentiel as refmod
        corp = {n: _json.loads((refmod.DONNEES / f"{n}.json")
                               .read_text(encoding="utf-8"))
                for n in ("annexe_ii", "restrictions", "substances_connues",
                          "methodes_alternatives_oecd", "sccs_params",
                          "claims_etiquetage")}
        recalcule = hashlib.sha256(_json.dumps(
            corp, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(recalcule, REF.sha256)
        sans_claims = dict(corp)
        del sans_claims["claims_etiquetage"]
        self.assertNotEqual(REF.sha256, hashlib.sha256(_json.dumps(
            sans_claims, sort_keys=True,
            ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(len(REF.claims["criteres_ue655"]), 6)   # C1..C6
        self.assertEqual(len(REF.claims["mentions_obligatoires_etiquetage"]),
                         8)


class TestE2EClaims(unittest.TestCase):
    @staticmethod
    def _etat():
        return Etat(run_id="run-2026-07-28-clm", study_id="COS-2026-060",
                    domaine="cosmetique", seed=20260727)

    def _sys(self, d, donnees):
        return environnement(str(Path(d) / "rt"),
                             Path(d) / "decisions.json", DECISIONS_OK,
                             self._etat(), donnees)

    def _compliance(self, d, sid="COS-2026-060"):
        st = StoreArtefacts(Path(d) / "rt" / "store")
        return st.lire_json(st.resoudre("compliance", sid,
                                        "compliance_report").ref)

    def test_nominal_claims_conformes_termine(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            etat = run_pipeline(self._etat(), self._sys(d, donnees), donnees)
            self.assertEqual(etat.statut, "TERMINE")
            comp = self._compliance(d)
            self.assertEqual(comp["verdict"], "CONFORME")
            present = {r["regle"] for r in comp["regles"]}
            for attendu in ("R-CLM-01-C1", "R-CLM-02-C1", "R-CLM-03-C3",
                            "R-ETQ-01-mentions-obligatoires",
                            "R-ETQ-02-couverture-inci",
                            "R-ETQ-03-ordre-inci"):
                self.assertIn(attendu, present)

    def test_claim_sanitaire_bloque_le_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["claims"] = list(donnees["claims"]) + [
                {"id": "C9", "type": "efficacite",
                 "texte": "Guérit les irritations cutanées rebelles",
                 "justificatif": "brochure marketing v3"}]
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat(), self._sys(d, donnees), donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)
            self.assertTrue(any("R-CLM-03" in k
                                for k in self._compliance(d)["regles_ko"]))

    def test_claim_contredit_par_les_resultats_bloque(self):
        """La direction favorable revendiquée (-1, réduction) est contredite
        par le résultat mesuré (effet positif produit) ⇒ sincérité KO."""
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["claims"][0]["direction_favorable"] = "-1"
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat(), self._sys(d, donnees), donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)
            comp = self._compliance(d)
            self.assertTrue(any(k.startswith("R-CLM-02")
                                for k in comp["regles_ko"]))

    def test_etiquetage_incoherent_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            donnees = generer()
            donnees["etiquetage"] = copy.deepcopy(donnees["etiquetage"])
            donnees["etiquetage"]["liste_inci"] = [
                "glycerin", "aqua", "niacinamide", "phenoxyethanol"]
            with self.assertRaises(PipelineBloque) as ctx:
                run_pipeline(self._etat(), self._sys(d, donnees), donnees)
            self.assertEqual(ctx.exception.regle,
                             RegleBlocage.NON_CONFORMITE_BLOQUANTE)


if __name__ == "__main__":
    unittest.main()
