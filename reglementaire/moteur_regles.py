"""
Moteur de règles réglementaires (moteur v2, contextuel).

Principes :
- règles = données (entrées du référentiel) + évaluateurs typés — chaque
  résultat cite sa référence et la version/hash du corpus ;
- contexte produit pris en compte : application (leave_on/rinse_off), usage,
  zone, population_cible — une restriction s'évalue CONTRE ce contexte ;
- inconnu réglementaire = INCERTAIN (revue experte), jamais OK par défaut ;
- statuts : OK / KO (bloquant) / INCERTAIN (revue) / INFO (avertissement
  d'étiquetage) — l'agent conformité mappe KO→blocage, INCERTAIN→G4.
"""
from __future__ import annotations

import re

SEUIL_METHODE_CONNUE = [
    re.compile(r"test d[' ]usage"), re.compile(r"sous contr[ôo]le"),
    re.compile(r"panel"), re.compile(r"hript"), re.compile(r"patch test"),
]


def _condition_matche(condition: dict, produit: dict) -> bool:
    return all(produit.get(champ) in valeurs
               for champ, valeurs in condition.items())


def _regle(id_, statut, preuve, reference, severite=None):
    return {"regle": id_, "statut": statut, "preuve": preuve,
            "reference": reference, "severite": severite or statut}


def evaluer_composition(ref, produit: dict, composition: list[dict]) -> list[dict]:
    regles = []
    inconnues = []
    for c in composition:
        brut, conc = c["inci"], c.get("concentration_pct", 0.0)
        trouve = ref.chercher_substance(brut)
        if trouve is None:
            inconnues.append(brut)
            regles.append(_regle(
                "R-COUV-01-non-referencee", "INCERTAIN",
                f"{brut!r} absent du référentiel — interdiction/restriction non "
                "vérifiables automatiquement",
                "couverture du corpus — revue expert réglementaire requise"))
            continue
        inci, entree = trouve["inci"], trouve["entree"]

        if trouve["type"] == "annexe_ii":
            # dérogations éventuelles (annexe III) évaluées dans le contexte produit
            mats = [d for d in entree.get("derogations", [])
                    if produit.get("usage") in d.get("usage", [])]
            if mats and conc <= mats[0]["max_pct"]:
                regles.append(_regle(
                    f"R-REG-01-derogation-{inci}", "OK",
                    f"{inci} {conc}% ≤ {mats[0]['max_pct']}% dans le cadre de la "
                    "dérogation annexe III", "annexe II + dérogation III",
                    severite="INFO"))
            else:
                regles.append(_regle(
                    f"R-REG-01-annexe-II-{inci}", "KO",
                    f"{inci} ∈ annexe II (substance interdite) à {conc}% — aucune "
                    "dérogation applicable au contexte produit",
                    "annexe II — substance interdite en UE"))
        elif trouve["type"] == "restriction":
            lims = [l for l in entree["limites"]
                    if _condition_matche(l.get("condition", {}), produit)]
            if not lims:
                regles.append(_regle(
                    f"R-REG-02-contexte-non-couvert-{inci}", "INCERTAIN",
                    f"{inci} restreint (annexe {entree['annexe']}) mais aucune "
                    "limite du corpus ne couvre ce contexte produit",
                    f"annexe {entree['annexe']} — revue experte requise"))
                continue
            for l in lims:
                ok = l.get("verdict") != "KO" and conc <= l["max_pct"]
                note = f" — {l['note']}" if l.get("note") else ""
                regles.append(_regle(
                    f"R-REG-02-{inci}", "OK" if ok else "KO",
                    f"{inci} {conc}% vs limite {l['max_pct']}% "
                    f"(annexe {entree['annexe']}, condition "
                    f"{l.get('condition') or 'générale'}){note}",
                    f"annexe {entree['annexe']}"))
            if entree.get("avertissements") and not any(
                    r["statut"] == "KO" for r in regles if inci in r["regle"]):
                regles.append(_regle(
                    f"R-REG-02-etiquetage-{inci}", "INFO",
                    "avertissements d'étiquetage requis : "
                    + "; ".join(entree["avertissements"]),
                    f"annexe {entree['annexe']} — mentions obligatoires"))
            if entree.get("note"):
                regles.append(_regle(
                    f"R-REG-02-note-{inci}", "INFO", entree["note"],
                    f"annexe {entree['annexe']} — note de corpus"))

    # agrégats (ex. somme des parabens)
    for ag in ref.agregats:
        groupe = {ref.canonique(e["inci"]): e for e in ref.restrictions.values()
                  if e.get("groupe_agregat") == ag["groupe"]}
        somme = sum(c.get("concentration_pct", 0.0)
                    for c in composition
                    if ref.canonique(c["inci"]) in groupe)
        if somme > 0:
            regles.append(_regle(
                f"R-REG-03-{ag['id']}",
                "OK" if somme <= ag["max_pct"] else "KO",
                f"somme {ag['groupe']} = {somme}% vs max {ag['max_pct']}%",
                ag["reference"]))

    couverture = {"total": len(composition),
                  "resolues": len(composition) - len(inconnues),
                  "inconnues": inconnues}
    return regles, couverture


def evaluer_methodes(ref, methodes_test: list[str]) -> list[dict]:
    regles = []
    for m in methodes_test:
        ml = m.lower()
        animale = [motif for motif in ref.motifs_animaux if motif in ml]
        if animale:
            regles.append(_regle(
                "R-MET-01-methode-animale", "KO",
                f"méthode animale détectée : {m!r} (motif {animale[0]!r}) — "
                "des alternatives validées existent",
                "art. 18 UE 1223/2009 — interdiction de l'expérimentation animale"))
            continue
        tg = re.search(r"(?:ocde|tg)\s*(\d{3}[a-z]?)", ml)
        if tg and ref.tg_valide(tg.group(1).upper()):
            entree = ref.tg_valide(tg.group(1).upper())
            regles.append(_regle(
                "R-MET-02-alternative-validee", "OK",
                f"OCDE TG {tg.group(1).upper()} : {entree['intitule']}",
                f"OCDE TG {tg.group(1).upper()} ({entree['statut']})"))
        elif any(p.search(ml) for p in SEUIL_METHODE_CONNUE):
            regles.append(_regle(
                "R-MET-02-methode-humaine-sous-controle", "OK",
                f"méthode humaine sous contrôle : {m!r}",
                "test d'usage sous contrôle — pratique encadrée"))
        else:
            regles.append(_regle(
                "R-MET-03-methode-non-identifiee", "INCERTAIN",
                f"méthode non reconnue par le référentiel : {m!r}",
                "revue méthodologique humaine requise"))
    return regles


def evaluer_donnees_requises(type_etude: str, dossier: dict) -> list[dict]:
    """Données minimales par type d'étude — verdict 'insuffisant' si manques."""
    regles = []
    if type_etude == "test_usage_controle":
        manques = []
        if dossier["n_lignes"] < 30:
            manques.append(f"n={dossier['n_lignes']} < 30 sujets")
        if dossier.get("var_reaction", "reaction_grade") \
                not in dossier.get("variables", []):
            manques.append("données de tolérance (grades) absentes")
        if not dossier.get("composition"):
            manques.append("composition INCI absente")
        produit = dossier.get("produit", {})
        for champ in ("application", "usage", "population_cible"):
            if not produit.get(champ):
                manques.append(f"contexte produit incomplet : {champ} manquant")
        regles.append(_regle(
            "R-DON-01-donnees-minimales", "KO" if manques else "OK",
            "; ".join(manques) if manques
            else "n, tolérance, INCI et contexte produit complets",
            "SCCS Notes of Guidance — évaluation de sécurité avant marché"))
    return regles


def evaluer_dossier(ref, dossier: dict) -> dict:
    regles, couverture = evaluer_composition(
        ref, dossier.get("produit", {}), dossier.get("composition", []))
    regles += evaluer_methodes(ref, dossier.get("methodes_test", []))
    regles += evaluer_donnees_requises(dossier.get("type_etude", ""), dossier)
    bloquantes = [r["regle"] for r in regles if r["statut"] == "KO"]
    incertaines = [r["regle"] for r in regles if r["statut"] == "INCERTAIN"]
    verdict = ("NON_CONFORME_BLOQUANT" if bloquantes else
               "CONFORME_SOUS_RESERVE" if incertaines else
               "CONFORME" if regles else "INSUFFISANT")
    return {"verdict": verdict, "regles": regles,
            "regles_ko": bloquantes, "regles_incertaines": incertaines,
            "couverture_inci": couverture,
            "corpus": {"version": ref.version, "sha256": ref.sha256}}
