# Architecture cible — Moteur de prospection foncière/immobilière

> Document de conception de l'**engine** (backend). Le front viendra plus tard.
> Mis à jour après cadrage avec le porteur du projet.

## 1. Objectifs métier

Trouver des biens/terrains pour investissement personnel, avec une intelligence
supérieure à Jinka :

- Agréger des **annonces** (biens, terrains constructibles, terrains à ruines/à rénover)
  depuis plusieurs portails.
- Qualifier chaque bien avec des **données officielles** : zonage d'urbanisme,
  constructibilité, risques, nuisances aériennes, comparables de prix.
- Repérer les **terrains bientôt constructibles** (zones AU du PLU).
- **Jeux de filtres** réutilisables + **recherches fréquentes** avec détection de
  nouveautés (déjà en place dans le socle).
- **Aide à la décision** : score d'investissement, dédoublonnage inter-sources,
  suivi des baisses de prix.

### Décisions de cadrage actées

| Sujet | Décision |
|---|---|
| Sources d'annonces | Leboncoin, PAP, SeLoger/Bien'ici, sites spécialisés terrains |
| Approche collecte | Scraping assumé, **mix par source** (HTTP léger d'abord, headless+proxies pour les durs) |
| Constructibilité | Zonage PLU actuel (GPU) + **zones AU** pour "bientôt constructible" |
| Risques/contraintes | Géorisques + **nuisances aériennes (PEB + servitudes T4/T5)** |
| Comparables | DVF (via Pappers) |
| Ruines / à rénover | Mots-clés annonces **+** croisement cadastre/DVF |
| Aide à la décision | Score d'investissement, dédoublonnage inter-sources, suivi baisse de prix |
| Périmètre | National, filtrage par zone côté recherche |

## 2. Vue d'ensemble : 3 familles de connecteurs

```
                 ┌──────────────────────────────────────────────┐
                 │                  ENGINE                        │
                 │                                                │
  Annonces  ───► │  ListingSource (search/get) ─► Normalisation   │
 (scrapers)      │            │                                   │
                 │            ▼                                   │
  Données   ───► │   Pipeline d'enrichissement (EnrichmentProvider)│
 officielles     │   zonage ▸ risques ▸ aérien ▸ comparables DVF  │
                 │            │                                   │
                 │            ▼                                   │
                 │   Dédoublonnage ▸ Scoring ▸ Suivi prix          │
                 │            │                                   │
                 │            ▼                                   │
                 │   FilterSet ▸ SavedSearch ▸ détection nouveautés│
                 └──────────────────────────────────────────────┘
```

### A. Sources d'annonces — `ListingSource` (interface existante)

| Connecteur | Mode collecte | Notes |
|---|---|---|
| `pappers` | API officielle | ✅ implémenté (données foncières/DVF) |
| `bienici` | API JSON | ✅ implémenté et vérifié en live (annonces) |
| `mock` | fixtures | ✅ implémenté (dev/tests) |
| `leboncoin` | API JSON + proxy | ✅ implémenté (parsing testé). ⚠️ Datadome → requiert `PROXY_URL` en live |
| `pap` | HTTP/headless + JSON-LD | ✅ implémenté (parsing testé). ⚠️ Cloudflare → headless/proxy en live |
| `seloger` | headless + JSON-LD | ✅ implémenté (parsing testé). ⚠️ Datadome → headless/proxy en live |
| `paruvendu` | HTTP léger | accessible (200) — candidat bonus réel |
| `agences` | **inbound** : IMAP + sites | ✅ implémenté. Newsletters d'agences (extraction LLM Haiku + repli heuristique) + scraping de sites d'agences. Zéro risque ToS. |

**Constat de terrain (sondages)** : Leboncoin, SeLoger et PAP renvoient un blocage
anti-bot (Datadome / Cloudflare) sans proxy ; Bien'ici et Paruvendu sont accessibles.
Le mode headless (Playwright, dépendance optionnelle) + `PROXY_URL` sont en place dans
`ScraperSource` pour les débloquer dans l'environnement d'exécution.

Base commune `ScraperSource` : gestion `httpx` **ou** Playwright selon la source,
rate-limiting, backoff, cache disque, `User-Agent`/headers réalistes, proxy
configurable (`PROXY_URL`), parsing → `NormalizedListing`.

### B. Enrichissement — nouvelle interface `EnrichmentProvider`

```python
class EnrichmentProvider(ABC):
    name: str
    def enrich(self, listing: NormalizedListing) -> dict: ...   # champs fusionnés
```

| Provider | Source de données | Champs produits |
|---|---|---|
| `gpu_zonage` | API Carto GPU (apicarto.ign.fr) | `zone_urba` (U/AU/A/N), `libelle`, `constructible` (bool), `est_zone_au` (bool) |
| `georisques` | api.georisques.gouv.fr | `risques` (argile, inondation, radon, PPRN, séisme…) |
| `aeronautique` | PEB + servitudes T4/T5 (GPU/SUP) | `peb_zone` (A–D), `sous_servitude_aero` (bool), `couloir_aerien` (bool) |
| `dvf_comparables` | Pappers / DVF | `prix_m2_secteur`, `ecart_prix_pct` |
| `cadastre` | API Carto cadastre | `contenance`, `parcelle`, géométrie (compléter les annonces) |

Exécution : pipeline appelé après la collecte ; résultats stockés dans
`Listing.enrichment` (JSON) + colonnes dérivées indexables (`constructible`,
`zone_urba`, `est_zone_au`, `peb_zone`, `score`).

### C. Intelligence transverse

- **Détection ruines / à rénover** : `services/classify.py`
  - mots-clés annonces (`ruine`, `grange`, `corps de ferme`, `à restaurer`,
    `à rénover`, `habitable après travaux`…) → `flag_ruine`, `flag_a_renover`.
  - croisement cadastre/DVF : bâti présent à faible valeur/m² ou ancien.
- **Dédoublonnage inter-sources** : empreinte (géo arrondie + tranche prix + surface)
  → regroupement, un bien canonique + ses occurrences par portail.
- **Score d'investissement** : combinaison `ecart_prix_pct` (vs comparables),
  bonus constructible/AU, malus risques/PEB/aérien → note 0–100.
- **Suivi baisse de prix** : table `PriceHistory` ; à chaque revue, si le prix
  change on historise et on lève un `flag_baisse_prix`.

## 3. Modèle de données — ajouts prévus

- `Listing` : + `enrichment` (JSON), + colonnes dérivées (`constructible`,
  `zone_urba`, `est_zone_au`, `peb_zone`, `flag_ruine`, `flag_a_renover`,
  `score`, `prix_m2_secteur`, `ecart_prix_pct`), + `canonical_id` (dédoublonnage).
- `PriceHistory(listing_id, prix, observed_at)`.
- (Existants : `FilterSet`, `SavedSearch`, `SearchRun`, `SeenListing`.)

## 4. Critères de recherche — extensions

Ajout aux `SearchCriteria` : `constructible_only`, `zones_urba` (U/AU/…),
`exclure_risques` (liste), `exclure_peb`, `hors_couloir_aerien`,
`ruine`/`a_renover`, `score_min`. Le filtrage en mémoire (`services/filters.py`)
gère ces champs sur les données enrichies ; les sources qui le peuvent les
poussent en amont (ex. Pappers).

## 5. Stratégie de collecte (anti-bot)

- **HTTP léger** (`httpx`) par défaut : PAP, sites spécialisés.
- **Headless** (Playwright) + **proxies rotatifs** (`PROXY_URL`) : Leboncoin, SeLoger.
- Garde-fous : `robots`-aware best-effort, délais aléatoires, cache, quotas par run,
  reprise sur erreur (le scheduler ne tombe jamais).
- ⚠️ ToS : ces portails interdisent le scraping. Usage **personnel** ; maintenance
  régulière attendue (sélecteurs, blocages).

## 6. Feuille de route (lots)

- **Lot A — Enrichissement open data** (faible risque, forte valeur)
  `EnrichmentProvider` + `gpu_zonage`, `georisques`, `aeronautique`, `dvf_comparables`,
  pipeline + champs dérivés + filtres `constructible/risques/aérien`. *(commence ici)*
- **Lot B — Score d'investissement** : ✅ implémenté. Moteur pondéré et **explicable**
  (détail des contributions), tolérant aux données partielles — fonctionne dès
  maintenant (état, nature, nuisances, baisse de prix) et intègre automatiquement les
  composantes du Lot A (affaire vs comparables, constructible/AU, risques, PEB) quand
  elles sont présentes. Filtre `score_min` + tri `sort=score`. (Classification ruines
  et suivi prix déjà livrés au Lot C.)
- **Lot C — Scraper PAP & sites spécialisés terrains** (HTTP léger) + dédoublonnage.
- **Lot D — Scrapers durs** (headless + proxies) : ✅ infra headless/proxy + Leboncoin,
  PAP, SeLoger (parsing testé offline) — nécessitent une validation live via proxy.
  (Paruvendu, réel et accessible, reste un bonus optionnel.)
- **Lot E — Newsletters d'agences** (ingestion email IMAP + extraction LLM Haiku +
  repli heuristique) + scraping de sites d'agences locales : ✅ implémenté.
  Source `agences` (inbound) alimentée par un job de scheduler, branchée sur le
  pipeline (classification, dédoublonnage, nouveautés). Zéro risque ToS (opt-in).
- **Lot E — Agrégation multi-sources** (source `all`) dans recherches fréquentes.
- **Lot F — Tests d'intégration, durcissement, doc.**
- *(Front : phase ultérieure.)*

## 7. Config additionnelle (.env)

```
PROXY_URL=                 # proxy(s) pour les scrapers "durs"
SCRAPER_RATE_LIMIT_MS=2000 # délai mini entre requêtes par source
GPU_API_URL=https://apicarto.ign.fr/api/gpu
GEORISQUES_API_URL=https://www.georisques.gouv.fr/api/v1
ENRICH_ON_SEARCH=true      # enrichissement à la volée vs asynchrone
```
