"""Agent Biostatistique — rédaction du SAP (plan d'analyse statistique).

Deux chemins, MÊME artefact verrouillable :
- `llm` : le provider propose les analyses via schéma contraint dont l'enum `op`
  EST le catalogue fermé ; l'agent revérifie ensuite les RÈGLES MÉTIER de façon
  déterministe (une seule primaire, variables existantes, fallback obligatoire
  pour les tests paramétriques). Toute violation ⇒ repli gabarit journalisé ;
- `deterministe` (défaut) : gabarits par type d'étude.

Le LLM ne choisit JAMAIS une méthode hors catalogue et ne CALCULE rien.
"""
from __future__ import annotations

from agents_impl.base import Contexte, depot, sortie
from core.exceptions import ErreurLogique
from core.state import Etat
from llm import prompts, schemas
from llm.exceptions import ErreurLLM
from llm.generation import generer_contraint
from stats_catalogue.ops import OPS


def _gabarit_usage_cosmetique(spec: dict, dq: dict) -> list[dict]:
    ep = spec["endpoint_principal"]
    groupe = spec.get("variable_groupe", "groupe")
    contraste = spec.get("contraste", ["produit", "controle"])
    marge = spec.get("marge_equivalence")
    if marge:   # objectif d'équivalence : TOST, marge Δ pré-définie (ICH E9)
        a1 = {"id": "A1", "role": "primaire", "op": "tost_equivalence",
              "var": ep, "par": groupe, "contraste": contraste,
              "marge": float(marge),
              "hypotheses": ["normalite_par_groupe"],
              "note": "pas de fallback rangs en MVP — échec de précondition "
                      "= déviation G3"}
    else:
        a1 = {"id": "A1", "role": "primaire", "op": "t_test_welch", "var": ep,
              "par": groupe, "contraste": contraste,
              "hypotheses": ["normalite_par_groupe"],
              "fallback": {"si": "non_normal", "op": "mann_whitney"}}
    analyses = [
        {"id": "A0a", "role": "descriptif", "op": "descriptif_continu",
         "var": ep, "par": groupe},
        a1,
        {"id": "A2", "role": "safety", "op": "proportion_exacte",
         "var": spec.get("var_reaction", "reaction_grade"),
         "definition": f">= {spec.get('seuil_grade_reaction', 2)}",
         "par": groupe},
    ]
    a4 = _bloc_sensibilite_mnar(spec)
    if a4:
        analyses.append(a4)
    return analyses


def _gabarit_observationnel(spec: dict, dq: dict) -> list[dict]:
    return _gabarit_usage_cosmetique(spec, dq)  # MVP : même trame, lexique contrôlé en aval


def _bloc_ajustement(spec: dict, mode: str) -> dict | None:
    """Analyse A3 « ajustement multivarié » si pré-déclarée dans la spec
    (`spec["ajustement_multivarie"] = {"covariables": [...], "epv_min": 10}`).

    Cadrage fail-closed :
    - l'exposition est TOUJOURS la 1re covariable du modèle (imposée) ;
    - cas-témoins APPARIÉE + ajustement ⇒ logistique conditionnelle hors
      catalogue : blocage (décision humaine G3), jamais d'approximation ;
    - EPV minimum >= 5 (défaut 10) — règle anti sur-ajustement ;
    - le modèle reste une ASSOCIATION ajustée (lexique contrôlé aval).
    """
    aj = spec.get("ajustement_multivarie")
    if not aj:
        return None
    conf = list(aj.get("covariables", []))
    epv_min = float(aj.get("epv_min", 10))
    expo = spec.get("var_exposition")
    if not expo:
        raise ErreurLogique("ajustement multivarié : 'var_exposition' requis "
                            "dans la spec")
    if not conf:
        raise ErreurLogique("ajustement multivarié : 'covariables' non vide "
                            "exigé (sinon = univarié déjà planifié)")
    if epv_min < 5:
        raise ErreurLogique("ajustement multivarié : 'epv_min' >= 5 exigé "
                            "(règle anti sur-ajustement)")
    base = {"id": "A3", "role": "ajustement", "var": expo,
            "var_exposition": expo, "covariables": conf, "epv_min": epv_min}
    if mode == "cas_temoins":
        if spec.get("appariement"):
            raise ErreurLogique(
                "cas-témoins appariée + ajustement ⇒ régression logistique "
                "CONDITIONNELLE hors catalogue — blocage : décision humaine G3")
        issue = spec.get("var_issue")
        if not issue:
            raise ErreurLogique("ajustement cas-témoins : 'var_issue' requis")
        return {**base, "op": "regression_logistique", "var_issue": issue,
                "note": ("OR ajusté (SAP verrouillé) — association ajustée ; "
                         "confusion non mesurée possible")}
    ev = spec.get("var_evenement")
    if not ev:
        raise ErreurLogique("ajustement cohorte : 'var_evenement' requis")
    tv = spec.get("var_temps_event")
    if tv:
        return {**base, "op": "cox_ph", "var_evenement": ev,
                "var_temps_event": tv,
                "note": ("HR ajusté (Cox/Breslow, SAP verrouillé) — risques "
                         "proportionnels SUPPOSÉS à confirmer G3 ; association "
                         "ajustée, non causale")}
    return {**base, "op": "regression_logistique", "var_issue": ev,
            "note": ("OR ajusté sur incidence (SAP verrouillé) — association "
                     "ajustée ; confusion non mesurée possible")}


# grille MNAR : plafond métier 5 σ — au-delà, l'hypothèse est caricaturale
# (biais systématique ≥ 5 écarts-types sur les imputés) et le ruban traverse
# largement zéro ; le domaine d'interprétabilité usuel est ≤ 2 σ (E9(R1))
DELTA_MNAR_MAX = 5.0


def _bloc_sensibilite_mnar(spec: dict) -> dict | None:
    """Analyse « sensibilite » ruban MNAR δ-ajusté sur SMD si pré-déclarée
    (`spec["sensibilite_mnar_smd"] = {"deltas": [...], "groupe": "produit"}`).

    Cadrage fail-closed :
    - grille δ en σ-unités : non vide, ≤ 16 points, floats ≥ 0, ≤ 5 σ,
      contient 0.0 (point de base) — triée de façon déterministe ;
    - `groupe` = bras pénalisé (typiquement celui qui porte les manquants),
      DOIT appartenir au contraste de la primaire ;
    - exécutable uniquement si l'endpoint principal est continu et comparé
      entre 2 bras avec imputation multiple (le runtime rend non
      interprétable + contradiction « sans objet MI » sinon — jamais de
      résultat de complaisance).
    """
    bloc = spec.get("sensibilite_mnar_smd")
    if not bloc:
        return None
    deltas = bloc.get("deltas")
    groupe_mnar = bloc.get("groupe")
    if not deltas or not all(isinstance(d, (int, float))
                             and not isinstance(d, bool) for d in deltas):
        raise ErreurLogique("sensibilite_mnar_smd : 'deltas' non vide exigé "
                            "(floats en σ-unités, 0.0 inclus)")
    if len(deltas) > 16:
        raise ErreurLogique("sensibilite_mnar_smd : grille δ ≤ 16 points "
                            "(budget de calcul verrouillé)")
    if any(float(d) < 0 or float(d) > DELTA_MNAR_MAX for d in deltas):
        raise ErreurLogique(f"sensibilite_mnar_smd : δ ∈ [0, {DELTA_MNAR_MAX:g}] "
                            "σ exigé (domaine d'interprétabilité déclaré)")
    if 0.0 not in {float(d) for d in deltas}:
        raise ErreurLogique("sensibilite_mnar_smd : la grille doit contenir "
                            "0.0 (base poolée de référence)")
    contraste = spec.get("contraste", ["produit", "controle"])
    if groupe_mnar not in contraste:
        raise ErreurLogique(f"sensibilite_mnar_smd : groupe pénalisé "
                            f"{groupe_mnar!r} hors contraste {contraste!r}")
    return {"id": "A4", "role": "sensibilite", "op": "tipping_point_mnar_smd",
            "var": spec["endpoint_principal"],
            "par": spec.get("variable_groupe", "groupe"),
            "contraste": list(contraste),
            "deltas": sorted(float(d) for d in deltas),
            "groupe_mnar": groupe_mnar,
            "note": ("ruban MNAR δ-ajusté contre l'effet observé (SMD de "
                     "Hedges, re-pool Rubin par cycle PMM) — bascule = "
                     "premier δ de perte de significativité dans le sens "
                     "observé ; hypothèse conservatrice pré-déclarée, "
                     "cf. docs/SENSIBILITE.md")}


def _gabarit_cas_temoins(spec: dict, dq: dict) -> list[dict]:
    """Cas-témoins : primaire = OR (apparié conditionnel si appariement 1:1).
    Ajustement multivarié uniquement si pré-déclaré (`ajustement_multivarie`)
    — A3 verrouillée au SAP, jamais post-hoc."""
    expo, issue = spec.get("var_exposition"), spec.get("var_issue")
    if not expo or not issue:
        raise ErreurLogique("cas-témoins : 'var_exposition' et 'var_issue' "
                            "(statut cas/témoin) requis dans la spec")
    analyses = [{"id": "A0a", "role": "descriptif", "op": "proportion_wilson",
                 "var": expo, "par": issue, "modalite": 1,
                 "note": "prévalence d'exposition par statut (descriptif)"}]
    if spec.get("appariement"):
        paire = spec.get("var_paire")
        if not paire:
            raise ErreurLogique("cas-témoins appariée : 'var_paire' requis "
                                "(appariement 1:1 déclaré)")
        analyses.append({"id": "A1", "role": "primaire", "op": "or_apparie",
                         "var": expo, "var_exposition": expo,
                         "var_issue": issue, "var_paire": paire,
                         "note": "OR conditionnel des paires discordantes "
                                 "(principe McNemar)"})
    else:
        analyses.append({"id": "A1", "role": "primaire",
                         "op": "odds_ratio_cas_temoins", "var": expo,
                         "var_exposition": expo, "var_issue": issue,
                         "note": "OR de Woolf + p exacte de Fisher"})
    a3 = _bloc_ajustement(spec, "cas_temoins")
    if a3:
        analyses.append(a3)
    return analyses


def _gabarit_cohorte(spec: dict, dq: dict) -> list[dict]:
    """Cohorte : primaire = HR/log-rang si temps d'événement, sinon RR.
    Le RR de fin de suivi est conservé en secondaire descriptif."""
    expo, ev = spec.get("var_exposition"), spec.get("var_evenement")
    if not expo or not ev:
        raise ErreurLogique("cohorte : 'var_exposition' et 'var_evenement' "
                            "requis dans la spec")
    groupe = spec.get("variable_groupe", expo)
    contraste = spec.get("contraste", ["expose", "non_expose"])
    analyses = [{"id": "A0a", "role": "descriptif", "op": "proportion_wilson",
                 "var": ev, "par": groupe, "modalite": 1,
                 "note": "incidence brute de l'événement par bras (descriptif)"}]
    if spec.get("var_temps_event"):
        analyses.append({"id": "A1", "role": "primaire", "op": "km_logrank_hr",
                         "var": ev, "par": groupe, "contraste": contraste,
                         "var_evenement": ev,
                         "var_temps_event": spec["var_temps_event"],
                         "note": "log-rang + HR (Peto) — risques relatifs "
                                 "constants SUPPOSÉS, à confirmer au G3"})
        analyses.append({"id": "A2", "role": "secondaire",
                         "op": "risque_relatif_cohorte", "var": ev,
                         "par": groupe, "contraste": contraste,
                         "var_evenement": ev,
                         "note": "RR de fin de suivi (descriptif, non ajusté)"})
    else:
        analyses.append({"id": "A1", "role": "primaire",
                         "op": "risque_relatif_cohorte", "var": ev,
                         "par": groupe, "contraste": contraste,
                         "var_evenement": ev})
    a3 = _bloc_ajustement(spec, "cohorte")
    if a3:
        analyses.append(a3)
    return analyses


def _gabarit_stabilite(spec: dict, dq: dict) -> list[dict]:
    bornes = spec.get("bornes_acceptation", {})
    if not bornes or not spec.get("var_temps"):
        raise ErreurLogique("stabilité : 'bornes_acceptation' et 'var_temps' "
                            "requis dans la spec")
    ep = spec["endpoint_principal"]
    analyses = [{"id": f"ST-{var}",
                 "role": "primaire" if var == ep else "secondaire",
                 "op": "tendance_lineaire", "var": var,
                 "par_temps": spec["var_temps"],
                 "bornes": bornes[var]} for var in bornes]
    # sensibilité « passage au grand mail » PRÉ-DÉCLARÉE sur l'endpoint
    # principal (ICH Q1E — extrapolation bornée) ; fenêtre/horizon venant de
    # spec["sensibilite_stabilite"] si fournie, sinon défauts verrouillés 12/6 ;
    # seuil et sens du franchissement NON fixés ici : déduits de façon
    # DÉTERMINISTE à l'exécution depuis bornes_acceptation (borne la plus
    # menacée) — jamais choisis à vue, tracé dans les assumptions.
    sens = spec.get("sensibilite_stabilite") or {}
    fenetre = float(sens.get("fenetre_mois", 12.0))
    horizon = float(sens.get("horizon_mois", 6.0))
    if fenetre <= 0 or not 0 < horizon <= 12:
        raise ErreurLogique(
            "sensibilite_stabilite : fenetre_mois > 0 et 0 < horizon_mois "
            "≤ 12 exigés (extrapolation bornée, ICH Q1E) — SAP non émis")
    analyses.append({"id": f"ST-SENS-{ep}", "role": "sensibilite",
                     "op": "tendance_fenetre_glissante", "var": ep,
                     "par_temps": spec["var_temps"],
                     "fenetre_mois": fenetre,
                     "horizon_mois": horizon,
                     "note": ("premier mois où la pire borne IC95 de l'OLS "
                              "local franchit la spécification — délai "
                              "mesurable de robustesse, extrapolation "
                              "bornée (≤ 12 mois), non mécaniste")})
    return analyses


GABARITS = {
    "test_usage_controle": _gabarit_usage_cosmetique,
    "tolerance_cutanee": _gabarit_usage_cosmetique,
    "observationnelle_transversale": _gabarit_observationnel,
    "cas_temoins": _gabarit_cas_temoins,
    "cohorte_prospective": _gabarit_cohorte,
    "cohorte_retrospective": _gabarit_cohorte,
    "stabilite": _gabarit_stabilite,
}

OPS_PARAMETRIQUES = {"t_test_welch"}

OPS_ASSOCIATION = {"odds_ratio_cas_temoins", "or_apparie",
                   "risque_relatif_cohorte", "km_logrank_hr"}

# Clés métier exigées par op d'association (contre-vérification déterministe)
CLES_METIER_ASSOCIATION = {
    "odds_ratio_cas_temoins": ["var_exposition", "var_issue"],
    "or_apparie": ["var_exposition", "var_issue", "var_paire"],
    "risque_relatif_cohorte": ["var_evenement"],
    "km_logrank_hr": ["var_evenement", "var_temps_event"],
    # ajustement multivarié (A3) — variables du modèle contrôlées plus bas
    "regression_logistique": ["var_exposition", "var_issue", "covariables"],
    "cox_ph": ["var_exposition", "var_evenement", "var_temps_event",
               "covariables"],
}
OPS_AJUSTEMENT = {"regression_logistique", "cox_ph"}


def _verifier_regles_metier(analyses: list[dict], spec: dict) -> None:
    """Contre-vérification déterministe de toute proposition LLM."""
    if sum(1 for a in analyses if a.get("role") == "primaire") != 1:
        raise ErreurLogique("exactement UNE analyse primaire exigée")
    aj_spec = spec.get("ajustement_multivarie")
    if aj_spec:
        a3 = next((a for a in analyses if a.get("role") == "ajustement"), None)
        if a3 is None:
            raise ErreurLogique(
                "ajustement multivarié pré-déclaré dans la spec : la "
                "proposition NE DOIT PAS l'omettre (repli gabarit)")
        if (set(a3.get("covariables") or [])
                != set(aj_spec.get("covariables", []))
                or float(a3.get("epv_min", 10))
                != float(aj_spec.get("epv_min", 10))):
            raise ErreurLogique(
                "l'ajustement proposé dévie de la déclaration de la spec "
                "(covariables/epv_min) — repli gabarit, jamais de dérive")
    # sensibilités PRÉ-DÉCLARÉES : la proposition ne doit ni les omettre ni
    # en dévier — et JAMAIS en proposer de non déclarées (dérive post-hoc)
    sens_sap = [a for a in analyses if a.get("role") == "sensibilite"]
    ops_sens_sap = {a.get("op") for a in sens_sap}
    bloc_stab = spec.get("sensibilite_stabilite") or {}
    if spec.get("bornes_acceptation") and spec.get("var_temps"):
        # type stabilité : le gabarit impose l'item « passage au grand mail »
        if "tendance_fenetre_glissante" not in ops_sens_sap:
            raise ErreurLogique("sensibilité stabilité (passage au grand "
                                "mail) omise par la proposition — repli "
                                "gabarit")
        att = {"fenetre_mois": float(bloc_stab.get("fenetre_mois", 12.0)),
               "horizon_mois": float(bloc_stab.get("horizon_mois", 6.0))}
        a_sens = next(a for a in sens_sap
                      if a.get("op") == "tendance_fenetre_glissante")
        if (float(a_sens.get("fenetre_mois", 0)) != att["fenetre_mois"]
                or float(a_sens.get("horizon_mois", 0)) != att["horizon_mois"]):
            raise ErreurLogique("la sensibilité stabilité proposée dévie de "
                                "la déclaration (fenetre/horizon) — repli "
                                "gabarit, jamais de dérive")
    elif "tendance_fenetre_glissante" in ops_sens_sap:
        raise ErreurLogique("tendance_fenetre_glissante proposée hors cadre "
                            "stabilité pré-déclaré — rejet")
    bloc_mnar = spec.get("sensibilite_mnar_smd")
    if bloc_mnar:
        if "tipping_point_mnar_smd" not in ops_sens_sap:
            raise ErreurLogique("sensibilité MNAR (ruban δ-ajusté SMD) "
                                "pré-déclarée dans la spec : la proposition "
                                "NE DOIT PAS l'omettre (repli gabarit)")
        attendu = _bloc_sensibilite_mnar(spec)
        a_mnar = next(a for a in sens_sap
                      if a.get("op") == "tipping_point_mnar_smd")
        if (sorted(float(d) for d in a_mnar.get("deltas", []))
                != attendu["deltas"]
                or a_mnar.get("groupe_mnar") != attendu["groupe_mnar"]):
            raise ErreurLogique("la sensibilité MNAR proposée dévie de la "
                                "déclaration (grille δ / groupe pénalisé) — "
                                "repli gabarit")
    elif "tipping_point_mnar_smd" in ops_sens_sap:
        raise ErreurLogique("tipping_point_mnar_smd proposée sans "
                            "sensibilite_mnar_smd déclarée dans la spec — "
                            "rejet (jamais de sensibilité post-hoc)")

    variables = set(spec.get("variables", {}))
    for a in analyses:
        if a["op"] not in OPS:
            raise ErreurLogique(f"{a['id']} : op hors catalogue")
        if a["var"] not in variables:
            raise ErreurLogique(f"{a['id']} : variable {a['var']!r} absente de la spec")
        if a["op"] in OPS_PARAMETRIQUES and not a.get("fallback"):
            raise ErreurLogique(f"{a['id']} : test paramétrique sans fallback "
                                "pré-spécifié")
        if a.get("fallback") and a["fallback"]["op"] not in OPS:
            raise ErreurLogique(f"{a['id']} : fallback hors catalogue")
        # cohérence rôle/op pour les sensibilités (pas de sensibilité
        # déguisée en analyse confirmatoire ni inversement)
        ops_sens = {"tendance_fenetre_glissante", "tipping_point_mnar_smd"}
        if a.get("role") == "sensibilite" and a["op"] not in ops_sens:
            raise ErreurLogique(f"{a['id']} : role 'sensibilite' incompatible "
                                f"avec l'op {a['op']!r}")
        if a["op"] in ops_sens and a.get("role") != "sensibilite":
            raise ErreurLogique(f"{a['id']} : {a['op']} est une sensibilité "
                                "— role 'sensibilite' exigé (jamais "
                                "confirmatoire)")
        if a["op"] == "tipping_point_mnar_smd":
            cont = a.get("contraste") or spec.get(
                "contraste", ["produit", "controle"])
            if a.get("groupe_mnar") not in cont:
                raise ErreurLogique(f"{a['id']} : groupe_mnar "
                                    f"{a.get('groupe_mnar')!r} hors contraste")
        for cle in CLES_METIER_ASSOCIATION.get(a["op"], []):
            if cle == "covariables":
                continue                       # contrôlées ci-dessous
            if not a.get(cle):
                raise ErreurLogique(f"{a['id']} : {a['op']} exige {cle!r}")
            if a[cle] not in variables:
                raise ErreurLogique(f"{a['id']} : {cle}={a[cle]!r} absente "
                                    "de la spec")
        if a["op"] in OPS_AJUSTEMENT:
            covs = a.get("covariables") or []
            if not covs:
                raise ErreurLogique(f"{a['id']} : ajustement sans covariables")
            for cov in covs:
                if cov not in variables:
                    raise ErreurLogique(
                        f"{a['id']} : covariable {cov!r} absente de la spec")
            if float(a.get("epv_min", 10)) < 5:
                raise ErreurLogique(f"{a['id']} : epv_min >= 5 exigé "
                                    "(anti sur-ajustement)")
            if a.get("var_exposition") in covs:
                raise ErreurLogique(f"{a['id']} : l'exposition est imposée en "
                                    "1re position — ne pas la redoubler")
        if a["op"] == "tendance_fenetre_glissante":
            # sensibilité stabilité pré-déclarée : paramètres bornés ICI
            # (l'op refuse aussi en aval — fail-closed double verrou)
            fenetre, horizon = (a.get("fenetre_mois"), a.get("horizon_mois"))
            if fenetre is None or not float(fenetre) > 0:
                raise ErreurLogique(f"{a['id']} : fenetre_mois > 0 exigé "
                                    "(sensibilité fenêtre glissante)")
            if horizon is None or not 0 < float(horizon) <= 12:
                raise ErreurLogique(f"{a['id']} : 0 < horizon_mois ≤ 12 exigé "
                                    "(extrapolation bornée, ICH Q1E)")
            var_temps = a.get("par_temps") or spec.get("var_temps")
            if not var_temps:
                raise ErreurLogique(f"{a['id']} : 'par_temps' (ou spec "
                                    "'var_temps') exigé")
            if ((a.get("spec_limite") is None)
                    != (a.get("direction") not in ("inferieur", "superieur"))):
                raise ErreurLogique(
                    f"{a['id']} : spec_limite et direction se déclarent "
                    "ENSEMBLE au SAP — ou s'omettent ensemble (déduction "
                    "déterministe depuis bornes_acceptation à l'exécution)")


def _defauts(analyses: list[dict], spec: dict) -> list[dict]:
    groupe = spec.get("variable_groupe", "groupe")
    for a in analyses:
        a.setdefault("par", groupe)
        if a["op"] in OPS_PARAMETRIQUES or a["op"] == "mann_whitney":
            a.setdefault("contraste", spec.get("contraste",
                                               ["produit", "controle"]))
    return analyses


def fabriquer(ctx: Contexte, llm=None):
    ctx.producteur = "agent.biostat"

    def agent(etat: Etat, entrees: dict) -> dict:
        spec, dq = entrees["spec"], entrees["dq"]
        fabrique = GABARITS.get(etat.type_etude)
        if fabrique is None:
            raise ErreurLogique(
                f"pas de gabarit SAP MVP pour {etat.type_etude!r} → escalade humaine")

        analyses = fabrique(spec, dq)
        multiplicite = ("gatekeeping : primaire d'abord ; Holm si secondaires "
                        "confirmatoires ; exploratoire étiqueté (plafond CC 0,5)")
        strat_manquants = ("cas complets si taux ≤ 5 % ; sinon MI requise "
                           "(hors MVP → escalade)")
        mode, assumptions = "gabarit_deterministe", [
            "gabarit déterministe conforme au catalogue d'ops"]

        if llm is not None:
            try:
                dq_resume = {"score_dq": dq.get("score_dq"),
                             "n": dq.get("n_lignes")}
                obj, meta_gen = generer_contraint(
                    llm, tache="proposition_sap",
                    systeme=prompts.systeme_biostat(),
                    utilisateur=prompts.utilisateur_biostat(
                        etat.type_etude, spec, dq_resume),
                    schema=schemas.PROPOSITION_SAP)
                propo = _defauts(list(obj["analyses"]), spec)
                _verifier_regles_metier(propo, spec)      # contre-vérification
                analyses = propo
                multiplicite = obj["gestion_multiplicite"]
                strat_manquants = obj["gestion_manquants_strategie"]
                mode = "llm"
                assumptions = ["analyses proposées par LLM, revalidées "
                               "mécaniquement (catalogue + règles métier)"]
                ctx.audit.log("llm", "GENERATION", meta_gen)
            except (ErreurLLM, ErreurLogique) as e:
                assumptions.append(f"proposition LLM rejetée ({e}) — repli "
                                   "gabarit déterministe")
                ctx.audit.log("agent.biostat", "REPLI_LLM", {"erreur": str(e)[:300]})

        sap = {
            "version_gabarit": "sap-1.0.0", "mode_proposition": mode,
            "study_id": etat.study_id, "type_etude": etat.type_etude,
            "endpoint_principal": {
                "variable": spec["endpoint_principal"], "unique": True,
                "justification": "critère pré-spécifié au protocole"},
            "endpoints_secondaires": spec.get("endpoints_secondaires", []),
            "population_analyse": {
                "definition": spec.get(
                    "population",
                    "tous sujets avec mesure du critère principal"),
                "exclusions": "règles pré-spécifiées uniquement (journal dédié)"},
            "analyses": analyses,
            "gestion_multiplicite": multiplicite,
            "gestion_manquants": {"strategie": strat_manquants,
                                  "sensibilite": ["analyse sans outliers "
                                                  "critiques documentés"]},
            "estimand": ({"strategie_evenements_intercurrents": "treatment_policy",
                          "cadre": "ICH E9(R1)"}
                         if etat.type_etude == "essai_randomise" else None),
            "decisions_puissance": {
                "note": "calcul de puissance a priori exigé à G3 si confirmatoire"},
            "pre_enregistrement": "hash du SAP déposé au verrou G3 avant tout calcul",
        }
        if etat.type_etude in ("cas_temoins", "cohorte_prospective",
                               "cohorte_retrospective"):
            a3 = next((a for a in analyses if a.get("role") == "ajustement"),
                      None)
            sap["garde_fous_observationnel"] = {
                "lexique": ("association ≠ causalité — vocabulaire causal "
                            "interdit au rapport (contrôlé par la relecture)"),
                "ajustement": (
                    f"ajustement multivarié PRÉ-DÉCLARÉ (A3 : {a3['op']}, "
                    f"covariables {a3['covariables']}, EPV_min "
                    f"{a3['epv_min']:g}) — verrouillé à G3 ; association "
                    "AJUSTÉE rapportée : confusion non mesurée possible ; "
                    "aucun ajustement post-hoc ; si A3 non interprétable "
                    "(EPV/séparation/colinéarité) : revue statisticien, "
                    "aucune mesure ajustée présentée"
                    if a3 else
                    "analyse non ajustée (univariée) : toute ampleur "
                    "rapportée reste exploratoire ; l'ajustement multivarié "
                    "est une décision humaine (G3)"),
                "biais": ("confusion non mesurée, biais de sélection et "
                          "d'information à documenter en section limites"),
                "reference": "STROBE — reporting des études observationnelles",
            }
        art = depot(ctx, etat.study_id, "sap", "sap", sap,
                    utilisant=[entrees.get("intention_ref"),
                               entrees.get("dq_ref")])
        return sortie(confidence=0.9, artefacts=[art],
                      assumptions=assumptions,
                      sap=sap, sap_ref=art.ref, sap_sha256=art.sha256)
    return agent
