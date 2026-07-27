"""Tests des composants noyau : audit chaîné, store immuable, gates fail-closed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit import JournalAudit
from core.bus import Bus, enveloppe
from core.exceptions import GateExpire, GateRefuse
from core.gates import FichierDecisionsProvider, GestionnaireGates
from core.store import StoreArtefacts


class TestAudit(unittest.TestCase):
    def test_chaine_et_verification(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "audit.jsonl"
            j = JournalAudit(p)
            j.log("a", "ACTION1", {"x": 1})
            j.log("b", "ACTION2", {"y": 2})
            ok, n, _ = JournalAudit.verifier(p)
            self.assertTrue(ok)
            self.assertEqual(n, 2)
            # reprise : l'objet rechargé poursuit la même chaîne
            j2 = JournalAudit(p)
            j2.log("c", "ACTION3", {})
            ok2, n2, _ = JournalAudit.verifier(p)
            self.assertTrue(ok2)
            self.assertEqual(n2, 3)

    def test_alteration_detectee(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "audit.jsonl"
            j = JournalAudit(p)
            j.log("a", "ACTION1", {"x": 1})
            j.log("a", "ACTION2", {"x": 2})
            lignes = p.read_text(encoding="utf-8").splitlines()
            e = json.loads(lignes[0])
            e["details"]["x"] = 999                       # altération
            lignes[0] = json.dumps(e)
            p.write_text("\n".join(lignes), encoding="utf-8")
            ok, _, msg = JournalAudit.verifier(p)
            self.assertFalse(ok)
            self.assertIn("altérée", msg)


class TestStore(unittest.TestCase):
    def test_versions_immuables(self):
        with tempfile.TemporaryDirectory() as d:
            s = StoreArtefacts(d)
            a1 = s.deposer("sap", "STU", "sap", b'{"v":1}', "agent.biostat")
            a2 = s.deposer("sap", "STU", "sap", b'{"v":2}', "agent.biostat")
            self.assertEqual(a1.version, 1)
            self.assertEqual(a2.supersedes, a1.ref)
            self.assertEqual(s.lire(a1.ref), b'{"v":1}')   # v1 intacte
            self.assertEqual(s.lire(a2.ref), b'{"v":2}')
            self.assertEqual(s.resoudre("sap", "STU", "sap").version, 2)


class TestGates(unittest.TestCase):
    def _manager(self, d, decisions):
        p = Path(d) / "decisions.json"
        p.write_text(json.dumps(decisions), encoding="utf-8")
        audit = JournalAudit(Path(d) / "audit.jsonl")
        return GestionnaireGates(FichierDecisionsProvider(p), audit,
                                 {"G3": ["biostatisticien"]})

    def test_absence_decision_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            g = self._manager(d, {})
            with self.assertRaises(GateExpire):
                g.attendre_decision("G3", "art://sap/x/sap/v1")

    def test_mauvais_role_bloque(self):
        with tempfile.TemporaryDirectory() as d:
            g = self._manager(d, {"G3": {
                "statut": "VALIDATED", "validateur_id": "u:x", "role": "stagiaire",
                "motif": "validation sans le rôle requis",
                "signature_ref": "sig:x"}})
            with self.assertRaises(GateExpire):
                g.attendre_decision("G3", "art://sap/x/sap/v1")

    def test_refus_motive(self):
        with tempfile.TemporaryDirectory() as d:
            g = self._manager(d, {"G3": {
                "statut": "REFUSED", "validateur_id": "u:bio", "role": "biostatisticien",
                "motif": "endpoint secondaire présenté comme primaire",
                "signature_ref": "sig:y"}})
            with self.assertRaises(GateRefuse):
                g.attendre_decision("G3", "art://sap/x/sap/v1")

    def test_validation_ok(self):
        with tempfile.TemporaryDirectory() as d:
            g = self._manager(d, {"G3": {
                "statut": "VALIDATED", "validateur_id": "u:bio",
                "role": "biostatisticien", "motif": "SAP conforme ICH E9",
                "signature_ref": "sig:z"}})
            dec = g.attendre_decision("G3", "art://sap/x/sap/v1")
            self.assertEqual(dec["validateur_id"], "u:bio")


class TestBus(unittest.TestCase):
    def test_contrat_enveloppe(self):
        bus = Bus()
        env = enveloppe("m1", "t1", "run-x", "agent.a", [], 0.9)
        bus.envoyer(env, {"k": "v"}, "orchestrateur")
        self.assertEqual(bus.recevoir("orchestrateur")["payload"]["k"], "v")
        with self.assertRaises(ValueError):
            bus.envoyer({"msg_id": "m"}, {}, "orchestrateur")


if __name__ == "__main__":
    unittest.main()
