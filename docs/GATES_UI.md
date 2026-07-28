# UI des gates de validation — dossiers de preuves, signature, reprise

Chantier « interface humaine des gates » de la plateforme agent-stat.
Complète `docs/ARCHITECTURE.md` §C.2 (gates G1→G6). Principes inchangés et
renforcés : **jamais de validation par défaut**, rôle habilité exigé, décision
motivée et chaînée au journal d'audit, reprise **strictement reproductible**.

---

## 1. Cycle d'un point d'attente

```
pipeline ──gate ouvert (journal)──> aucune décision ?
        │  export automatique du dossier de preuves (md + json)
        ▼  PipelineBloque GATE_HUMAIN_NON_VALIDE + checkpoint BLOQUE persistant
humain ──lit le dossier (scores, substance, hash, preuves, consignes)
        │  python3 -m ui_gates.cli sign ...   (recevabilité fail-closed)
        ▼  décision fusionnée dans decisions.json + événement audit chaîné
python3 -m ui_gates.cli resume ...   (rejeu déterministe complet)
        ▼
   gate suivant … jusqu'à TERMINE
```

## 2. Dossier de preuves (`ui_gates/dossier.py`)

Généré **automatiquement** à l'ouverture d'un gate sans décision (et à la
demande via `export`) dans `<runtime>/exports/gates/dossier_<G>.{md,json}` :

- artefact à valider : `art://…` + **SHA-256 à revérifier**, statut, producteur,
  provenance (`prov_used`) ;
- scores **DQ / RA / CC** + verbalisation de confiance ;
- substance métier selon le gate :
  - G1 : qualification du type d'étude (confiance, contradictions) ;
  - G3 : résumé SAP (endpoint unique, analyses, multiplicité, manquants,
    garde-fous observationnel) + résumé DQ ;
  - G4 : signaux safety (nature, grade, mesures) ou verdict/règles de conformité ;
  - G5 : objections bloquantes de la critique croisée ;
  - G6 : décision proposée, conclusion directionnelle, conformité, signaux, limites ;
- preuves traçables `art://…#sha256:…`, pièces minimales à consulter ;
- consignes fail-closed + **commande de signature exacte**.

Rien dans le dossier ne décide : c'est une aide à la décision d'un humain
habilité. L'échec de l'export est journalisé et **ne masque jamais l'attente**.

## 3. Signature (`ui_gates/signature.py` + `cli.py`)

Recevabilité — **toute violation refuse l'écriture** (code 2, registre inchangé) :

- `statut ∈ {VALIDATED, REFUSED}` (aucune autre valeur, aucun défaut) ;
- `role ∈ ROLES_GATES[gate]` (ex. G3 : biostatisticien) ;
- `motif ≥ 10 caractères` ; `pieces_consultees` non vide ;
- `signature_ref` horodatée et antisèche de contenu (`sig:<ts>:u:<id>:g<n>:<hash8>`) ;
- écriture atomique (fusion, jamais d'écrasement des autres gates) ;
- événement `GATE_DECISION_DEPOSEE:<G>` chaîné au journal (voir §5).

Mode interactif (`--interactif`) : affiche le dossier puis demande statut et
motif — **l'entrée vide = abandon**, jamais de validation par défaut.

```bash
python3 -m ui_gates.cli sign --racine runtime/x --gate G3 \
    --validateur u:bio-042 --role biostatisticien --decision VALIDATED \
    --motif "SAP conforme ICH E9, endpoint unique, fallback pré-spécifié" \
    --pieces sap dq_report
python3 -m ui_gates.cli status --racine runtime/x --etat checkpoints/ckpt_..._BLOQUE.json
python3 -m ui_gates.cli resume --racine runtime/x --etat checkpoints/ckpt_..._BLOQUE.json \
    --donnees donnees.json            # rc 0 = TERMINE · rc 3 = nouvelle attente
```

## 4. Reprise (`orchestration/reprise.py`)

Sémantique : **rejeu déterministe complet** depuis l'ingestion avec mêmes
`run_id` et données d'entrée (contrat explicite) :

- store **idempotent par contenu** (`core/store.py`) : octets identiques ⇒
  artefact existant renvoyé — aucune version dupliquée après reprise ;
- journal d'audit **rescellé** à l'ouverture + événement `REPRISE_PIPELINE` ;
- checkpoint `BLOQUE` persistant écrit par l'orchestrateur (consommé par
  `status`/`resume`) ;
- `--llm off` (défaut CLI) = rejeu déterministe pur ; `"env"` reprend dans le
  mode du run initial (bit-à-bit reproductible en déterministe ou llm-simule,
  les providers simulés étant scriptés).

⚠ Si le rejeu produit un contenu **différent** de celui signé (ex. LLM HTTP
réel non déterministe), une nouvelle version est déposée : la décision du
provider-fichier MVP n'étant pas liée à une version d'artefact, la liaison
**signature ↔ version** est un durcissement prévu (chantier futur) — en
production, la signature électronique portera le hash revérifié.

Fix de reproductibilité livré avec ce chantier : les durées d'exécution
wall-clock sont désormais **hors contenu hashé** (`durees_ms` = observabilité
du contrôleur) — deux runs indépendants produisent le **même SHA-256** de
résultats (test dédié).

## 5. Intégrité du journal en présence de plusieurs écrivains

La chaîne SHA-256 (`prev_hash`) impose **une seule instance active** par
fichier : toute écriture depuis une instance rescellée obsolète est détectée
par `verifier()`. Règle d'usage (respectée par la CLI et les reprises) :
ré-instancier `JournalAudit` avant toute écriture suivant des ajouts externes.

## 6. Vérifications

```bash
python3 demo/run_gates_ui.py           # cycle attente→signature×2→reprise×2
python3 -m unittest discover -s tests  # 132 tests (dont 14 du chantier)
```

La démo prouve : blocage G3 fail-closed + export auto ; refus d'une signature
de « stagiaire » ; signatures G3/G6 chaînées ; reprises avec refs **toutes en
v1** (idempotence) ; journal 100 % intègre.

## 7. Ce qui reste humain et prochain chantier

- Décision G3/G4/G6 : **toujours humaine, motivée, habilitée** — l'outil
  présente les preuves, il ne suggère pas la décision.
- Prochain chantier logique : liaison signature ↔ hash/version d'artefact
  (durcissement du provider, préparation à une signature eIDAS) et SLA par
  gate mesurés en temps réel.
