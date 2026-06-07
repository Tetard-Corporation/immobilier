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
