# agent-stat

Système multi-agents d'analyse statistique et d'aide à la décision — études médicales &
essais cosmétiques. Plateforme de **décision assistée** : l'IA prépare, calcule (moteur
déterministe), documente et propose ; l'humain valide aux étapes critiques (fail-closed).

## Conception

| Chemin | Description |
|---|---|
| `docs/ARCHITECTURE.md` | Conception complète : vision, architecture 16 agents, pipeline 14 étapes + 7 gates, méthodologie stats, scores DQ/RA/CC, volets cosmétique & médical, sécurité/conformité, MVP |
| `agents/registry.yaml` | Contrat machine-lisable des 16 agents (mission, I/O, outils, succès, échec, escalade) + gates humains + seuils |
| `schemas/etat.schema.json` | Schéma JSON de l'état de pipeline (mémoire partagée, verrous, gates, provenance) |

## Code (MVP exécutable — stdlib Python ≥ 3.10, zéro dépendance)

| Package | Rôle |
|---|---|
| `core/` | Socle : audit append-only chaîné SHA-256 (`audit.py`), store d'artefacts immutables versionnés (`store.py`), état + checkpoints (`state.py`), bus de messages typés (`bus.py`), gates humains fail-closed (`gates.py`), exceptions (`exceptions.py`) |
| `stats_catalogue/` | Moteur de calcul déterministe : distributions pures (`dist.py`), catalogue d'ops versionné (`ops.py` — Welch, Mann-Whitney, χ², Fisher exact, McNemar, Kruskal-Wallis, Anderson-Darling, IC Wilson/Clopper-Pearson, MoS…), double exécution contrôlée par hash (`controller.py`) |
| `agents_impl/` | 12 agents MVP : compréhension, dataqualité (score DQ), biostat (SAP), EDA, biais, hypothèses (fallbacks pré-spécifiés), manquants, anomalies, inférentiel (verrou SAP), sécurité (MoS/signaux), conformité (règles citées), redaction (faits/inférences/reco séparés), relecture (recalcul indépendant), exporteur |
| `orchestration/` | Machine à états (`orchestrator.py`) : contrats vérifiés, retries bornés sur erreurs techniques uniquement, 10 règles de blocage fail-closed, verrou SAP SHA-256, registre des décisions ; câblage des 14 étapes (`pipeline.py`) ; moteur de scores RA/CC plafonnés (`scores.py`) |
| `demo/` | Cas d'usage synthétique déterministe (`jeu_donnees.py`) + démo de bout en bout (`run_demo.py`) |
| `tests/` | 39 tests : valeurs de référence stats, audit/tamper, gates, parcours nominal, 5 scénarios de blocage, contre-analyse relecture |

## Exécuter

```bash
python3 demo/run_demo.py                    # pipeline complet sur données synthétiques
python3 -m unittest discover -s tests      # batterie de tests
python3 - <<'EOF'
from core.audit import JournalAudit
print(JournalAudit.verifier("runtime/demo/audit.jsonl"))   # (True, n, 'chaîne intègre')
EOF
```

La démo écrit dans `runtime/demo/` (ignoré par git) : store d'artefacts, journal d'audit
vérifiable, checkpoints d'état, `exports/<study_id>/rapport_final.md`.

## Garanties implémentées (traçables dans le code)

- **Calcul 100 % déterministe** — double exécution dont les SHA-256 doivent coïncider, sinon blocage ;
- **Verrou anti p-hacking** — SAP validé humain (G3) + gelé par hash avant tout calcul inférentiel ; toute modification = déviation bloquante ;
- **Fail-closed partout** — pas de décision humaine ⇒ blocage (jamais de validation par défaut) ;
- **P-values jamais seules** — IC 95 % + taille d'effet dans chaque fait rapporté, chaque chiffre citant `art://…#sha256:…` ;
- **Contre-analyse** — la relecture recalcule indépendamment chaque chiffre depuis les entrées embarquées dans l'artefact résultats ;
- **Lexique contrôlé** — hors essai randomisé, tournures d'association uniquement ;
- **Scores plafonnés** — DQ (formule pondérée + caps), RA, CC ; CC < 0,40 ⇒ le gabarit interdit de conclure ;
- **Journal d'audit chaîné** — toute altération détectée par vérification de chaîne.
