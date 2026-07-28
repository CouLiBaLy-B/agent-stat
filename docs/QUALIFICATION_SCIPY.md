# Qualification numérique contre scipy (oracle gelé)

Ce document décrit la **qualification hors runtime** du catalogue
statistique (`stats_catalogue/`) contre scipy : méthodologie, périmètre,
tolérances mesurées, et procédure de régénération de l'oracle.

> Principe produit inchangé : **le runtime reste 100 % stdlib**. scipy est
> utilisé UNIQUEMENT comme oracle de qualification, dans un environnement
> isolé, pour produire des **valeurs gelées** embarquées dans le dépôt.
> Ni le pipeline, ni les démos, ni les tests n'importent scipy ; scipy
> n'entre jamais dans la chaîne de calcul d'une étude réelle.

---

## 1. Architecture

```
tests/qualification/
├── jeux.py               # jeux de données déterministes stdlib (SEED = 20260728)
├── reference_scipy.py    # VALEURS GELÉES — généré, ne pas éditer à la main
└── test_qualification.py # tests stdlib : catalogue vs valeurs gelées
scripts/
└── qualifier_scipy.py    # GÉNÉRATEUR de la référence — nécessite scipy épinglé,
                          # outil développeur, jamais exécuté en CI/runtime
```

- `jeux.py` est la source unique des données : toute modification y changeant
  les jeux impose une **régénération de la référence** (§4) puis le
  re-mesurage des tolérances (§3).
- `qualifier_scipy.py` n'importe **aucun** module de `stats_catalogue` : les
  valeurs de référence proviennent de scipy ou de ré-implémentations
  indépendantes des formules publiées (Woolf, Katz, Wilson, Newcombe,
  log-rang de Mantel, estimateur de Peto, TOST, pooling de Rubin, OLS),
  adossées à scipy pour les primitives de lois.
- `test_qualification.py` compare champ par champ, en stdlib pur, et peut
  donc s'exécuter partout (CI incluse) sans aucune dépendance.

## 2. Périmètre qualifié

**Distributions (`stats_catalogue/dist.py`)** — 15 grilles, **496 points** :
`norm_cdf`, `norm_sf`, `norm_ppf`, `t_cdf`, `t_sf`, `t_ppf`, `chi2_cdf`,
`chi2_sf`, `f_cdf`, `f_sf`, `binom_cdf`, `beta_ppf`, `incbeta`, `gamma_p`,
`gamma_q` — y compris queues extrêmes (p. ex. `t_sf(30 ; 29) ≈ 1e-23`,
`chi2_sf` jusqu'à ~1e-130, `norm_cdf(−8)`).

**Opérations (`stats_catalogue/ops.py`)** — **28 paquets** sur jeux
déterministes (continu, ex æquo forcés par arrondi, cellules nulles,
censure, échantillons déséquilibrés) :

| Famille | Ops qualifiées | Oracle |
|---|---|---|
| moyennes | `t_test_welch` (×2, variances égales/très inégales), `tost_equivalence` | `scipy.stats.ttest_ind`, formule TOST + `t` |
| rangs | `mann_whitney` (continuité ± ex æquo), `kruskal_wallis` (± correction d'ex æquo — scipy ne la fait pas, ré-implémentation indépendante) | `scipy.stats.mannwhitneyu`, ré-implémentation + `chi2` |
| tableaux | `odds_ratio_cas_temoins` (Woolf + Haldane), `risque_relatif_cohorte` (Katz + différence Newcombe), `fisher_exact_2x2`, `mcnemar`, `or_apparie`, `chi2_independance` (+ V de Cramér) | `scipy.stats.fisher_exact`, `binomtest`, `chi2_contingency`, ré-implémentations publiées |
| proportions | `proportion_exacte` (Clopper-Pearson), `proportion_wilson` | `beta.ppf`, formule Wilson |
| stabilité | `tendance_lineaire` (OLS + IC de prévision), `tendance_fenetre_glissante` (sensibilité « passage au grand mail ») — §3.6 | `linregress` + formule IC prévision |
| survie | `km_logrank_hr` (KM, log-rang Mantel, HR de Peto) — jeux simple et censuré | ré-implémentation indépendante tabulée par dict |
| imputation | `pooling_rubin` | ré-implémentation indépendante des règles de Rubin |
| descriptif | `descriptif_continu` (moyenne, sd, quartiles type 7, se), `smd_groupes` | `np.mean/std/percentile` |
| normalité | `test_normalite` (Anderson-Darling) — cas spécial, §3.3 | `scipy.stats.anderson(method='interpolate')` |
| ajustement | `regression_logistique` (IRLS), `cox_ph` (Newton, Breslow) — cas spécial, §3.4 | BFGS `scipy.optimize` avec jacobiens exacts (ré-implémentations indépendantes), inversions `numpy.linalg` |

## 3. Tolérances (mesurées, puis fixées avec marge)

Règle hybride unique : un écart est recevable ssi
`|mesuré − référence| ≤ max(TOL_ABS, TOL_REL × |référence|)`.

### 3.1 Distributions : `DIST_REL = 1e-10`, `DIST_ABS = 1e-12`

Écarts max mesurés sur les 496 points :

| Grille | écart relatif max | écart absolu max | cause |
|---|---|---|---|
| `norm_ppf` | 2,4e-12 | 1,1e-11 | itérations de Newton (inversion) |
| `t_ppf` | 6,2e-13 | 1,8e-11 | idem |
| `norm_cdf` (x = −8) | 1,8e-2 | **2,7e-17** | queue extrême : relatif peu lisible, absolu couvre |
| `t_sf` / `f_sf` (queues) | ~1 | **7,7e-14** | underflow du complément 1−cdf : scipy ≈ 1e-23, stdlib exactement 0 → tolérance ABSOLUE seule, assumée (aucun seuil réglementaire ne travaille à 1e-14 près) |
| toutes les autres | ≤ 6e-14 | ≤ 1e-14 | bruit d'arrondi flottant |

### 3.2 Opérations : `OPS_REL = 1e-9`, `OPS_ABS = 1e-12`

Écart relatif max mesuré hors p-values d'Anderson : **≈ 3,2e-6**
(`p_valeur_pente` de l'OLS, différence d'itérations dans `linregress`) ;
toutes les autres comparaisons ≤ 3e-13, la grande majorité ≤ 1e-15
(accord quasi bit-à-bit). Les intervalles de confiance du catalogue sont
qualifiés en les reconstruisant depuis les quantiles de référence
(`tc95`, `tc90` stockés dans l'oracle) — cela qualifie conjointement les
quantiles de `dist.py` en situation réelle.

### 3.3 Anderson-Darling : règle spécifique documentée

scipy 1.17 (`method="interpolate"`) interpole la p-value depuis des tables
précalculées **plafonnées à [0,01 ; 0,15]** ; notre catalogue applique les
formules asymptotiques de **Marsaglia & Marsaglia** sur la statistique
**corrigée de Stephens** \(A^{2*} = A^2(1 + 0{,}75/n + 2{,}25/n^2)\).
Ce sont deux familles d'interpolation différentes : comparer les p en
valeur n'aurait pas de sens. On qualifie donc :

1. **la statistique A² brute** — accord quasi bit-à-bit (écart mesuré
   ≤ 4e-14 ; les deux implémentations normalisent par sd à `ddof=1`) ;
2. **le verdict à α = 5 %** — identique à scipy sur les deux jeux
   (`normalite_ok` : scipy p = 0,15 → `normal_ok` ; `normalite_ko`
   gamma(1,5) : scipy p = 0,01 → `non_normal`, idem côté catalogue) ;
3. la **cohérence interne** `verdict ↔ p` de notre implémentation.

Les jeux ont été **choisis** pour être interprétables par les deux
familles (un gaussien « malchanceux » rejeté par scipy à p = 0,0138 a été
écarté comme jeu `normalite_ok`, pour ne pas figer une discordance
d'arête dans l'oracle).

### 3.4 Modèles multivariés : `MULTI_REL = 1e-5`, `MULTI_ABS = 1e-9`

Le `regression_logistique` (IRLS) et le `cox_ph` (Newton sur vraisemblance
partielle de Breslow) du catalogue sont qualifiés contre des
contre-implémentations **indépendantes** par nature : BFGS
(`scipy.optimize.minimize`) avec jacobiens analytiques, matrices inversées
par `numpy.linalg` — aucun code partagé avec le catalogue hors primitives
de lois et jeux. La convergence de l'oracle est jugée sur la **norme du
gradient final** (< 10⁻⁵), la line-search BFGS signalant parfois une
« precision loss » alors que l'optimum est atteint (comportement documenté
de scipy).

Écarts mesurés (1 jeu logistique n = 240 ; 2 jeux Cox n = 300/260 dont
ex æquo massifs et censure à 70 %) : **4,2e-9** en relatif max
(logistique : β, se, IC, LL1/LL0, LRT, pseudo-R², AIC) et **1,7e-7**
(Cox : β, se, HR, IC, LL partielle, χ² du score, p). Tolérances fixées à
**×60** de marge. Contrôle croisé interne supplémentaire, indépendant de
l'oracle : sans ex æquo sur les temps d'événements, le test du score de
Cox à 1 covariable binaire **égale** la statistique du log-rang (propriété
exacte, validée à 10⁻⁹).

### 3.6 Sensibilité : fenêtre glissante & ruban MNAR (tolérance `OPS`)

Les deux ops de sensibilité du catalogue (`tendance_fenetre_glissante`,
`tipping_point_mnar_smd`) sont qualifiées — comme l'ajustement — par des
**contre-implémentations indépendantes** ne partageant aucune ligne de
calcul avec le catalogue :

- `tendance_fenetre_glissante:tendance_glissante` : OLS local par fenêtre
  via `scipy.stats.linregress`, IC95 de prévision par primitives
  numpy/Student. Comparaison sur chaque fenêtre couverte (pente, prévision,
  borne pessimiste, demi-IC, mois de franchissement prévu) ; franchissement
  (`franchit`) et premiers mois à **égalité stricte** ;
- `tipping_point_mnar_smd:mnar_ruban` : σ_ref poolé, SMD « catalogue »
  (sp = √((v1+v2)/2)) + variance asymptotique `_hedges_np`, re-pooling
  `_rubin_np` — tous numpy. Comparaison par δ du ruban (θ, se, p, ddl,
  décalage en unités) + σ_ref ; `delta_bascule` à égalité stricte ;
  monotonie |θ| recalculée sur les θ gelés ;
- entrées partagées bit-à-bit via `tests/qualification/jeux.py`
  (`entrees_tendance_glissante()`, `entrees_mnar_ruban()` — le ruban se
  qualifie sur des copies alignées jitterées : les entrées du tipping point
  SONT des listes de copies, pas besoin d'un PMM réel — `jeux.py` n'importe
  jamais le catalogue).

Écart relatif maximal mesuré : **7,1e-10** (tendance : bit-à-bit exact ;
y compris sur les ddl ~7e4 du Barnard-Rubin de l'oracle) — sous la
tolérance commune `OPS_REL = 1e-9`.

## 4. Régénération de l'oracle

Prérequis : un venv **hors dépôt** avec scipy épinglé (l'assert de version
dans le générateur refuse toute autre version) :

```bash
python3 -m venv /tmp/qualif
/tmp/qualif/bin/pip install "scipy==1.17.1"
/tmp/qualif/bin/python scripts/qualifier_scipy.py
python3 -m unittest discover -s tests/qualification   # doit passer
```

Gardes intégrées :

- le générateur échoue si la version de scipy n'est pas exactement celle
  épinglée (`SCIPY_EPINGLET`) ;
- le sanitizer `_py()` convertit récursivement les types numpy en types
  Python natifs — la référence gelée reste **importable en stdlib pur**
  (numpy 2.x : un `repr()` naïf produirait `np.float64(...)` et casserait
  l'import) ;
- `TestReferenceSaine.test_aucun_nan_ni_inf_gele` vérifie à chaque run de
  tests qu'aucun `nan`/`inf` n'est gelé dans l'oracle (piège réellement
  rencontré : un jeu dégénéré à variance nulle faisait rendre `nan` à
  scipy — c'est la suite de tests qui doit le dire, pas un utilisateur) ;
- `TestReferenceSaine` verrouille la méta épinglée et le volume attendu
  (≥ 15 grilles dist, ≥ 15 paquets d'ops — aujourd'hui 26) ainsi que
  l'absence de nan/inf gelés.

Après toute régénération : relancer le mesurage des écarts max (recette
du §3) ; si un écart dépasse la tolérance fixée, soit le nouveau résultat
est correct et on documente le relèvement de tolérance **motivé**, soit
c'est une régression et on corrige `stats_catalogue` — jamais l'inverse
(sauf erreur démontrée de l'oracle, tracée dans ce document).

## 5. Ce que cette qualification ne couvre pas (assumé)

- Les domaines hors des grilles (p. ex. `t_ppf` à ddl non entier < 1,
  beta_ppf en queue extrême < 1e-6) sont couverts par les tests unitaires
  du module mais **pas** contre scipy : y étendre la grille est un chantier
  possible.
- Les p-values de normalité ne sont volontairement pas comparées en valeur
  (§3.3).
- La qualification mesure l'accord numérique sur plateforme x86-64 /
  CPython 3.11 ; les tolérances hybrides absorbent les variations d'arrondi
  attendues entre plateformes IEEE-754.
- scipy 1.17.1 est l'oracle épinglé **actuel** ; monter de version
  d'oracle se fait en régénérant la référence + re-mesurage (§4), avec
  revue des différences.

---

*Références : scipy 1.17.1 (`scipy.stats`), Stephens (1974) pour la
correction d'Anderson-Darling, Marsaglia & Marsaglia (2004, JSS 9-2) pour
les p-values asymptotiques, Woolf (1955), Katz et al. (1978), Wilson
(1927), Newcombe (1998, méthode 10), Mantel (1966), Peto & Peto (1972),
Rubin (1987), Clopper & Pearson (1934).*
