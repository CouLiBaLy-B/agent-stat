# Analyses de sensibilité déclaratives & verrou toctou des sorties

**Version :** 1.1.0 · `tipping_point_mnar_smd` 1.1.0 (marquage de
renversement — additif, calculs invariants) · câblage pipeline des deux ops
dans les gabarits SAP + schéma LLM + relecture · étend
`stats_catalogue/ops.py`, `stats_catalogue/imputation.py`,
`stats_catalogue/controller.py`, `agents_impl/`, `llm/schemas.py` —
100 % stdlib, 100 % déterministe.

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

Sortie : `base` (δ = 0), `ruban[]` par δ (θ, se, p, ddl, `significatif`,
décalage transcrit en unités de la variable, **`renverse`**), `delta_bascule`
(premier δ où la significativité se perd **dans le sens observé**),
`theta_monotone`, `delta_renversement`, `verdict`, `hypothese` (scénario
conservateur à pré-déclarer ; n'infirme ni ne confirme MAR).

**Renversement d'effet (1.1.0).** La pénalisation δ·σ_ref étant affine,
θ(δ) est quasi linéaire décroissante : |θ| dessine un **V**. Au-delà du
point où θ traverse zéro, l'effet est **renversé** par la pénalisation —
et la p-value brute (toutes directions) peut redevenir « significative ».
L'op marque explicitement cette zone (depuis la 1.1.0) :

- `renverse: true` sur chaque δ où θ a changé de signe par rapport à la
  base ; `delta_renversement` = premier δ renversé ;
- la **bascule** reste le *premier* δ de perte de significativité dans le
  sens observé — unique par construction (le `significatif` nu est une
  mesure brute toutes directions, le champ `renverse` rétablit la lecture) ;
- `theta_monotone = false` signale que la grille a traversé zéro (c'est un
  diagnostic, pas un incident) ;
- le `verdict` l'explicite : « …l'effet est RENVERSÉ par la pénalisation
  dès δ=… σ — toute « re-significativité » au-delà est l'artefact du
  renversement, pas une résurrection de l'effet observé ».

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

## 4. Position dans le pipeline (câblage 1.1.0)

Les deux sensibilités sont de **vraies analyses du SAP** (rôle dédié
`sensibilite`), pré-déclarées via la spec, verrouillées à G3 comme toute le
reste du plan, puis exécutées, recalculées par la relecture et restituées
dans le rapport — la même chaîne de preuve que la primaire :

1. **Déclaration.** `spec["sensibilite_stabilite"] = {"fenetre_mois": …,
   "horizon_mois": …}` (défauts verrouillés 12 / 6 ; > 0 et ≤ 12 exigés,
   ICH Q1E) pour le « passage au grand mail » ; `spec["sensibilite_mnar_smd"]
   = {"deltas": […], "groupe": …}` pour le ruban MNAR — grille non vide,
   ≤ 16 points, δ ∈ [0 ; 5 σ], **0.0 obligatoire** (base poolée), groupe
   pénalisé ∈ contraste (typiquement le bras qui porte les manquants).
2. **Gabarits SAP.** `ST-SENS-<endpoint>` (stabilité, endpoint principal) ;
   `A4` (usage 2 groupes à endpoint continu). Seuil et sens du
   franchissement ne sont PAS figés au SAP : **déduits de façon
   déterministe** à l'exécution depuis `bornes_acceptation` (borne la plus
   menacée par la droite des moyennes de réplicats — jamais choisis à vue,
   tracé en `assumptions` ; pré-déclarables explicitement via
   `spec_limite` + `direction`, ensemble ou pas du tout).
3. **Contre-vérification LLM (`_verifier_regles_metier`).** Omit ⇒ rejet ;
   dévier (décalage grille/groupe/fenêtre/horizon) ⇒ rejet ; proposer une
   sensibilité **non déclarée** dans la spec ⇒ rejet (jamais de post-hoc) ;
   incohérence rôle/op (`sensibilite` ↔ les 2 ops du catalogue, dans les
   deux sens) ⇒ rejet. Tout rejet = repli gabarit journalisé.
4. **Exécution (agent inférentiel).** Fenêtre glissante : points bruts
   (réplicats non agrégés — dispersion lot conservée, IC de prévision
   globale) via le contrôleur (tickets `exec_seq`). Ruban MNAR : copies PMM
   **alignées** des deux bras de la primaire (Welch/Mann-Whitney) ;
   « sans objet » (pas d'imputation déclenchée, primaire hors cadre) ⇒
   non interprétable + contradiction tracée — jamais de ruban de
   complaisance. La sensibilité ne modifie ni la primaire ni les scores
   (RA/CC figés par version : elle éclaire, elle ne corrige pas).
5. **Relecture & rapport.** La relecture recalcule les deux ops depuis les
   entrées archivées (whitelist étendue : `points`, `fenetre_mois`,
   `horizon_mois`, `spec_limite`, `direction`, `colonnes_g1/g2`, `deltas`,
   `groupe_ajuste`) ; un fait sourcé par analyse sensibilité paraît dans
   `lignes_faits` (verdict complet, renversement inclus le cas échéant).
6. **Schéma LLM.** `ANALYSE_ITEM` : rôle `sensibilite` + propriétés
   `fenetre_mois`, `horizon_mois` (≤ 12), `spec_limite`, `direction`,
   `deltas` (1 à 16), `groupe_mnar` — borne fine revérifiée en aval.

La sensibilité historique du bloc MI (`A1_sensibilite_MI` : pooling Rubin +
δ sur différence brute, automatique dès qu'une MI tourne) est **inchangée** ;
le ruban SMD A4 est son complément pré-déclaré et standardisé.

Démo de bout en bout : `python3 demo/run_sensibilite.py` (stabilité à
dérive lente — franchissement anticipé détecté dès t0=0 alors que le lot
est encore conforme à 12 mois ; ruban MNAR avec bascule δ=0,25 σ et
renversement marqué à δ=1 σ ; reproductibilité inter-runs prouvée).

## 5. Qualification numérique

Les deux ops sont **gelées dans l'oracle scipy** (`tests/qualification/`,
26 → 28 paquets) : contre-implémentations indépendantes
(`scipy.stats.linregress` + numpy pour les fenêtres ; σ_ref/SMD/Rubin
numpy pour le ruban — zéro code partagé avec le catalogue). Écart relatif
max mesuré : **7,1e-10** (< tolérance OPS 1e-9), franchissements et bascules
à égalité stricte, monotonie recalculée — voir
`docs/QUALIFICATION_SCIPY.md` §3.6. Le marquage de renversement (1.1.0) est
validé par tests dédiés (`tests/test_sensibilite_pipeline.py`) sans régéler
l'oracle : il n'altère aucun calcul gelé.

---

*Cadre méthodologique : tipping point sensitivity analysis (Yan, Lee & Li,
2009 ; CHMP E9(R1) estimands) ; fenêtres glissantes de détection en
stabilité (démarche ICH Q1E, extrapolation bornée).*
