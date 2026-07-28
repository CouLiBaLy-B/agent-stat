# Liaison signature ↔ hash/version d'artefact, SLA temps réel, préparation eIDAS

> Chantier de durcissement des gates humains. Problème traité : le provider
> fichier MVP ne liait pas une signature humaine à la **version** d'artefact
> validée — une validation obtenue sur un contenu pouvait être réutilisée sur
> un autre (rejeu divergent, SAP régénéré, registre retouché). Désormais,
> **une décision humaine n'existe que liée à la version exacte qu'elle
> valide**, avec une preuve d'intégrité recalculable et une fenêtre de
> validité mesurée en temps réel. Tout écart **bloque** (fail-closed).

## 1. Propriétés garanties

| # | Propriété | Mécanisme | Événement audit |
|---|-----------|-----------|-----------------|
| 1 | La décision désigne la version présentée | `artefact_ref` + `artefact_sha256` exigés et comparés à l'artefact que l'orchestrateur présente au gate | `GATE_LIAISON_INVALIDE:<G>` si mismatch |
| 2 | Le registre ne peut pas être retouché après dépôt | preuve `sig-2.0.0` : empreinte sha256 sur (gate, validateur, statut, motif, pièces triées, ref, sha256, horodatage), recalculée au gate | `GATE_PREUVE_ALTEREE:<G>` si recalcul ≠ empreinte |
| 3 | Une validation est bornée dans le temps | SLA mesuré = `depose_le` − première ouverture du gate pour CETTE version (relue dans le journal, tous runs confondus) ; dépassement ⇒ décision expirée | `SLA_GATE_MESURE:<G>` (attente_h, dans_les_delais) |
| 4 | Jamais de signature aveugle | la CLI `sign` résout la version courante depuis le store (via `--etat`) et l'inscrit dans la décision ; le dépôt refuse toute décision sans liaison | `GATE_DECISION_DEPOSEE:<G>` (avec ref + empreinte) |
| 5 | Décisions pré-déposées possibles sans rupture de chaîne de preuve | pré-liaison par rejeu déterministe (runtime jetable) : les hash sont récoltés, pas inventés | `origine_liaison` inscrite dans chaque décision |

Rien de tout cela ne change le principe cardinal : **l'absence de décision
bloque**, aucune validation par défaut à l'expiration d'un SLA (ici, c'est
la décision *tardive* qui est expirée — on ne valide jamais « à sa place »).

## 2. Format de preuve `sig-2.0.0` (`core/signature.py`)

```json
{
  "format": "sig-2.0.0",
  "niveau": "SES_REFERENCE_TRACABLE",
  "algorithme_empreinte": "sha256",
  "champs_lies": ["gate_id", "validateur_id", "statut", "motif", "pieces",
                  "artefact_ref", "artefact_sha256", "horodatage_utc"],
  "horodatage_utc": "2026-07-28T10:00:00+00:00",
  "empreinte": "<sha256 hex>",
  "artefact_lie": {"ref": "art://sap/STU/sap/v1", "sha256": "<…>"},
  "eidas": {
    "niveau_actuel": "SES (référence traçable chaînée à l'audit)",
    "niveaux_cibles_production": ["AES", "QES"],
    "certificat": null,
    "horodatage_qualifie": null,
    "prestataire_confiance": null
  }
}
```

- **Empreinte** = sha256 du JSON canonique des champs liés. Toute retouche
  (motif, statut, pièces ajoutées, ref pointée ailleurs, horodatage modifié,
  validateur usurpé) est détectée au gate — voir
  `tests/test_signature_liaison.py::test_retouche_detectee`.
- **Préparation eIDAS** (Règlement (UE) n° 910/2014) : le bloc `eidas` fige
  le schéma cible — AES/QES via prestataire de confiance qualifié, horodatage
  qualifié. En MVP, le niveau est *SES référence traçable* : l'intégrité
  repose sur l'empreinte + le journal d'audit chaîné (WORM). Le branchement
  d'un PSCE se fera en remplissant les champs réservés, **sans changement de
  schéma** ni modification de la logique de vérification.

## 3. Vérification au gate (`core/gates.py`)

`GestionnaireGates.attendre_decision(gate_id, artefact_ref, sla_h,
artefact_sha256=…)` — l'orchestrateur transmet **toujours** le sha256 de la
version qu'il présente (`gate_humain`). Chaîne de contrôles :

1. champs de recevabilité (statut, validateur, rôle, motif, signature) ;
2. **liaison** : présence ref/sha256/`depose_le`/preuve, égalité avec la
   version présentée, empreinte recalculée ;
3. **SLA** : mesure et expiration ;
4. statut REFUSED/VALIDATED, habilitation du rôle.

Cas de rejeu divergent (LLM http réel) : le pipeline produit une **nouvelle
version** (v2) → la liaison v1 ne colle plus → blocage fail-closed avec
dossier ré-exporté. Jamais de réutilisation silencieuse d'une validation.

## 4. Pré-liaison des décisions pré-déposées (`ui_gates/liaison.py`)

Pour les flux de développement/démonstration (décisions déposées avant
l'exécution) :

```
prelier_decisions(gabarits, etat, donnees, llm=…)
  1. rejeu muet complet dans un runtime JETABLE, provider « auto-lieur »
     (valide chaque gate en capturant la version présentée) ;
  2. chaque gabarit (statut/validateur/rôle/motif/pièces — substance métier)
     est LIÉ à la version récoltée + preuve sig-2.0.0 + depose_le ;
  3. « origine_liaison » trace la nature technique de la liaison.
```

Garanties : en mode déterministe ou `llm-simule` (scripté), les versions du
run réel sont identiques (démontré aussi par
`test_prelieur_decisions_conformes_et_stables`) → liaison acceptée. Un
gabarit pour un gate non franchi au rejeu reste **non lié** → blocage s'il
était atteint (données différentes) : cohérent et fail-closed.

## 5. CLI (`ui_gates.cli`)

```bash
# la CLI lie automatiquement la version courante (ref + sha256) :
python3 -m ui_gates.cli sign --racine <RUNTIME> \
    --etat <RUNTIME>/checkpoints/ckpt_<run>_BLOQUE.json \
    --gate G3 --validateur u:bio-042 --role biostatisticien \
    --decision VALIDATED --motif "SAP relu et conforme ICH E9" \
    --pieces sap dq_report
# stdout : ✔ décision VALIDATED … + ligne « liée à art://… sha256:… »
```

- `--etat` est désormais **requis** pour `sign` : impossible de signer sans
  désigner ce qu'on valide (résolution depuis le store, G4 auto-détecte
  safety/compliance).
- `status` affiche la liaison de chaque décision déposée.
- Signer un gate dont l'artefact n'existe pas encore (pipeline pas arrivé
  là) est refusé (usage) — pas de signature « dans le vide ».

## 6. Limites assumées (MVP)

- `depose_le` est horodaté côté dépôt (CLI/pré-lieur). En production, c'est
  le **serveur de signature** qui horodatera (source de temps de confiance,
  voir bloc eIDAS `horodatage_qualifie`). Le SLA mesuré vaut alors preuve
  opposable ; en MVP il est informatif mais fail-closed.
- L'empreinte protège l'intégrité du registre, pas sa confidentialité, ni
  l'authentification du validateur (MVP : identifiant déclaré ; production :
  annuaire + rôles + MFA de la console de validation).
- La vérification de liaison couvre l'artefact principal du gate ; les
  pièces annexes (`pieces_consultees`) restent déclaratives.
