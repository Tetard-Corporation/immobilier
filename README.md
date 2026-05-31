# immobilier — Moteur de prospection foncière/immobilière

Application personnelle pour trouver des terrains/biens d'investissement, façon Jinka
mais avec des **filtres avancés**, des **jeux de filtres réutilisables**, des
**recherches fréquentes** avec détection de nouveautés, et de l'enrichissement
(constructibilité, risques, comparables) à venir.

> Phase actuelle : **engine (backend)**. Le front viendra plus tard.
> Conception détaillée : [`docs/architecture.md`](docs/architecture.md).

## Fonctionnalités de l'engine

- **Sources pluggables** (`ListingSource`) :
  - `pappers` — API officielle Pappers Immobilier (données foncières/DVF). Clé requise.
  - `bienici` — annonces Bien'ici (API JSON). Aucune clé ; géo fine filtrée côté client.
  - `bienici` / `leboncoin` / `pap` / `seloger` — annonces de portails (scraping ;
    Leboncoin/PAP/SeLoger nécessitent un proxy + navigateur headless en live).
  - `agences` — **newsletters d'agences (IMAP) + sites d'agences** : ingestion inbound,
    extraction par l'API Claude (Haiku) avec repli heuristique. Zéro risque ToS.
  - `mock` — jeu de données de démo (dev/tests, hors-ligne).
- **Recherche multi-critères** normalisée (localisation, prix, surfaces terrain/bâti,
  type de bien, DPE, état : ruine / à rénover / baisse de prix).
- **Jeux de filtres** (`FilterSet`) réutilisables.
- **Recherches fréquentes** (`SavedSearch`) + scheduler + **détection des nouveautés**
  (badge in-app, marquage « tout vu », historique des runs).
- **Aide à la décision** : classification ruines/à rénover, dédoublonnage inter-sources,
  suivi des baisses de prix.

## Démarrage (backend)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # configurer PAPPERS_API_KEY, PROXY_URL... (optionnels)
uvicorn app.main:app --reload
```

API documentée sur `http://localhost:8000/docs`.

### Exemples

```bash
# Recherche d'annonces (Bien'ici) : terrains <= 80k autour de Bordeaux
curl -X POST 'http://localhost:8000/api/search?source=bienici&dedupe=true' \
  -H 'Content-Type: application/json' \
  -d '{"property_types":["terrain"],"prix_max":80000,"code_postal":"33000"}'

# Recherche sur données foncières (mock si pas de clé Pappers)
curl -X POST 'http://localhost:8000/api/search?source=mock' \
  -H 'Content-Type: application/json' \
  -d '{"property_types":["terrain"],"departement":"GIRONDE"}'
```

## Tests

```bash
cd backend && source .venv/bin/activate && pytest
```

Les tests tournent **sans réseau ni clé** (source mock + fixtures). Le connecteur
Bien'ici est testé sur sa logique de normalisation/filtres (offline).

## Configuration

Voir [`backend/.env.example`](backend/.env.example). Points clés :
`PAPPERS_API_KEY`, `DATABASE_URL` (SQLite par défaut), `PROXY_URL` (scrapers protégés),
`SCHEDULER_ENABLED`, `SCRAPER_RATE_LIMIT_MS`.

## Feuille de route

Voir [`docs/architecture.md`](docs/architecture.md) — connecteurs supplémentaires
(PAP, Leboncoin, SeLoger via proxies), enrichissement open data (zonage GPU/zones AU,
Géorisques, PEB aérien, comparables DVF), scoring d'investissement, puis le front.
