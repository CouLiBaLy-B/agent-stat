# Parcours observationnel (médical) — cas-témoins · cohortes

Chantier « méthodologie observationnel » de la plateforme agent-stat.
Complète `docs/ARCHITECTURE.md` (vision), `docs/METHODOLOGIE_P1.md` (TOST, MI,
stabilité) et `docs/REFERENTIEL_REGLEMENTAIRE.md`. Principe fondateur inchangé :
**calcul 100 % déterministe, lexique contrôlé, validation humaine aux gates —
la plateforme est une aide à la décision, pas un substitut aux experts.**

---

## 1. Périmètre

| Design | Qualification (règles lexicales) | Primaire | Secondaire/descriptif |
|---|---|---|---|
| Cas-témoins **appariée 1:1** (`cas_temoins` + `appariement`) | motifs « cas-témoins » (0,8) + « apparié » (0,3) | `or_apparie` (OR conditionnel) | `proportion_wilson` par statut |
| Cas-témoins non appariée | motif « cas-témoins » | `odds_ratio_cas_temoins` | idem |
| Cohorte prospective/rétrospective avec temps d'événement | « cohorte » + « (pro|rétro)spective » | `km_logrank_hr` | `risque_relatif_cohorte` + incidences Wilson |
| Cohorte sans temps d'événement | idem | `risque_relatif_cohorte` | incidences Wilson |
| `observationnelle_transversale` | inchangé : gabarit descriptif, pas d'op d'association primaire | — | — |

`essai_randomise` conserve le droit au vocabulaire d'effet (inchangé —
gabarit hérité de `_gabarit_usage_cosmetique` en MVP).

## 2. Nouvelles opérations du catalogue (version 1.0.0, déterministes)

| Op | Estimation | IC 95 % | p | Garde-fous |
|---|---|---|---|---|
| `odds_ratio_cas_temoins(a,b,c,d)` | OR = a·d/b·c | Woolf (log OR) | Fisher exact | Haldane-Anscombe +0,5 **déclarée** si cellule nulle |
| `or_apparie(paires_b, paires_c)` | OR = b/c (discordants) | log(b′/c′) ± z√(1/b′+1/c′) | McNemar exact | +0,5 déclarée sur discordance nulle ; b=c=0 ⇒ non interprétable |
| `risque_relatif_cohorte(a,b,c,d)` | RR = R₁/R₀ et RD | Katz (log RR) ; RD : Newcombe méthode 10 (bornes Wilson) | Fisher exact | Haldane déclarée ; bras vide ⇒ non interprétable |
| `km_logrank_hr(t₁,e₁,t₂,e₂)` | KM par groupe + HR de Peto : log HR = (O₁−E₁)/V, SE = 1/√V | exp(log HR ± z·SE) | log-rang (Mantel), χ²₁ | groupe sans événement ⇒ non interprétable ; hypothèse « risques relatifs constants » explicitée **à confirmer** |

Chaque sortie embarque mesure + IC95 % + p (jamais la p seule), `interpretable`,
et une mention `lecture` non causale. Cellules d'entrée (comptes, séries) sont
stockées dans l'artefact `results` ⇒ la **relecture recalcule chaque chiffre**
(clefs de recalcul ajoutées au filtre de la vérification croisée).

Valeurs de référence gelées en tests (`tests/test_observationnel.py`) : table
(90, 40, 60, 110) → OR = 4,125 ; (40, 20) → OR apparié 2,0 ; (20, 180, 10,
190) → RR = 2,0 / RD = 0,05 ; exemple log-rang à 5+5 sujets (E₁ = 3,9187,
V = 1,1984, χ²₁ = 0,7042, HR = 0,4646) — toutes recalculées à la main.

### Limites d'estimation assumées (MVP)
- **Aucun ajustement multivarié** (régression logistique, Cox) : le SAP porte
  un bloc `garde_fous_observationnel` qui l'explicite, la rédaction le répète
  en inférences et limites, et toute ampleur est marquée « exploratoire ».
- HR Peto = approximation documentée (log-rank summary), pas un Cox.
- Sensibilité MI (PMM + Rubin + tipping point) scopée aux primaires continues
  (`t_test_welch`, `mann_whitney`) ; non appliquée aux ops d'association au MVP
  — recalcul de sensibilité par la relecture reste un chantier ultérieur.

## 3. Lexique causal opérationnel de bout en bout

| Étape | Mécanisme |
|---|---|
| Compréhension | qualification `cas_temoins` / `cohorte_*` (règles lexicales versionnées) |
| SAP (G3) | gabarits dédiés + bloc `garde_fous_observationnel` (lexique, ajustement, biais, réf. STROBE) — **verrouillé SHA-256 avant tout calcul** |
| Inférentiel | seuls les OR/RR/HR conditionnels aux données sont calculés (catalogue fermé) |
| Scores | `calculer_cc(..., observationnel_non_ajuste=True)` ⇒ **CC ≤ 0,60** (« moderee » max) — une association univariée ne soutient jamais une confiance élevée |
| Rédaction | FAITS : mesure + IC + p sourcés `art://…#sha256:` ; INFÉRENCES : « Association ≠ causalité », « non ajustée » ; conclusion `association_significative`/`association_non_significative` |
| Relecture | tournures causales interdites étendues (cause, réduit le risque, effet protecteur, dû à l'exposition…) ; objection **bloquante** `conclusion_hors_lexique_observationnel` si la conclusion n'est pas un label `association_*`/`tost_*` ; recalcul indépendant des chiffres |

## 4. Règles réglementaires/méthodologiques ajoutées (moteur v2)

| Règle | Portée | Statut |
|---|---|---|
| `R-DON-03-donnees-observationnel` | exposition + issue présentes ; cas-témoins appariée ⇒ variable de paire ; cohorte ⇒ événement (+ temps si déclaré) ; n ≥ 40 | OK / **KO → blocage `NON_CONFORMITE_BLOQUANTE`** |
| `R-OBS-01-lexique-association` | lexique d'association obligatoire + ampleur exploratoire (non ajustée) | **INFO** (réf. STROBE) — n'altère pas le verdict |

## 5. LLM derrière contrats (rappel du cadre)

- `ANALYSE_ITEM` étendu : `var_exposition`, `var_issue`, `var_evenement`,
  `var_temps_event`, `var_paire`, `modalite`/`modalite_evenement` (entiers 0/1
  au MVP — les modalités string du LLM sont refusées par le schéma et repliées).
- Enum `op` toujours = catalogue fermé (19 ops) ; catalogue de prompts
  `DESIGNATIONS_OPS` complété (ops P1 + observationnelles, suffixées
  « ASSOCIATION »).
- Contre-vérification métier : clés requises par op d'association
  (`CLES_METIER_ASSOCIATION`) — une proposition `or_apparie` sans `var_paire`
  est rejetée → **repli gabarit journalisé**.
- Le provider simulé recâblé reproduit les gabarits observationnels ; démo
  `AGENT_STAT_LLM_MODE=llm-simule python3 demo/run_observationnel.py` verte.

## 6. Démonstrations

```bash
python3 demo/run_observationnel.py     # cas-témoins appariée + cohorte HR
python3 -m unittest discover -s tests  # 118 tests (86 → 118, dont 32 obs.)
```

Sorties type (seed 20260727) :
- `MED-CT-2026-101` : OR apparié = 2,00 · IC95 [1,31 ; 3,06] · p = 1,4e-3 →
  `association_significative` · CONFORME · CC 0,60 « moderee ».
- `MED-CO-2026-102` : HR Peto = 2,75 · IC95 [1,84 ; 4,09] · p = 6,6e-7 ·
  RR = 2,19 [1,56 ; 3,09] → `association_significative` · CONFORME · CC 0,60.

## 7. Ce qui reste HUMAIN (rappel)

- Biostatisticien (G3) : adéquation OR/RR/HR à la question, hypothèse de
  proportionnalité (HR), stratégie d'ajustement éventuelle, puissance.
- Épidémiologiste / responsable d'étude (G6) : causalité (Bradford Hill),
  biais de sélection/information non mesurables ici, portée externe.
- Clinicien : pertinence clinique de l'exposition et de l'issue.

La plateforme **détecte, quantifie, trace et encadre** — elle ne conclut jamais
à la causalité sur un design observationnel.
