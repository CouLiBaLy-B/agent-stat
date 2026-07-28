# Méthodologie P1 — équivalence, imputation multiple, stabilité

**Version :** 1.0.0 · étend `stats_catalogue/`, `agents_impl/analyses.py`,
`reglementaire/moteur_regles.py`, démos `run_demo.py` / `run_stabilite.py`.

## Ajouts au catalogue d'opérations (toutes déterministes, double-exécution)

| Op | Contenu | Garde-fous |
|---|---|---|
| `tost_equivalence` | TOST bilatéral (approx. Welch) : rejet conjoint des deux tests unilatéraux (H0 : diff ≤ −Δ, H0 : diff ≥ +Δ), IC 90 %, verdict `equivalence_demontree / non_demontree` | **Δ exigée au SAP** (blocage sinon) ; p non significatif ≠ équivalence — le gabarit interdit la confiance abusive ; pas de fallback rangs (échec de précondition = déviation G3) |
| `pooling_rubin` | Règles de Rubin (Barnard-Rubin ddl) : θ poolé, SE, IC 95 %, p, variance intra/inter, augmentation relative, **FMI** | m ≥ 2 exigé ; utilisé par l'inférentiel uniquement via le catalogue |
| `tendance_lineaire` | OLS y~temps : pente + IC, p, prédiction à l'échéance + IC de prédiction, moyenne au dernier point, résidus, R² | n ≥ 4 et ≥ 2 temps uniques ; la règle métier (bornes) vit dans le moteur réglementaire (R-STAB-01), pas dans le test |

## Imputation multiple (`stats_catalogue/imputation.py` + agent manquants)

- **Diagnostic** : taux par variable ; écran MAR grossier = Fisher exact sur
  (manquant × groupe) — suspicion MNAR tracée en contradiction ;
- **PMM** (predictive mean matching) : régression simple sur le prédicteur baseline
  complet le cas échéant, donneur tiré parmi les k=5 plus proches voisins de la
  moyenne prédite (valeurs imputées = valeurs **réellement observées**), seed
  dérivée par imputation ⇒ reproductible ; sans prédicteur exploitable : hot-deck ;
- **Stratégies** : ≤ 5 % → cas complets · 5–40 % → PMM `m = max(20, taux×100)` ·
  > 40 % → blocage data management (rappel : G2 bloque déjà < 90 % de complétude) ;
- **Sensibilité MNAR intégrée à l'inférentiel** : effet poolé Welch+Rubin sur les m
  jeux + **delta-adjustment / tipping point** (δ pénalisant le produit sur les seules
  cellules imputées ; grille 0,5→4 ; `delta_flip` = premier δ où p ≥ 0,05) ;
- **Concordance** primaire (cas complets) vs sensibilité poolée → alimente le
  score RA (`concordance_sensibilite`).

## Parcours « stabilité » de bout en bout

Nouvelle route `type_etude=stabilite` : gabarit SAP par paramètre borné →
`tendance_lineaire` par paramètre ; règles réglementaires dédiées :
- **R-DON-02** (données minimales stabilité : var_temps, bornes d'acceptation,
  ≥ 3 points temporels) ;
- **R-STAB-01** par paramètre : moyenne à l'échéance hors bornes → **KO** (blocage
  NON_CONFORMITÉ) ; IC95 % de prédiction croisant une borne → **INCERTAIN** (dérive
  plausible avant échéance → surveillance humaine) ; sinon OK.

Détails d'implémentation : agents safety/biais s'auto-désactivent (`non_applicable`)
quand le design n'a pas de variable de tolérance/groupe ; **clé de doublon
longitudinale composite** (sujet × temps) dans le score DQ — les mesures répétées
légitimes ne sont plus pénalisées en doublons.

## Fait marquant de cohérence

Le pipeline a été réordonné `hypothèses → manquants → inférentiel` : la gestion des
données manquantes **précède** désormais le calcul (les imputations alimentent la
sensibilité), conformément à la bonne pratique — le document d'architecture gardait
un ordre CPA hérité ; cette correction est consignée ici (ADR implicite).

## Vérifications (86 tests)

Valeurs de référence Rubin calculées à la main · PMM déterminisme + propriété
« imputé ∈ observé » · TOST démontré/non · droite parfaite (pente/IC/R²) · E2E :
TOST (décision « Équivalence démontrée »), MI m=20 avec FMI et tipping δ,
stabilité nominale CONFORME, **stabilité en rupture (pH 12 m ≈ 4,2 < 5,0) →
blocage NON_CONFORMITE_BLOQUANTE**.
