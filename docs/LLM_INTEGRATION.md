# Intégration LLM derrière contrats d'agents (chantier 2.1)

**Version :** 1.0.0 · relie `llm/`, `agents_impl/comprehension.py`, `agents_impl/biostat.py`.

## Principe

Le LLM est un **composant interchangeable derrière un contrat**, jamais un point de
confiance : il **classe, propose et rédige**, il ne **calcule** ni ne **conclut**.
Chaque sortie LLM traverse trois barrières avant d'avoir le moindre effet :

```
LLM → 1) schéma JSON (enum = catalogue fermé, au niveau du prompt ET du validateur)
      → 2) règles métier déterministes (1 primaire, variables existantes, fallback…)
      → 3) garde-fous aval inchangés (verrou SAP G3, relecture par recalcul, CC…)
      └─ toute défaillance → REPLI DÉTERMINISTE journalisé (jamais de blocage silencieux)
```

## Composants (`llm/`)

| Module | Rôle |
|---|---|
| `provider.py` | `LLMProvider` (protocole) · `ProviderOpenAICompatible` (HTTP chat/completions stdlib, `AGENT_STAT_LLM_BASE_URL/API_KEY/MODEL`) · `ProviderSimule` (scripté, tests/démo) · `provider_depuis_env()` (`AGENT_STAT_LLM_MODE ∈ {off, llm-simule, http}`) |
| `validation.py` | validateur JSON-Schema minimal — renvoie la liste des erreurs localisées (remplaçable par `jsonschema` épinglé sans changement de contrat) |
| `generation.py` | boucle génération → validation → **rétroaction bornée (erreurs de schéma seulement)** → ErreurLLM après N tentatives |
| `schemas.py` | contrats opposables versionnés ; `ANALYSE_ITEM.op.enum = catalogue d'ops` trié |
| `prompts.py` | prompts système versionnés, contenus documents encapsulés `<donnees>` (anti-injection) |
| `simule.py` | répondeurs déterministes : il s'agit de tester la **plomberie**, pas l'intelligence |

Activation : `AGENT_STAT_LLM_MODE=llm-simule python3 demo/run_demo.py` (ou `http` avec
les variables d'endpoint). Mode `off` par défaut : comportement strictement identique à
l'existant (tests de non-régression à l'appui).

## Menaces → contrôles

| Menace | Contrôle implémenté |
|---|---|
| Méthode statistique hallucinée | enum `op` = catalogue fermé **dans le schéma** ; contre-vérification `_verifier_regles_metier` ; toute op hors catalogue → rejet + repli |
| Chiffres inventés | le LLM n'a accès à **aucun** chiffre de résultat (il propose des plans) ; tout calcul reste dans le moteur déterministe double-exécuté ; la relecture recalcule |
| Biais de design (2 critères primaires, variable inexistante, test paramétrique sans fallback) | règles métier déterministes post-génération, violation ⇒ repli gabarit |
| Prompt injection depuis documents ingérés | contenu encapsulé `<donnees>`, consigne explicite « jamais d'instruction », champ `contradictions` pour remonter les injonctions hostiles ; outils en allowlist inchangés |
| Non-déterminisme / dérive | température 0, identité provider épinglée, hash prompt+réponse au journal d'audit, repli déterministe à iso-sortie |
| Données personnelles vers le LLM | amont : pseudonymisation obligatoire (politique plateforme) ; le LLM ne reçoit que métadonnées/spec, jamais les lignes patients (vérifié par construction des prompts) |
| Indisponibilité / quota / sortie non JSON | `ReponseNonJSON`/`ErreurLLM` → repli déterministe ; audit `REPLI_LLM` avec motif |

## Traçabilité

Chaque génération écrit au journal d'audit chaîné : `llm GENERATION {tache, provider,
prompt_sha, reponse_sha, tentatives, schema_ok}` — un auditeur peut ainsi prouver quel
artefact provient d'un LLM, avec quel prompt et quelle réponse, sans stocker les
contenus (confidentialité) puisque les artefacts résultants, eux, sont versionnés.

## Ce que le LLM ne fera JAMAIS (rappel, redondant par conception)

- exécuter ou choisir un calcul hors catalogue · accéder aux données avant le verrou
  G3 · déverrouiller/modifier un SAP gelé · trancher un gate humain · formuler une
  conclusion plus forte que le score CC (le gabarit l'interdit, la relecture recalcule).

## Phase suivante (2.2 ou 2.4)

Le chantier **référentiel réglementaire structuré** (annexes II–VI consolidées,
normalisation INCI, taux d'absorption SCCS) ou le chantier **méthodologie P1**
(imputation multiple, TOST/équivalence, parcours stabilité) peuvent être enchaînés
indifféremment ; les contrats stabilisés ici les absorbent sans refonte.
