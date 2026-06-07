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
`localStorage`). Le vote s'affiche dans le feed (ta note + moyenne) et le détail
par personne dans la fiche du bien.

**Sans Supabase configuré**, le vote fonctionne quand même en mode *local*
(localStorage, par navigateur) — pratique pour tester l'UX, mais non partagé.

### Mise en place (5 min, gratuit)
1. Crée un projet sur [supabase.com](https://supabase.com).
2. **SQL Editor** → exécute (⚠️ **remplace `CHANGE-MOI`** par votre code partagé) :
   ```sql
   create table if not exists votes (
     id         bigint generated always as identity primary key,
     bien_id    text not null,
     voter      text not null,
     stars      int  not null check (stars between 1 and 5),
     updated_at timestamptz not null default now(),
     unique (bien_id, voter)        -- 1 vote par (bien, personne) -> upsert
   );
   alter table votes enable row level security;

   -- Lecture libre (afficher les notes), mais AUCUNE écriture directe par anon.
   create policy "anon read" on votes for select using (true);
   revoke insert, update, delete on votes from anon;

   -- Écriture uniquement via cette fonction, qui valide le code partagé.
   -- Le code vit ICI (ta base privée), jamais dans le site public.
   create or replace function cast_vote(p_bien_id text, p_voter text, p_stars int, p_secret text)
   returns void language plpgsql security definer set search_path = public as $$
   begin
     if p_secret is distinct from 'CHANGE-MOI' then
       raise exception 'code invalide';
     end if;
     if p_stars < 1 or p_stars > 5 then
       raise exception 'note hors bornes';
     end if;
     insert into votes (bien_id, voter, stars, updated_at)
     values (p_bien_id, p_voter, p_stars, now())
     on conflict (bien_id, voter) do update
       set stars = excluded.stars, updated_at = now();
   end; $$;
   grant execute on function cast_vote(text, text, int, text) to anon;
   ```
3. **Settings → API** : copie l'URL du projet et la clé **anon public**.
4. Édite `config.js` : renseigne `SUPABASE_URL`, `SUPABASE_ANON_KEY` et la liste
   `USERS`. **Ne mets pas le code** dans `config.js` (il serait public).
5. Partage le code (`CHANGE-MOI`) à vos 6 de vive voix. Chacun le saisit **une fois**
   dans le front (overlay « Qui es-tu ? »), il est mémorisé sur son appareil.

> 🔒 **Pourquoi ça bloque les votes extérieurs :** la clé anon est publique mais ne
> donne que la **lecture**. Écrire passe obligatoirement par `cast_vote`, qui exige
> le code — lequel n'apparaît **nulle part** dans le site (ni repo, ni `config.js`).
> Un inconnu tombant sur l'URL peut voir les notes mais pas voter. Ce n'est pas de
> la crypto (le code circule en clair entre vous), mais ça suffit à votre usage.

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
