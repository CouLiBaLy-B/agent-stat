# Système multi-agents d'analyse statistique et d'aide à la décision — Médical & Cosmétique

**Version :** 1.0.0 · **Statut :** proposition d'architecture · **Branche :** `arena/019fa3d0-agent-stat`

> Plateforme de **décision assistée par IA**. Elle prépare, calcule, documente et propose.
> Elle ne **décide** jamais seule : tout jalon sensible (plan d'analyse, signal de sécurité,
> conclusion finale) passe par une **validation humaine obligatoire et journalisée**.

Artefacts complémentaires dans ce dépôt :

| Fichier | Rôle |
|---|---|
| `agents/registry.yaml` | Contrat machine-lisable des 16 agents (mission, I/O, outils, succès, échec, escalade) |
| `schemas/etat.schema.json` | Schéma JSON de l'état de pipeline (mémoire partagée transactionnelle) |
| `orchestration/orchestrator.py` | Squelette d'orchestrateur (machine à états, gates, retries, blocages) |

---

# A. Vision générale

## A.1 Positionnement

Système **multi-agentique orchestré** qui transforme des données hétérogènes (protocoles,
données cliniques, essais labo, panels, stabilité, innocuité, efficacité, sensoriel,
incidents, documents réglementaires) en livrables exploitables : protocole structuré,
plan statistique (SAP), plan de tests, analyses descriptives/inférentielles, détection
d'anomalies, synthèse des risques, conclusion décisionnelle, rapport final.

Trois principes non négociables :

1. **Autonomie bornée** — chaque agent a un rôle unique, un contrat d'interface (JSON Schema),
   une liste d'outils en *allowlist*, et un périmètre d'écriture restreint. Pas de conversation
   libre entre agents de type "société d'agents" : toute communication transite par
   l'orchestrateur et des messages typés.
2. **Séparation des cerveaux** — le LLM sert à **comprendre, structurer, orchestrer, rédiger** ;
   le **calcul statistique est 100 % déterministe** (code Python/R exécuté en sandbox, seeds
   fixées, double exécution). Un LLM ne calcule jamais une p-value "de tête".
3. **Traçabilité totale** — chaque artefact est versionné (semver + SHA-256), chaque décision
   est enregistrée dans un registre, chaque action dans un journal d'audit chaîné (append-only),
   chaque hypothèse est déclarée. On peut rejouer n'importe quel run à l'identique.

## A.2 Cadre réglementaire intégré par construction

| Cadre | Ce que le système en applique |
|---|---|
| Règlement (CE) n°1223/2009 (cosmétiques UE) | Évaluation de sécurité **avant** mise sur le marché ; structure du **CPSR** (partie A : informations ; partie B : évaluation par un *safety assessor* qualifié — **humain**) ; interdiction des tests animaux → priorité aux méthodes in vitro / alternatives validées (OCDE) |
| Notes de guidance SCCS | Calcul MoS = NOAEL / SED (seuil par défaut 100), exemples d'exposition SED, données d'innocuité par ingrédient |
| ICH E9 | Principes statistiques des essais cliniques : SAP pré-spécifié, populations d'analyse, gestion de la multiplicité, intervalles de confiance |
| ICH E9(R1) | Cadre des **estimands** (5 stratégies d'événements intercurrents) + analyses de sensibilité alignées sur l'estimand |
| BPC / esprit GxP, ISO 22716 | Documentation, reproductibilité, gestion des déviations |
| RGPD (art. 9 données de santé) | Pseudonymisation **déterministe avant tout passage au LLM**, minimisation, registre des traitements, durées de rétention |
| AI Act (UE) | Système potentiellement à haut risque → *human oversight* (art. 14), transparence, journalisation, gestion des risques, fiche système |

> ⚠️ Le système **produit des preuves et des signaux**, la décision réglementaire (mise sur le
> marché, signalement, arrêt d'étude) reste à des rôles humains nommés : biostatisticien,
> toxicologue / *safety assessor*, expert réglementaire, investigateur clinicien.

## A.3 Arbitrages structurants

| Arbitrage | Choix retenu | Alternative écartée | Justification |
|---|---|---|---|
| Topologie multi-agents | **Orchestrateur central + machine à états** | Graphe d'agents conversationnels pair-à-pair | Auditabilité, reproductibilité, blocage déterministe ; le pair-à-pair est indéboguable en contexte réglementé |
| Moteur de calcul | **Bibliothèque d'opérations statistiques pré-approuvées** (MVP) → code généré sandboxé + double exécution (Phase 1) | Calcul direct par le LLM | Zéro hallucination numérique, reproductibilité, revalidation bornée à un catalogue d'ops versionné |
| Mémoire partagée | **État JSON unique versionné** (source de vérité) + référentiel d'artefacts immutables ; vector store uniquement pour la recherche documentaire | Historique de chat comme mémoire | L'état JSON est contrôlable par schéma, diffable, auditable ; un chat ne l'est pas |
| Gates humains | **Bloquants, synchrone/asynchrone avec SLA**, signature électronique | Revue a posteriori | Les erreurs en amont (SAP mal posé) corrompent tout l'aval : la revue doit précéder le calcul inférentiel |
| Référentiel réglementaire | **Règles déterministes + base structurée** (annexes II–VI, restrictions) ; RAG documentaire en appoint cité | LLM "qui connaît la réglementation" | Le droit doit être vérifiable règle par règle ; le RAG cite ses sources, le LLM n'invente pas |

---

# B. Architecture multi-agentique

## B.1 Vue en couches

```
┌─────────────────────────────────────────────────────────────────────────┐
│ COUCHE EXPÉRIENCE          UI analyste · UI gates de validation · API    │
├─────────────────────────────────────────────────────────────────────────┤
│ COUCHE REPORTING           Assembleur de rapports · gabarits · export    │
│                          (PDF/DOCX/JSON) · registre des livrables        │
├─────────────────────────────────────────────────────────────────────────┤
│ COUCHE ORCHESTRATION       Machine à états (LangGraph/Temporal) · bus    │
│                          d'événements · planificateur · politique retry  │
├──────────────┬──────────────────────────────┬───────────────────────────┤
│ AGENTS       │   AGENTS ANALYSE             │  AGENTS GARDE-FOUS        │
│ AMONT        │   DataQualité · Biais ·      │  Conformité réglementaire │
│ Compréhension│   Hypothèses · EDA ·         │  Sécurité/Tolérance ·     │
│ Protocole    │   Inférentiel · Manquants ·  │  Relecture critique ·     │
│              │   Anomalies · Rédaction      │  Validation humaine       │
├──────────────┴──────────────────────────────┴───────────────────────────┤
│ SERVICES OUTILLÉS        Moteur stats (catalogue d'ops sandboxé) ·       │
│                          Moteur de règles DQ · Référentiel réglementaire │
│                          Pseudonymisation PII · Moteur de gabarits       │
├─────────────────────────────────────────────────────────────────────────┤
│ MÉMOIRE & PREUVES        État JSON (SOT) · store d'artefacts immutables  │
│                          (S3+SHA256) · registre des décisions · journal  │
│                          d'audit chaîné · graphe de provenance (PROV)    │
└─────────────────────────────────────────────────────────────────────────┘
```

## B.2 L'équipe d'agents (16 rôles — contrat complet dans `agents/registry.yaml`)

Chaque agent : rôle unique, entrées/sorties schématisées, outils en allowlist, critères de
succès/échec mesurables, conditions d'escalade. Extrait condensé :

| # | Agent | Mission (unique) | Sorties clés | Escalade humaine si… |
|---|---|---|---|---|
| 1 | **Orchestrateur** | Router, séquencer, arbitrer, bloquer, journaliser. Ne produit aucun contenu métier | Plan d'exécution, décisions de routage, verrous de gate | Conflit d'agents non résolu, 3 retries échoués, gate qualité franchi négativement |
| 2 | **Compréhension du besoin** | Transformer la demande + documents en fiche d'intention structurée (objectif, question PICO-like, contexte réglementaire) | `intention.json` (type d'étude pressenti, endpoints pressentis, contraintes) | Ambiguïté majeure sur l'objectif, documents contradictoires |
| 3 | **Conception de protocole** | Structurer/rédiger le protocole ou le plan de test (design, critères, population, procédures) | `protocole.json/md`, matrice critères↔mesures | Protocole entrant incomplet sur un élément critique (endpoint principal absent) |
| 4 | **Biostatistique (SAP)** | Rédiger le plan d'analyse statistique : endpoints, populations, tests, multiplicité, manquants, sensibilité, estimands | `sap.json/md` versionné + hash **verrouillé avant calcul** | Design non standard sans solution cataloguée, puissance < seuil, estimand ambigu |
| 5 | **Nettoyage / Qualité des données** | Exécuter le référentiel de contrôles DQ, produire le score de fiabilité | `dq_report.json` + score DQ + liste d'anomalies actionnables | DQ < 0,50 ; endpoint principal < 90 % de complétude |
| 6 | **Gestion des biais** | Détecter ses biais (sélection, mesure, reporting, confusion, temporalité, écart protocole) | `bias_report.json` (biais, preuves, gravité, mitigation) | Biais critique non mitigeable (ex. confusion non mesurée) |
| 7 | **Contrôle des hypothèses** | Vérifier les préconditions de chaque test prévu au SAP (normalité, variance, proportional hazards…) | `assumptions.json` (test→hypothèses→verdict→fallback) | Hypothèse clé violée sans fallback pré-spécifié |
| 8 | **Analyse exploratoire (EDA)** | Décrire, visualiser, profiler ; **ne jamais toucher à l'inférentiel** | `eda_report.json/md`, dictionnaire des données | — (remonte au 5/6 si anomalie) |
| 9 | **Analyse inférentielle** | Exécuter **uniquement** le SAP validé via le catalogue d'ops ; consigner toute déviation | `results_inferential.json` (estimations, IC, p, tailles d'effet) | Déviation au SAP nécessaire → repasse par Gate G3 |
| 10 | **Gestion des manquants** | Diagnostiquer MCAR/MAR/MNAR, appliquer la stratégie du SAP, sensibilité MNAR | `missingness.json` (mécanisme, méthode, sensibilités) | > 40 % de manquants sur endpoint principal, suspicion MNAR forte |
| 11 | **Détection d'anomalies** | Outliers univariés/multivariés, fraudes statistiques (Benford, doublons partiels, patterns impossibles) | `anomalies.json` (détections + règle déclenchante) | Outlier > seuil critique sur endpoint principal sans explication |
| 12 | **Sécurité / Tolérance** | Synthèse des événements/réactions : incidence, gravité, sévérité cutanée, MoS, signaux | `safety_report.json` + liste de signaux gradués | Signal grade ≥ 2 ou MoS < 100 → **blocage + revue humaine obligatoire** |
| 13 | **Conformité réglementaire** | Appliquer le moteur de règles (annexes II–VI, restrictions, données requises, méthodes alternatives), détecter zones d'incertitude | `compliance_report.json` (règle, statut, preuve, référence) | Ingrédient restreint hors limite, dossier incomplet, toute zone d'incertitude non levée |
| 14 | **Rédaction du rapport** | Assembler faits / inférences / recommandations **typés** dans les gabarits, citer chaque artefact | `rapport_draft.{md,json}` | Incohérence détectée entre résultats et données sources |
| 15 | **Relecture critique** | Contre-analyser : recalcul indépendant des chiffres cités, chasse au sur-claim, vérification citations | `critique.json` (objections numérotées, verdict) | Toute objection bloquante (chiffre non reproduit, conclusion non supportée) |
| 16 | **Validation humaine** | Interface de gate : présenter, recueillir décision motivée + signature électronique | `validation.json` signée (qui/quand/quoi/pourquoi) | **C'est l'humain.** Expiration SLA → rappel puis blocage |

Règles anti-empiètement (mécaniques, pas déclaratives) : contrat JSON Schema par agent,
namespace d'écriture dédié dans le store d'artefacts, outils disjoints par rôle, l'agent 8
(EDA) n'a **pas accès** au moteur inférentiel, l'agent 9 n'a pas accès aux données avant gel du SAP.

## B.3 Communication inter-agents

Tous les messages passent par le bus de l'orchestrateur — jamais d'agent à agent direct :

```json
{
  "envelope": {
    "msg_id": "msg_01J…", "task_id": "t-014", "run_id": "run-2026-07-27-0007",
    "from": "agent.biostat", "to": "orchestrateur",
    "artifact_refs": ["art://sap/v3#sha256:…"],
    "confidence": 0.86,
    "assumptions": ["Population PP définie selon SAP v3 §4.2"],
    "contradictions": [],
    "needs_human": false,
    "schema_version": "1.0.0"
  },
  "payload": { }
}
```

| Mode | Quand | Mécanisme |
|---|---|---|
| **Séquentiel** | Dépendances fortes (DQ → SAP → calcul) | Transition d'état de la machine, l'input de l'étape N+1 = artefacts signés de l'étape N |
| **Parallèle (fan-out/fan-in)** | Étapes indépendantes : DQ ∥ Biais ∥ EDA ; Conformité ∥ Safety ; rapport synthèse ∥ tables | Barrière de jointure : l'orchestrateur attend tous les artefacts ou un timeout, puis agrège |
| **Arbitrage** | Désaccord entre agents (ex. Relecture vs Rédaction ; Safety vs Inférentiel sur un signal) | 1) règle déterministe si cataloguée ; 2) ré-exécution par l'agent émetteur avec objections jointes (max 2 allers-retours) ; 3) escalade humaine traçée |
| **Reprise sur erreur** | Erreur technique (timeout outil, crash sandbox) | Retry ×3, backoff exponentiel 30 s×2ⁿ, clé d'idempotence pour éviter les doubles écritures, circuit-breaker par outil. Erreur **non retryable** (schéma invalide, gate échoué) → routage remédiation immédiat, pas de retry aveugle |
| **Blocage** | Critères qualité non atteints, signal sécurité, gate humain expiré | État `BLOCKED(motif, débloqueur_requis)`, le pipeline ne reprend que sur événement de déblocage journalisé (validation humaine, artefact correctif) |

---

# C. Pipeline et logique métier

## C.1 Pipeline de bout en bout (14 étapes + 7 gates)

```
[1] Ingestion ──G0──▶ [2] Qualification type d'étude ──G1──▶ [3] Complétude
      ▶ [4] Data quality ──G2──▶ [5] Plan d'analyse / plan de test ──G3 (HUMAIN)──▶
      ▶ [6] Choix métriques ▶ [7] Calcul statistique ▶ [8] Manquants ▶ [9] Biais &
      anomalies ▶ [10] Safety/tolérance ──G4 (HUMAIN si signal)──▶ [11] Rédaction
      ▶ [12] Critique croisée ──G5──▶ [13] Validation humaine ──G6──▶ [14] Export
```

- **G0** — intégrité fichiers, PII purgées, artefacts hashés. Bloque si : virus/format illisible, PII non pseudonymisables.
- **G1** — type d'étude qualifié avec confiance ≥ 0,8 (sinon humain). Routage médical/cosmétique.
- **G2** — score DQ ≥ 0,60 (par défaut) ET complétude endpoint principal ≥ 90 %.
- **G3** — **SAP ou plan de test validé par un humain compétent AVANT tout calcul inférentiel** (verrou anti p-hacking). Le SAP est hashé ; toute modification ultérieure = déviation tracée.
- **G4** — tout signal safety grade ≥ 2 ou MoS < 100 → revue safety assessor/toxicologue.
- **G5** — toutes les objections bloquantes de la relecture levées (ou arbitrées humain).
- **G6** — signature finale du rapport par les rôles requis selon le type d'étude.

## C.2 Matrice de routage des cas d'usage

| Cas d'usage | Agents activés (au-delà du tronc commun) | Particularités du plan |
|---|---|---|
| Étude observationnelle transversale | 6 renforcé (biais sélection/mesure) | Descriptif + associations ajustées ; langage "association" obligatoire |
| Cas-témoins | 4, 6, 7, 9 | OR + IC, appariement respecté (tests appariés), biais de rappel documenté |
| Cohorte rétro/prospective | 4, 6, 7, 9, 10 | RR/HR, temps de suivi, censure, biais de temps immortel contrôlé |
| Essai randomisé | 4, 7, 9, 10, 12 | ITT prioritaire, SAP verrouillé pré-dégel, multiplicité hiérarchique |
| Sous-groupes | 4, 9 | Test d'interaction exigé, pré-spécification obligatoire, étiquette "exploratoire" sinon |
| Tolérance/sécurité médicale | 12, 15, 16 | Incidences + IC exacts, gradation, liste signaux |
| Innocuité cosmétique | 12, 13, 16 | MoS/SED par ingrédient, référentiel SCCS, méthodes alternatives d'abord |
| Test d'usage sous contrôle | 8, 9, 12 | Scores cliniques ordinaux → non paramétrique / modèles ordinaux, n et durée vs guidance |
| Tolérance cutanée | 9, 12, 13 | Échelles érythème/desquamation, % réactions grade ≥ 2 avec seuil produit |
| Stabilité | 8, 9, 13 | Tendance pH/viscosité/aspect/micro, accéléré vs temps réel, règles PASS/FAIL pré-définies |
| Compatibilité formule/packaging | 8, 13 | Perte de masse, migration, critères binaires + photos horodatées |
| Perception utilisateur | 8, 9 | Likert → médiane/IQR, top-2-box + IC, modèle cumulatif, **jamais de test t sur Likert brut sans justification** |
| Avant commercialisation | tous + G4/G6 renforcés | Assemblage type CPSR partie A ; partie B signée par safety assessor humain |
| Rapport intermédiaire | tronc + 14/15 | Verrouillage des données à date, étiquette "intermédiaire — non décisionnel" sauf DSMB |
| Rapport final d'étude | tous | Checklist complétude type CONSORT/STROBE adaptée |

## C.3 Pseudo-code d'orchestration

```python
# Voir orchestration/orchestrator.py pour le squelette complet exécutable
def run_study(dossier):
    state = State.load_or_init(dossier.study_id)            # état JSON versionné
    audit.open(run_id=state.run_id, chain_prev=state.last_audit_hash)

    execute(step=ingestion,      agent="ocomprehension", gate=G0_integrite)
    execute(step=qualification,  agent="ocomprehension", gate=G1_type_etude,
            on_low_confidence=escalade_humaine)

    parallel(                                   # fan-out
        execute(step=data_quality, agent="dataqualite", gate=G2_dq_score),
        execute(step=biais,        agent="biais"),
        execute(step=eda,          agent="eda"),
    )                                           # fan-in : attente de tous

    execute(step=sap, agent="biostat")
    gate_humain(G3, artefact=state.sap, role="biostatisticien",
                sla="72h", blocant=True)        # verrou SAP avant tout inférentiel
    state.sap.lock(sha256(state.sap))           # toute modif ultérieure = déviation

    execute(step=calcul,    agent="inferentiel",
            preconditions=[hypotheses_ok, sap_locked],
            tools=["stats.catalogue"])          # sandbox, seed fixée, double exécution
    execute(step=manquants, agent="manquants", selon=state.sap.missing_strategy)

    parallel(
        execute(step=anomalies,  agent="anomalies"),
        execute(step=safety,     agent="securite",
                post_gate=lambda r: gate_humain(G4) if r.max_grade >= 2
                                    or r.mos_min < 100 else None),
        execute(step=conformite, agent="conformite", moteur="regles.reglement"),
    )

    draft  = execute(step=rapport,  agent="redaction")
    review = execute(step=critique, agent="relecture", entree=draft)
    while review.objections_bloquantes and round < 2:      # arbitrage borné
        draft, review = reprendre(redaction, objections=review), relire(draft)
        round += 1
    if review.objections_bloquantes: escalade_humaine("conflit_redaction_relecture")

    gate_humain(G6, artefact=draft, roles=roles_requis(state.type_etude))
    export(state, formats=state.outputs_demandes)          # versionné + signé
```

**Règles de reprise** : retry ×3 + backoff exponentiel sur erreur technique ; clé
d'idempotence par (run_id, step) pour interdire les doubles écritures ; aucune reprise
aveugle sur erreur logique (schéma, gate) → remédiation humaine ; reprise "propre" possible
depuis n'importe quelle étape grâce à l'état versionné (checkpoint par étape).

**Règles de blocage (dur)** : DQ < seuil · complétude endpoint < 90 % · G3/G4/G6 non validés ·
contradiction d'agents critique non arbitrée · artefact obligatoire absent (ex. consentement,
make-list ingrédients) · déviation SAP non approuvée · suspicion de manipulation de données.

**Règles de versioning** : chaque artefact = `art://{type}/{study_id}/{name}/v{n}` +
SHA-256, **immuable** (toute modification = nouvelle version + lien `supersedes`) ; pin des
versions : catalogue d'ops stats, prompts, modèle LLM, données (snapshot DVC), seed ; graphe de
provenance W3C PROV (`used`, `wasGeneratedBy`, `wasDerivedFrom`) permettant le rejeu à l'identique.

## C.4 Structure d'état (extrait — schéma complet : `schemas/etat.schema.json`)

```json
{
  "run_id": "run-2026-07-27-0007",
  "study_id": "COS-2026-014",
  "type_etude": "test_usage_controle",
  "domaine": "cosmetique",
  "phase_pipeline": "ANALYSE_INFERENTIELLE",
  "artefacts": [{"ref": "art://sap/COS-2026-014/sap/v2", "sha256": "…", "status": "VALIDATED"}],
  "scores": {"fiabilite_donnees": 0.83, "robustesse_analyse": null, "confiance_conclusion": null},
  "gates": [{"id": "G3", "statut": "VALIDATED", "validateur": "u:bio-042", "date": "…", "motif": "…"}],
  "signaux_securite": [],
  "decisions": [], "verrous": {"sap_sha256": "…"}, "seed": 20260727
}
```

## C.5 Choix méthodologiques (quand / pas quand / garde-fous)

| Méthode | Quand | Quand **ne pas** | Garde-fous système |
|---|---|---|---|
| Descriptif (n, %, moy/ET, méd/IQR) | Toujours, avant tout | — | Cohérence unités ; EDA sans inférence |
| IC 95 % | Tout effet rapporté | — | Les p-values seules sont **refusées** dans un rapport (IC + taille d'effet obligatoires) |
| t de Student / Welch | Comparaison de moyennes, n modéré, résidus ≈ normaux | Données ordinales, queues lourdes marquées | Vérif hypothèses (agent 7) → fallback Welch puis Mann-Whitney |
| Mann-Whitney / Wilcoxon | Ordinales, non-normales, petits n | Interprétation "différence de médianes" si distributions de formes différentes | Formuler comme P(X>Y) (stochastic superiority) |
| χ² / Fisher exact | Tableaux de contingence ; Fisher si effectifs < 5 | Données appariées (→ McNemar) | Fallback automatique Fisher si Cochran violée |
| ANOVA / Kruskal | ≥ 3 groupes | Post-hoc sans correction | Post-hoc Tukey/Dunn corrigé |
| Modèles mixtes / GEE | Mesures répétées, données corrélées | Une seule mesure par sujet | Structure de covariance pré-spécifiée ; convergence contrôlée |
| Survie (KM, log-rank, Cox) | Délai jusqu'événement avec censure | Pas de censure → préférer modèle binaire si horizon fixe | Hypothèse des risques proportionnels testée (Schoenfeld) |
| **TOST équivalence** | Démontrer l'absence de différence *cliniquement pertinente* (marge Δ pré-définie) | En l'absence de marge justifiée, ou pour "sauver" un p > 0,05 | La marge Δ est au SAP, validée humain ; p non significatif ≠ équivalence — le système **interdit** cette tournure |
| Non-infériorité | Comparaison à référence où la supériorité n'est pas attendue | Marge non justifiée historiquement | Marge conservatrice documentée ; analyse ITT + PP concordantes |
| Multiplicité | Primaire hiérarchisé (gatekeeping), secondaires clés (Holm), exploratoire (BH-FDR) | BH sur les endpoints primaires d'un essai confirmatoire | Hiérarchie fixée au SAP ; résultats exploratoires **étiquetés** et plafond de confiance |
| Robustesse outliers | Analyse primaire pré-spécifiée **+** sensibilité avec/sans | Suppression silencieuse d'outliers | Aucune exclusion sans règle pré-définie ; tout outil "d'exclusion" journalisé |
| Imputation multiple (MCMC/PMM) | MAR plausible, > 5 % manquants | MNAR fort probable sans sensibilité ; variable quasi vide | Diagnostic mécanisme (agent 10), pooling de Rubin, m ≥ fraction manquante×100 (règle pratique) |
| Sensibilité MNAR | Endpoint à manquants substantiels, doute sur MAR | — | Δ-adjustment, imputation référence (J2R/CIR), tipping-point présenté **en tableau** |
| Estimands (ICH E9(R1)) | Tout essai avec événements intercurrents | — | Attributs de l'estimand au SAP ; stratégie (treatment-policy, hypothetical, composite, while-on-treatment, principal stratum) choisie **avant** les résultats |
| Sous-groupes | Pré-spécifiés, test d'interaction significatif | Conclure sur un sous-groupe post-hoc non puissant | Interaction exigée ; étiquette exploratoire ; plafond confiance 0,5 |
| Sur-ajustement | Modèles multivariés | < 10–20 EPV, variables sélectionnées sur les données | Règle EPV bloquante ; sélection = pénalisation pré-spécifiée ; validation interne bootstrap |
| Pré-enregistrement | Toute étude confirmatoire | — | G3 verrouille SAP (hash) avant gel des données ; journal de toute déviation |
| Reproductibilité | Tout run | — | Seed fixée, environnement épinglé, **double exécution** dont les hashes doivent coïncider, sinon blocage |

## C.6 Data quality — contrôles et scores

Référentiel de contrôles déterministes (moteur type Great Expectations/Pandera) :

- **Complétude** par variable et par strate, avec criticité (endpoint principal = critique) ;
- **Cohérence** : domaines de valeurs, unités, référentiels, règles croisées (âge/date naissance) ;
- **Doublons** : exacts, partiels (fuzzy sur clés), sujets dupliqués inter-fichiers ;
- **Valeurs aberrantes** : IQR×3, |z| > 4, robust z (MAD), isolation forest (multivarié) — *détection, jamais suppression* ;
- **Cohérence temporelle** : visite V2 < V1, dates postérieures à l'extraction, écarts de fenêtres protocolaires ;
- **Variables incompatibles** : corrélations impossibles, codages contradictoires (sexe/ grossesse) ;
- **Biais de sélection / mesure / reporting** : tableau comparatif inclus vs exclus, SMD > 0,1 sur baselines, taux de manquants différentiel ≥ 5 pts entre bras ;
- **Écart protocole/données** : variables attendues absentes, visites hors fenêtre, critères d'inclusion violés dans les données.

**Scores (0–1, formules versionnées au registre des décisions) :**

```
DQ  = 0,25·Complétude_w + 0,20·Cohérence + 0,10·(1−Doublons) + 0,15·(1−Outliers_crit)
      + 0,10·Temporalité + 0,20·ÉcartProtocole          (pondérations par criticité)
  plafonds : DQ ≤ 0,50 si complétude endpoint principal < 90 %
             DQ ≤ 0,60 si doublons sujets > 2 % non résolus

RA  = 0,30·Hypothèses_validées + 0,25·Concordance_sensibilité + 0,20·Diagnostics_modèle
      + 0,15·Multiplicité_maîtrisée + 0,10·Validation_interne
  plafond : RA ≤ 0,50 si un test primaire a perdu son fallback d'hypothèse

CC  = min(DQ, RA) × facteur(puissance_atteinte, pré_enregistrement, convergence_signaux)
  plafonds : CC ≤ 0,40 si DQ < 0,60 · CC ≤ 0,50 si analyse post-hoc
             CC ≤ 0,60 si endpoint non pré-spécifié
  verbalisation : ≥0,80 élevée · 0,60–0,79 modérée · 0,40–0,59 faible · <0,40 insuffisante ⇒ ne pas conclure
```

## C.7 Volet cosmétique — logique spécifique

Distinctions internes strictes (l'agent Conformité vérifie qu'aucun rapport ne les confond) :
**innocuité** (sécurité systémique : MoS = NOAEL/SED ≥ 100 par ingrédient, réf. SCCS) ·
**tolérance** (réactions locales : scores cliniques, % sujets grade ≥ 2) · **efficacité
perçue** (auto-évaluation, claims) · **stabilité** (physico-chimie + micro dans le temps) ·
**conformité** (réglementaire documentaire).

Règles de détection (moteur déterministe, référentiel versionné) :

| Détection | Règle exemple (paramétrable, versionnée) | Conséquence système |
|---|---|---|
| Substance interdite/restreinte | INCI ∈ annexe II → BLOQUANT ; ∈ annexes III–VI et concentration > limite → BLOQUANT | Blocage + préconisation reformulation, escalade expert réglementaire |
| MoS insuffisante | MoS < 100 sur un ingrédient sans justification SCCS | Signal grade 3 → G4 humain obligatoire |
| Signal de tolérance | % sujets réaction grade ≥ 2 > seuil produit (défaut 5 %) désirré de l'attrition | Signal grade 2–3, recommandation test complémentaire (HRIPT élargi) |
| Données insuffisantes | n < 30 test d'usage, durée < claim, pas de contrôle stabilité micro | Verdict "insuffisant — compléter", **interdiction de conclure favorable** |
| Méthode animale | Détection d'essai in vivo animal dans les pièces | Non-conformité majeure UE, réorientation méthodes alternatives validées (OCDE) |
| Zone d'incertitude | NOAEL manquant → SED estimé sur analogues ; extrapolation population (enfants) | Étiquette "incertitude" + revue toxicologue requise |

Résultats défavorables : jamais maquillés — remontés comme signaux avec matrice de risque
(gravité × probabilité × détectabilité), hiérarchisation, et obligation de proposer soit des
tests complémentaires, soit un avis défavorable motivé. Le rapport final liste **les données
manquantes requises** avant toute mise sur le marché.

## C.8 Volet médical — logique spécifique

- **Critère principal unique** (ou hiérarchie gatekeeping), secondaires et exploratoires
  distingués dans le SAP ; population d'analyse déclarée par endpoint (ITT / mITT / PP / Safety) ;
- **Exclusions** : uniquement sur règles pré-spécifiées, tracées une à une (qui, règle, impact) ;
- **Sensibilité** : au moins une analyse par fragilité identifiée (manquants, outliers, hypothèse) ;
- **Association ≠ causalité** : lexique contrôlé par le moteur de gabarits — en observationnel,
  les tournures causales ("réduit", "prévient", "guérit") sont **remplacées automatiquement** par
  "est associé à", sauf validation explicite d'un cadre causal (DAG validé humain, hypothèses
  d'identification listées, chevauchement/positivité contrôlés) ;
- **Anti p-hacking / cherry-picking** : SAP hashé pré-calcul (G3), journal de **toutes** les
  analyses exécutées (y compris non rapportées), multiplicité, interdiction de réordonner les
  endpoints après coup, tout résultat rapporté doit citer une ligne du SAP ou porter l'étiquette
  "post-hoc exploratoire" ;
- **Interprétation prudente** : la Relecture vérifie chaque conclusion contre les chiffres et CC ;
  absence de significativité ≠ absence d'effet ; CC < 0,40 ⇒ le gabarit n'autorise pas de
  conclusion directionnelle.

## C.9 Formats de sortie (gabarits versionnés)

Résumé exécutif (1 p., décision proposée + scores DQ/RA/CC) · **SAP** · **plan de tests
cosmétiques** (design, n, durée, critères d'arrêt, acceptabilité) · **rapport de résultats**
(méthodes → faits → inférences → recommandations, sections mécaniquement séparées) · **liste des
risques** (matrice gravité × probabilité × détectabilité) · **actions recommandées** (priorisées,
responsable suggéré) · **limites et hypothèses** (obligatoire, non vide) · **validation humaine
requise** (jalons restants, rôles, échéances). Tout chiffre du rapport porte une référence
d'artefact + hash ; tout fait/inférence/recommandation est typé.

---

# D. Sécurité, conformité et validation

## D.1 Règles dures du système

1. **Aucune conclusion non supportée** : chaque affirmation quantitative cite artefact + hash ;
   la Relecture **recalcule** chaque chiffre cité ; échec ⇒ objection bloquante.
2. **Données insuffisantes = verdict explicite** ("insuffisant"), jamais comblé par un raisonnement.
3. **Risque élevé ⇒ blocage sans validation humaine** : signaux safety grade ≥ 2, MoS < 100,
   non-conformité réglementaire bloquante, DQ < 0,50.
4. **Journal d'audit append-only chaîné** (SHA-256 de l'entrée précédente, stockage WORM) :
   qui/quoi/quand/pourquoi, entrées, sorties, versions, prompts, seeds, décisions humaines.
5. **Registre des décisions** : chaque arbitrage (routage, arbitrage inter-agents, déviation SAP,
   gate) en ADR horodaté ; rejouable.
6. **Faits / inférences / recommandations** : trois canaux typés dans tous les artefacts et rapports.
7. **Hypothèses et limites** : section obligatoire contrôlée non vide par la Relecture.

## D.2 Gates humains obligatoires

| Gate | Objet | Validateur | Comportement au refus / expiration |
|---|---|---|---|
| G1 | Type d'étude ambigu (conf < 0,8) | Méthodologiste | Correction → requalification |
| G3 | SAP / plan de test **avant calcul** | Biostatisticien (+ toxicologue si cosmétique) | Renvoi motivé → nouvelle version ; SLA 72 h puis blocage |
| G4 | Tout signal sécurité / MoS / conformité risquée | Safety assessor / toxicologue / expert réglementaire | Blocage dur tant que non tranché |
| G6 | Rapport final | Rôles requis selon study type | Export impossible |

Signature électronique à chaque gate (identité, rôle, horodatage, motif), esprit 21 CFR Part 11 /
Annex 11 ; validation du système lui-même en démarche type GAMP 5 (IQ/OQ/PQ, traçabilité
exigences ↔ tests), revalidation ciblée à chaque changement de catalogue d'ops ou de gabarit.

## D.3 Sécurité des données et des modèles

- **PII/santé** : pseudonymisation **déterministe** avant tout appel LLM ; vault de correspondance
  séparé ; minimisation des champs ; RGPD art. 9, registre des traitements, DPIA.
- **Exécution de code** : sandbox éphémère sans réseau, CPU/RAM/temps bornés, image épinglée ;
  sortie = artefacts schématisés uniquement.
- **Défense prompt-injection** : les contenus de documents ingérés sont traités comme **données**
  (encapsulage, balisage, jamais comme instructions système) ; outils en allowlist par agent ;
  détecteur d'instructions hostiles à l'ingestion ; whitelisting des chemins d'export.
- **Accès** : RBAC par rôle humain et par agent, secrets hors repo, chiffrement au repos/en transit.

---

# E. MVP recommandé

## E.1 Périmètre (10–12 semaines, 1 équipe plateforme + 1 biostat + 1 reg)

**2 parcours seulement** : (a) étude observationnelle médicale simple (descriptif + comparaison
bivariée + modèle ajusté) ; (b) test d'usage / tolérance cosmétique (scores ordinaux + safety +
checklist conformité). **8 agents** : Orchestrateur, Compréhension, DataQualité, Biostatistique(SAP),
Rédaction, Relecture, Conformité (règles), Validation humaine. Les agents 6, 10, 11 existent en
version réduite intégrée au 5 et au 4.

**Choix MVP** : machine à états LangGraph · store d'artefacts S3-compatible + index Postgres ·
état JSON + JSON Schema · audit JSONL chaîné · moteur stats = **catalogue d'opérations
pré-approuvées** (scipy/statsmodels, ~20 ops : pas de code généré en MVP) · UI gates Streamlit ·
rapports Markdown → PDF via gabarits.

**Explicitement hors MVP** : génération de code libre sandboxée, inférence causale avancée,
équivalence/non-infériorité, stabilité multi-temps modélisée, RAG réglementaire complet,
intégrations LIMS/eCRF/CTMS, multi-études.

## E.2 Roadmap

| Phase | Contenu | Critère de sortie |
|---|---|---|
| **MVP** | 2 parcours, 8 agents, gates G2/G3/G4/G6, scores DQ/RA/CC, audit chaîné | 20 études historiques rejouées : qualification correcte ≥ 95 %, 100 % des chiffres reproduits par double exécution, 0 conclusion non supportée en revue humaine |
| **P1** | 16 agents complets, code généré sandboxé + double exécution, stabilité & compatibilité packaging, équivalence/TOST, référentiel réglementaire structuré (annexes II–VI) | Injection de fautes contrôlées détectées ≥ 98 % ; temps de cycle −50 % vs manuel |
| **P2** | DAG causaux + propension (étiquetés), multi-études, intégrations LIMS/eCRF, surveillance de dérive des agents, qualification GAMP complète | Dossier de validation auditable ; revue blanche externe |

## E.3 Ce qui reste humain — matin même du MVP

Validation du SAP et des estimands · qualification d'un signal de sécurité et avis toxicologique
(MoS, CPSR partie B — *safety assessor* qualifié) · décision go/no-go et signalements · validation
de tout cadre causal · arbitrage des conflits d'agents · acceptation de toute déviation méthodologique.
L'IA prépare, mesure, documente et propose ; **l'humain tranche, signe et engage sa responsabilité**.

## E.4 Métriques de pilotage

Taux de reprise humaine par gate (cible < 15 % après montée en charge) · précision de qualification ·
% runs bit-identiques (cible 100 %) · objections bloquantes relecture / rapport · détection de fautes
injectées · temps de cycle bout-en-bout · taux d'études bloquées à juste titre vs à tort (calibration
des seuils DQ/gates — révisée trimestriellement via le registre des décisions).
