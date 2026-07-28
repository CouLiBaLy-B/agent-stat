"""Tests du durcissement « liaison signature ↔ hash/version d'artefact ».

Propriétés garanties (fail-closed) :
1. une décision liée (ref + sha256 exacts, preuve sig-2.0.0, depose_le) passe ;
2. une décision liée à une AUTRE version/hash bloque (GATE_LIAISON_INVALIDE) ;
3. une décision SANS liaison bloque quand l'artefact est présenté (orchestrateur) ;
4. un registre retouché après dépôt bloque (empreinte ≠ recalculée) ;
5. une décision déposée HORS SLA (mesuré depuis la 1re ouverture du gate
   pour cette version) bloque — la validité d'une validation est bornée ;
6. la pré-liaison (ui_gates.liaison) produit des décisions conformes et est
   stable entre deux rejeus déterministes.
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import signature as sig
from core.audit import JournalAudit
from core.gates import FichierDecisionsProvider, GestionnaireGates
from core.exceptions import GateExpire, PipelineBloque
from core.state import Etat
from demo.jeu_donnees import DECISIONS_OK, generer
from orchestration.pipeline import run_pipeline
from tests.outillage import ecrire_decisions, environnement
from ui_gates.signature import fabriquer_signature

REF = "art://sap/STU/sap/v1"
SHA = "deadbeef" * 8                                    # 64 caractères hex


def _decision_liee(**kw):
    ref_sig, preuve = fabriquer_signature(
        "G3", "u:bio", "VALIDATED", "justification suffisamment longue",
        ["sap"], REF, SHA)
    d = {"statut": "VALIDATED", "validateur_id": "u:bio",
         "role": "biostatisticien",
         "motif": "justification suffisamment longue",
         "pieces_consultees": ["sap"],
         "signature_ref": ref_sig, "preuve_signature": preuve,
         "artefact_ref": REF, "artefact_sha256": SHA,
         "depose_le": datetime.now(timezone.utc).isoformat()}
    d.update(kw)
    return d


def _manager(d, decisions):
    p = Path(d) / "decisions.json"
    p.write_text(json.dumps(decisions), encoding="utf-8")
    audit = JournalAudit(Path(d) / "audit.jsonl")
    return GestionnaireGates(FichierDecisionsProvider(p), audit,
                             {"G3": ["biostatisticien"]})


class TestPreuveSig2(unittest.TestCase):
    def test_fabriquer_et_verifier(self):
        d = _decision_liee()
        self.assertTrue(sig.verifier_preuve("G3", d))
        p = d["preuve_signature"]
        self.assertEqual(p["format"], "sig-2.0.0")
        self.assertEqual(p["niveau"], "SES_REFERENCE_TRACABLE")
        self.assertEqual(p["algorithme_empreinte"], "sha256")
        self.assertEqual(p["artefact_lie"], {"ref": REF, "sha256": SHA})
        self.assertEqual(p["eidas"]["niveaux_cibles_production"],
                         ["AES", "QES"])
        self.assertIsNone(p["eidas"]["horodatage_qualifie"])

    def test_retouche_detectee(self):
        d = _decision_liee()
        for clef, v in (("motif", "autre texte que celui signé"),
                        ("statut", "REFUSED"),
                        ("pieces_consultees", ["sap", "inventee"]),
                        ("artefact_sha256", "0" * 64),
                        ("validateur_id", "u:usurpateur")):
            t = dict(d, **{clef: v})
            self.assertFalse(sig.verifier_preuve("G3", t),
                             f"retouche de {clef} non détectée")
        t = dict(d)
        t["preuve_signature"] = dict(d["preuve_signature"],
                                     horodatage_utc="2030-01-01T00:00:00+00:00")
        self.assertFalse(sig.verifier_preuve("G3", t))


class TestGatesLies(unittest.TestCase):
    def test_liee_passe_et_audit_marque(self):
        with tempfile.TemporaryDirectory() as d:
            g = _manager(d, {"G3": _decision_liee()})
            dec = g.attendre_decision("G3", REF, sla_h=72,
                                      artefact_sha256=SHA)
            self.assertEqual(dec["validateur_id"], "u:bio")
            lignes = [json.loads(l) for l in
                      (Path(d) / "audit.jsonl").read_text("utf-8").splitlines()]
            valide = next(e for e in lignes if e["action"] == "GATE_VALIDE:G3")
            self.assertTrue(valide["details"]["liee"])
            sla = next(e for e in lignes
                       if e["action"] == "SLA_GATE_MESURE:G3")
            self.assertTrue(sla["details"]["dans_les_delais"])

    def test_autre_version_ou_hash_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            g = _manager(d, {"G3": _decision_liee()})
            with self.assertRaisesRegex(GateExpire, "re-signature exigée"):
                g.attendre_decision("G3", REF, sla_h=72,
                                    artefact_sha256="0" * 64)
        with tempfile.TemporaryDirectory() as d:
            g = _manager(d, {"G3": _decision_liee()})
            with self.assertRaisesRegex(GateExpire, "re-signature exigée"):
                g.attendre_decision("G3", "art://sap/STU/sap/v2", sla_h=72,
                                    artefact_sha256=SHA)

    def test_sans_liaison_bloque_quand_artefact_presente(self):
        with tempfile.TemporaryDirectory() as d:
            nu = {k: v for k, v in _decision_liee().items()
                  if k not in ("artefact_ref", "artefact_sha256",
                               "depose_le", "preuve_signature")}
            g = _manager(d, {"G3": nu})
            with self.assertRaisesRegex(GateExpire, "non liée"):
                g.attendre_decision("G3", REF, sla_h=72, artefact_sha256=SHA)

    def test_registre_retouche_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            t = _decision_liee(motif="motif retouché après dépôt (fraude)")
            g = _manager(d, {"G3": t})
            with self.assertRaisesRegex(GateExpire, "altéré"):
                g.attendre_decision("G3", REF, sla_h=72, artefact_sha256=SHA)
            lignes = [json.loads(l) for l in
                      (Path(d) / "audit.jsonl").read_text("utf-8").splitlines()]
            self.assertTrue(any(e["action"] == "GATE_PREUVE_ALTEREE:G3"
                                for e in lignes))

    def test_depot_hors_sla_bloque_et_mesure_journalisee(self):
        with tempfile.TemporaryDirectory() as d:
            tardif = (datetime.now(timezone.utc)
                      + timedelta(hours=200)).isoformat()
            g = _manager(d, {"G3": _decision_liee(depose_le=tardif)})
            with self.assertRaisesRegex(GateExpire, "hors SLA"):
                g.attendre_decision("G3", REF, sla_h=72, artefact_sha256=SHA)
            lignes = [json.loads(l) for l in
                      (Path(d) / "audit.jsonl").read_text("utf-8").splitlines()]
            mesure = next(e for e in lignes
                          if e["action"] == "SLA_GATE_MESURE:G3")
            self.assertFalse(mesure["details"]["dans_les_delais"])
            self.assertGreater(mesure["details"]["attente_h"], 72.0)


class _Pipeline(unittest.TestCase):
    def _etat(self):
        return Etat(run_id="run-2026-07-27-liaison", study_id="COS-2026-050",
                    domaine="cosmetique", seed=20260727)

    def _prelier(self, d):
        return ecrire_decisions(Path(d) / "decisions.json", DECISIONS_OK,
                                self._etat(), generer())

    def _run_bloque(self, d):
        from orchestration.pipeline import construire_systeme
        sys_ = construire_systeme(str(Path(d) / "rt"),
                                  str(Path(d) / "decisions.json"),
                                  backoff_base_s=0.0)
        with self.assertRaises(PipelineBloque) as ctx:
            run_pipeline(self._etat(), sys_, generer())
        return ctx.exception

    def _audit_actions(self, d):
        return [json.loads(l)["action"] for l in
                (Path(d) / "rt" / "audit.jsonl")
                .read_text("utf-8").splitlines()]


class TestPipelineLiaison(_Pipeline):
    def test_prelieur_decisions_conformes_et_stables(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._prelier(d)
            for g, dec in reg.items():
                self.assertTrue(dec["artefact_ref"].startswith("art://"))
                self.assertEqual(len(dec["artefact_sha256"]), 64)
                self.assertTrue(dec["depose_le"])
                self.assertTrue(sig.verifier_preuve(g, dec))
                self.assertIn("pre-liaison technique",
                              dec["origine_liaison"])
                # la substance métier du gabarit est conservée
                self.assertEqual(dec["motif"], DECISIONS_OK[g]["motif"])
                self.assertEqual(dec["role"], DECISIONS_OK[g]["role"])
            # stabilité du rejeu : mêmes versions liées au second appel
            with tempfile.TemporaryDirectory() as d2:
                reg2 = ecrire_decisions(Path(d2) / "decisions.json",
                                        DECISIONS_OK, self._etat(), generer())
                self.assertEqual(
                    {g: r["artefact_sha256"] for g, r in reg.items()},
                    {g: r["artefact_sha256"] for g, r in reg2.items()})

    def test_run_nominal_avec_decisions_liees(self):
        with tempfile.TemporaryDirectory() as d:
            sys_ = environnement(str(Path(d) / "rt"),
                                 Path(d) / "decisions.json", DECISIONS_OK,
                                 self._etat(), generer())
            etat = run_pipeline(self._etat(), sys_, generer())
            self.assertEqual(etat.statut, "TERMINE")
            valides = [a for a in self._audit_actions(d)
                       if a.startswith("GATE_VALIDE:")]
            self.assertIn("GATE_VALIDE:G3", valides)
            self.assertIn("GATE_VALIDE:G6", valides)

    def test_hash_lie_different_bloque_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._prelier(d)
            reg["G3"]["artefact_sha256"] = "0" * 64   # ≠ version présentée
            Path(d, "decisions.json").write_text(
                json.dumps(reg), encoding="utf-8")
            e = self._run_bloque(d)
            self.assertIn("re-signature exigée", e.motif)
            self.assertIn("GATE_LIAISON_INVALIDE:G3", self._audit_actions(d))

    def test_decision_non_liee_bloque_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._prelier(d)
            for c in ("artefact_ref", "artefact_sha256",
                      "preuve_signature", "depose_le"):
                reg["G3"].pop(c)
            Path(d, "decisions.json").write_text(
                json.dumps(reg), encoding="utf-8")
            e = self._run_bloque(d)
            self.assertIn("non liée", e.motif)

    def test_registre_retouche_bloque_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._prelier(d)
            reg["G3"]["motif"] = "validation obtenue sur un autre contenu, " \
                                 "motif retouché"
            Path(d, "decisions.json").write_text(
                json.dumps(reg), encoding="utf-8")
            e = self._run_bloque(d)
            self.assertIn("altéré", e.motif)
            self.assertIn("GATE_PREUVE_ALTEREE:G3", self._audit_actions(d))

    def test_decision_hors_sla_bloque_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._prelier(d)
            reg["G3"]["depose_le"] = (datetime.now(timezone.utc)
                                      + timedelta(hours=500)).isoformat()
            Path(d, "decisions.json").write_text(
                json.dumps(reg), encoding="utf-8")
            e = self._run_bloque(d)
            self.assertIn("hors SLA", e.motif)
            actions = self._audit_actions(d)
            self.assertIn("SLA_GATE_MESURE:G3", actions)


if __name__ == "__main__":
    unittest.main()
