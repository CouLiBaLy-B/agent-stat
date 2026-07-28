"""CLI des gates humains (stdlib) — signature et reprise FAIL-CLOSED.

Commandes :
  comptes : annuaire d'authentification (init / add) — PBKDF2-HMAC-SHA256
            salé par compte, écriture atomique, jamais de secret en clair
  export  : (ré)écrit le dossier de preuves d'un gate (md + json)
  sign    : AUTHENTIFIE le validateur contre l'annuaire (secret prouvé, compte
            actif, anti force brute) puis dépose une décision recevable (motif,
            pièces exigés) LIÉE automatiquement à la version courante de
            l'artefact (ref + sha256 résolus depuis le store via --etat) +
            preuve sig-2.0.0. Le rôle n'est JAMAIS auto-déclaré : il est lu
            dans l'annuaire (--role = contrôle de cohérence / désambiguation).
            Si le mode PSCE est actif, la preuve est scellée (cachet +
            horodatage qualifié SIMULÉS — cf. ui_gates/eidas.py) ; validateur
            sans certificat ou niveau insuffisant ⇒ refus, aucune dégradation.
  status  : affiche l'état du pipeline et le gate en attente
  resume  : rejoue le pipeline bloqué après signature (rejeu déterministe)

Secrets : --secret, ou $AGENT_STAT_SIGN_SECRET, ou saisie masquée
(--interactif). Mode PSCE : --psce ou $AGENT_STAT_PSCE_MODE (off|simulateur).

Exemples :
  python3 -m ui_gates.cli comptes init --comptes runtime/obs/comptes.json
  python3 -m ui_gates.cli comptes add --comptes runtime/obs/comptes.json \
      --ident u:bio-042 --roles biostatisticien
  python3 -m ui_gates.cli status --racine runtime/obs --etat ckpt.json
  python3 -m ui_gates.cli sign --racine runtime/obs --gate G3 \
      --etat checkpoints/ckpt_<run>_BLOQUE.json \
      --validateur u:bio-042 --decision VALIDATED \
      --motif "SAP conforme ICH E9" --pieces sap dq_report \
      --psce simulateur
  python3 -m ui_gates.cli resume --racine runtime/obs --etat ckpt.json \
      --donnees donnees.json
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                          # noqa: E402
from core.state import Etat                                  # noqa: E402
from core.store import StoreArtefacts                        # noqa: E402
from orchestration.pipeline import ROLES_GATES               # noqa: E402
from ui_gates import auth                                    # noqa: E402
from ui_gates import dossier as dossier_mod                  # noqa: E402
from ui_gates import eidas as eidas_mod                      # noqa: E402
from ui_gates.signature import RegleSignature, deposer_decision  # noqa: E402

ENV_SECRET_SIGN = "AGENT_STAT_SIGN_SECRET"

ARTEFACTS_GATES = {
    "G1": ("intention", "intention"),
    "G3": ("sap", "sap"),
    "G4": None,                         # safety OU compliance — auto-détection
    "G5": ("critique", "critique"),
    "G6": ("report", "rapport_draft"),
}


class ErreurUsage(Exception):
    pass


def _etat_depuis(chemin: str) -> Etat:
    return Etat.charger(chemin)


def _store(racine: str) -> StoreArtefacts:
    return StoreArtefacts(Path(racine) / "store")


def _resoudre_artefact_gate(store: StoreArtefacts, study_id: str, gate_id: str,
                            artefact_force: str | None):
    """Résout l'artefact du gate ; G4 teste safety puis compliance."""
    if artefact_force:
        type_, _, name = artefact_force.partition(":")
        if not type_ or not name:
            raise ErreurUsage("--artefact attendu au format TYPE:NOM")
        return store.resoudre(type_, study_id, name)
    if gate_id == "G4":
        for type_, name in (("safety", "safety_report"),
                            ("compliance", "compliance_report")):
            try:
                return store.resoudre(type_, study_id, name)
            except KeyError:
                continue
        raise ErreurUsage("aucun artefact safety/compliance trouvé pour G4")
    cible = ARTEFACTS_GATES.get(gate_id)
    if cible is None:
        raise ErreurUsage(f"gate {gate_id!r} inconnu de la cartographie")
    try:
        return store.resoudre(cible[0], study_id, cible[1])
    except KeyError as e:
        raise ErreurUsage(
            f"artefact {cible[0]}/{cible[1]} introuvable pour {gate_id} — "
            "le pipeline n'est pas encore arrivé à ce gate ?") from e


def cmd_export(args) -> int:
    etat = _etat_depuis(args.etat)
    store = _store(args.racine)
    art = _resoudre_artefact_gate(store, etat.study_id, args.gate,
                                  args.artefact)
    roles = ROLES_GATES.get(args.gate, [])
    out = args.out or str(Path(args.racine) / "exports" / "gates")
    p_md, p_json = dossier_mod.exporter(store, out, etat, args.gate, art,
                                        args.sla, roles)
    print(f"✔ dossier écrit : {p_md}\n✔ métadonnées  : {p_json}")
    return 0


def _secret_signataire(args, usage: str) -> str:
    """Ordre : --secret > $AGENT_STAT_SIGN_SECRET > saisie masquée (interactif).
    Jamais de secret en clair dans un fichier, jamais journalisé."""
    secret = args.secret or os.environ.get(ENV_SECRET_SIGN)
    if secret:
        return secret
    if args.interactif:
        return getpass.getpass(
            f"Secret du compte {args.validateur or args.ident} "
            f"({usage}, jamais affiché ni journalisé) : ")
    raise ErreurUsage(
        f"secret manquant (--secret ou ${ENV_SECRET_SIGN}) — fail-closed : "
        "la console refuse toute opération non authentifiée")


def _role_du_compte(roles_compte: list[str], role_demande: str | None,
                    roles_gate: list[str], gate_id: str) -> str:
    """Le rôle déposé est LU DANS L'ANNUAIRE (jamais auto-déclaré).
    --role sert de contrôle de cohérence et de désambiguation quand le
    compte cumule plusieurs rôles habilités au gate."""
    eligibles = ([r for r in roles_compte if r in roles_gate]
                 if roles_gate else list(roles_compte))
    if role_demande:
        if role_demande not in roles_compte:
            raise ErreurUsage(
                f"rôle {role_demande!r} absent de l'annuaire du compte — le "
                "rôle n'est plus auto-déclaré, il est lu dans l'annuaire")
        if roles_gate and role_demande not in roles_gate:
            raise ErreurUsage(
                f"rôle {role_demande!r} non habilité au gate {gate_id} — "
                f"rôles recevables : {roles_gate}")
        return role_demande
    if not eligibles:
        raise ErreurUsage(
            f"aucun rôle du compte {sorted(roles_compte)} n'est habilité au "
            f"gate {gate_id} — rôles recevables : {roles_gate}")
    if len(eligibles) > 1:
        raise ErreurUsage(
            f"plusieurs rôles du compte habilités au gate {gate_id} "
            f"{sorted(eligibles)} — préciser --role (lu dans l'annuaire, "
            "pas auto-déclaré)")
    return eligibles[0]


def cmd_comptes(args) -> int:
    if args.action == "init":
        apercu = auth.initialiser(args.comptes)
        print(f"✔ annuaire initialisé : {args.comptes} "
              f"({len(apercu['comptes'])} compte)")
        return 0
    args.secret = args.secret or os.environ.get(ENV_SECRET_SIGN)
    if not args.secret:
        args.secret = getpass.getpass(
            f"Secret du nouveau compte {args.ident} (jamais affiché) : ")
    compte = auth.ajouter_compte(args.comptes, args.ident, args.roles,
                                 args.secret, sel=args.sel)
    print(f"✔ compte {args.ident} enregistré (rôles {compte['roles']}, "
          f"PBKDF2 {compte['iterations']} itérations)")
    return 0


def cmd_sign(args) -> int:
    roles = ROLES_GATES.get(args.gate, [])
    # LIAISON OBLIGATOIRE : la version signée est résolue depuis le store.
    etat = _etat_depuis(args.etat)
    store = _store(args.racine)
    art = _resoudre_artefact_gate(store, etat.study_id, args.gate,
                                  args.artefact)

    # AUTHENTIFICATION OBLIGATOIRE — annuaire PBKDF2, anti force brute ;
    # le rôle est lu dans l'annuaire (jamais auto-déclaré recevable).
    chemin_comptes = args.comptes or str(Path(args.racine) / "comptes.json")
    secret = _secret_signataire(args, "authentification signature")
    compte = auth.authentifier(chemin_comptes, args.validateur, secret)
    role = _role_du_compte(compte.get("roles") or [], args.role, roles,
                           args.gate)
    print(f"✔ compte authentifié : {args.validateur} (rôle déposé : {role})")

    # PSCE : off (SES référence chaînée, architecture historique) ou
    # simulateur (cachet + horodatage qualifié simulés — certificat exigé).
    psce = eidas_mod.resoudre_psce(mode=args.psce)
    if psce is not None:
        cert = psce.certificat_du(args.validateur)      # fail-closed sinon
        print(f"✔ PSCE {psce.mode} : certificat {cert.get('serie')} "
              f"(niveau {cert.get('niveau')}, émetteur "
              f"{cert.get('emetteur', '?')[:40]}…)")

    decision = {"statut": args.decision, "validateur_id": args.validateur,
                "role": role, "motif": args.motif,
                "pieces_consultees": args.pieces,
                "artefact_ref": art.ref, "artefact_sha256": art.sha256}
    if args.interactif:
        print("== SIGNATURE INTERACTIVE (entrée vide = ABANDON, aucune "
              "validation par défaut) ==")
        out = str(Path(args.racine) / "exports" / "gates")
        p_md, _ = dossier_mod.exporter(store, out, etat, args.gate, art,
                                       args.sla, roles)
        print(p_md.read_text(encoding="utf-8"))
        statut = input(f"Statut [VALIDATED/REFUSED] ({args.decision}) : ").strip() or None
        decision["statut"] = statut or args.decision
        motif = input(f"Motif (≥ 10 caractères) "
                      f"[{decision['motif']!r}] : ").strip() or args.motif
        decision["motif"] = motif
        if not args.pieces:
            brut = input("Pièces consultées (séparées par des espaces) : ").strip()
            decision["pieces_consultees"] = brut.split() if brut else []
    try:
        audit = JournalAudit(Path(args.racine) / "audit.jsonl")
        ecrite = deposer_decision(
            Path(args.racine) / "decisions.json", audit, args.gate, decision,
            roles, psce=psce)
    except (RegleSignature, eidas_mod.PSCEIndisponible) as e:
        print(f"✘ décision refusée (fail-closed) : {e}", file=sys.stderr)
        return 2
    print(f"✔ décision {ecrite['statut']} enregistrée pour {args.gate} "
          f"({ecrite['signature_ref']})")
    print(f"  liée à {ecrite['artefact_ref']} "
          f"sha256:{ecrite['artefact_sha256'][:16]}…")
    eidas = ecrite["preuve_signature"].get("eidas") or {}
    if eidas.get("cachet_signature"):
        jeton = (eidas.get("horodatage_qualifie") or {}).get("gen_time_utc")
        print(f"  cachet eIDAS {eidas.get('niveau_actuel')} + horodatage "
              f"qualifié simulé {jeton} — intégrité vérifiée au gate")
    return 0


def cmd_status(args) -> int:
    etat = _etat_depuis(args.etat)
    print(f"Étude {etat.study_id} · run {etat.run_id}")
    print(f"  statut : {etat.statut} · phase : {etat.phase}")
    print(f"  scores : DQ={etat.scores.get('fiabilite_donnees')} · "
          f"RA={etat.scores.get('robustesse_analyse')} · "
          f"CC={etat.scores.get('confiance_conclusion')} "
          f"({etat.scores.get('verbalisation_cc')})")
    if etat.blocage:
        b = etat.blocage
        print(f"  BLOCAGE : {b['regle_blocage']} — {b['motif']}")
        print(f"  débloqueur requis : {b['debloqueur_requis']}")
    chemin = Path(args.racine) / "decisions.json"
    if chemin.exists():
        reg = json.loads(chemin.read_text(encoding="utf-8"))
        print(f"  décisions déposées : {sorted(reg)}")
        for g in sorted(reg):
            d = reg[g]
            liaison = (f" → {d['artefact_ref']} "
                       f"(sha256:{str(d['artefact_sha256'])[:12]}…)"
                       if d.get("artefact_ref") else " — NON LIÉE")
            print(f"    {g} : {d.get('statut')} par {d.get('validateur_id')} "
                  f"(rôle {d.get('role')}){liaison}")
            eidas = (d.get("preuve_signature") or {}).get("eidas") or {}
            if eidas.get("cachet_signature"):
                print(f"        cachet {eidas.get('niveau_actuel')} + "
                      f"horodatage qualifié simulé "
                      f"{(eidas.get('horodatage_qualifie') or {}).get('gen_time_utc')}")
    else:
        print("  aucune décision déposée")
    return 0


def cmd_resume(args) -> int:
    from orchestration.reprise import reprendre_pipeline
    etat = _etat_depuis(args.etat)
    donnees = json.loads(Path(args.donnees).read_text(encoding="utf-8"))
    try:
        etat = reprendre_pipeline(etat, args.racine,
                                  str(Path(args.racine) / "decisions.json"),
                                  donnees, llm=args.llm)
    except Exception as e:
        blocage = getattr(getattr(e, "etat", None), "blocage", None)
        if blocage:
            print(f"⏸ nouveau point d'attente/blocage : "
                  f"{blocage['regle_blocage']} — {blocage['motif']}")
            return 3
        raise
    print(f"✔ pipeline {etat.statut} · scores {etat.scores}")
    return 0


def construire_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ui_gates.cli", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sous = p.add_subparsers(dest="commande", required=True)

    c = sous.add_parser("comptes", help="annuaire d'authentification : init / "
                                        "add (PBKDF2 salé, jamais de secret "
                                        "en clair)")
    csous = c.add_subparsers(dest="action", required=True)
    ci = csous.add_parser("init", help="crée un annuaire vide (refuse "
                                       "d'écraser un annuaire existant)")
    ci.add_argument("--comptes", required=True)
    ca = csous.add_parser("add", help="ajoute un compte validateur")
    ca.add_argument("--comptes", required=True)
    ca.add_argument("--ident", required=True)
    ca.add_argument("--roles", nargs="+", required=True)
    ca.add_argument("--secret", default=None,
                    help=f"sinon ${ENV_SECRET_SIGN} ou saisie masquée")
    ca.add_argument("--sel", default=None,
                    help="sel hex FIXE — tests/démos DÉTERMINISTES "
                         "uniquement (jamais en production)")
    ca.add_argument("--interactif", action="store_true")
    ca.add_argument("--validateur", default=None, help=argparse.SUPPRESS)
    c.set_defaults(fn=cmd_comptes)

    e = sous.add_parser("export", help="écrit le dossier de preuves d'un gate")
    e.add_argument("--racine", required=True)
    e.add_argument("--etat", required=True, help="checkpoint JSON de l'état")
    e.add_argument("--gate", required=True, choices=sorted(ARTEFACTS_GATES))
    e.add_argument("--artefact", help="TYPE:NOM (force la cible)")
    e.add_argument("--sla", type=int, default=72)
    e.add_argument("--out", help="dossier de sortie (défaut: exports/gates)")
    e.set_defaults(fn=cmd_export)

    s = sous.add_parser("sign", help="dépose une décision de gate LIÉE à la "
                                     "version courante de l'artefact")
    s.add_argument("--racine", required=True)
    s.add_argument("--gate", required=True, choices=sorted(ARTEFACTS_GATES))
    s.add_argument("--etat", required=True,
                   help="checkpoint JSON (résout l'étude et la version "
                        "d'artefact à lier — jamais de signature aveugle)")
    s.add_argument("--validateur", required=True)
    s.add_argument("--secret", default=None,
                   help=f"preuve du compte — sinon ${ENV_SECRET_SIGN} ou "
                        "saisie masquée (--interactif) ; jamais en fichier")
    s.add_argument("--comptes", default=None,
                   help="annuaire PBKDF2 (défaut : <racine>/comptes.json) — "
                        "absent ⇒ refus (jamais d'auto-déclaration)")
    s.add_argument("--role", default=None,
                   help="contrôle de cohérence / désambiguation — le rôle "
                        "déposé est TOUJOURS lu dans l'annuaire")
    s.add_argument("--psce", default=None,
                   choices=list(eidas_mod.MODES),
                   help="off (défaut historique) | simulateur (cachet + "
                        "horodatage qualifié simulés) — sinon "
                        "$AGENT_STAT_PSCE_MODE")
    s.add_argument("--decision", default=None,
                   choices=["VALIDATED", "REFUSED"])
    s.add_argument("--motif", default="")
    s.add_argument("--pieces", nargs="*", default=[])
    s.add_argument("--interactif", action="store_true",
                   help="affiche le dossier et demande confirmation")
    s.add_argument("--artefact")
    s.add_argument("--sla", type=int, default=72)
    s.set_defaults(fn=cmd_sign)

    t = sous.add_parser("status", help="état du pipeline et gate en attente")
    t.add_argument("--racine", required=True)
    t.add_argument("--etat", required=True)
    t.set_defaults(fn=cmd_status)

    r = sous.add_parser("resume", help="rejoue le pipeline après signature")
    r.add_argument("--racine", required=True)
    r.add_argument("--etat", required=True)
    r.add_argument("--donnees", required=True,
                   help="JSON d'entrée (contract identique au 1er run)")
    r.add_argument("--llm", default="off", choices=["off", "env"],
                   help="off : déterministe pur (reprise reproductible)")
    r.set_defaults(fn=cmd_resume)
    return p


def main(argv: list[str] | None = None) -> int:
    args = construire_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ErreurUsage, auth.AuthRefusee, eidas_mod.PSCEIndisponible,
            OSError, json.JSONDecodeError, KeyError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
