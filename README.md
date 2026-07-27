# agent-stat

Système multi-agents d'analyse statistique et d'aide à la décision — études médicales &
essais cosmétiques. Plateforme de **décision assistée** : l'IA prépare, calcule (moteur
déterministe), documente et propose ; l'humain valide aux étapes critiques.

## Contenu

| Chemin | Description |
|---|---|
| `docs/ARCHITECTURE.md` | Conception complète : vision, architecture 16 agents, pipeline 14 étapes + 7 gates, méthodologie stats, scores DQ/RA/CC, volets cosmétique & médical, sécurité/conformité, MVP |
| `agents/registry.yaml` | Contrat machine-lisable des 16 agents (mission, I/O, outils, succès, échec, escalade) + gates humains + seuils par défaut |
| `schemas/etat.schema.json` | Schéma JSON de l'état de pipeline (mémoire partagée, verrous, gates, provenance) |
| `orchestration/orchestrator.py` | Squelette d'orchestrateur : machine à états, retries, blocages fail-closed, verrou SAP, audit chaîné |
