---
name: converge-filters
description: >-
  Consomme tous les votes et commentaires des utilisateurs (Supabase) pour faire
  CONVERGER le set de filtres « têtard » vers ce qui convient le mieux au groupe :
  propose de nouveaux poids de critères (si convergence possible), crée/met à jour
  les sous-sets par utilisateur, exploite les commentaires pour repérer et corriger
  des erreurs backend (données/scoring), puis RECALCULE toutes les offres historiques
  (ré-export) et déploie. À utiliser quand le groupe a accumulé des votes/commentaires
  (ex. « fais converger les filtres », « mets à jour le set avec les votes »,
  « analyse les retours du groupe »).
---

# Convergence du set à partir des votes & commentaires

Objectif : transformer les votes/commentaires du groupe en (1) un meilleur set commun,
(2) des sous-sets par personne, (3) des corrections backend, puis recalculer tous les
biens. **Toujours proposer avant d'appliquer** — la convergence n'est pas toujours possible.

## Étape 1 — Analyser
Depuis la racine du dépôt :
```bash
cd backend && . .venv/bin/activate && cd ..
python .claude/skills/converge-filters/analyze_votes.py
```
Lis le rapport Markdown imprimé **et** `proposal.json`. Il contient : poids actuels vs
proposés (avec corrélation note-critère ↔ note globale, et confiance), les divergences
inter-utilisateurs, les profils par utilisateur, et les commentaires (dont ceux
potentiellement révélateurs d'une **erreur de donnée/scoring**).

## Étape 2 — Vérifier la suffisance des données
Si le rapport indique « trop peu de votes » (peu d'étoiles), **arrête-toi** : explique au
groupe qu'il faut plus de votes pour converger de façon fiable, et propose éventuellement
de ne traiter que les commentaires (Étape 5). Ne force jamais des poids sur des données
insuffisantes (confiance « faible »).

## Étape 3 — Décider avec le groupe (AskUserQuestion)
Présente une synthèse courte : changements de poids notables (⬆️/⬇️), critères qui
**divergent** (à laisser en perso), et sous-sets dérivables. Demande via `AskUserQuestion`
ce qu'on applique :
- **Set global** : appliquer les nouveaux poids (uniquement les critères à confiance
  suffisante ; laisse les divergents inchangés).
- **Sous-sets** : créer/mettre à jour les sous-sets par utilisateur ayant assez de votes.
- **Rien pour l'instant** (juste un rapport).

Respecte les divergences : si un critère diverge fortement, **ne l'impose pas** dans le
set global — il a vocation à vivre dans les sous-sets perso.

## Étape 4 — Appliquer
Depuis `backend/` (venv actif) :
```bash
cd backend && . .venv/bin/activate
python ../.claude/skills/converge-filters/apply_proposal.py --global --subsets
```
(adapte les flags selon le choix du groupe). Le set global est **versionné**
(`criteria._history`) avant modification = « nouvelle version » traçable.

## Étape 5 — Commentaires → corrections backend
Parcours `comments_flagged` de `proposal.json`. Pour chaque retour qui pointe une **erreur
réelle** (ex. « la gare n'est pas à X », « pas isolé du tout », mauvaise commune, score
incohérent), agis sur le moteur :
- libellé/détail trompeur → corrige `backend/app/services/preferences.py` ;
- donnée fausse (gare manquante, distance) → corrige la source (`gares.csv`, providers,
  caches Overpass `data/*_cache.json`) ;
- classification erronée (état, features) → `services/classify.py` / enrichissement.
Distingue « l'algo se trompe » (à corriger) de « l'utilisateur n'aime pas » (→ poids, pas
un bug). N'invente pas de correction : si le commentaire est ambigu, liste-le pour le
groupe au lieu de deviner.

## Étape 6 — Recalculer TOUTES les offres + déployer
Tout changement de set ou de données impose un recalcul complet :
```bash
cd backend && . .venv/bin/activate
python -m pytest -q
python -c "from app.services import gares; gares._load.cache_clear(); from app.services.export_static import export_to_dir; from app.db import SessionLocal; print(export_to_dir(SessionLocal(), '../data', download_photos=True))"
```
Puis commit sur la branche de travail, ouvre une PR vers `main` et merge-la (le push
direct sur `main` est bloqué) — voir le workflow habituel. Vérifie le déploiement Pages.

## Garde-fous
- Toujours **proposer → confirmer → appliquer**. Jamais d'application silencieuse de poids.
- La convergence est **conditionnelle** : s'il n'y a pas de consensus, privilégie les
  sous-sets perso et garde le set global stable.
- Les poids restent dans [1, 5]. Les sous-sets sont des *overrides* fusionnés au parent.
- Un commentaire ne modifie le backend que s'il révèle une **erreur objective**.
