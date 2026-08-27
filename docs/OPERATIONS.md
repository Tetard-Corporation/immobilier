# Protocole d'exploitation (collecte, Supabase, sauvegardes)

Ce document capitalise les pièges rencontrés pour ne plus repartir de zéro. Lis-le
avant une collecte, une convergence de scoring, ou toute intervention sur Supabase.

---

## 0. Runbook : mettre à jour les biens

La procédure complète, **dans cet ordre**, et **en local** (cf. §1). Chaque étape indique
ce qui casse si on la saute — toutes ont déjà coûté une collecte.

### Prérequis (une seule fois)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-scrapers.txt
playwright install chromium
```
Il faut aussi **Google Chrome installé** (le harvester Datadome pilote le canal `chrome`,
pas le Chromium de Playwright — ce dernier est reconnu et bloqué).

### 1. Regarder qui d'autre travaille dans le dossier

```bash
git status --short          # des fichiers modifiés qui ne sont pas à toi = autre session
```
`backend/immobilier.db` **et** `data/data.json` sont des états **partagés**. Deux sessions
Claude qui collectent en parallèle se marchent dessus : c'est arrivé, 200 biens SeLoger
effacés (voir « Pièges » plus bas). Si l'arbre est sale et que ce n'est pas ton travail,
commence par te synchroniser avec l'autre session.

### 2. Générer les cookies Datadome

```bash
python scripts/datadome_cookies.py           # leboncoin + seloger
```
Écrit `backend/.env`. Sans cookie, leboncoin et seloger se déclarent **indisponibles** et
la collecte les saute silencieusement — vérifier :
```bash
python -c "from app.sources.leboncoin import LeboncoinSource as L; \
from app.sources.seloger import SeLogerSource as S; print('lbc', L().available, '| slg', S().available)"
```

### 3. Collecter, source par source

```bash
SCRAPER_RATE_LIMIT_MS=3000 EXPORT_NO_LIVE_OVERPASS=1 python collect_leboncoin.py
SCRAPER_RATE_LIMIT_MS=3000 EXPORT_NO_LIVE_OVERPASS=1 python collect_seloger.py
EXPORT_NO_LIVE_OVERPASS=1 python collect_littoral.py
```

Trois réglages qui ne sont pas décoratifs :

- **`SCRAPER_RATE_LIMIT_MS=3000`** — à ce rythme, 200 requêtes SeLoger passent sans
  incident. Plus vite, le cookie Datadome se brûle (§2).
- **`EXPORT_NO_LIVE_OVERPASS=1`** — l'export de fin de collecte n'interroge alors pas
  Overpass. Indispensable : le réchauffage (étape 4) écrit les mêmes fichiers de cache,
  et deux processus qui les écrivent en même temps s'écrasent.
- **laisser l'export se faire** (ne pas passer `--no-export` en comptant exporter plus
  tard). `data/data.json` est la **source de vérité durable** ; la base SQLite est un
  store de travail que la collecte suivante peut réinitialiser. Un bien collecté mais pas
  exporté n'existe pas.

### 4. Réchauffer les caches Overpass

```bash
OVERPASS_URL=https://overpass.osm.ch/api/interpreter python warm.py
```
Remplit `backend/data/poi_cache.json` (commerces, remontées) et `infra_cache.json`
(autoroute, rail, randonnées) — ce sont eux qui font passer les critères
*commerces / calme / rando* de « pending » à noté.

**Choisir le miroir Overpass est le réglage qui compte.** Mesuré sur ce jeu de données :

| Endpoint | Temps / requête | Taux de succès | 1 100 points |
|---|---|---|---|
| `overpass-api.de` (défaut) | ~13 s | **44 %** | ~4 h, incomplet |
| `overpass.osm.ch` | **0,4 s** | **100 %** | **3 min 41 s** |

L'instance principale est saturée et rejette (406/429) ; le miroir suisse encaisse le lot
entier sans un seul échec. `overpass.kumi.systems` et `overpass.private.coffee` étaient
tous deux en panne (500/502) au moment du test.

**Ne pas augmenter `WARM_WORKERS`** (2 par défaut) : c'est le nombre de slots
qu'Overpass accorde par IP, et au-delà les requêtes sont rejetées.

Le set 4 (« Littoral breton ») a **deux caches de plus**, sans lesquels ses deux critères
les plus lourds sortent en `n/a` :
```bash
python scripts/warm_sea_distance.py     # distance_mer  -> data/sea_cache.json
```
(`en_hauteur_geo` lit `data/relief_cache.json`, rempli à l'enrichissement.)

**Contrôle obligatoire** — le nombre d'entrées doit avoir augmenté :
```bash
python -c "import json; print(len(json.load(open('data/poi_cache.json'))), \
len(json.load(open('data/infra_cache.json'))))"
```
Si le run finit sur `⚠ N POI et M INFRA abandonnés`, relancer : `warm.py` est idempotent,
il ne redemande pas les points déjà en cache.

### 5. Export final, puis les pépites

Une fois les caches chauds, ré-exporter **sans** `EXPORT_NO_LIVE_OVERPASS` pour que les
critères Overpass soient pris en compte :
```bash
python -m app.services.export_static ../data
```
Puis, pour ne garder que le haut du panier d'un set (les autres sets sont préservés) :
```bash
EXPORT_MIN_MATCH_SCORE=<seuil> EXPORT_PRIMARY_SET_ID=<set> python -m app.services.export_static ../data
```
Calibrer le seuil en regardant la distribution avant de trancher, plutôt qu'en tâtonnant :
```bash
python -c "
import json; from collections import Counter
d=json.load(open('../data/data.json')); SET='4'
s=sorted(b['scores_by_set'][SET]['match_score'] for b in d['biens']
         if (b.get('scores_by_set') or {}).get(SET))
print([(round(x), sum(1 for y in s if y>=x)) for x in range(60, 95, 5)])"
```

Repères mesurés (jeu du 24 août 2026, 1 152 biens, caches Overpass chauds) :

| Set | Seuil | Nb de pépites |
|---|---|---|
| 4 — Bretagne sud | 76 | 30 |
| 4 — Bretagne sud | **78** | **18** ← cible ~15-20 |
| 4 — Bretagne sud | 80 | 9 |
| 1 — têtard | 78 | ~15 (repère d'août 2026) |

⚠️ L'export pépites **retire de `data.json` les biens du set primaire sous le seuil**. Le
catalogue complet reste dans la base SQLite : un `python -m app.services.export_static
../data` sans seuil le restaure. À ne pas lancer si une autre session travaille sur ce set.

### 6. Vérifier, puis committer

```bash
pytest                       # doit rester au vert
```
Committer `data/data.json` **avec** les photos qu'il référence et les caches réchauffés,
dans le même commit : c'est un instantané cohérent. Et **ne stager que ses propres
fichiers** si une autre session travaille en parallèle (`git add <chemins>`, jamais
`git add -A`).

**Ne pas committer `data/photos/` en entier.** Le dossier pèse 1 Go et l'essentiel ne sert
à rien : le site ne peut afficher que les biens présents dans `data.json`, donc après une
coupe pépites, les photos des biens écartés sont du poids mort. La règle est de suivre
exactement les fichiers que `data.json` référence — après un export pépites, ça se compte
en dizaines de fichiers et en méga-octets :

```bash
python -c "
import json, os, subprocess
d = json.load(open('data/data.json'))
refs = sorted({'data/' + p for x in d['biens'] for p in (x.get('photos') or [])})
tracked = set(subprocess.run(['git','ls-files','-z','--','data/photos'],
                             capture_output=True, text=True).stdout.split('\0'))
missing = [p for p in refs if p not in tracked]
print(len(missing), 'fichiers,', round(sum(os.path.getsize(p) for p in missing)/1e6, 1), 'Mo')
open('/tmp/photos_a_ajouter.txt','w').write('\n'.join(missing))"
xargs git add -- < /tmp/photos_a_ajouter.txt   # `xargs -a` n'existe pas sur macOS
```

`data.json` ne stocke que des chemins locaux (`photos/<source>_<id>/0.jpg`), jamais l'URL
de l'annonce : une photo non committée est une image manquante sur le site publié, pas un
repli silencieux.

### Pièges qui coûtent une collecte entière

| Symptôme | Cause | Parade |
|---|---|---|
| Des biens collectés disparaissent | une collecte a appelé `seed_from_data_json()`, qui **vide** la table avant de la reconstruire depuis `data.json` | exporter à chaque collecte ; `collect_seloger.py` ne seede que si la base est vide (`--reseed` pour forcer) |
| `warm.py` tourne 1 h et le cache ne grossit pas | `WARM_WORKERS` > 2 → Overpass répond 406/429, les erreurs étaient avalées | rester à 2 workers ; le message `⚠ … abandonnés` signale le rendement nul |
| Réchauffage interminable (~13 s/requête) même à 2 workers | l'instance `overpass-api.de` est saturée (44 % d'échecs) | `OVERPASS_URL=https://overpass.osm.ch/api/interpreter` (0,4 s/requête, 100 %) |
| Redirection vers `geo.captcha-delivery.com` | cookie Datadome brûlé par une rafale | `SCRAPER_RATE_LIMIT_MS=3000` ; regénérer le cookie |
| leboncoin/seloger ne ramènent rien, sans erreur | garde-fou `available` : ni proxy ni cookie | étape 2, et vérifier `available` |
| Critères commerces/calme/rando en « pending » | export fait avec `EXPORT_NO_LIVE_OVERPASS=1` et cache froid | étape 4 puis ré-export (étape 5) |
| Scores incohérents avec le code | `data.json` exporté avant un recalibrage du scoring | ré-exporter après tout changement de `scoring.py`/`preferences.py` |

---

## 1. Où faire tourner quoi : cloud vs local

Il y a **deux environnements** possibles pour Claude Code, et ils n'ont PAS les mêmes
capacités réseau. C'est la source de la plupart des confusions.

| Capacité | Conteneur cloud (claude.ai/code) | Local (VS Code / terminal `claude`) |
|---|---|---|
| `curl` / `httpx` (Python) sortant | ✅ (direct **et** via proxy) | ✅ |
| **Navigateur** (Chromium/Playwright) sortant | ❌ **aucun egress** (`ERR_CONNECTION_RESET` même sur example.com) | ✅ |
| IP de sortie | datacenter (bloquée par Datadome) | **résidentielle** (passe Datadome) |
| bienici (API JSON httpx) | ✅ marche | ✅ |
| leboncoin / seloger (Datadome) | ❌ (pas de cookie possible ici) | ✅ (cookie navigateur + IP résidentielle) |
| Supabase (API REST) | ✅ (réseau ouvert) | ✅ |

**Règle d'or : tout ce qui touche à un navigateur ou à Datadome (leboncoin, seloger)
DOIT se faire en local.** Le conteneur cloud ne peut pas piloter de navigateur (son
process réseau est cloisonné) et son IP datacenter est de toute façon bloquée par Datadome.

### Passer en local
- **Téléporter la session courante** (garde le contexte + la branche) : depuis un clone
  du repo, `claude --teleport` puis choisir la session (branche déjà poussée requise).
- **Ou** ouvrir le repo dans VS Code / lancer `claude` en local sur la branche de travail.

---

## 2. Collecte des annonces

Pipeline commun (par source) : *search → enrichissement → scoring par set → upsert →
export `data/data.json` (+ photos)*.

| Source | Accès | Contrainte |
|---|---|---|
| **bienici** | API JSON (`realEstateAds.json`) via httpx | Aucune — marche partout, **y compris cloud** |
| **leboncoin** | API JSON (`finder/search`) | Datadome → **cookie + IP résidentielle** (local) |
| **seloger** | HTML de la SERP `classified-search` | Datadome → **cookie + IP résidentielle** (local) |
| agences | IMAP + sites | dépend de la config IMAP |

Garde-fou `available` : leboncoin et seloger se déclarent **indisponibles** tant que ni
`PROXY_URL` ni le cookie Datadome (`LEBONCOIN_DATADOME` / `SELOGER_DATADOME`) ne sont
fournis — pour ne pas gaspiller d'appels qui renverraient 403.

### Datadome : générer et utiliser le cookie (EN LOCAL uniquement)

Automatisé — le script pilote le **Chrome installé** (canal `chrome`, et non le Chromium
de Playwright, que Datadome reconnaît), avec un profil persistant pour ne pas repasser le
challenge à chaque fois. S'il tombe sur un challenge humain, il attend qu'on le résolve
dans la fenêtre :

```bash
cd backend
python scripts/datadome_cookies.py                  # leboncoin + seloger
python scripts/datadome_cookies.py --site seloger   # un seul site
```

Il écrit `backend/.env` (gitignoré) : `LEBONCOIN_DATADOME`, `SELOGER_DATADOME` et
`SCRAPER_USER_AGENT`. Ce dernier compte : **Datadome recoupe le cookie avec l'UA** de la
requête, un UA qui ne correspond pas au navigateur émetteur fait retomber sur un challenge.

⚠️ **Le cookie est lié à l'IP.** Il faut le générer ET collecter depuis la **même IP
résidentielle**. Un cookie créé chez toi puis rejoué depuis le conteneur cloud
(IP datacenter) sera rejeté.

⚠️ **Le cookie se brûle sur une rafale de requêtes.** Vécu : une dizaine d'appels
rapprochés sur seloger et la réponse devient une redirection vers
`geo.captcha-delivery.com` — il faut alors relancer le script. En collecte, garder
`SCRAPER_RATE_LIMIT_MS=3000` (200 requêtes SeLoger passent sans incident à ce rythme).

### SeLoger : le portail a changé (2026)

`/list.htm` renvoie un **404 sec** et les annonces n'ont plus de JSON-LD par bien. Ce que
`backend/app/sources/seloger.py` cible désormais :

- **SERP** `GET /classified-search?distributionTypes=Buy&estateTypes=…&projectTypes=Resale&locations=…&page=N`,
  rendue côté serveur, ~30 cartes par page. Le parsing s'appuie sur les `data-testid`
  (`serp-core-classified-card-testid`, `cardmfe-price-testid`, `cardmfe-keyfacts-testid`,
  `cardmfe-description-box-address`) — **jamais sur les classes CSS**, qui sont des hachés
  Emotion regénérés à chaque build.
- **Vocabulaires** : `estateTypes` = `House | Apartment | Plot | Building | Parking`
  (`Land`/`Terrain` font répondre **500**) ; `projectTypes` = `Resale | New_Build |
  Projected | Life_Annuity` (on ne garde que `Resale` : le neuf est du promoteur).
- **Filtres réellement appliqués côté serveur** : `priceMin` / `priceMax` uniquement.
  `maximumPrice`, `surfaceMin`, `landSurfaceMin` sont acceptés puis **ignorés** — les
  surfaces se filtrent donc côté client.
- **`locations` n'accepte que les identifiants internes AVIV** (`AD08FR<n>` pour une
  commune) : ni code postal, ni code INSEE. On les résout via l'URL SEO de commune
  (`/immobilier/achat/immo-<slug>-<dept>/`), qui expose le `placeId` dans son HTML, et on
  met la correspondance en cache dans `backend/data/seloger_places.json`. Plusieurs
  `placeId` passent en une requête (résultats fusionnés) — d'où des lots de 15.
- **Limites connues** : les cartes n'exposent **pas de coordonnées** (on géolocalise au
  centroïde de commune, comme la source « agences ») ; et un code postal rural couvrant
  plusieurs communes, on ne retient que **la plus peuplée**
  (`services.geo_communes.main_commune_for_postcode`). Les *communes nouvelles* issues de
  fusions récentes n'ont pas d'URL SEO et sont sautées (vu sur Valserhône, Entrelacs,
  Saint-Genix-les-Villages, Val-d'Arc, Valromey-sur-Séran, Vallées-d'Antraigues-Asperjoc).

```bash
cd backend
SCRAPER_RATE_LIMIT_MS=3000 python collect_seloger.py --zone ploemeur --dry-run  # sans écrire
SCRAPER_RATE_LIMIT_MS=3000 python collect_seloger.py                            # collecte + export
```

### Zone « têtard » (référence)
Drôme/Ardèche/Savoie/Ain — maisons, budget ≤ 600 k€. Codes postaux et centres géo dans
`backend/collect_leboncoin.py` (`TETARD_ZIPS`) et déductibles des biens existants.

### Zone « Littoral breton » (set 4)
Terrains et petites maisons d'exception en bord de mer, budget ≤ 400 k€, collectés via
**bienici** pour le bâti historique du set, et via leboncoin/seloger pour le reste
(`collect_leboncoin.py`, `collect_seloger.py`, zone `ploemeur`) :
```bash
python backend/collect_littoral.py                 # collecte + enrichissement + export
python backend/collect_littoral.py --cap 40        # limite le nb de biens enrichis (test)
python backend/collect_littoral.py --rescore-only  # pas de collecte : re-note et ré-exporte
```
Quatre foyers de collecte (`PIVOTS`) : la Côte de Granit Rose (Perros-Guirec/Trégastel,
Plougrescant/Tréguier, Trébeurden/Lannion, dept 22) et le Morbihan (Ploemeur, plus la
vallée de la Laïta — à 12 km ce foyer couvre Quimperlé en amont et l'embouchure du Pouldu
en aval).

Le « plutôt côté mer, plutôt en hauteur » n'est pas un filtre de zone mais **deux critères
mesurés**, et c'est ce qui les rend fiables :

- **`distance_mer`** — distance réelle à la côte (`dist_mer_m`), pas une heuristique. Le
  long de la Laïta, l'embouchure (~0 km) prime sur l'amont (Quimperlé, ~12 km) sans qu'on
  ait à décider à la main ce qui compte comme littoral.
- **`en_hauteur_geo`** — proéminence réelle du relief IGN sur une couronne de 300 m. Sur
  une côte basse, l'altitude absolue ne dit rien ; la proéminence distingue le promontoire
  du terrain plat.

Les deux ont besoin de leurs caches, **à réchauffer avant l'export** sans quoi les critères
sortent en `n/a` :
```bash
python backend/scripts/warm_sea_distance.py            # -> backend/data/sea_cache.json
```
Incrémental et résumable : les points déjà en cache sont sautés.

Une version antérieure notait la côte via un référentiel de littoral fabriqué à la main
(critère `near_sea`, `build_littoral_dataset.py`). Elle a été retirée au profit de la
distance mesurée — voir l'historique git si le référentiel doit être ressorti.

### Export « pépites » (peu de biens, haut du panier)
L'export accepte un filtre optionnel qui ne conserve que les biens d'un set au-dessus
d'un seuil de score, **en préservant les autres sets** (ex. Pauline) :
```bash
EXPORT_MIN_MATCH_SCORE=78 EXPORT_PRIMARY_SET_ID=1 python -m app.services.export_static ../data
```
Repère de calibrage (dataset d'août 2026) : seuil **78 → ~15 pépites** têtard (set 1).

---

## 3. Supabase (votes & commentaires)

### Configuration
`config.js` (racine) porte `SUPABASE_URL` + `SUPABASE_ANON_KEY` (clé *publishable*,
publique, protégée par RLS). C'est ce que lit le front **et** les scripts d'analyse/backup.

Pour retrouver ces valeurs : dashboard Supabase → projet → **Settings → API** (URL du
projet + clé `anon`/publishable). Le **Project Ref** est le sous-domaine de l'URL.

### Pièges vécus
- **Projet en pause** : les projets gratuits s'endorment après ~1 semaine d'inactivité.
  Symptôme : l'app ne charge plus les votes. Fix : dashboard → *Restore/Resume*.
- **Projet supprimé / ref changé** : le host ne résout plus du tout (NXDOMAIN public).
  Diagnostic rapide :
  ```bash
  curl -s -H "accept: application/dns-json" \
    "https://dns.google/resolve?name=<ref>.supabase.co&type=A" | python3 -m json.tool
  # Status 3 = NXDOMAIN (projet mort)  |  Status 0 = existe
  ```
  Fix : créer/pointer le bon projet, mettre à jour `config.js`, recréer la table (ci-dessous),
  restaurer les votes.

### (Re)créer la table `votes`
La clé anon ne peut pas faire de DDL. Exécuter le SQL dans **SQL Editor** du dashboard :
`supabase/migrations/20260607000000_votes.sql` (table + RLS lecture/écriture anon).

Reset propre (repartir d'une table vide) : `delete from votes;` dans le SQL Editor.

---

## 4. Sauvegarde & restauration de la DB votes

**La DB ne doit jamais être perdue.** La source de vérité durable est un dump committé
dans git : `data/votes_backup.json`.

- **Sauvegarde manuelle** :
  ```bash
  python backend/scripts/backup_votes.py   # -> data/votes_backup.json  (puis git commit)
  ```
- **Sauvegarde automatique** : le workflow `.github/workflows/backup-votes.yml` tourne
  chaque jour (04:17 UTC) et committe le dump s'il a changé. Déclenchable à la main via
  l'onglet **Actions → Sauvegarde des votes Supabase → Run workflow**.
- **Restauration** (nouveau projet ou après incident) : recréer la table (§3) puis
  ```bash
  python backend/scripts/restore_votes.py            # upsert idempotent
  python backend/scripts/restore_votes.py --dry-run  # aperçu sans écrire
  ```
  La restauration est **non destructive** (merge sur la clé `bien_id,voter,criterion`).

Les scripts sont **sans dépendance** (urllib) et lisent `config.js` : ils tournent
partout (cloud ou local).

---

## 5. Convergence du scoring à partir des votes

Skill `converge-filters` (`.claude/skills/converge-filters/`) :
`python .claude/skills/converge-filters/analyze_votes.py` → rapport + `proposal.json`
→ décision groupe → `apply_proposal.py` → **recalcul de tous les biens** (ré-export).

⚠️ **Prérequis : il faut de vrais votes.** Sur une DB vide, il n'y a rien à faire
converger. La convergence n'a de sens qu'après que le groupe ait noté des biens.

---

## 6. Diagnostic réseau (mémo)

```bash
# Le proxy d'egress et ses derniers refus :
curl -sS "$HTTPS_PROXY/__agentproxy/status" | python3 -m json.tool

# Un host est-il joignable ? (403 = serveur répond ; 000 = pas de connexion ; 502 = refus gateway)
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://<host>/

# Egress DIRECT (sans proxy) :
https_proxy= HTTPS_PROXY= curl -s -o /dev/null -w "%{http_code}\n" https://example.com/
```

`403` d'un portail = anti-bot (Datadome/Cloudflare), pas un blocage réseau. `000` +
NXDOMAIN public = l'hôte n'existe pas. `502` de la gateway = refus de policy **ou**
échec upstream (host mort) — vérifier le DNS public pour trancher.
