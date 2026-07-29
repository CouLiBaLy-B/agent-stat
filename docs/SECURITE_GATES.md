# Sécurité des gates : authentification des validateurs + cachets eIDAS

**Version :** 1.0.0 · modules `ui_gates/auth.py`, `ui_gates/eidas.py`,
`core/eidas.py`, `core/signature.py`, `core/gates.py`, `ui_gates/signature.py`,
`ui_gates/cli.py` — 100 % stdlib.

Deux menaces traitées, toujours en **fail-closed** :

1. **usurpation** : historiquement, `sign` acceptait `--validateur`/`--role`
   *auto-déclarés* — quiconque pouvait signer au nom d'un rôle habilité ;
2. **preuve non scellée** : la preuve `sig-2.0.0` garantit l'intégrité du
   registre mais n'avait ni cachet de prestataire de confiance ni
   horodatage qualifié (champs eIDAS réservés à `None`).

---

## 1. Annuaire d'authentification (`ui_gates/auth.py`)

- **PBKDF2-HMAC-SHA256** salé par compte (60 000 itérations par défaut,
  réduites en tests) — le secret n'est **jamais** stocké en clair, jamais
  journalisé, jamais en fichier de configuration ;
- comparaison à **temps constant** (`hmac.compare_digest`) ;
- **anti force brute** : 5 échecs consécutifs ⇒ verrouillage **15 minutes**,
  persisté *dans l'annuaire* (supprimer un fichier temporaire ne déverrouille
  rien) ; pendant le verrou, **même le bon secret est refusé** ; REMISE à zéro
  au premier succès ;
- `initialiser` **refuse d'écraser** un annuaire existant — l'effacement des
  comptes est un incident, pas une option CLI ;
- annuaire **absent** (ou illisible, ou format inconnu) ⇒ la console
  **refuse de signer** : aucune authentification par défaut dégradée ;
- écriture **atomique** (tmp + remplacement) ;
- compte **inactive** ⇒ refus ; ajout de doublon ⇒ refus.

Provisionnement : `python3 -m ui_gates.cli comptes init|add` (secret via
`--secret`, `$AGENT_STAT_SIGN_SECRET` ou saisie masquée).

Ce module ne remplace pas un IdP d'entreprise (OIDC/SAML) : c'est la référence
traçable MVP — le branchement production garde le même contrat
(`authentifier(chemin, ident, secret) -> compte`).

### Rôle lu dans l'annuaire (jamais auto-déclaré)

Le `--role` historique devient un **contrôle de cohérence** :

- si fourni, il doit figurer dans les rôles du compte (sinon refus) et être
  habilité au gate visé ;
- si omis : un seul rôle du compte habilité au gate ⇒ retenu ; plusieurs ⇒
  `--role` exigé (désambiguation auditée) ; aucun ⇒ refus.

## 2. Cachets eIDAS SIMULÉS (`core/eidas.py` + `ui_gates/eidas.py`)

> ⚠ **Ce n'est pas de la cryptographie.** Les cachets sont des empreintes
> SHA-256 composites **recalculables publiquement** : ils prouvent la
> structure de la preuve, l'intégrité des champs et le câblage complet
> (console → PSCE → preuve → vérification au gate), mais n'opposent aucune
> barrière à un attaquant. La cible production est un **PSCQ réel**
> (certificats qualifiés, RSA/ECDSA, **RFC 3161** pour l'horodatage) branché
> derrière **le même contrat** sans changer le schéma `sig-2.0.0`.

Contrat `ServicePSCE` (comme `llm/`) :
`certificat_du(validateur)` / `fabrique_eidas(validateur, empreinte, gate)`.
Implémentation `PSCESimulation` : registre de certificats (fiches
**publiques** — sujet, émetteur, série, période de validité, niveau,
qualifié ; jamais de clé), compteur de jetons TSA monotone.

Invariants :

- le **cachet** couvre **exactement** l'empreinte de liaison `sig-2.0.0`
  (gate·validateur·statut·motif·pièces·artefact·horodatage) + la fiche
  complète du certificat + l'instant d'émission ;
- le **jeton TSA** couvre l'empreinte + `gen_time` + `serial` ;
- toute altération d'un champ couvert ⇒ vérification fausse ⇒
  **`GATE_CACHET_INVALIDE:<G>`** au gate (fail-closed) ;
- hiérarchie de niveaux `SES < AES_SIMULE < QES_SIMULE` ; VALIDATION aux
  gates G1–G6 : **AES_SIMULE minimum** requis — un certificat SES ou absent
  ⇒ refus du dépôt (`PSCEIndisponible`), **aucune** dégradation silencieuse
  en SES.

Branchement : `AGENT_STAT_PSCE_MODE ∈ {off, simulateur, real}` (défaut `off` —
rétrocompatibilité des décisions historiques : l'absence de cachet reste
recevable, et c'est la console qui décide d'en exiger ; tout cachet/présent
est vérifié quel que soit le mode).

- `real` : stub qui lève `PSCEIndisponible` avec message explicite. Le vrai
  PSCQ (certificats qualifiés eIDAS, RSA/ECDSA, **TSA RFC 3161** pour
  l'horodatage qualifié, IdP OIDC/SAML derrière `authentifier`) **doit
  implémenter exactement** le contrat `ServicePSCE` (même que le simulateur).
  Le mode "real" force le branchement production sans dégradation silencieuse.

## 3. Chaîne complète (prouvée par tests + `demo/run_securite_gates.py`)

```
comptes init/add  →  sign : authentifier (secret) ─ verrou si 5 échecs
                   → rôle lu dans l'annuaire
                   → liaison version (ref + sha256)   [inchangé]
                   → preuve sig-2.0.0                  [inchangé]
                   → PSCE : cachet + jeton couvrant l'empreinte
reprise           → verifier_preuve (liaison)  →  GATE_PREUVE_ALTEREE
                   → verifier_cachets_eidas    →  GATE_CACHET_INVALIDE
                   → SLA mesuré                 →  décision expirée
```

`status` affiche le niveau du cachet et l'horodatage qualifié simulé ;
l'audit journalise `eidas_niveau` / `cachet_eidas` à chaque dépôt.

## 4. Limites assumées (et chantier production)

- Les cachets simulés sont **recalculables** : ils détectent la retouche
  « naïve » d'un registre, pas un attaquant qui recalculerait tout — c'est
  exactement le rôle du **PSCQ réel** (clé privée sous contrôle exclusif du
  signataire, certificat qualifié eIDAS) et de la **TSA RFC 3161**.
- L'annuaire fichier convient au MVP mono-poste ; la cible production est un
  IdP (OIDC/SAML) + comptes provisionnés hors bande.
- Le verrou temporel s'appuie sur l'horloge système (acceptable MVP : la
  falsification exige déjà l'accès admin, qui contrôle aussi l'annuaire).

---

*Références : Règlement (UE) n° 910/2014 (eIDAS) — niveaux SES/AES/QES,
prestataires de services de confiance qualifiés ; RFC 3161 (Time-Stamp
Protocol) ; PBKDF2 (RFC 8018).*
