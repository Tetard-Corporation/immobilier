# Front statique — têtard (GitHub Pages)

Front **100 % statique** (HTML/CSS/JS, aucun build) qui lit un instantané JSON
produit par le backend. Hébergeable gratuitement sur GitHub Pages.

## Ce que fait le front
- **Mode Scroll** : feed des biens de l'historique (dédoublonnés), galerie photo
  balayable directement (scroll tactile / flèches).
- **Mode Carte** : Leaflet + OpenStreetMap, marqueurs colorés selon le match du set.
- **Tri & filtres** : par set / sous-set (têtard, ↳ Léo), score min, favoris.
- **Clic sur un bien** : tableau comparatif des scores — match par critère et par
  set (sous-score + texte explicatif) + détail du score d'investissement (piliers).

## Données
`data/data.json` + `data/photos/` sont générés par le backend :

```bash
cd backend
python -m app.services.export_static ../docs/data           # avec photos
python -m app.services.export_static ../docs/data --no-photos # sans télécharger
```

> ⚠️ GitHub Pages est statique : il ne peut **ni exécuter le moteur Python ni
> scraper**. Le front affiche donc un **instantané**. Pour rafraîchir : relancer
> des recherches côté backend, puis ré-exporter et committer.

## Vote par étoiles (Supabase)

Vote 1–5 ⭐ par bien et par personne, **sans login** (on se fait confiance). Au
1er chargement, un overlay « Qui es-tu ? » fait choisir son nom (mémorisé en
`localStorage`). La **note globale** s'affiche dans le feed (ta note + moyenne) et
le détail par personne dans la fiche. Dans la fiche, on peut **aussi (en option)
noter chaque critère** du set : le tableau « Critères » place côte à côte le score
**algo**, **ton vote** et la **moyenne du groupe**.

**Sans Supabase configuré**, le vote fonctionne quand même en mode *local*
(localStorage, par navigateur) — pratique pour tester l'UX, mais non partagé.

### Mise en place (5 min, gratuit)
1. Crée un projet sur [supabase.com](https://supabase.com).
2. **SQL Editor** → exécute :
   ```sql
   create table if not exists votes (
     id         bigint generated always as identity primary key,
     bien_id    text not null,
     voter      text not null,
     criterion  text not null default '__overall__',  -- '__overall__' = note globale
     stars      int  not null check (stars between 1 and 5),
     updated_at timestamptz not null default now(),
     unique (bien_id, voter, criterion)   -- 1 vote par (bien, personne, critère)
   );
   alter table votes enable row level security;
   create policy "anon read"   on votes for select using (true);
   create policy "anon insert" on votes for insert with check (true);
   create policy "anon update" on votes for update using (true) with check (true);
   ```
3. **Settings → API** : copie l'URL du projet et la clé **anon public**.
4. Édite `config.js` : renseigne `SUPABASE_URL`, `SUPABASE_ANON_KEY` et la liste
   `USERS` (vos prénoms).

> 🔓 La clé anon est **publique** (c'est prévu : protégée par RLS). Les policies
> ci-dessus sont permissives : quiconque connaît l'URL du site peut voter — risque
> assumé (usage entre amis, URL non diffusée).

## Activer GitHub Pages
1. Repo → **Settings → Pages**.
2. **Source : Deploy from a branch**.
3. Branche : celle qui contient ce dossier, dossier **`/docs`**.
4. Le site sera servi sur `https://<user>.github.io/<repo>/`.

`.nojekyll` empêche tout traitement Jekyll.

## Poids du repo
Les photos sont stockées en local (choix retenu : robustesse si l'annonce
disparaît) et **optimisées à l'enregistrement** : redimensionnées à ≤ 1280 px et
recompressées en JPEG progressif (qualité 78, métadonnées supprimées) via Pillow
— typiquement **−55 % de poids**. Réglages : `_MAX_DIM`, `_JPEG_QUALITY`,
`_MAX_PHOTOS` dans `export_static.py`. Sur un gros historique, surveiller la taille
du repo (limite Pages ~1 Go).
