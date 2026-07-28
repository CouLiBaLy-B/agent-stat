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

Provisionnement préalable : `python3 -m ui_gates.cli comptes init|add`
(annuaire PBKDF2 — jamais de secret en clair).

Recevabilité — **toute violation refuse l'écriture** (code 2, registre inchangé) :

- **authentification obligatoire** : le validateur prouve son compte
  (secret vs annuaire PBKDF2 salé, anti force brute, annuaire absent ⇒
  refus) — le rôle est **lu dans l'annuaire**, jamais auto-déclaré
  (`--role` = cohérence/désambiguation) — cf. `docs/SECURITE_GATES.md` ;
- `statut ∈ {VALIDATED, REFUSED}` (aucune autre valeur, aucun défaut) ;
- `role ∈ ROLES_GATES[gate]` (ex. G3 : biostatisticien) ;
- `motif ≥ 10 caractères` ; `pieces_consultees` non vide ;
- **liaison obligatoire** à la version signée : `artefact_ref` +
  `artefact_sha256` sont résolus automatiquement par la CLI depuis le store
  (`--etat`, requis) — impossible de signer sans désigner ce qu'on valide ;
- preuve **sig-2.0.0** : `signature_ref` horodatée + empreinte recoalculable
  liée à la version — cf. `docs/SIGNATURE_LIAISON.md` ; mode PSCE
  (`AGENT_STAT_PSCE_MODE=simulateur`) : **cachet + horodatage qualifié
  simulés** couvrant l'empreinte (certificat exigé, niveau ≥ AES_SIMULE),
  intégrité vérifiée au gate (`GATE_CACHET_INVALIDE`) — cf.
  `docs/SECURITE_GATES.md` ;
- horodatage de dépôt `depose_le` (mesure du SLA, §3.bis) ;
- écriture atomique (fusion, jamais d'écrasement des autres gates) ;
- événement `GATE_DECISION_DEPOSEE:<G>` chaîné au journal (voir §5).

Mode interactif (`--interactif`) : affiche le dossier puis demande statut et
motif — **l'entrée vide = abandon**, jamais de validation par défaut.

```bash
python3 -m ui_gates.cli sign --racine runtime/x --gate G3 \
    --etat runtime/x/checkpoints/ckpt_<run>_BLOQUE.json \
    --comptes runtime/x/comptes.json \
    --validateur u:bio-042 --decision VALIDATED \
    --motif "SAP conforme ICH E9, endpoint unique, fallback pré-spécifié" \
    --pieces sap dq_report                  # secret : --secret / $AGENT_STAT_SIGN_SECRET / saisie masquée
# avec cachet + horodatage qualifié SIMULÉS (cf. SECURITE_GATES.md) :
AGENT_STAT_PSCE_MODE=simulateur python3 -m ui_gates.cli sign ... --psce simulateur
python3 -m ui_gates.cli status --racine runtime/x --etat checkpoints/ckpt_..._BLOQUE.json
python3 -m ui_gates.cli resume --racine runtime/x --etat checkpoints/ckpt_..._BLOQUE.json \
    --donnees donnees.json            # rc 0 = TERMINE · rc 3 = nouvelle attente
```

## 3.bis Liaison version + SLA mesuré (vérifiés au gate, fail-closed)

Depuis le durcissement « liaison signature ↔ hash/version »
(`docs/SIGNATURE_LIAISON.md`), l'orchestrateur présente au gate la version
EXACTE de l'artefact (ref + sha256) et exige :

1. **décision liée** à cette version — signature obtenue sur une autre
   version/hash ⇒ blocage (`GATE_LIAISON_INVALIDE`) ;
2. **preuve intègre** — registre retouché après dépôt ⇒ blocage
   (`GATE_PREUVE_ALTEREE`, empreinte recalculée ≠ empreinte stockée) ;
3. **dépôt dans le SLA** — mesuré en temps réel depuis la première ouverture
   du gate pour cette version (`SLA_GATE_MESURE`) ; dépôt tardif ⇒ décision
   expirée ⇒ blocage (jamais de validation par défaut : on RE-SIGNE).

Les décisions **pré-déposées** (démos/tests) passent par la **pré-liaison**
(`ui_gates/liaison.py`) : rejeu déterministe en runtime jetable qui récolte
les versions puis lie chaque gabarit (preuve + horodatage inclus).

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
réel non déterministe), une nouvelle version est déposée : la liaison
signature ↔ version (§3.bis) ne colle alors plus ⇒ le gate **bloque** et
ré-exporte le dossier pour une nouvelle validation. Fail-closed, voulu.

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
python3 -m unittest discover -s tests  # 147 tests
```

La démo prouve : blocage G3 fail-closed + export auto ; refus d'une signature
de « stagiaire » ; signatures G3/G6 **liées** (ref + sha256 affichés) ;
**falsification du registre détectée** (motif retouché ⇒ blocage) ; reprises
avec refs **toutes en v1** (idempotence) ; **SLA mesuré et respecté** ;
journal 100 % intègre.

## 7. Ce qui reste humain et prochain chantier

- Décision G3/G4/G6 : **toujours humaine, motivée, habilitée, liée à la
  version** — l'outil présente les preuves, il ne suggère pas la décision.
- Chantier livré : liaison signature ↔ hash/version + SLA temps réel +
  préparation eIDAS (`docs/SIGNATURE_LIAISON.md`).
- Prochains chantiers : branchement d'un prestataire de confiance qualifié
  (AES/QES + horodatage qualifié, champs `eidas` déjà réservés) ; console de
  validation web (authentification + rôles) devant la CLI.
