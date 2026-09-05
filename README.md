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
  - `notaires` — API JSON publique d'immobilier.notaires.fr. Ni clé, ni cookie, ni
    anti-bot. **Inventaire distinct** : successions, adjudications, biens ruraux, que
    les portails n'ont pas (mesuré sur la zone têtard : 42 % des maisons absentes de la
    base). Recherche par département uniquement.
  - `paruvendu` — annonces Paruvendu (HTML rendu côté serveur, aucune protection).
  - `agences` — **newsletters d'agences (IMAP) + sites d'agences** : ingestion inbound,
    extraction par l'API Claude (Haiku) avec repli heuristique. Zéro risque ToS.

  Notaires et Paruvendu ne publient pas de coordonnées : `services.enrich.annotate` leur
  pose le **centroïde communal** (flag `position_commune`), sans quoi les filtres
  géographiques des sets restent inertes sur elles.
  - `mock` — jeu de données de démo (dev/tests, hors-ligne).
- **Recherche multi-critères** normalisée (localisation, prix, surfaces terrain/bâti,
  type de bien, DPE, état : ruine / à rénover / baisse de prix).
- **Filtre avancé par préférences pondérées (ranking)** : aucune exclusion, un
  `match_score` classe les biens. Préférences géo (corridor entre villes, proximité
  gare via open data, autour d'une ville), budget/SCI, chambres (plancher **et**
  plafond), **espace modulable en dortoir** (grange, combles aménageables, dépendance —
  la capacité d'accueil qu'on peut se donner, distincte des chambres existantes),
  jardin, terrain, travaux légers, **exposition / durée d'ensoleillement**,
  sans vis-à-vis, nature d'exception, authentique. Critères `pending` prêts à
  s'activer (trajet train, fibre, relief, randonnées). **Parseur de brief en langage
  naturel** (`POST /api/brief/parse`, IA Claude + repli heuristique).
- **Enrichissement open data** (`?enrich=true`) : zonage PLU/constructibilité + zones AU
  (GPU/IGN), risques (Géorisques), altitude/relief (IGN) — sans clé ;
  **exposition et durée d'ensoleillement** (heures de soleil direct au 21 décembre,
  orientation et pente du versant, calculées sur le modèle d'altitude IGN — le critère qui
  sépare l'adret de l'ubac et qu'aucune annonce ne donne ; voir
  `backend/app/services/soleil.py` et `scripts/warm_ensoleillement.py`) ; temps de trajet
  train (estimation **sans clé** ; clé Navitia/SNCF optionnelle pour les horaires réels), **qualité de l'eau/pollution** (Hub'Eau : pesticides,
  nitrates, PFAS), **profil socio** (âge médian, orientation politique → préférences
  `population_jeune`/`orientation_gauche`) ; **attractivité locative saisonnière**
  (« Airbnb » : remontée mécanique, lac, hébergement touristique et restauration relevés
  sur OpenStreetMap — ce qu'un logement peut se louer à la semaine, mesuré et non lu dans
  l'annonce ; voir `backend/app/services/tourisme.py` et `scripts/warm_tourisme.py`).
  Alimente filtres, préférences et score. État via `GET /api/enrichment/status`.
- **Jeux de filtres** (`FilterSet`) réutilisables.
- **Registre des critères** (`services/criteres.py`) : un identifiant stable, une famille
  et un nom canonique par critère — le libellé encode les paramètres et change, l'id non.
  Exporté dans `data.json`, il sert au regroupement et à la **pondération personnelle**
  du front (chacun ses poids, cf. [`docs/criteres.md`](docs/criteres.md)).
- **Recherches fréquentes** (`SavedSearch`) + scheduler + **détection des nouveautés**
  (badge in-app, marquage « tout vu », historique des runs).
- **Critères de santé du bien et du lieu** : `dpe` (performance énergétique — une maison
  habitable classée G est habitable *et* une passoire), `risques_naturels` (aléas
  Géorisques pondérés par leur gravité) et `qualite_eau` (Hub'Eau). Les deux derniers
  réutilisent la mesure du score d'investissement plutôt qu'un second barème.
- **Aide à la décision** : **score d'investissement** explicable (filtre `score_min`,
  tri `sort=score`), classification ruines/à rénover, dédoublonnage inter-sources,
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

## Données socio (population jeune / orientation politique)

Le critère `population_jeune` / `orientation_gauche` s'appuie sur `backend/data/communes_socio.csv`. Pour le peupler à l'échelle nationale :

```bash
cd backend
# part_gauche (présidentielle 2022 T1, open data) — téléchargement auto
python scripts/build_socio_dataset.py
# + âge médian via un CSV INSEE (optionnel)
python scripts/build_socio_dataset.py --age-csv age_median.csv --age-code-col CODGEO --age-value-col AGEMED
```

`part_gauche` et `age_median` sont indépendamment optionnels (la préférence correspondante reste `pending` si absente).

## Configuration

Voir [`backend/.env.example`](backend/.env.example). Points clés :
`PAPPERS_API_KEY`, `DATABASE_URL` (SQLite par défaut), `PROXY_URL` (scrapers protégés),
`SCHEDULER_ENABLED`, `SCRAPER_RATE_LIMIT_MS`.

## Feuille de route

Voir [`docs/architecture.md`](docs/architecture.md) — connecteurs supplémentaires
(PAP, Leboncoin, SeLoger via proxies), enrichissement open data (zonage GPU/zones AU,
Géorisques, PEB aérien, comparables DVF), scoring d'investissement, puis le front.
