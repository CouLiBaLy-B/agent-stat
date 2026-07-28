# Analyses de sensibilité déclaratives & verrou toctou des sorties

**Version :** 1.0.0 · étend `stats_catalogue/ops.py`, `stats_catalogue/imputation.py`
et `stats_catalogue/controller.py` — 100 % stdlib, 100 % déterministe.

Deux exigences croisées :

1. la **robustesse** d'une conclusion ne s'improvise pas après coup : chaque
   analyse de sensibilité est une op **du catalogue**, pré-déclarée au SAP
   (G3), avec paramètres, hypothèse et lecture figés dans la sortie ;
2. une sortie exécutable n'existe qu'**UNE fois** : le contrôleur verrouille
   le couple (op, entrées) par run — la première sortie est la seule
   possible, et toute ré-exécution qui diverge est un **toctou** bloqué.

---

## 1. `tendance_fenetre_glissante` — passage au grand mail

**Question métier (stabilité/série temporelle réglementaire) :** à partir de
quel mois la détection d'un franchissement de seuil devient-elle possible ?

**Calcul :** pour chaque origine `t0` observée, OLS local sur les points de
`[t0, t0 + fenetre_mois]` (≥ 4 points, ≥ 2 temps distincts), prévision à
`t0 + fenetre_mois + horizon_mois` avec IC95 (Student, n−2 ddl), et on
consigne la **pire borne** dans le sens du franchissement
(`direction="inferieur"` → borne inférieure, `"superieur"` → supérieure) :

- `fenetres[]` : par origine — pente, prévision, borne pessimiste,
  franchissement oui/non, mois de franchissement prévu ;
- `premier_t0_franchissement` / `premier_mois_franchissement_prevu` : le
  délai mesurable de robustesse (`null` si jamais franchi sur la série) ;
- `verdict` : phrase normalisée.

**Barrières fail-closed :** direction hors `{inferieur, superieur}` refusée ;
≥ 6 points sur la série ; `fenetre_mois > 0` ; `0 < horizon_mois ≤ 12` ;
fenêtres à moins de 4 points sautées (tracé par `n_fenetres`) ; si aucune
fenêtre couverte ⇒ non interprétable. Extrapolation **bornée par
construction** : jamais au-delà de `t0 + fenêtre + horizon`, horizon ≤ 12
mois — le champ `hypothese` rappelle que ce n'est **pas un modèle
mécaniste** de dégradation, seulement une robustesse de détection, et qu'il
doit être pré-déclaré au SAP (fenêtre, horizon, seuil).

```python
ops.executer("tendance_fenetre_glissante", seed,
             points=[{"mois": 0, "valeur": 26.0}, ...],
             fenetre_mois=12, horizon_mois=6,
             spec_limite=24.5, direction="inferieur")
```

## 2. `tipping_point_mnar_smd` — ruban MNAR δ-ajusté

**Question métier (données manquantes) :** quelle ampleur de biais MNAR
systématique faudrait-il pour renverser la conclusion ?

**Calcul :** à partir des `m` copies PMM complétées des deux groupes
(sorties de `imputer_pmm`), pour chaque `δ` d'une grille déclarée
(en unités de `σ_ref` = écart-type poolé sur **toutes** les copies) :

1. les imputés du `groupe_ajuste` (`"g1"` ou `"g2"` — typiquement celui qui
   porte les manquants) sont décalés **contre l'effet observé**
   (−signe(θ_base)·δ·σ_ref : δ > 0 atténue toujours |SMD|) ;
2. le SMD de Hedges et sa variance asymptotique sont recalculés par cycle ;
3. re-pooling par `pooling_rubin` (t de Barnard-Rubin) ; significativité =
   `p < alpha`.

Sortie : `base` (δ = 0), `ruban[]` par δ (θ, se, p, ddl, significativité,
décalage transcrit en unités de la variable), `delta_bascule` (premier δ où
la significativité se perd), `theta_monotone` (|θ(δ)| monotone décroissant
⇒ bascule **unique**), `verdict` (« FRAGILE au-delà de δ=… » / « ROBUSTE :
aucune bascule sur la grille »), `hypothese` (scénario conservateur à
pré-déclarer ; n'infirme ni ne confirme MAR).

**Barrières fail-closed :** m ≥ 2 copies alignées, longueurs constantes,
grille de floats non vide, `groupe_ajuste ∈ {g1, g2}`, σ_ref non nul.

```python
ops.executer("tipping_point_mnar_smd", seed,
             colonnes_g1=cols1, colonnes_g2=cols2,
             deltas=[0.0, 0.1, 0.25, 0.5, 0.75, 1.0],
             groupe_ajuste="g2")
```

## 3. Verrou toctou du contrôleur — première sortie unique

`ControleurExecution.executer` garantit désormais, pour chaque
`(run, op, entrées)` :

- la **première sortie** produite porte un ticket séquentiel monotone
  (`exec_seq`, journalisé dans `journal_execution` de l'artefact résultats)
  — c'est la preuve P2 « premières sorties exécutables » du lot ;
- tout appel ultérieur **aux mêmes entrées** est **ré-exécuté puis
  comparé** : concordance bit-à-bit ⇒ la première sortie est servie
  inchangée (`exec_rejoue: true`, `_copie_memoire: true`, **aucune**
  nouvelle trace au journal) ; divergence ⇒
  `ErreurLogique("toctou détecté")` ⇒ **blocage dur reproductibilité** ;
- la double exécution intra-appel (hashes SHA-256 concordants) reste
  appliquée à chaque calcul effectif.

Ce verrou couvre un toctou « silencieux » (données mutées entre deux
appels alors que les entrées extraites semblent identiques : la
ré-exécution diverge) comme un rejeu non déclaré d'une sortie déjà rendue.
Il est **compatible avec l'usage métier légitime** (ex. même statistique
exacte demandée pour deux sous-groupes : la première sortie est servie,
tracée), et ne contient **aucun** wall-clock dans le contenu hashé —
la reproductibilité inter-runs est intacte (voir `run_gates_ui.py`,
« store idempotent, toutes les refs en v1 »).

## 4. Position dans le pipeline

- Les deux ops sont au catalogue (registre `OPS`, versions 1.0.0) et
  archivées via `ops.executer` : l'inférentiel ne peut invoquer que des ops
  verrouillées au SAP — la sensibilité suit exactement la même chaîne
  (schéma LLM en §mode LLM : `role: "secondaire"` planifié comme toute
  analyse pré-déclarée).
- La sensibilité MNAR opéré par les résultats imputés (`A1_sensibilite_MI`,
  δ sur différence brute) reste en place ; le **ruban SMD** est la brique
  réutilisable catalogue destinée aux endpoints standardisés (SMD), aux
  déclinaisons ultérieures (δ en unités cliniques, grilles par scénario).
- Qualification numérique : ces ops composent des primitives déjà qualifiées
  contre scipy 1.17.1 (OLS/Student, Rubin, Hedges) — voir
  `docs/QUALIFICATION_SCIPY.md`.

---

*Cadre méthodologique : tipping point sensitivity analysis (Yan, Lee & Li,
2009 ; CHMP E9(R1) estimands) ; fenêtres glissantes de détection en
stabilité (démarche ICH Q1E, extrapolation bornée).*
