# Front statique — têtard (GitHub Pages)

Front **100 % statique** (HTML/CSS/JS, aucun build) qui lit un instantané JSON
produit par le backend. Hébergeable gratuitement sur GitHub Pages.

> Les fichiers du front sont **à la racine du repo** (servis par Pages depuis
> `main` /root → `https://<owner>.github.io/immobilier/`). `.nojekyll` désactive
> Jekyll pour que tout soit servi tel quel.

## Ce que fait le front
- **Mode Scroll** : feed des biens de l'historique (dédoublonnés), galerie photo
  balayable directement (scroll tactile / flèches).
- **Mode Carte** : Leaflet + OpenStreetMap, marqueurs colorés selon le match du set.
- **Tri & filtres** : par set (têtard, Pauline, Littoral breton), score min, favoris.
- **Clic sur un bien** : tableau comparatif des scores — match par critère et par
  set (sous-score + texte explicatif) + détail du score d'investissement (piliers).

## Données
`data/data.json` + `data/photos/` sont générés par le backend :

```bash
cd backend
python -m app.services.export_static ../data            # avec photos (racine /data)
python -m app.services.export_static ../data --no-photos # sans télécharger
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
**algo**, **ton vote** et la **moyenne du groupe**. On peut enfin laisser un
**commentaire optionnel** avec sa note globale (affiché sous l'avis de chacun).

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
     stars      int  check (stars between 1 and 5),    -- nullable : commenter sans noter
     comment    text,                                 -- commentaire optionnel
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

## Poids des critères : chacun les siens (⚖️)

Le groupe n'a qu'un classement pour sept personnes. Le bouton **⚖️ Mes poids** en donne
un par personne, **sans toucher à la collecte ni aux mesures** : les sous-scores par
critère sont déjà calculés bien par bien à l'export, seule leur **pondération** change,
et elle se rejoue dans le navigateur.

- **Échelle à 5 niveaux** — 1 accessoire, 2 utile, 3 important, 4 très important,
  5 essentiel — plus **∅ ignorer**, qui sort le critère du calcul. Un critère jamais
  réglé garde le poids du set.
- **Seuils personnels** : sur les critères dont la donnée est exportée bien par bien
  (budget, chambres, format, surface, terrain, jardin, altitude, DPE), on règle aussi le
  **seuil** — « 5 chambres minimum » quand le set en demande 3. Le poids dit combien un
  critère compte, le seuil dit ce qu'on veut : deux personnes peuvent mettre 5 aux
  chambres sans chercher la même maison. Un champ vide = le seuil du set.
- **Lentille de classement** (menu « Poids » dans la barre du haut) : classer le feed et
  la carte avec les poids du set (défaut), les miens, la **moyenne du groupe**, ou ceux
  de quelqu'un d'autre — voir le catalogue avec ses yeux.
- **Convergence** : le panneau dit sur quoi le groupe est d'accord, ce qu'il laisse
  tomber ensemble, et où ça coince (avec les deux extrêmes nommés), plus la proximité
  deux à deux. C'est la matière première de la skill `converge-filters`.
- **Couverture de mesure** : chaque critère indique sur quelle part du catalogue il est
  réellement mesuré quand c'est sous 80 %. Mettre 5 à l'exposition (mesurée sur 48 % des
  biens), c'est classer l'autre moitié sans elle.
- Les poids se règlent aussi **critère par critère** depuis la fiche d'un bien : cliquer
  une ligne du tableau « Match » ouvre la note, le commentaire **et** le poids.

Le calcul rejoue `services/preferences.evaluate` à l'identique (moyenne pondérée des
critères mesurés, mêmes ancres de contraste, paliers d'exigences, pénalité des biens
déclassés) : vérifié sur les 3 792 scores de l'instantané, écart maximum 0,10.

> **Stockage** : la table `votes` existante, sous un bien fictif `__poids__:<set>` —
> `criterion` = l'id du critère, `stars` = le poids (`null` = ignorer), `comment` = les
> seuils personnels en JSON. Aucune migration SQL à faire. Un script qui analyse les votes doit **exclure `bien_id like
> '__poids__%'`**.

Voir [`docs/criteres.md`](docs/criteres.md) pour le registre des critères, ce que chacun
mesure, et le diagnostic chiffré du set actuel.

## Activer GitHub Pages
1. Repo → **Settings → Pages**.
2. **Source : Deploy from a branch**.
3. Branche : **`main`**, dossier **`/ (root)`** (le front est à la racine).
4. Le site sera servi sur `https://<owner>.github.io/immobilier/`.

`.nojekyll` (à la racine) empêche tout traitement Jekyll.

## Poids du repo
Les photos sont stockées en local (choix retenu : robustesse si l'annonce
disparaît) et **optimisées à l'enregistrement** : redimensionnées à ≤ 1280 px et
recompressées en JPEG progressif (qualité 78, métadonnées supprimées) via Pillow
— typiquement **−55 % de poids**. Réglages : `_MAX_DIM`, `_JPEG_QUALITY`,
`_MAX_PHOTOS` dans `export_static.py`. Sur un gros historique, surveiller la taille
du repo (limite Pages ~1 Go).
