# Référentiel réglementaire structuré (chantier 2.2)

**Version :** 2.0.0 · relie `reglementaire/`, `agents_impl/gardefous.py` (agent conformité).

## Pourquoi

L'échantillon pédagogique v1 est remplacé par un **corpus structuré versionné** et un
**moteur de règles contextuel**. Bénéfices : dérogations annexe II/III gérées dans le
**contexte produit** (leave-on/rinse-off, usage, zone, population), restrictions III–VI,
agrégats (somme des parabens), méthodes animales détectées face aux alternatives OCDE
validées, **mesure de couverture INCI honnête** — une substance absente du corpus est
rapportée INCERTAIN (revue experte), jamais conforme par défaut.

## Contenu (`reglementaire/donnees/`, corpus v2.0.0)

| Fichier | Contenu | Exemples traités en tests |
|---|---|---|
| `annexe_ii.json` | ~13 substances interdites (JOUE, extraction partielle **NON EXHAUSTIVE**), synonymes, réfs fiables seulement (`ref=null` + `a_verifier` sinon) | hydroquinone 1339 avec **dérogation III/14 ongles artificiels ≤ 0,02 %**, formaldehyde 1577 |
| `restrictions.json` | Annexes III–VI : limites **conditionnelles** (`condition: {application/usage/zone/population_cible}`) | MIT/MCIT 0,0015 % **rinse-off uniquement**, salicylate interdit < 3 ans, phénoxyéthanol ≤ 1 %, parabens 0,14/0,4 % + **agrégat 0,8 %**, filtres UV, H₂O₂, thioglycolates, DMDM hydantoïne |
| `substances_connues.json` | INCI courants sans restriction — base de la couverture | aqua, glycerin, niacinamide, tocopherol… |
| `methodes_alternatives_oecd.json` | TG OCDE validées (431, 439, 428, 442C/D/E, 437, 492B, 471, 487) + motifs animaux interdits | "Draize" → KO art. 18 ; "OCDE 439" → OK ; méthode inconnue → INCERTAIN |
| `sccs_params.json` | MoS ≥ 100, absorption par défaut 50 %, ordres d'exposition/jour, réf. SCCS NoG 12 | consommés par l'agent sécurité (MoS) |

`Referentiel.sha256` = empreinte du corpus complet, **citée dans chaque
compliance_report** (preuve de la version de droit appliquée à cette étude).

## Moteur (`reglementaire/moteur_regles.py`)

Règles = données + évaluateurs typés. Statuts : `OK` / `KO` (→ blocage dur) /
`INCERTAIN` (→ gate humain G4 expert réglementaire) / `INFO` (étiquetage, notes).
Principes stricts : contexte produit obligatoire (données requises R-DON-01 le vérifie),
dérogation non applicable au contexte = interdiction appliquée, agrégats calculés sur la
composition normalisée, INCI inconnue = couverture −1 + INCERTAIN tracé.

Verdicts d'ensemble : `CONFORME` / `CONFORME_SOUS_RESERVE` (INCERTAIN → G4) /
`NON_CONFORME_BLOQUANT` (KO → blocage) / `INSUFFISANT`.

## Cycle de vie du corpus (process de production)

1. **Import consolidé** depuis le texte consolidé officiel (JOUE / EUR-Lex) — extraction assistée, jamais sans revue ;
2. **Revue expert réglementaire** — validation humaine du corpus (gate G-corpus, ADR au registre) ;
3. **Version + hash** — bump `version`, hash recalculé automatiquement ;
4. **Requalification** — les études en cours sont marquées `a_revoir_corpus` ; exécution de la batterie de tests de non-régression (règles de référence gelées dans `tests/test_reglementaire.py`) ;
5. **Traçabilité** — chaque rapport d'étude prouve version+hash du droit appliqué.

## Limites assumées (v2)

Corpus **non exhaustif** (annexe II réelle ≈ 1 700 entrées) ; concentrations toxico
précises (SED/MoS par ingrédient) hors corpus, gérées par l'agent sécurité + revue
toxicologue ; frontière cosmétique/médicament, allégations (claims UE 655/2013) et
étiquetage complet non couverts ; avertissements de population/nano signalés en INFO,
non bloquants. Objectif Phase 2 : exhaustivité annexes II–VI via pipeline d'import +
revue, couverture INCI mesurée en continu.
