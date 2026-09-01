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

### 2 bis. L'entonnoir : filtrer avant d'enrichir

L'enrichissement coûte **~2,3 s par bien**, la mesure fine de la distance à la mer 4 appels
IGN de plus. Sur la collecte du littoral, 929 annonces ont été enrichies pour une vingtaine
de pépites : la quasi-totalité du temps est partie dans des biens que le score écartait
ensuite. `app/services/entonnoir.py` renverse l'ordre — filtrer d'abord, enrichir ensuite —
en quatre étages, du moins cher au plus précis :

| Étage | Ce qu'il regarde | Coût |
|---|---|---|
| 0 — annonce | texte, prix, surfaces | aucun appel |
| 1 — commune | distance à la mer du barycentre communal | 2 appels IGN **par commune** |
| 2 — point | enrichissement complet (`enrich_listing`) | ~2,3 s par bien |
| 3 — fin | distance mer et proéminence au point près | ~4 appels IGN par bien |

**L'étage 1 est celui qui change l'économie du pipeline.** La distance à la mer coûte le
même appel pour toutes les annonces d'une commune : quelques dizaines d'appels là où le
calcul au point près en demande des milliers, et il trie sur le critère qui décide vraiment.
Le cache `backend/data/commune_mer_cache.json` est permanent — une seconde collecte dans la
même région ne repaie pas cet étage.

```bash
python collect_littoral.py --max-km-mer 10     # écarte les communes à plus de 10 km (défaut)
python collect_littoral.py --max-km-mer 0      # étage commune sauté
python collect_littoral.py --keep 200          # plafond dur, appliqué à la note d'annonce
```

**Ne pas compter sur le texte pour choisir les pépites.** Mesuré sur 690 biens dont les
pépites étaient connues : garder les 60 meilleures annonces au texte n'en conservait que
**7 sur 18**, le top-150 onze, le top-300 treize. Les critères qui décident — distance à la
mer, proéminence du relief — sont *mesurés* et n'apparaissent jamais dans l'annonce.
L'étage 0 sert donc à écarter le rebut évident (pavillon neuf, lotissement viabilisé), pas
à sélectionner. Le tri par qualité, c'est l'étage 1.

**Deux garde-fous, parce qu'un entonnoir qui perd une pépite coûte plus qu'il ne rapporte :**

- un bien dont la commune n'a pas pu être mesurée est **retenu** — mieux vaut enrichir pour
  rien qu'écarter sur une mesure manquante ;
- un bien dont l'annonce parle de **bord d'eau** est retenu même en commune lointaine. Le
  set note `bord_eau` (rivière, étang, lac, ria, aber, estuaire) au poids 4 : l'eau du set
  ne se réduit pas à la mer, et la distance à la côte ne mesure pas les rivières.

Mesuré sur les 840 biens du set : 225 écartés, 2 repêchés au bord d'eau, **11 pépites sur
12 conservées**. La perdue (Caouënnec-Lanvézéac, 80,0) est à plus de 12 km de la mer et son
annonce ne mentionne aucune eau — elle montait sur le rapport qualité/prix et le terrain.
C'est le compromis assumé de l'étage : sur un set littoral, l'arrière-pays sort.

**Deux profils, parce que les deux sets ne se décident pas au même endroit.**

| Profil | Étage 0 (annonce) | Étage 1 (commune) |
|---|---|---|
| `littoral` (set 4) | écarte le rebut évident | distance à la mer |
| `montagne` (set 1, têtard) | écarte le rebut évident | **référence de prix DVF**, puis altitude |

Dans les deux cas, **l'étage 0 écarte, il ne sélectionne pas** — la tentation est de
croire que têtard échappe à la règle parce que budget, capacité et prix au m² sont des
champs bruts de l'annonce. C'est faux pour le prix : 2 700 €/m² est cher dans le Diois et
bon marché en Savoie du lac. Un prix au m² ne devient un critère qu'une fois rapporté à
son marché, et c'est ce que fait l'étage 1 (DVF par commune, 0,3 à 0,8 s, cache permanent).

```bash
python collect_tetard.py --min-altitude 250   # écarte les communes plus basses (défaut)
python collect_tetard.py --min-altitude 0     # étage altitude sauté
python collect_tetard.py --keep 700           # plafond, appliqué à la note d'annonce
```

**Calibrage du plafond : ne pas descendre sous 30 %.** Mesuré sur les 450 biens du set,
dont 12 pépites connues :

| On garde le top | pépites conservées |
|---|---|
| 5 % | 5/12 |
| 11,5 % | 7/12 |
| 20 % | 10/12 |
| **30 %** | **11/12** |

À 30 % la seule perdue est à 451 336 €, au-dessus du plafond budgétaire, donc écartée à
juste titre. La première collecte a coupé à 11,5 % et n'aurait gardé que 7 pépites sur 12.

Mêmes garde-fous que côté mer : une commune non mesurée est **retenue**, et une annonce
qui parle d'eau, de bois ou de vue dégagée est **repêchée** même en commune basse —
l'altitude ne mesure ni une rivière ni un point de vue sur la vallée.

⚠️ **Un étage qui échoue en silence ressemble trait pour trait à un étage qui ne trouve
rien.** L'API IGN plafonne les rafales : sur un lot de plusieurs centaines de communes,
l'étage altitude en a mesuré 25 puis écarté 3 biens sur 2 180 — et le garde-fou « commune
non mesurée = retenue » a fait passer ce no-op pour un run propre. L'entonnoir annonce
maintenant le nombre de communes non mesurées ; si la ligne `⚠ N communes non mesurées`
apparaît, l'étage n'a pas tranché et il faut relancer (les caches sont incrémentaux).

### 2 ter. Ajouter une agence locale

Le moteur ingère les sites d'agences par quatre voies, essayées dans cet ordre. Les trois
dernières ne demandent **aucun code** :

| Voie | Ce qu'elle lit | À écrire |
|---|---|---|
| A | JSON-LD sur la page de liste | rien (rare) |
| B | parser dédié au domaine | un parser dans `agences_parsers.py` |
| C | JSON-LD sur chaque fiche | rien |
| D | texte de chaque fiche, via l'extracteur | rien |

**Sonder avant d'ajouter**, avec le script prévu :

```bash
python scripts/probe_agence.py https://www.agence.fr/nos-biens
python scripts/probe_agence.py --cap 6 --fichier candidats.txt
```

Il répond à la seule question qui compte : combien de biens **avec prix ET commune** ce
site donne, et par quelle voie. Sans commune un bien n'est pas géocodable, donc invisible
pour les filtres de zone et le scoring — un site qui ne donne que des prix ne sert à rien.

Mesuré sur dix agences bretonnes et de prestige : **0 sur 10 par les voies A à C, 8 sur 10
en ajoutant la voie D**. Les sites d'agences n'exposent presque jamais de données
structurées, mais le prix et le code postal sont en clair dans la page. Les deux échecs
restants sont un site rendu en JavaScript et un serveur qui ne répond pas.

Ajouter ensuite l'agence dans `backend/agences.yaml` :

```yaml
  - nom: "Orpi Trégorimmo"
    set_id: 4          # sans lui, ses biens sont notés pour TOUS les sets
    sites:
      - https://www.orpi.com/orpi-tregorimmo/
```

`set_id` n'est pas décoratif : sans lui, `set_ids` reste vide et l'export note le bien
pour tous les sets — une agence bretonne apparaîtrait dans le set Drôme/Ardèche.

**Réseaux nationaux de prestige** (Safti Prestige, Barnes) : ils passent la voie D mais
leur flux n'est pas filtrable par région, donc la collecte ramène Paris et Béziers pour un
set breton. Les ajouter suppose soit une URL filtrée par région, soit un set dédié aux
biens d'exception, où le budget ne serait plus un critère éliminatoire.

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

🛑 **`overpass.osm.ch` ne couvre PAS la France. Ne pas l'utiliser.** Cette page l'a
recommandé, sur la foi d'un taux de succès de 100 % — mesuré sur le code HTTP, pas sur le
contenu. L'instance est suisse : sur un point français elle répond **200 avec zéro
élément**, réponse rigoureusement indiscernable de « il n'y a pas de commerce ici ». Un
réchauffage de 2 224 points s'est déclaré réussi en 7 minutes et a rempli le cache de
zéros ; 850 biens neufs se sont retrouvés à « zéro commerce », dont des bourgs à
supermarché, et le critère *village vivant* est tombé à 0,01 de moyenne sur tout le lot.
Le classement s'en est trouvé faussé de bout en bout, sans un seul message d'erreur.

| Endpoint | Temps / requête | Couverture France |
|---|---|---|
| **`overpass.openstreetmap.fr`** (défaut) | ~1 s | ✅ vérifiée contre le cache sain |
| `overpass-api.de` | ~13 s, souvent refusé | ✅ mais saturé (44 % d'échecs) |
| `overpass.osm.ch` | 0,4 s | ❌ **répond vide sur la France** |
| `kumi.systems`, `private.coffee`, `maps.mail.ru` | — | en panne (500/502/504) au test |

`warm.py` interroge maintenant un **point témoin** au démarrage — un bourg ardéchois dont
on sait qu'il a des commerces — et refuse de tourner si l'instance y répond zéro. Une
instance qui ment ne peut plus remplir le cache en silence.

**Ne pas augmenter `WARM_WORKERS`** (2 par défaut) : c'est le nombre de slots
qu'Overpass accorde par IP, et au-delà les requêtes sont rejetées.

Le set 4 (« Littoral breton ») a **deux caches de plus**, sans lesquels ses deux critères
les plus lourds sortent en `n/a` :
```bash
python scripts/warm_sea_distance.py     # distance_mer  -> data/sea_cache.json
```
(`en_hauteur_geo` lit `data/relief_cache.json`, rempli à l'enrichissement.)

Le set 1 (« têtard ») en a **deux**, pour `ensoleillement` et `attractivite_airbnb` :
```bash
python scripts/warm_ensoleillement.py   # -> data/soleil_cache.json
python scripts/warm_tourisme.py         # -> data/tourisme_cache.json
```
87 points d'altitude IGN par bien (4 requêtes groupées, ~5 s) : c'est ce qui donne les
heures de soleil direct au 21 décembre, l'orientation et la pente du versant. Trop cher
pour l'export d'un catalogue entier, donc **l'export lit le cache et ne mesure jamais en
direct** — exactement comme la distance à la mer. Le piège est le même que partout
ailleurs ici : sans réchauffage le critère sort en `pending`, il est alors *exclu* du score
au lieu de le baisser, et rien ne le dit. Le contrôle tient en une ligne — le fond des
gorges reçoit 0 h de soleil, l'adret 6 à 8 :
```bash
python -c "import json; c=json.load(open('data/soleil_cache.json')); \
h=sorted(v['soleil_hiver_h'] for v in c.values()); \
print(len(h), 'points ; min', h[0], 'médiane', h[len(h)//2], 'max', h[-1])"
```

`warm_tourisme.py` relève, autour de chaque point, la remontée mécanique la plus proche
(25 km), le lac (12 km), les hébergements touristiques (5 km), les restaurants (3 km) et
les sites (10 km) — c'est-à-dire les quatre choses qui font qu'un logement se loue à la
semaine. Une requête Overpass par point, ~5 s.

**Il réchauffe les CANDIDATS, pas le catalogue.** 5 300 biens du set × 5 s font sept
heures, pour un panier d'une quinzaine de pépites et d'un témoin par massif. Le script
applique donc le même raisonnement que `services/entonnoir.py` — filtrer avant de payer
la mesure chère : les N mieux notés de chaque zone, plus tout ce qui dépasse un plancher.

```bash
python scripts/warm_tourisme.py                          # défaut : 25 par zone, ≥ 70
python scripts/warm_tourisme.py --par-zone 40 --score-min 65
python scripts/warm_tourisme.py --tout                   # sans entonnoir (des heures)
```

Ce choix a une conséquence, et elle est fermée côté scoring plutôt qu'ici : un bien non
mesuré verrait le critère sortir en `pending`, donc **exclu du score au lieu de le
baisser**, et il monterait. Le huitième palier du set exige la mesure au-dessus de 78
(seuil 0 : on demande le relevé, pas une bonne note). Un bien non mesuré plafonne donc
à 78 et ne peut pas déloger un bien mesuré.

Contrôle — la note doit s'étaler, pas se tasser :
```bash
python -c "import json; from app.services.tourisme import noter; \
c=json.load(open('data/tourisme_cache.json')); \
n=sorted(noter(v)['note'] for v in c.values()); \
print(len(n), 'points ; min', n[0], 'médiane', n[len(n)//2], 'max', n[-1])"
```

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
Puis, pour ne garder que le haut du panier (les sets non cités sont préservés) :
```bash
EXPORT_PEPITES="1:78.5,4:80" python -m app.services.export_static ../data
```

⚠️ **Citer TOUS les sets déjà resserrés, pas seulement celui sur lequel on travaille.**
La base garde le catalogue complet de chaque set quand `data.json` n'en publie que le haut
du panier : le set 4 y pèse 901 biens pour 12 publiés. Un export qui ne resserre que le
set 1 republie donc les 889 autres et annule le resserrage breton — sans erreur, sans
avertissement, et c'est le site qui le dit à ta place.

**Republier un set à l'identique** plutôt que le recouper, quand une correction de
données a déplacé ses scores :
```bash
EXPORT_CONSERVER="4:../data/data.json" python -m app.services.export_static ../data
```
Le set 4 est alors republié exactement tel qu'il l'était, quels que soient les nouveaux
scores. C'est le bon outil quand une réparation profite à un set qu'on n'est pas en train
de retravailler : après la correction Overpass, la règle « ≥ 80 » du set breton
sélectionnait 32 biens au lieu de 12. Recouper le set de quelqu'un d'autre au passage
n'est pas une décision qui se prend à l'export.

**Publier le meilleur bien de chaque massif**, en plus des pépites :
```bash
EXPORT_PEPITES="1:80,4:80" EXPORT_MEILLEUR_ZONE="1:70" python -m app.services.export_static ../data
# ou, depuis le collecteur :
python collect_tetard.py --rescore-only --pepites "1:80,4:80" --meilleur-zone 1:70
```
Le haut du panier, seul, ne montre qu'une chose : les secteurs où le budget achète
quelque chose. Rien ne dit alors ce que 250 k€ donnent en Tarentaise, au bord du Léman ou
dans le Queyras. `EXPORT_MEILLEUR_ZONE="1:70"` ajoute le meilleur bien de chacune des
39 zones du set, **même sous le seuil des pépites**, à condition qu'il tienne le plancher
(70 = les paliers budget, travaux et jardin sont passés). Une zone qui n'a rien au-dessus
n'a pas de témoin, et l'export le dit :

```
témoins de zone (set 1) : 21 zones publiées · 18 sans rien au-dessus de 70 : Chamonix, …
```

Sur le site, ces biens portent un badge « ⛰ témoin » et un sélecteur « Massif » permet de
les comparer entre eux. Ils comptent dans `stats.n_temoins_zone`.

L'ancienne écriture pour un seul set reste acceptée :
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
| 4 — Littoral breton | 78 | 18 (jeu du 24 août) |
| 4 — Littoral breton | **80** | **12** ← publié |
| 1 — têtard | 84,8 | 13 (jeu du 27 août, 1 300 biens, plafond 450 k€) |
| 1 — têtard | 82,5 | 12 (jeu du 28 août, 3 310 biens, plafond 300 k€, 5 paliers) |
| 1 — têtard | **82** | **13** ← publié, cible 12-15 |
| 1 — têtard | 81,5 | 15 |

⚠️ **Le seuil du set 1 est à recalibrer** depuis le tour du 30 août : plafond ramené à
250 k€, format plafonné (5 chambres max), jardin exigé, critère `ensoleillement` ajouté (7 paliers
au lieu de 5). Sur les 14 biens publiés, six sortent du budget et sont plafonnés à 70 : à
82 le panier serait vide. Recalibrer avec la distribution (commande ci-dessus) après la
première collecte complète, plutôt que de reconduire l'ancien seuil.

⚠️ Les seuils ne sont pas transposables d'un jeu à l'autre : après la correction Overpass,
le même seuil de 80 est passé de 12 à 32 pépites côté breton. **Recalibrer sur la
distribution du moment**, à chaque fois.

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
| Tous les biens d'une agence au même prix rond | l'extracteur a lu le **décor du site** : borne d'un curseur de recherche (« Prix compris entre 0 € et 1 000 000 € »), garantie financière. Le prix retenu est le plus gros montant plausible de la page, donc le décor gagne contre le bien | corrigé (les valeurs annoncées par un intervalle sont disqualifiées) ; second filet : l'ingestion écarte tout prix répété sur la moitié d'un catalogue |
| Des milliers de biens disparaissent après une collecte leboncoin | `seed_from_data_json()` **vide** la table et la reconstruit depuis `data.json` — qui ne contient plus que les pépites | corrigé (`seed_if_empty()` par défaut, `--reseed` pour forcer) ; vérifier le compte en base avant/après |
| Des biens SeLoger sans aucune photo | le parseur de cartes ne lisait pas les `<img>` | corrigé ; pour les biens déjà en base, rafraîchir leur `raw` sans ré-enrichir |
| Des biens collectés disparaissent | une collecte a appelé `seed_from_data_json()`, qui **vide** la table avant de la reconstruire depuis `data.json` | exporter à chaque collecte ; `collect_seloger.py` ne seede que si la base est vide (`--reseed` pour forcer) |
| `warm.py` tourne 1 h et le cache ne grossit pas | `WARM_WORKERS` > 2 → Overpass répond 406/429, les erreurs étaient avalées | rester à 2 workers ; le message `⚠ … abandonnés` signale le rendement nul |
| Réchauffage interminable (~13 s/requête) même à 2 workers | l'instance `overpass-api.de` est saturée (44 % d'échecs) | `OVERPASS_URL=https://overpass.osm.ch/api/interpreter` (0,4 s/requête, 100 %) |
| Redirection vers `geo.captcha-delivery.com` | cookie Datadome brûlé par une rafale | `SCRAPER_RATE_LIMIT_MS=3000` ; regénérer le cookie |
| leboncoin/seloger ne ramènent rien, sans erreur | garde-fou `available` : ni proxy ni cookie | étape 2, et vérifier `available` |
| Critères commerces/calme/rando en « pending » | export fait avec `EXPORT_NO_LIVE_OVERPASS=1` et cache froid | étape 4 puis ré-export (étape 5) |
| Scores incohérents avec le code | `data.json` exporté avant un recalibrage du scoring | ré-exporter après tout changement de `scoring.py`/`preferences.py` |
| Une correction de `classify.py` ne change rien sur le site | `condition`/`niveau_travaux` sont des **colonnes**, écrites une fois à la collecte ; l'export les relit sans reclasser | `python scripts/reclasser.py --dry-run` puis sans `--dry-run`, avant de ré-exporter |
| Des ruines et des mobil-homes en haut du classement | Un prix très bas est noté comme une bonne affaire par `budget` **et** par `rapport_qualite_prix` (poids 5) : deux critères sur trois du haut du barème récompensent le défaut qu'ils devraient signaler | plancher de prix dans `budget` (`budget_min`) **et** palier au niveau du panier ; le défaut d'un bien bon marché n'est jamais écrit dans l'annonce, aucun critère mesuré ne le rattrape |
| Une annonce annonce 4 pièces dans 35 m² | Le repli « pièces − 1 » du critère de chambres ne recoupait rien : il accordait 3 chambres à un mobil-home | `m2_min_par_piece` (20 m² par pièce, communs compris) borne l'estimation par la surface |
| Un critère au poids fort à `pending` sur la moitié du lot | `pending` est **exclu** du score, pas compté zéro : un bien non mesuré monte au lieu de descendre | réchauffer (§4) ; et poser un palier « critère mesuré » comme le fait le set 1 pour le rapport qualité/prix et l'attractivité |

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

### Zone « têtard » (set 1)
Maison de retrait entre copains — Alpes et Préalpes à l'est de l'axe Lyon-Valence (Drôme
est, Isère, Savoie, Haute-Savoie, Hautes-Alpes, Ain), à moins de **4h30** porte-à-porte de
Paris, **budget ≤ 250 k€** (600 k€, puis 450 k€, puis 300 k€ en août 2026).

Quatre sources : **bienici** (pivots montagne, sans cookie), **leboncoin** et **seloger**
(cookie Datadome, §2), et six **agences** de la zone (`agences.yaml`).

```bash
python backend/collect_tetard.py                 # collecte + enrichissement + export
python backend/collect_tetard.py --pivot diois   # un seul foyer de collecte
python backend/collect_tetard.py --rescore-only  # pas de collecte : re-note et ré-exporte
```

**Trente-neuf foyers de collecte** (`PIVOTS`), en deux groupes.

*Le cœur du set* (16), sur la partie montagne des départements couverts et sous les
4h30 : Diois, Vercors (drômois et isérois), Chartreuse, Trièves, Matheysine, Oisans,
Belledonne, Bauges, Maurienne, Aravis, Dévoluy, Bugey — et, depuis le 30 août,
Albertville, le Beaufortain et le Val d'Arly, demandés par le groupe. Les points de
départ ont quitté la vallée du Rhône, d'où venait l'essentiel de l'ancien haut de
classement (Châteauneuf-sur-Isère, 154 m d'altitude, était premier).

*Le reste des Alpes* (23, ajoutés le 31 août) : lac du Bourget, avant-pays savoyard,
Tarentaise, Haute-Tarentaise, Vanoise, Annecy, Faucigny, Chablais, Léman, Mont-Blanc,
Chamonix, Briançonnais, Queyras, Champsaur, Embrunais, Buëch, Baronnies, Ubaye,
Haute-Provence, Verdon, Lure-Forcalquier, Mercantour, Alpes d'Azur. Ces foyers-là ne sont
pas là pour produire des pépites — la plupart sortent des 4h30 et `temps_acces` les note
en conséquence. Ils répondent à une question que le classement seul ne pose jamais : à
250 k€, qu'est-ce qu'on a en Tarentaise, dans le Queyras, dans l'Ubaye ? L'export en
publie le meilleur bien (§5, `EXPORT_MEILLEUR_ZONE`).

Chaque pivot est aussi une **zone de comparaison** (`ZONES`) : un bien est rattaché au
pivot le plus proche dans un rayon de 30 km, ce qui partitionne les Alpes en massifs
disjoints. Le rattachement est déclaré dans les critères du set, donc il se réapplique à
chaque export sans repasser sur la base.

Le Beaufortain (4h10 porte-à-porte) et le Val d'Arly (4h06) dépassent la consigne
initiale des 4h ; c'est pourquoi `temps_acces` est passé à 4h30. Collecter une zone puis
la noter zéro sur un critère de poids 3 n'aurait servi à rien — le barème reste
décroissant et continue de préférer le proche (0,60 à 3h, 0,13 à 4h10).

Les codes postaux leboncoin restent dans `backend/collect_leboncoin.py` (`TETARD_ZIPS`).

**Ce que le set note, et pourquoi.** Deux critères mènent le classement — ce que le bien
vaut pour son prix, et ce qu'on a devant la porte :

- **`rapport_qualite_prix`** (poids 5) — prix au m² du bien contre celui des ventes du
  secteur (DVF). C'est le ratio qui parle, pas le prix absolu : 2 000 €/m² est cher en
  Ardèche et donné au bord du lac du Bourget.
- **`coin_nature`** et **`relief_mountain`** (poids 4) — « l'accès à la nature/montagne
  grand OUI ». Mesurés, pas devinés dans le texte.
- **`tranquillite` avec `poids_isolement: 0`** — « mais pas isolé ». Le critère garde le
  vis-à-vis et le lotissement, il cesse de récompenser le bout du monde.
- **`chambres_min` (min 3)** — « 3/4 chambres ». Le critère se replie sur les pièces puis
  sur la surface : sans ce repli il était `n/a` sur la moitié des annonces, donc neutre,
  et une maison d'**une seule pièce** est arrivée deuxième du classement.
- **`logement_compact` (idéal 4, max 5)** — « pas des maisons immenses », précisé en
  « 5 chambres ça reste ok, mais qu'on ne survalorise pas les biens grands : un petit
  3 chambres bien placé vaut mieux qu'un grand mal placé ». Le critère ne récompense
  **jamais** la taille, il décote l'immense : 3 et 4 chambres à 1,0, 5 à 0,75, 6 à 0,375.
  Le set n'avait qu'un plancher, et les pépites publiées allaient jusqu'à 7 chambres pour
  268 m². `surface_habitable` est retombé au poids 1 (seuil 90 m²) pour la même raison :
  c'était le dernier endroit où la taille payait pour elle-même.
- **`ensoleillement`** (poids 4) — exposition et **durée** de soleil, mesurées sur le
  relief IGN et non lues dans l'annonce (« plein sud » y est un argument de vente).
  Heures de soleil direct au 21 décembre, orientation et pente du versant : mesuré sur
  les pivots, le fond des gorges de l'Arly reçoit **0 h**, l'adret de Maurienne **6 h**,
  à altitude et prix au m² comparables. Demande le cache réchauffé (§4).
- **`budget`** (poids 4) — une **fourchette**, 180 à 250 k€, et non un plafond.
  « Pas de secret : quand un bien est à 100 ou 150 k€, c'est qu'il y a un problème ;
  a priori on n'aura rien qui nous intéresse en dessous de 180 k€ » (1er septembre). Le
  critère notait auparavant 1,0 tout ce qui passait sous 70 % du budget : le bon marché
  était un avantage, et un mobil-home à 75 000 € marquait autant qu'une maison à
  175 000 €. Sous le plancher la note tombe d'un cran net (0,78) puis décroît jusqu'à
  0,15 à mi-plancher. Le plancher **ne filtre pas la collecte** : le meilleur bien d'un
  massif pauvre reste une information.
- **`attractivite_airbnb`** (poids 4) — ce que le bien peut se louer à la semaine, mesuré
  et non lu dans l'annonce (« idéal investissement locatif » y est un argument de vente).
  Quatre relevés OSM : remontée mécanique (l'hiver), lac et sites (l'été), hébergement
  touristique déjà installé (le marché existe), restaurants (ce qu'il faut sur place), plus
  une prime pour les endroits qui ont les deux saisons. Relevé sur les pivots : Chamonix
  1,00 · Aix-les-Bains 1,00 · Beaufort 0,92 · Barcelonnette 0,80 · Jarrier 0,78 · Die 0,71 ·
  Hauteville-Lompnes 0,23. Demande le cache réchauffé (§4). **La densité d'hébergements
  mesure l'offre, pas la demande** : c'est le compromis assumé du critère, aucune source
  ouverte ne donne le taux d'occupation d'une commune.
- **`jardin` (≥ 300 m²)** + palier — « jardin requis ». Distinct de `has_terrain`, qui
  reste le souhait de GRAND terrain : ici c'est un plancher. Une annonce qui décrit un
  extérieur sans en donner la surface note 0,7 (assez pour le palier) ; une qui n'en dit
  rien vaut `n/a`, et `n/a` ne remplit pas une exigence.

**Huit paliers** (`EXIGENCES`) ferment les portes par lesquelles un bien mal mesuré —
ou hors sujet — monte au classement :

| Au-dessus de | Il faut | Pourquoi |
|---|---|---|
| 70 | être dans le budget | Le critère budget est **pondéré** : il pénalise le dépassement sans l'exclure, et un bien excellent partout ailleurs le compense sans peine. Mesuré : sept des treize premières pépites dépassaient le plafond, jusqu'à +39 %. Un plafond doit être un palier, pas un poids. |
| 70 | pas de gros travaux | Même raison : `light_works` étant pondéré, une ruine bien placée et bon marché se rattrapait ailleurs. Seuil 0,85 sur le barème du critère — habitable, à rafraîchir et à rénover passent ; gros travaux (0,4) et ruine (0,1) non. **L'état non renseigné ne valide pas non plus**, et c'est 45 % des annonces : le palier coûte cher, c'est assumé. Sur un site fait pour voter, un bien dont on ignore l'état ne se juge pas — comme un bien sans photo. |
| 70 | un jardin | `has_terrain` était pondéré, donc rattrapable : un bien sans extérieur restait éligible. Seuil 0,5 = ~110 m² mesurés, ou un extérieur décrit sans surface. |
| 75 | trois chambres avérées | Sinon une maison d'une seule pièce finit deuxième. |
| 85 | un format de maison de retrait | Le plafond que le set n'avait pas, et il ne ferme la porte qu'à l'immense : 0,7 sur `logement_compact` laisse passer 5 chambres (0,75) et arrête à 6 (0,375), ou ~210 m² quand les chambres manquent. Palier haut (85, pas 78) parce que « 5 chambres ça reste ok » : ce n'est qu'en tête de classement que le format cesse d'être négociable. |
| 78 | un rapport qualité/prix mesuré | Sans surface bâtie il n'y a rien à comparer, et le bien montait précisément parce qu'il était peu mesuré. |
| 78 | un prix ≥ 180 k€ | Le pendant BAS du plafond budgétaire, et il tient au même raisonnement : un prix est un palier, pas un poids. Le problème d'un bien à 130 k€ n'est jamais écrit dans l'annonce — c'est pourquoi aucun critère mesuré ne le rattrape, et pourquoi le rapport qualité/prix (poids 5) le récompense au contraire : à 739 €/m² contre 1 462 dans le secteur, une maison coupée en deux logements au bout d'un chemin note 1,0. Palier haut (78) et non 70 : sous 180 k€ un bien peut rester le **témoin** de son massif — c'est même l'information que les témoins servent à donner. Ce qu'on refuse, c'est qu'il monte dans le panier. |
| 78 | une attractivité locative mesurée | Même raison, pour le critère le plus cher à relever (~5 s Overpass par point, donc réchauffé sur les seuls candidats). Seuil 0 : on exige le relevé, pas une bonne note. Un bien dans un coin sans tourisme reste recevable ; ce qu'on refuse, c'est qu'il passe devant un autre parce qu'on ne l'a pas regardé. |
| 85 | une nature ou un relief avérés | Un critère jamais mesuré ne prouve rien. |

**Trois types de biens sont conservés mais plafonnés très bas** (facteur 0,15 à 0,2 sur
le match), parce que le prix affiché ne décrit pas ce qu'on achète : le **viager /
nue-propriété** (le prix est le bouquet, le bien reste occupé), la **résidence de
tourisme** sous bail commercial (jouissance restreinte, gestion imposée), et le
**mobil-home / emplacement de camping** — habitation légère de loisirs, parc résidentiel
de loisirs (on n'achète pas le sol, l'occupation est saisonnière, la revente se fait à
perte). Le troisième a été ajouté le 1er septembre après qu'un « chalet de montagne de
4 pièces de 35 m² situé au camping "la motte flottante" », 75 000 €, soit entré dans les
pépites à 81,3. Détection sur le texte : « camping » seul ne suffit pas — « à 2 km d'un
camping » est un argument de voisinage —, il faut que le bien soit *dans* le camping ou
qu'il se nomme lui-même. 29 biens concernés sur les 7 086 en base.

**Les agences de la zone** (`set_ids: [1]`) : Agence Cévenole, Bauges Immobilier,
Christine Miranda, Espaces Atypiques Drôme-Ardèche, Diois Immobilier, Orpi Ain Agences.

Sondées et **écartées**, pour mémoire : Groupe Mercure démarre à 1,2 M€ (un réseau de
prestige n'apporte rien sous 250 k€) ; API Pélussin publie des loyers ; Nestenn Yssingeaux
et GTI ne donnent pas la commune ; Le Rouge et le Noir est rendu en JavaScript.

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
Voir §5 ci-dessus : `EXPORT_PEPITES="1:78.5,4:80"`, et **citer tous les sets déjà
resserrés** sous peine de republier leur catalogue complet.

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
