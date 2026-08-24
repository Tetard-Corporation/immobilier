# Protocole d'exploitation (collecte, Supabase, sauvegardes)

Ce document capitalise les pièges rencontrés pour ne plus repartir de zéro. Lis-le
avant une collecte, une convergence de scoring, ou toute intervention sur Supabase.

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

### Zone « Bretagne sud » (set 4)
Terrains d'exception, budget ≤ 400 k€, collectés via **bienici** (seule source joignable
sans navigateur ni proxy) :
```bash
python backend/collect_bretagne_sud.py          # collecte + enrichissement + export
python backend/collect_bretagne_sud.py --cap 40 # limite le nb de biens enrichis (test)
```
Deux foyers de collecte (`ZONES`) : Ploemeur/littoral, et la vallée de la Laïta
(Quimperlé, Rédené, Clohars-Carnoët, Guidel, Gestel). Le « plutôt côté mer » n'est pas un
filtre de zone mais le critère `near_sea`, qui note la distance au littoral **ouvert** :
le long de la Laïta, l'embouchure (~0 km) prime sur l'amont (Quimperlé, ~12 km). Le
référentiel de côte est `backend/data/littoral_bretagne_sud.json`, régénérable via
`python scripts/build_littoral_dataset.py` (les rias en sont exclues, sinon Pont-Scorff
passerait pour du bord de mer).

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
