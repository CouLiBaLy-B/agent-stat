"""CLI des gates humains (stdlib) — signature et reprise FAIL-CLOSED.

Commandes :
  export  : (ré)écrit le dossier de preuves d'un gate (md + json)
  sign    : dépose une décision recevable (rôle, motif, pièces exigés)
            LIÉE automatiquement à la version courante de l'artefact
            (ref + sha256 résolus depuis le store via --etat) + preuve
            sig-2.0.0 — impossible de signer sans désigner ce qu'on valide
  status  : affiche l'état du pipeline et le gate en attente
  resume  : rejoue le pipeline bloqué après signature (rejeu déterministe)

Exemples :
  python3 -m ui_gates.cli status --racine runtime/obs --etat ckpt.json
  python3 -m ui_gates.cli sign --racine runtime/obs --gate G3 \
      --etat checkpoints/ckpt_<run>_BLOQUE.json \
      --validateur u:bio-042 --role biostatisticien --decision VALIDATED \
      --motif "SAP conforme ICH E9" --pieces sap dq_report
  python3 -m ui_gates.cli resume --racine runtime/obs --etat ckpt.json \
      --donnees donnees.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_REPO))

from core.audit import JournalAudit                          # noqa: E402
from core.state import Etat                                  # noqa: E402
from core.store import StoreArtefacts                        # noqa: E402
from orchestration.pipeline import ROLES_GATES               # noqa: E402
from ui_gates import dossier as dossier_mod                  # noqa: E402
from ui_gates.signature import RegleSignature, deposer_decision  # noqa: E402

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


def cmd_sign(args) -> int:
    roles = ROLES_GATES.get(args.gate, [])
    # LIAISON OBLIGATOIRE : la version signée est résolue depuis le store.
    etat = _etat_depuis(args.etat)
    store = _store(args.racine)
    art = _resoudre_artefact_gate(store, etat.study_id, args.gate,
                                  args.artefact)
    decision = {"statut": args.decision, "validateur_id": args.validateur,
                "role": args.role, "motif": args.motif,
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
            roles)
    except RegleSignature as e:
        print(f"✘ décision refusée (fail-closed) : {e}", file=sys.stderr)
        return 2
    print(f"✔ décision {ecrite['statut']} enregistrée pour {args.gate} "
          f"({ecrite['signature_ref']})")
    print(f"  liée à {ecrite['artefact_ref']} "
          f"sha256:{ecrite['artefact_sha256'][:16]}…")
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
    s.add_argument("--role", required=True)
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
    except (ErreurUsage, OSError, json.JSONDecodeError, KeyError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
