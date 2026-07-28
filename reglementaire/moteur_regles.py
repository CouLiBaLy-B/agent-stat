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
    re.compile(r"physico"), re.compile(r"stabilit[ée]"),
    re.compile(r"vieillissement"), re.compile(r"microbiolog"),
    re.compile(r"challenge test"), re.compile(r"organoleptique"),
    re.compile(r"spectro"), re.compile(r"chromato"),
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


TYPES_OBSERVATIONNELS = {"cas_temoins", "cohorte_prospective",
                         "cohorte_retrospective"}


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
    if type_etude == "stabilite":
        manques = []
        if not dossier.get("var_temps"):
            manques.append("variable temporelle (var_temps) absente")
        bornes = dossier.get("bornes_acceptation", {})
        variables = dossier.get("variables", [])
        if not bornes:
            manques.append("bornes d'acceptation non définies (R-ACCEPT)")
        for var in bornes:
            if var not in variables:
                manques.append(f"paramètre borné '{var}' absent des données")
        if len(set(dossier.get("points_temps", []))) < 3:
            manques.append("moins de 3 points de mesure temporels")
        regles.append(_regle(
            "R-DON-02-donnees-stabilite", "KO" if manques else "OK",
            "; ".join(manques) if manques
            else "bornes, série temporelle ≥ 3 points et paramètres complets",
            "bonnes pratiques stabilité (esprit ICH Q1A adapté cosmétique)"))
    if type_etude in TYPES_OBSERVATIONNELS:
        variables = dossier.get("variables", [])
        manques = []
        if dossier.get("var_exposition") not in variables:
            manques.append("variable d'exposition non déclarée dans les données")
        if type_etude == "cas_temoins":
            if dossier.get("var_issue") not in variables:
                manques.append("variable de statut cas/témoin non déclarée")
            if dossier.get("appariement") \
                    and dossier.get("var_paire") not in variables:
                manques.append("appariement déclaré sans variable de paire")
        else:
            if dossier.get("var_evenement") not in variables:
                manques.append("variable d'événement (issue) non déclarée")
            t_ev = dossier.get("var_temps_event")
            if t_ev and t_ev not in variables:
                manques.append(f"temps de suivi '{t_ev}' absent des données")
        if dossier.get("n_lignes", 0) < 40:
            manques.append(f"n={dossier.get('n_lignes', 0)} < 40 sujets "
                           "(puissance et stabilité des estimations)")
        regles.append(_regle(
            "R-DON-03-donnees-observationnel", "KO" if manques else "OK",
            "; ".join(manques) if manques else
            "exposition, issue et appariement/temps de suivi complet·s ; "
            "effectif suffisant pour l'estimation non ajustée",
            "STROBE — données minimales des études observationnelles"))
        regles.append(_regle(
            "R-OBS-01-lexique-association", "INFO",
            "lexique d'association obligatoire (association ≠ causalité), "
            "analyse non ajustée ⇒ ampleur exploratoire ; les deux sont "
            "contrôlés mécaniquement par la relecture critique",
            "STROBE / bonnes pratiques de vocabulaire causal",
            severite="INFO"))
    return regles


def _norm_texte(s: str) -> str:
    return " ".join(str(s).lower().split())


def evaluer_claims(ref, claims: list[dict], contexte: dict) -> list[dict]:
    """UE 655/2013 — critères communs des allégations (cosmétique).

    - R-CLM-01 étayage (C3) : justificatif exigé pour toute allégation ;
    - R-CLM-02 concordance résultats (C2/C3) : une allégation d'efficacité
      doit être appuyée par un résultat mesuré, interprétable, significatif
      et dans la direction favorable — résultat contredisant ⇒ allégation
      trompeuse ⇒ KO ;
    - R-CLM-03 lexique contrôlé : motifs sanitaires/trompeurs interdits ⇒ KO ;
      « sans X » avec X ∈ annexe II ⇒ KO (C1 : le respect de la loi n'est
      pas un avantage) ; « sans X » autre ⇒ INFO (C5, risque de dénigrement).
    Lexique NON EXHAUSTIF : un OK n'est pas une autorisation, seulement
    « aucun motif contrôlé détecté ».
    """
    ref655 = ref.claims
    regles: list[dict] = []
    resultats = contexte.get("resultats") or {}
    variables = set(contexte.get("variables") or [])
    idx_termes = [(t["motif"].lower(), t) for t in
                  ref655.get("termes_interdits", [])]
    rx_sans = re.compile(ref655.get("regle_sans_x", {})
                         .get("motif_regex", r"sans\s+([a-z\- ]+)"))

    for claim in claims:
        cid = claim.get("id", "?")
        texte = _norm_texte(claim.get("texte", ""))
        type_c = claim.get("type", "marketing")

        # ---- R-CLM-01 : étayage -------------------------------------------
        just = (claim.get("justificatif") or "").strip()
        if just:
            regles.append(_regle(
                f"R-CLM-01-{cid}", "OK",
                f"justificatif déclaré : {just[:90]}",
                "UE 655/2013 C3 — étayage", severite="INFO"))
        else:
            statut = "KO" if type_c in ("efficacite", "tolerance") \
                else "INCERTAIN"
            regles.append(_regle(
                f"R-CLM-01-{cid}", statut,
                f"allégation {cid} ({type_c}) sans justificatif — tout claim "
                "doit être étayé au dossier (C3)",
                "UE 655/2013 C3 — étayage obligatoire"))

        # ---- R-CLM-02 : concordance avec les résultats mesurés ------------
        if type_c == "efficacite":
            endpoint = claim.get("endpoint")
            if not endpoint:
                regles.append(_regle(
                    f"R-CLM-02-{cid}", "INCERTAIN",
                    f"allégation d'efficacité {cid} sans critère de mesure "
                    "associé (endpoint absent)",
                    "UE 655/2013 C2/C3"))
            elif endpoint not in variables:
                regles.append(_regle(
                    f"R-CLM-02-{cid}", "KO",
                    f"critère '{endpoint}' non mesuré dans l'étude — "
                    "allégation non étayable sur ces données",
                    "UE 655/2013 C2/C3"))
            elif not resultats:
                regles.append(_regle(
                    f"R-CLM-02-{cid}", "INCERTAIN",
                    "aucun résultat calculé disponible pour confronter "
                    "l'allégation", "UE 655/2013 C3"))
            else:
                mesurees = [ana.get("resultat", {})
                            for ana in resultats.values()
                            if (ana.get("var")
                                or ana.get("resultat", {}).get("var"))
                            == endpoint
                            and ana.get("role") == "primaire"]
                if not mesurees:
                    mesurees = [ana.get("resultat", {})
                                for ana in resultats.values()
                                if (ana.get("var")
                                    or ana.get("resultat", {}).get("var"))
                                == endpoint]
                if not mesurees:
                    regles.append(_regle(
                        f"R-CLM-02-{cid}", "INCERTAIN",
                        f"aucun résultat calculé sur '{endpoint}'",
                        "UE 655/2013 C3"))
                else:
                    r = mesurees[0]
                    sens = 1.0 if claim.get("direction_favorable", "+1") \
                        not in ("-1", -1) else -1.0
                    p = r.get("p_valeur")
                    diff = r.get("difference")
                    equivalence = r.get("verdict") == "equivalence_demontree"
                    ok = r.get("interpretable") and (
                        equivalence
                        or (p is not None and p < 0.05
                            and (diff is None or diff * sens > 0)))
                    detail = (f"résultat '{endpoint}' : p={p}, "
                              f"différence={diff}, verdict="
                              f"{r.get('verdict')}, interprétable="
                              f"{r.get('interpretable')}")
                    regles.append(_regle(
                        f"R-CLM-02-{cid}", "OK" if ok else "KO",
                        (f"résultat concordant avec l'allégation "
                         f"({detail})") if ok else
                        (f"résultat NON concordant — allégation trompeuse au "
                         f"sens du critère de sincérité ({detail})"),
                        "UE 655/2013 C2 — sincérité"))

        # ---- R-CLM-03 : lexique contrôlé -----------------------------------
        ko_motifs = [t for motif, t in idx_termes if motif in texte]
        infos: list[str] = []
        for m in rx_sans.finditer(texte):
            x = re.split(r"\s+et\s+|[,.]", m.group(1).strip())[0].strip()
            if not x:
                continue
            from reglementaire.referentiel import normaliser_inci
            if normaliser_inci(x) in ref.annexe_ii:
                ko_motifs.append({
                    "motif": f"sans {x}", "categorie": "sans_x_annexe_ii",
                    "motif_legal": f"« sans {x} » alors que {x} est interdite "
                                   "(annexe II) : alléguer le respect de la "
                                   "loi n'est pas un avantage",
                    "reference": "UE 655/2013 C1"})
            else:
                infos.append(
                    f"« sans {x} » : risque de dénigrement d'ingrédients "
                    "conformes (C5) — revue experte")
        if ko_motifs:
            t = ko_motifs[0]
            regles.append(_regle(
                f"R-CLM-03-{cid}", "KO",
                f"motif interdit {t['motif']!r} ({t['categorie']}) : "
                f"{t['motif_legal']}", t["reference"]))
        elif infos:
            regles.append(_regle(
                f"R-CLM-03-{cid}", "INFO", " ; ".join(infos),
                "UE 655/2013 C5", severite="INFO"))
        else:
            regles.append(_regle(
                f"R-CLM-03-{cid}", "OK",
                "aucun motif interdit du lexique contrôlé détecté "
                "(lexique de démonstration non exhaustif)",
                "UE 655/2013 — critères communs", severite="INFO"))
    return regles


def evaluer_etiquetage(ref, etiquette: dict,
                       composition: list[dict]) -> list[dict]:
    """Art. 19 UE 1223/2009 — étiquetage.

    - R-ETQ-01 : mentions obligatoires présentes et non vides (KO sinon) ;
    - R-ETQ-02 : couverture INCI — tout ingrédient déclaré dans la
      composition doit figurer sur l'étiquette (KO sinon) ;
    - R-ETQ-03 : ordre décroissant de concentration pour les ingrédients
      > 1 % (KO sinon) ; ingrédient d'étiquette non déclaré ⇒ INFO.
    """
    from reglementaire.referentiel import normaliser_inci
    regles: list[dict] = []
    art19 = ref.claims.get("reference_etiquetage",
                           "art. 19 UE 1223/2009")
    mentions = ref.claims.get("mentions_obligatoires_etiquetage", [])

    manquantes = [m["libelle"] for m in mentions
                  if not etiquette.get(m["champ"])]
    regles.append(_regle(
        "R-ETQ-01-mentions-obligatoires",
        "KO" if manquantes else "OK",
        ("mentions manquantes ou vides : " + "; ".join(manquantes))
        if manquantes else
        f"{len(mentions)} mentions obligatoires présentes",
        art19))

    etiquettes = [normaliser_inci(e) for e in etiquette.get("liste_inci", [])]
    composees = [(normaliser_inci(c["inci"]),
                  c.get("concentration_pct", 0.0))
                 for c in composition]
    if etiquettes:
        absentes = [inci for inci, _ in composees if inci not in etiquettes]
        regles.append(_regle(
            "R-ETQ-02-couverture-inci",
            "KO" if absentes else "OK",
            ("ingrédients du dossier ABSENTS de l'étiquette : "
             + ", ".join(absentes)) if absentes else
            f"les {len(composees)} ingrédients déclarés figurent tous sur "
            "l'étiquette", art19))

        au_dessus_1 = [(inci, conc) for inci, conc in composees
                       if conc > 1.0 and inci in etiquettes]
        ordre_etiquette = sorted(au_dessus_1,
                                 key=lambda t: etiquettes.index(t[0]))
        attendu = sorted(au_dessus_1, key=lambda t: -t[1])
        desordre = [f"{inci} ({conc} %)" for (inci, conc), (a, _)
                    in zip(ordre_etiquette, attendu) if inci != a]
        regles.append(_regle(
            "R-ETQ-03-ordre-inci",
            "KO" if desordre else "OK",
            ("ordre décroissant > 1 % non respecté : " + ", ".join(desordre))
            if desordre else
            f"{len(au_dessus_1)} ingrédients > 1 % en ordre décroissant "
            "réglementaire", art19))
        non_declares = [e for e in etiquettes
                        if e not in {i for i, _ in composees}]
        if non_declares:
            regles.append(_regle(
                "R-ETQ-03b-incoherence-dossier", "INFO",
                "ingrédients d'étiquette non déclarés dans la composition du "
                "dossier : " + ", ".join(non_declares),
                "cohérence dossier/étiquette", severite="INFO"))
    return regles


def evaluer_stabilite(dossier: dict) -> list[dict]:
    """R-STAB-01 : respect des bornes d'acceptation à l'échéance observée,
    tendance et marge de sécurité (IC de prédiction)."""
    regles = []
    res = dossier.get("resultats") or {}
    bornes = dossier.get("bornes_acceptation", {})
    for aid, ana in res.items():
        if ana.get("op_retenue") != "tendance_lineaire":
            continue
        r = ana.get("resultat", {})
        if not r.get("interpretable"):
            regles.append(_regle(f"R-STAB-01-{aid}", "INCERTAIN",
                                 "tendance non interprétable (points insuffisants)",
                                 "revue surveillance stabilité"))
            continue
        lo, hi = bornes.get(r["var"], [None, None])
        if lo is None:
            continue
        moymax = r["moyenne_tmax"]
        ic = r.get("ic95_prevision_tmax", [None, None])
        if not (lo <= moymax <= hi):
            statut, pourquoi = "KO", (f"moyenne à l'échéance {moymax:.3f} hors "
                                      f"bornes [{lo} ; {hi}]")
        elif ic[0] is not None and (ic[0] < lo or ic[1] > hi):
            statut, pourquoi = "INCERTAIN", (
                f"IC95 % de prédiction [{ic[0]:.3f} ; {ic[1]:.3f}] croise les "
                f"bornes [{lo} ; {hi}] — dérive plausible avant échéance")
        else:
            statut, pourquoi = "OK", (
                f"moyenne {moymax:.3f} et IC de prédiction dans [{lo} ; {hi}] "
                f"(pente {r['pente']:+.4f}/unité temps)")
        regles.append(_regle(f"R-STAB-01-{aid}", statut,
                             f"{r['var']} : {pourquoi}",
                             "bornes d'acceptation du protocole de stabilité"))
    return regles


def evaluer_dossier(ref, dossier: dict) -> dict:
    regles, couverture = evaluer_composition(
        ref, dossier.get("produit", {}), dossier.get("composition", []))
    regles += evaluer_methodes(ref, dossier.get("methodes_test", []))
    regles += evaluer_donnees_requises(dossier.get("type_etude", ""), dossier)
    if dossier.get("type_etude") == "stabilite":
        regles += evaluer_stabilite(dossier)
    if dossier.get("domaine") == "cosmetique":
        composition = dossier.get("composition", [])
        claims = dossier.get("claims") or []
        etiquette = dossier.get("etiquetage")
        if claims:
            regles += evaluer_claims(ref, claims, {
                "resultats": dossier.get("resultats"),
                "variables": dossier.get("variables")})
        else:
            regles.append(_regle(
                "R-CLM-00-aucune-declaree", "INFO",
                "aucune allégation produit déclarée au dossier — le contrôle "
                "UE 655/2013 s'appliquera dès qu'une allégation est revendiquée "
                "(tout claim non déclaré n'est pas vérifié)",
                "UE 655/2013 C3", severite="INFO"))
        if etiquette:
            regles += evaluer_etiquetage(ref, etiquette, composition)
        else:
            regles.append(_regle(
                "R-ETQ-00-non-fourni", "INFO",
                "étiquetage non fourni — mentions art. 19 à produire avant "
                "mise sur le marché (contrôle non bloquant à ce stade)",
                "art. 19 UE 1223/2009", severite="INFO"))
    bloquantes = [r["regle"] for r in regles if r["statut"] == "KO"]
    incertaines = [r["regle"] for r in regles if r["statut"] == "INCERTAIN"]
    verdict = ("NON_CONFORME_BLOQUANT" if bloquantes else
               "CONFORME_SOUS_RESERVE" if incertaines else
               "CONFORME" if regles else "INSUFFISANT")
    return {"verdict": verdict, "regles": regles,
            "regles_ko": bloquantes, "regles_incertaines": incertaines,
            "couverture_inci": couverture,
            "corpus": {"version": ref.version, "sha256": ref.sha256}}
