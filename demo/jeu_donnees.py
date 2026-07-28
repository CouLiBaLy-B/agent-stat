"""Générateur déterministe du cas d'usage démo : test d'usage cosmétique.

60 sujets, 2 groupes (produit / contrôle), endpoint continu `delta_score`,
grades de réaction cutanée, composition INCI, méthodes alternatives déclarées.
Toutes les données sont SYNTHÉTIQUES (RNG seedé) — aucune donnée personnelle.
"""
from __future__ import annotations

import random


def generer(seed: int = 20260727, n_par_groupe: int = 30,
            manquants_endpoint: int = 0, age_desequilibre: float = 0.0) -> dict:
    rng = random.Random(seed)
    rows = []
    for i in range(2 * n_par_groupe):
        groupe = "produit" if i < n_par_groupe else "controle"
        mu = 12.0 if groupe == "produit" else 8.0
        rows.append({
            "sujet": f"S{i + 1:03d}",
            "groupe": groupe,
            "date_v1": "2026-03-02",
            "date_v2": "2026-03-30",
            "age": round(rng.gauss(35 + (age_desequilibre if groupe == "produit"
                                         else 0), 8), 1),
            "delta_score": round(rng.gauss(mu, 5), 2),
            "reaction_grade": 1 if rng.random() < 0.05 else 0,
        })
    for k in range(manquants_endpoint):           # scénarios de blocage G2
        rows[k]["delta_score"] = None

    return {
        "raw": {"metadata": {
            "objectif": ("Évaluation de la tolérance et de l'efficacité perçue "
                         "d'un produit cosmétique (crème) lors d'un test d'usage "
                         "sous contrôle dermatologique"),
            "design_indice": ("test d'usage sous contrôle dermatologique, "
                              "deux groupes parallèles, 4 semaines"),
            "endpoints": ["delta_score"], "pieces": ["protocole_v2.pdf"]}},
        "datasets": {"rows": rows},
        "spec": {
            "sujet_id": "sujet", "endpoint_principal": "delta_score",
            "variable_groupe": "groupe", "contraste": ["produit", "controle"],
            "covariables_baseline": ["age"],
            "var_reaction": "reaction_grade", "seuil_grade_reaction": 2,
            "seuil_reactions_pct": 5.0,
            "population": "tous sujets avec mesure J28 du critère principal",
            "visite_ordre": ["date_v1", "date_v2"],
            "endpoints_secondaires": [],
            "variables": {
                "sujet":          {"type": "id"},
                "groupe":         {"type": "categorielle", "critique": True,
                                   "domaine": ["produit", "controle"]},
                "date_v1":        {"type": "date"},
                "date_v2":        {"type": "date"},
                "age":            {"type": "continue", "domaine": [18, 70]},
                "delta_score":    {"type": "continue", "critique": True,
                                   "domaine": [-10, 30]},
                "reaction_grade": {"type": "categorielle", "domaine": [0, 1, 2, 3]},
            }},
        "ingredients": [
            {"inci": "niacinamide", "noael_mg_kg_j": 200.0, "sed_mg_kg_j": 1.0},
            {"inci": "phenoxyethanol", "noael_mg_kg_j": 183.0, "sed_mg_kg_j": 0.88},
        ],
        "produit": {"application": "leave_on", "usage": "creme_visage",
                    "zone": "visage", "population_cible": "adulte"},
        "composition": [
            {"inci": "aqua", "concentration_pct": 72.0},
            {"inci": "glycerin", "concentration_pct": 8.0},
            {"inci": "niacinamide", "concentration_pct": 4.0},
            {"inci": "phenoxyethanol", "concentration_pct": 0.5},   # ≤ 1,0 % : OK
            {"inci": "dmdm hydantoin", "concentration_pct": 0.3},   # ≤ 0,6 % : OK
        ],
        "methodes_test": ["in vitro OCDE 439 (irritation cutanée, épiderme reconstruit)",
                          "test d'usage sous contrôle dermatologique"],
        # Allégations revendiquées (contrôlées par R-CLM-01/02/03 — UE 655/2013)
        "claims": [
            {"id": "C1", "type": "efficacite",
             "texte": "Améliore visiblement le confort et l'aspect de la peau "
                      "après 4 semaines d'utilisation",
             "endpoint": "delta_score", "direction_favorable": "+1",
             "justificatif": "étude test d'usage COS-2026-014, 60 sujets, "
                             "4 semaines, contrôle parallèle"},
            {"id": "C2", "type": "tolerance",
             "texte": "Tolérance cutanée évaluée sous contrôle dermatologique",
             "justificatif": "test d'usage sous contrôle dermatologique, "
                             "grades de réaction consignés"},
            {"id": "C3", "type": "marketing",
             "texte": "Texture légère à absorption rapide",
             "justificatif": "évaluation sensorielle interne réf. SEN-2026-07"},
        ],
        # Étiquetage produit (contrôlé par R-ETQ-01/02/03 — art. 19)
        "etiquetage": {
            "responsable_nom_adresse": "Cosmétique Démo SAS, 12 rue des Tests, 75010 Paris",
            "pays_origine": "France",
            "contenu_nominal": "50 ml e",
            "pao_ou_dluo": "12M après ouverture",
            "precautions": "Éviter le contact avec les yeux. Usage externe uniquement.",
            "numero_lot": "L2026-0714",
            "fonction_produit": "Crème de soin visage (confiance cutanée)",
            "liste_inci": ["aqua", "glycerin", "niacinamide",
                           "phenoxyethanol", "dmdm hydantoin"],
        },
    }


# --------------------------------------------------------------------------
# Annuaire d'authentification des validateurs (démo UNIQUEMENT).
#
# `secret` n'existe en clair QUE dans cette démo synthétique : la console
# n'en stocke jamais la valeur (PBKDF2-HMAC-SHA256 salé, cf. ui_gates/auth).
# `sel` fixé ⇒ annuaire déterministe (tests/démos reproductibles) ; ne
# JAMAIS injecter de sel en production (défaut : secrets.token_hex).
COMPTES_DEMO = {
    "u:bio-042": {"roles": ["biostatisticien"],
                  "secret": "bio-demo-2026",
                  "sel": "d3f1cb0a0e4a4b01"},
    "u:dir-007": {"roles": ["responsable_etude"],
                  "secret": "dir-demo-2026",
                  "sel": "b2c48d1f9a3344aa"},
    "u:stagiaire-9": {"roles": ["stagiaire"],
                      "secret": "stag-demo-2026",
                      "sel": "aa55c1df0022ee11"},
    "u:tech-003": {"roles": ["technicien"],
                   "secret": "tech-demo-2026",
                   "sel": "0f3a77be91c4d210"},
}

# Certificats PSCE (fiches PUBLIQUES — jamais de clé) pour le cachet eIDAS
# SIMULÉ branché derrière `ui_gates/eidas.py`. Structure = cible PSCQ réel
# (sujet / émetteur / série / période de validité / niveau / qualifié) ; les
# cachets produits restent recalculables publiquement → PAS de cryptographie,
# PSCQ + RFC 3161 réels requis en production derrière le même contrat.
CERTIFICATS_PSCE_DEMO = {
    "u:bio-042": {
        "sujet": "CN=Biostat Demo 042, O=Cosmetique Demo SAS, C=FR",
        "emetteur": "CN=PSCE Demo CA, O=PSCE-DEMO, C=FR",
        "serie": "PSCE-2026-0042",
        "debut": "2026-01-01T00:00:00Z", "fin": "2027-12-31T23:59:59Z",
        "niveau": "QES_SIMULE", "qualifie": True},
    "u:dir-007": {
        "sujet": "CN=Direction Etudes 007, O=Cosmetique Demo SAS, C=FR",
        "emetteur": "CN=PSCE Demo CA, O=PSCE-DEMO, C=FR",
        "serie": "PSCE-2026-0007",
        "debut": "2026-01-01T00:00:00Z", "fin": "2027-12-31T23:59:59Z",
        "niveau": "AES_SIMULE", "qualifie": True},
    "u:tech-003": {
        "sujet": "CN=Technicien 003, O=Cosmetique Demo SAS, C=FR",
        "emetteur": "CN=PSCE Demo CA, O=PSCE-DEMO, C=FR",
        "serie": "PSCE-2026-0003",
        "debut": "2026-01-01T00:00:00Z", "fin": "2026-12-31T23:59:59Z",
        "niveau": "SES", "qualifie": False},   # insuffisant pour les gates
    # u:stagiaire-9 : volontairement AUCUN certificat — la console refuse le
    # dépôt en mode PSCE (fail-closed, aucune dégradation silencieuse).
}


# Gabarits de décisions de gates (substance métier UNIQUEMENT) : la liaison
# à la version d'artefact (ref + sha256), la preuve sig-2.0.0 et l'horodatage
# de dépôt sont calculés par `ui_gates.liaison` (pré-liaison par rejeu
# déterministe) ou par la CLI — jamais écrits à la main ici.
DECISIONS_OK = {
    "G3": {"statut": "VALIDATED", "validateur_id": "u:bio-042",
           "role": "biostatisticien",
           "motif": "SAP conforme aux principes ICH E9, endpoint unique, "
                    "fallback pré-spécifié.",
           "pieces_consultees": ["sap", "dq_report"]},
    "G6": {"statut": "VALIDATED", "validateur_id": "u:dir-007",
           "role": "responsable_etude",
           "motif": "Résultats cohérents, limites documentées, aucun signal.",
           "pieces_consultees": ["rapport_draft", "critique", "safety_report",
                                 "compliance_report"]},
}
