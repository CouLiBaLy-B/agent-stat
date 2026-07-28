# Allégations ue 655/2013 + étiquetage art. 19 — contrôle réglementaire

> Chantier « claims » de la plateforme agent-stat. Complète
> `docs/REFERENTIEL_REGLEMENTAIRE.md` (corpus v2) : le moteur de règles
> contrôle désormais les **allégations produit** (critères communs du
> Règlement (UE) n° 655/2013) et l'**étiquetage** (mentions art. 19 UE
> 1223/2009), avec vérification déterministe de la concordance
> allégué ↔ mesuré. Corpus de démonstration **non exhaustif** — chaque règle
> cite sa référence et la version/hash du corpus.

## 1. Entrées contractuelles (dossier produit)

```jsonc
"claims": [
  {"id": "C1", "type": "efficacite",           // efficacite|tolerance|marketing
   "texte": "Améliore visiblement le confort de la peau…",
   "endpoint": "delta_score",                   // requis pour efficacite
   "direction_favorable": "+1",                 // +1 (défaut) | -1
   "justificatif": "étude test d'usage COS-2026-014, 60 sujets, 4 semaines"}
],
"etiquetage": {
  "responsable_nom_adresse": "…", "pays_origine": "…", "contenu_nominal": "…",
  "pao_ou_dluo": "…", "precautions": "…", "numero_lot": "…",
  "fonction_produit": "…", "liste_inci": ["aqua", "glycerin", …]
}
```

## 2. Règles R-CLM (UE 655/2013 — critères communs C1..C6)

| Règle | Exigence | Verdicts |
|---|---|---|
| `R-CLM-01-<id>` | **Étayage** (C3) : tout claim porte un justificatif | efficacite/tolerance sans justificatif ⇒ **KO** ; marketing ⇒ INCERTAIN |
| `R-CLM-02-<id>` | **Concordance allégué ↔ mesuré** (C2 sincérité) : résultat sur l'endpoint, interprétable, significatif (p<0,05) **et** dans la direction favorable — ou **équivalence démontrée** (TOST) | contredit / non significatif / endpoint non mesuré ⇒ **KO** ; endpoint non déclaré / résultats absents ⇒ INCERTAIN |
| `R-CLM-03-<id>` | **Lexique contrôlé** : motifs sanitaires (guérit, cicatrise, psoriasis, tue les virus…) et trompeurs absolus (sans danger, sans produit chimique, efficace à 100 %…) ⇒ **KO** ; « sans X » avec **X ∈ annexe II** ⇒ **KO** (C1 : le respect de la loi n'est pas un avantage) ; « sans X » autre ⇒ **INFO** (C5, risque de dénigrement) ; aucun motif détecté ⇒ OK *avec avertissement de lexique non exhaustif* |

Principes inchangés : corpus hashé et versionné (`claims_etiquetage.json`
 inclus dans l'empreinte `Referentiel.sha256`) ; un OK n'est **pas** une
autorisation réglementaire, seulement « aucun motif contrôlé détecté ».

## 3. Règles R-ETQ (art. 19 UE 1223/2009)

| Règle | Exigence | Verdict |
|---|---|---|
| `R-ETQ-01-mentions-obligatoires` | 8 mentions obligatoires présentes et non vides | manquantes ⇒ **KO** |
| `R-ETQ-02-couverture-inci` | tout ingrédient de la composition figure sur l'étiquette | absent ⇒ **KO** |
| `R-ETQ-03-ordre-inci` | ingrédients > 1 % en ordre décroissant de concentration | désordre ⇒ **KO** |
| `R-ETQ-03b-incoherence-dossier` | ingrédient d'étiquette non déclaré dans la composition | INFO (cohérence dossier) |
| `R-CLM-00` / `R-ETQ-00` | claims/étiquetage absents du dossier (cosmétique) | **INFO** — contrôle appliqué dès déclaration ; le dossier produit final reste requis avant marché |

Le domaine `medical` ne déclenche aucune de ces règles (vérifié par test).

## 4. Intégration pipeline

- L'agent **conformité** alimente le moteur avec `claims`/`etiquetage`/`domaine`
  + les résultats inférentiels (champ `var` désormais exposé par analyse —
  schéma de résultat enrichi, déterministe) ;
- les **KO claims/étiquetage entrent dans `regles_ko`** ⇒ verdict
  `NON_CONFORME_BLOQUANTE` ⇒ blocage dur avec déblocage « expert
  réglementaire » — identique aux autres KO réglementaires ;
- les dossiers de gate G4/G6 affichent déjà `regles_ko`/`regles_incertaines` :
  les nouvelles règles y apparaissent sans autre changement.

## 5. Démonstration et tests

```bash
python3 demo/run_demo.py       # nominal : 10 règles R-CLM/R-ETQ OK, CONFORME
python3 -m unittest tests.test_claims   # 28 tests dédiés
```

Prouvé par les tests e2e : claim sanitaire ⇒ blocage ; claim contredit par
le résultat mesuré (direction favorable opposée) ⇒ blocage ; étiquette INCI
incohérente (couverture/ordre) ⇒ blocage ; nominal avec claims conformes ⇒
TERMINE ; verdict agrégé non régressif sur stabilité/observationnel (INFO
seulement si claims/étiquetage absents).

## 6. Limites assumées

- Lexique interdit **déclaratif et non exhaustif** (14 motifs) : le contrôle
  humain G4/G6 reste l'autorité ; tout motif nouveau s'ajoute au corpus JSON
  (avec référence), ce qui change le hash de corpus — tracé.
- Extraction « sans X » simple (premier groupe jusqu'à « et »/ponctuation) ;
les tournures exotiques remontent en INFO, jamais validées « par défaut ».
- La concordance concerne les résultats **calculés par le pipeline** ; un
justificatif externe (publication) reste à revue experte.
- L'ordre INCI ≤ 1 % est libre (réglementairement) : aucune règle au-delà.
