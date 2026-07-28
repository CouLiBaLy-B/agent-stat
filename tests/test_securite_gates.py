"""Tests du durcissement sécurité des gates : authentification (annuaire
PBKDF2 + anti force brute), cachets eIDAS SIMULÉS (PSCE), vérification
d'intégrité des cachets au gate et câblage CLI.

Rappel d'architecture : les cachets « AES_SIMULE »/« QES_SIMULE » et les
jetons « TSA_SIMULEE » sont recalculables publiquement — ils prouvent la
STRUCTURE de la preuve eIDAS et le câblage fail-closed, PAS la cryptographie
(PSCQ + RFC 3161 réels derrière le même contrat en production).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import eidas as moteur                              # noqa: E402
from core import signature as sig                             # noqa: E402
from core.audit import JournalAudit                          # noqa: E402
from core.exceptions import GateExpire, PipelineBloque        # noqa: E402
from core.gates import FichierDecisionsProvider, GestionnaireGates  # noqa: E402
from core.state import Etat                                   # noqa: E402
from demo.jeu_donnees import CERTIFICATS_PSCE_DEMO, COMPTES_DEMO, generer  # noqa: E402
from orchestration.pipeline import ROLES_GATES, construire_systeme, run_pipeline  # noqa: E402
from tests import outillage                                   # noqa: E402
from ui_gates import auth                                     # noqa: E402
from ui_gates import cli                                      # noqa: E402
from ui_gates import eidas as eidas_mod                       # noqa: E402
from ui_gates.signature import deposer_decision               # noqa: E402

EMP = "ab" * 32
TZ = timezone.utc


# ------------------------------------------------------------------- annuaire

class TestAnnuaireAuth(unittest.TestCase):
    def _chemin(self, d):
        return Path(d) / "comptes.json"

    def test_init_puis_refus_decraer(self):
        with tempfile.TemporaryDirectory() as d:
            auth.initialiser(self._chemin(d))
            with self.assertRaisesRegex(auth.AuthRefusee,
                                        "ré-initialisation refusée"):
                auth.initialiser(self._chemin(d))

    def test_annuaire_absent_refuse_par_defaut(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(auth.AuthRefusee, "absent"):
                auth.authentifier(self._chemin(d), "u:x", "s")

    def test_secret_jamais_en_clair_et_pbkdf2_salable(self):
        with tempfile.TemporaryDirectory() as d:
            c = self._chemin(d)
            auth.initialiser(c)
            auth.ajouter_compte(c, "u:a", ["biostatisticien"], "secret-de-a",
                                sel="aa11bb22")
            brut = c.read_text(encoding="utf-8")
            self.assertNotIn("secret-de-a", brut)
            compte = json.loads(brut)["comptes"]["u:a"]
            # la valeur stockée est recalculable publiquement (PBKDF2 salé)
            attendu = auth._pbkdf2("secret-de-a", "aa11bb22",
                                   compte["iterations"])
            self.assertEqual(compte["pbkdf2_sha256"], attendu)
            self.assertNotEqual(compte["pbkdf2_sha256"], "secret-de-a")

    def test_compte_doublon_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            c = self._chemin(d)
            auth.initialiser(c)
            auth.ajouter_compte(c, "u:a", ["x"], "s1", sel="aa11")
            with self.assertRaisesRegex(auth.AuthRefusee, "déjà présent"):
                auth.ajouter_compte(c, "u:a", ["x"], "s2", sel="bb22")

    def test_authentifier_ok_remise_zero(self):
        with tempfile.TemporaryDirectory() as d:
            c = self._chemin(d)
            auth.initialiser(c)
            auth.ajouter_compte(c, "u:a", ["biostatisticien"], "bon",
                                sel="aa11", iterations=1000)
            try:
                auth.authentifier(c, "u:a", "faux")
            except auth.AuthRefusee:
                pass
            compte = auth.authentifier(c, "u:a", "bon")
            self.assertEqual(compte["echecs_consecutifs"], 0)

    def test_verrou_temporel_deterministe(self):
        with tempfile.TemporaryDirectory() as d:
            c = self._chemin(d)
            auth.initialiser(c)
            auth.ajouter_compte(c, "u:a", ["x"], "bon", sel="aa11",
                                iterations=1000)
            t0 = datetime(2026, 7, 28, 10, 0, tzinfo=TZ)
            for _ in range(auth.MAX_ECHECS):
                with self.assertRaises(auth.AuthRefusee):
                    auth.authentifier(c, "u:a", "faux", maintenant=t0)
            # verrouillé pendant la fenêtre…
            with self.assertRaisesRegex(auth.AuthRefusee, "verrouill"):
                auth.authentifier(c, "u:a", "bon",
                                  maintenant=t0 + timedelta(minutes=1))
            # …et libéré après
            compte = auth.authentifier(
                c, "u:a", "bon",
                maintenant=t0 + timedelta(minutes=auth.VERROU_MIN + 1))
            self.assertEqual(compte["roles"], ["x"])
            self.assertIsNone(compte["verrouille_jusqu_a"])
            self.assertEqual(compte["echecs_consecutifs"], 0)

    def test_compte_desactive_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            c = self._chemin(d)
            auth.initialiser(c)
            auth.ajouter_compte(c, "u:a", ["x"], "bon", sel="aa11",
                                actif=False, iterations=1000)
            with self.assertRaisesRegex(auth.AuthRefusee, "désactivé"):
                auth.authentifier(c, "u:a", "bon")


# ------------------------------------------------------------------- cachets

class TestMoteurEidas(unittest.TestCase):
    CERT = CERTIFICATS_PSCE_DEMO["u:bio-042"]

    def test_cachet_aller_retour_et_altérations(self):
        cachet = moteur.fabriquer_cachet(EMP, self.CERT,
                                         emis_le_utc="2026-07-28T10:00:00+00:00")
        self.assertTrue(moteur.verifier_cachet(cachet, EMP))
        self.assertFalse(moteur.verifier_cachet(cachet, "cd" * 32))
        for clef, val in (("valeur", "0" * 64),
                          ("emis_le_utc", "2001-01-01T00:00:00+00:00")):
            t = dict(cachet, **{clef: val})
            self.assertFalse(moteur.verifier_cachet(t, EMP), clef)
        cert2 = dict(self.CERT, serie="PSCE-0000-XXXX")
        self.assertFalse(moteur.verifier_cachet(
            dict(cachet, certificat=cert2), EMP))
        self.assertFalse(moteur.verifier_cachet(
            dict(cachet, format="autre"), EMP))
        self.assertIn("SIMULE", cachet["avertissement"].upper())

    def test_jeton_tsa_aller_retour_et_altérations(self):
        jeton = moteur.fabriquer_jeton_tsa(EMP, serial=7,
                                           gen_time_utc="2026-07-28T10:00:00+00:00")
        self.assertTrue(moteur.verifier_jeton_tsa(jeton, EMP))
        self.assertFalse(moteur.verifier_jeton_tsa(jeton, "cd" * 32))
        self.assertFalse(moteur.verifier_jeton_tsa(
            dict(jeton, gen_time_utc="2001-01-01T00:00:00+00:00"), EMP))
        self.assertFalse(moteur.verifier_jeton_tsa(dict(jeton, serial=8), EMP))
        self.assertFalse(moteur.verifier_jeton_tsa(
            dict(jeton, jeton="0" * 64), EMP))

    def test_niveau_ordre(self):
        self.assertTrue(moteur.niveau_au_moins("QES_SIMULE", "AES_SIMULE"))
        self.assertFalse(moteur.niveau_au_moins("SES", "AES_SIMULE"))
        self.assertFalse(moteur.niveau_au_moins("INCONNU", "AES_SIMULE"))


class TestPSCE(unittest.TestCase):
    def test_certificat_absent_refuse(self):
        psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
        with self.assertRaisesRegex(eidas_mod.PSCEIndisponible,
                                    "aucun certificat"):
            psce.certificat_du("u:stagiaire-9")

    def test_niveau_insuffisant_refuse(self):
        psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
        with self.assertRaisesRegex(eidas_mod.PSCEIndisponible, "niveau SES"):
            psce.fabrique_eidas("u:tech-003", EMP, "G3")

    def test_fabrique_eidas_verifiable_et_serial_monotone(self):
        psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
        b1 = psce.fabrique_eidas("u:bio-042", EMP, "G3")
        b2 = psce.fabrique_eidas("u:bio-042", EMP, "G3")
        self.assertEqual(b1["niveau_actuel"], "QES_SIMULE")
        self.assertTrue(moteur.verifier_cachet(b1["cachet_signature"], EMP))
        self.assertTrue(moteur.verifier_jeton_tsa(
            b1["horodatage_qualifie"], EMP))
        self.assertEqual(b2["horodatage_qualifie"]["serial"],
                         b1["horodatage_qualifie"]["serial"] + 1)

    def test_resoudre_modes(self):
        self.assertIsNone(eidas_mod.resoudre_psce(mode="off"))
        self.assertEqual(eidas_mod.resoudre_psce(mode="simulateur").mode,
                         "simulateur")
        with self.assertRaises(eidas_mod.PSCEIndisponible):
            eidas_mod.resoudre_psce(mode="production-non-branchee")


class TestCachetsDansPreuve(unittest.TestCase):
    def _preuve_scellee(self):
        psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
        preuve = sig.fabriquer_preuve("G3", "u:bio-042", "VALIDATED",
                                      "motif de test suffisamment long",
                                      ["sap"], "art://sap/X/sap/v1", "aa" * 32,
                                      horodatage_utc="2026-07-28T10:00:00+00:00")
        preuve = dict(preuve)
        preuve["eidas"] = psce.fabrique_eidas("u:bio-042",
                                              preuve["empreinte"], "G3")
        return preuve

    def test_absence_de_cachet_recevable(self):
        preuve = sig.fabriquer_preuve("G3", "u:bio-042", "VALIDATED", "m" * 12,
                                      ["sap"], "art://sap/X/sap/v1", "aa" * 32)
        self.assertTrue(sig.verifier_cachets_eidas(preuve))

    def test_cachet_integre_puis_falsification(self):
        preuve = self._preuve_scellee()
        self.assertTrue(sig.verifier_cachets_eidas(preuve))
        eidas = dict(preuve["eidas"])
        eidas["cachet_signature"] = dict(eidas["cachet_signature"],
                                         valeur="0" * 64)
        self.assertFalse(sig.verifier_cachets_eidas(
            dict(preuve, eidas=eidas)))

    def test_gate_bloque_cachet_alteree(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            audit = JournalAudit(racine / "audit.jsonl")
            decisions = racine / "decisions.json"
            psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
            deposer_decision(decisions, audit, "G3", {
                "statut": "VALIDATED", "validateur_id": "u:bio-042",
                "role": "biostatisticien",
                "motif": "SAP relu et conforme au verrou",
                "pieces_consultees": ["sap"],
                "artefact_ref": "art://sap/X/sap/v1",
                "artefact_sha256": "aa" * 32}, ROLES_GATES["G3"], psce=psce)
            # falsifie le cachet dans le registre
            reg = json.loads(decisions.read_text("utf-8"))
            reg["G3"]["preuve_signature"]["eidas"]["cachet_signature"][
                "valeur"] = "0" * 64
            decisions.write_text(json.dumps(reg, ensure_ascii=False),
                                 encoding="utf-8")
            g = GestionnaireGates(FichierDecisionsProvider(decisions), audit,
                                  ROLES_GATES)
            with self.assertRaisesRegex(GateExpire, "cachet eIDAS altéré"):
                g.attendre_decision("G3", "art://sap/X/sap/v1",
                                    artefact_sha256="aa" * 32)
            lignes = [json.loads(l) for l in
                      (racine / "audit.jsonl").read_text("utf-8").splitlines()]
            self.assertTrue(any(e["action"] == "GATE_CACHET_INVALIDE:G3"
                                for e in lignes))

    def test_depot_sans_psce_puis_avec_psce(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            audit = JournalAudit(racine / "audit.jsonl")
            decisions = racine / "decisions.json"
            ech = deposer_decision(decisions, audit, "G3", {
                "statut": "VALIDATED", "validateur_id": "u:bio-042",
                "role": "biostatisticien", "motif": "motif suffisamment long",
                "pieces_consultees": ["sap"],
                "artefact_ref": "art://sap/X/sap/v1",
                "artefact_sha256": "aa" * 32}, ROLES_GATES["G3"])
            eidas = ech["preuve_signature"]["eidas"]
            self.assertIsNone(eidas.get("cachet_signature"))
            psce = eidas_mod.PSCESimulation(CERTIFICATS_PSCE_DEMO)
            ech2 = deposer_decision(decisions, audit, "G3", {
                "statut": "VALIDATED", "validateur_id": "u:bio-042",
                "role": "biostatisticien", "motif": "motif suffisamment long",
                "pieces_consultees": ["sap"],
                "artefact_ref": "art://sap/X/sap/v1",
                "artefact_sha256": "aa" * 32}, ROLES_GATES["G3"], psce=psce)
            self.assertTrue(sig.verifier_cachets_eidas(
                ech2["preuve_signature"]))
            # psce fail-closed : validateur sans certificat
            with self.assertRaises(eidas_mod.PSCEIndisponible):
                deposer_decision(decisions, audit, "G3", {
                    "statut": "VALIDATED", "validateur_id": "u:stagiaire-9",
                    "role": "biostatisticien",
                    "motif": "motif suffisamment long",
                    "pieces_consultees": ["sap"],
                    "artefact_ref": "art://sap/X/sap/v1",
                    "artefact_sha256": "aa" * 32}, ROLES_GATES["G3"], psce=psce)


# ------------------------------------------------------------------- CLI

class TestCLISecurite(unittest.TestCase):
    def _pipeline_a_l_attente(self, racine: Path):
        donnees = generer()
        (racine / "donnees.json").write_text(
            json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
        etat = Etat(run_id="run-sec-cli", study_id="COS-2026-060",
                    domaine="cosmetique", seed=20260728)
        with self.assertRaises(PipelineBloque):
            run_pipeline(etat, construire_systeme(
                str(racine), str(racine / "decisions.json"),
                backoff_base_s=0.0), donnees)
        return sorted((racine / "checkpoints").glob("ckpt_*.json"))[-1]

    def test_comptes_init_add_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = str(Path(d) / "comptes.json")
            rc = cli.main(["comptes", "init", "--comptes", c])
            self.assertEqual(rc, 0)
            rc = cli.main(["comptes", "init", "--comptes", c])
            self.assertEqual(rc, 2)          # écrasement refusé
            rc = cli.main(["comptes", "add", "--comptes", c,
                           "--ident", "u:bio-042", "--roles", "biostatisticien",
                           "--secret", "bio-demo-2026", "--sel", "d3f1cb0a"])
            self.assertEqual(rc, 0)

    def test_sign_exige_annuaire_et_secret(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            ckpt = self._pipeline_a_l_attente(racine)
            base = ["sign", "--racine", str(racine), "--gate", "G3",
                    "--etat", str(ckpt), "--validateur", "u:bio-042",
                    "--decision", "VALIDATED",
                    "--motif", "SAP relu et conforme au verrou",
                    "--pieces", "sap"]
            # pas d'annuaire : refus (même avec un secret passé)
            rc = cli.main(base + ["--secret", "bio-demo-2026"])
            self.assertEqual(rc, 2)
            # annuaire créé, secret absent : refus
            outillage.creer_comptes_demo(racine)
            rc = cli.main(base)
            self.assertEqual(rc, 2)
            # mauvais secret : refus
            rc = cli.main(base + ["--secret", "faux"])
            self.assertEqual(rc, 2)
            self.assertFalse((racine / "decisions.json").exists()
                             and "G3" in json.loads(
                                 (racine / "decisions.json").read_text("utf-8")))

    def test_sign_scelle_psce_et_status_l_affiche(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            ckpt = self._pipeline_a_l_attente(racine)
            outillage.creer_comptes_demo(racine)
            os.environ["AGENT_STAT_PSCE_MODE"] = "simulateur"
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main(["sign", "--racine", str(racine),
                                   "--gate", "G3", "--etat", str(ckpt),
                                   "--validateur", "u:bio-042",
                                   *outillage.args_sign(racine, "u:bio-042"),
                                   "--decision", "VALIDATED",
                                   "--motif", "SAP relu et conforme",
                                   "--pieces", "sap", "dq_report"])
                self.assertEqual(rc, 0, buf.getvalue())
            finally:
                os.environ.pop("AGENT_STAT_PSCE_MODE", None)
            reg = json.loads((racine / "decisions.json").read_text("utf-8"))
            eidas = reg["G3"]["preuve_signature"]["eidas"]
            self.assertEqual(eidas["niveau_actuel"], "QES_SIMULE")
            self.assertTrue(sig.verifier_cachets_eidas(
                reg["G3"]["preuve_signature"]))
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(["status", "--racine", str(racine),
                          "--etat", str(ckpt)])
            self.assertIn("QES_SIMULE", buf.getvalue())

    def test_sign_psce_certificat_absent_refuse_via_cli(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            ckpt = self._pipeline_a_l_attente(racine)
            comptes = outillage.creer_comptes_demo(racine)
            auth.ajouter_compte(comptes, "u:bio-777", ["biostatisticien"],
                                "bio7-demo-2026", sel="77aa11bb",
                                iterations=10_000)
            rc = cli.main(["sign", "--racine", str(racine), "--gate", "G3",
                           "--etat", str(ckpt), "--validateur", "u:bio-777",
                           "--secret", "bio7-demo-2026",
                           "--psce", "simulateur",
                           "--decision", "VALIDATED",
                           "--motif", "SAP relu et conforme",
                           "--pieces", "sap"])
            self.assertEqual(rc, 2)

    def test_role_lu_dans_annuaire_pas_autodeclare(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            c = outillage.creer_comptes_demo(racine)
            # le compte bio-042 n'a que 'biostatisticien' dans l'annuaire :
            # demander --role responsable_etude est incohérent → refus
            compte = auth.authentifier(c, "u:bio-042",
                                       outillage.SECRET_DEMO["u:bio-042"])
            self.assertEqual(compte["roles"], ["biostatisticien"])
            with self.assertRaises(cli.ErreurUsage):
                cli._role_du_compte(compte["roles"], "responsable_etude",
                                    ROLES_GATES["G6"], "G6")
            # désambiguation : compte multi-rôles → --role exigé
            with self.assertRaises(cli.ErreurUsage):
                cli._role_du_compte(["biostatisticien", "responsable_etude"],
                                    None, ROLES_GATES["G6"], "G6")
            self.assertEqual(
                cli._role_du_compte(["biostatisticien", "responsable_etude"],
                                    "biostatisticien", ROLES_GATES["G6"],
                                    "G6"),
                "biostatisticien")


if __name__ == "__main__":
    unittest.main()
