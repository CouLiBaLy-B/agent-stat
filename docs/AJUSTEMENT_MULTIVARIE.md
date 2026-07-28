# Ajustement multivarié cadré (régression logistique & Cox)

Ce document décrit le chantier « ajustement multivarié cadré » : pourquoi,
comment il est verrouillé au SAP, quels garde-fous s'appliquent, et comment
les implémentations sont qualifiées. Il prolonge `docs/OBSERVATIONNEL.md`
(lexique d'association) et `docs/QUALIFICATION_SCIPY.md` (oracle gelé).

> Règle produit rappelée : **un ajustement multivarié n'existe que s'il est
> pré-déclaré au SAP et verrouillé humainement (G3).** Aucun ajustement
> post-hoc, aucun « essai de modèles » : la plateforme exécute EXACTEMENT le
> modèle verrouillé, ou refuse de produire une mesure (fail-closed).

---

## 1. Pourquoi

Les mesures univariées (OR de Woolf, RR, HR de Peto) livrées par le parcours
observationnel du MVP peuvent être **biaisées par confusion** : p. ex. une
exposition plus fréquente chez les sujets âgés paraît neutre en brut alors
que la mesure générante est protectrice (démo `run_ajustement.py` :
HR brut 0,846 p = 0,257 ⇒ HR ajusté 0,419 p = 4×10⁻⁷). L'ajustement
multivarié corrige les confondeurs MESURÉS et pré-déclarés — jamais la
confusion non mesurée, qui demeure une limite explicite du rapport.

## 2. Catalogue : deux nouvelles ops déterministes (version 1.0.0)

| Op | Méthode | Sorties |
|---|---|---|
| `regression_logistique` | IRLS (Newton-Raphson itératif), systèmes normaux résolus par élimination de Gauss à pivot partiel (stdlib) | par covariable : β, se, z, p de Wald bilatérale, **OR ajusté** + IC95 % ; intercept ; LL, LL du modèle nul, χ² du modèle (LRT), pseudo-R² de McFadden, AIC, n, EPV, itérations |
| `cox_ph` | vraisemblance partielle de **Breslow** (ex æquo), itérations de Newton gradient + information exacts ; covariables **centrées** (invariant exact du modèle, stabilité numérique) | par covariable : β, se, z, p de Wald, **HR ajusté** + IC95 % ; LL partielle, test du score global χ², n, événements, EPV, itérations |

Règles invariantes du catalogue : **p jamais seule** (IC95 % + mesure
d'effet sur chaque coefficient), `interpretable: False` + `motif` en cas de
garde-fou, aucune valeur silencieusement dégénérée.

Algèbre : `_gauss_resoudre` / `_gauss_inverser` (pivot partiel, pivot
< 10⁻¹² ⇒ singulière ⇒ refus). Aucune dépendance externe.

## 3. Garde-fous fail-closed (les deux modèles)

| Garde-fou | Seuil | Motif renvoyé |
|---|---|---|
| événements par variable (**EPV** = min(cas, témoins) [logistique] ou événements [Cox] / nb covariables) | < `epv_min` (SAP, ≥ 5, défaut 10) | `EPV insuffisant` — anti sur-ajustement |
| colinéarité exacte entre covariables | pivot < 10⁻¹² | matrice d'information singulière |
| quasi-séparation | \|β\| > 15 (OR/HR > 3×10⁶) | modèle non identifiable — revue statisticien |
| non-convergence | > 60 itérations | échec IRLS/Newton explicite |
| covariable constante | min = max exact | variance nulle |
| trop de covariables | > 14 (+ intercept ≤ 15) | borne dure |
| données | n < 20, contrats 0/1, temps ≤ 0, désalignement | refus ciblé |

Cas-témoins **appariée** + ajustement ⇒ blocage SAP : la régression
logistique *conditionnelle* n'est pas au catalogue — décision humaine G3,
jamais d'approximation silencieuse.

Covariables catégorielles à > 2 modalités ⇒ blocage : le codage en k−1
indicatrices est hors MVP (décision G3).

Conséquence pipeline : une analyse A3 non interprétable produit une
**contradiction d'arbitrage tracée** (`etat.decisions`), un fait de rapport
« NON interprétable — aucune mesure ajustée présentée », et rabat la
conclusion sur la mesure univariée (exploratoire).

## 4. Verrouillage SAP (G3) et flux pipeline

1. **Spécification** : `spec["ajustement_multivarie"] = {"covariables": [...], "epv_min": 10}` (spécification d'étude, revue humaine).
2. **Agent biostat** : le gabarit du type observationnel ajoute l'analyse
   **A3 (`role: "ajustement"`)** — `regression_logistique` (cas-témoins non
   appariée ; cohorte sans temps) ou `cox_ph` (cohorte avec temps
   d'événement). L'**exposition est imposée en 1re covariable** ; la règle
   « exactement UNE primaire » est préservée (A1 reste l'association brute,
   rapportée à titre descriptif). Contrôles fail-closed : covariables
   existantes dans la spec, `epv_min ≥ 5`, pas de redoublement de
   l'exposition. Le bloc `garde_fous_observationnel.ajustement` bascule en
   texte « PRÉ-DÉCLARÉ + tracé EPV + interdiction du post-hoc ».
3. **Agent inférentiel** : construit X/y de façon déterministe — modalités
   déclarées (exposé/témoin, cas, événement), covariables `continue` en
   float, binaires en 0/1 par modalité ordonnée — en **cas complets**
   (cohérent avec la stratégie manquants du SAP), double exécution + hash.
4. **Conclusion directionnelle** : quand A3 est interprétable, la mesure
   d'intérêt pré-déclarée est l'association **ajustée** (p de Wald de
   l'exposition) ⇒ labels `association_ajustee_significative` /
   `_non_significative` (toujours préfixés `association` — lexique préservé).
5. **Rapport** : FAITS sourcés `art://…#sha256:…` avec TOUS les coefficients
   (OR/HR + IC95 % + p Wald, EPV, n, pseudo-R²/χ²) ; INFÉRENCES rappelant
   « association ajustée ≠ causalité » ; LIMITES listant les hypothèses NON
   testées (forme fonctionnelle / risques proportionnels) comme charge du
   statisticien à G3.
6. **Relecture** : recalcul indépendant de l'ajustement depuis les entrées
   embarquées (clés `y`, `x`, `temps`, `evenements`, `epv_min` ajoutées à la
   liste blanche) + objections lexique/CC inchangées.

## 5. Scores (formule versionnée scores-1.1.0)

- Observationnel **non ajusté** : CC ≤ **0,60** (inchangé).
- Observationnel **ajusté** (A3 interprétable) : CC ≤ **0,75** — le plafond
  est levé, mais une association ajustée n'atteint **jamais** le niveau
  « confiance élevée » (≥ 0,8) réservé à l'expérimental : la confusion NON
  mesurée demeure. Les autres plafonds (DQ < 0,60 ⇒ ≤ 0,40 ; post-hoc
  ⇒ ≤ 0,50 ; endpoint non pré-spécifié ⇒ ≤ 0,60) restent prioritaires.
- Historique des constantes en tête de `orchestration/scores.py`
  (modification = décision de registre — ce chantier).

## 6. Qualification numérique (oracle gelé)

- **Oracle** (`scripts/qualifier_scipy.py`) : contre-implémentations
  INDÉPENDANTES de l'IRLS/Newton du catalogue — BFGS `scipy.optimize` avec
  jacobiens exacts (log-vraisemblance logistique ; log-vraisemblance
  partielle de Breslow), inversions `numpy.linalg`, convergence vérifiée par
  norme du gradient final (la line-search BFGS échoue parfois par
  « precision loss » alors que l'optimum est atteint).
- **Tolérances** mesurées sur 1 jeu logistique (n = 240, confusion d'âge) et
  2 jeux Cox (n = 300/260, ex æquo massifs, censure 30 %/70 %) : écarts
  relatifs max 4,2×10⁻⁹ (logistique) / 1,7×10⁻⁷ (Cox) ⇒
  `MULTI_REL = 1e-5`, `MULTI_ABS = 1e-9` (marge ×60), documentées au §3.4 de
  `docs/QUALIFICATION_SCIPY.md`.
- **Contrôle croisé interne** (théorie validée numériquement) : SANS ex æquo
  sur les temps d'événements, le test du score de Cox à UNE covariable
  binaire **coïncide exactement** avec la statistique du log-rang de Mantel
  (test dédié en qualification, tolérance 10⁻⁹). Avec ex æquo massifs,
  l'approximation de Breslow diffère légèrement — attendu et documenté.

## 7. Limites assumées (à la charge de l'humain)

- **Hypothèses non testées par le moteur** : linéarité du logit (forme
  fonctionnelle des covariables continues), risques proportionnels
  (résidus de Schoenfeld) — déclarées à G3 et reprises en section limites ;
  chantier de diagnostics (Schoenfeld, dfbeta) possible.
- Cas complets : la stratégie manquants du SAP (≤ 5 %) s'applique aux
  analyses ajustées ; la MI poolée n'est pas câblée aux modèles
  multivariés (extension possible).
- Pas de logistique conditionnelle (cas-témoins appariée), pas de codage
  k−1 indicatrices, pas de sélection automatique de modèle, pas
  d'interactions dans la spec MVP.
- La mesure ajustée reste une **association** : aucun lexique causal n'est
  débloqué par l'ajustement (relecture inchangée).

## 8. Vérification

```bash
python3 -m unittest tests.test_ajustement tests.test_ajustement_pipeline
python3 -m unittest discover -s tests/qualification   # vs oracle BFGS gelé
python3 demo/run_ajustement.py                        # E2E : brut → ajusté
AGENT_STAT_LLM_MODE=llm-simule python3 demo/run_ajustement.py
```

*Références : Hosmer, Lemeshow & Sturdivant (2013) pour la logistique ;
Cox (1972) ; Breslow (1974) pour les ex æquo ; Mantel (1966) pour le
contrôle croisé log-rang ; Peduzzi et al. (1996) pour la règle EPV ≥ 10 ;
STROBE pour le reporting.*
