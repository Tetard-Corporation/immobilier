"""Registre des critères : une identité stable et une famille pour chaque mesure.

Un critère de set s'écrit aujourd'hui `{kind, label, weight, params}`. Le `label` est ce
que les gens lisent — et c'est très bien qu'il encode les paramètres (« Prix entre
180 000 € et 250 000 € » dit plus que « Budget »). Mais il ne peut pas servir d'IDENTITÉ :

- il change dès qu'un paramètre change (le budget est passé de 600 k€ à 250 k€ en trois
  tours de table), donc tout ce qui est rangé sous ce libellé se perd au tour suivant ;
- il diffère d'un set à l'autre pour LA MÊME mesure : `nuisance_sonore` s'appelle « Loin
  d'une route passante / autoroute / rail » dans têtard, « Au calme » chez Pauline et
  « Calme (loin autoroute/rail) » en Bretagne. Rien ne dit que c'est le même critère ;
- le `kind` seul ne suffit pas non plus : le set breton a cinq préférences `feature`
  différentes (bord de mer, bord d'eau, vue, en hauteur, isolé).

Ce registre donne donc à chaque critère :

- un `id` stable — le `kind`, ou `feature:<nom>` quand le kind est générique. C'est la clé
  sous laquelle chacun range ses poids personnels : elle survit à une reformulation du
  libellé et à un changement de paramètres, et elle est la même d'un set à l'autre ;
- une `famille`, pour qu'une vingtaine de critères se lise comme sept groupes ;
- un `court` : le nom canonique de la mesure, identique partout, qui rend les sets
  comparables entre eux ;
- un `quoi` : ce qui est réellement mesuré, en une phrase. Un critère qu'on pondère sans
  savoir ce qu'il mesure est un vote à l'aveugle.

Le registre est exporté dans `data.json` (clé `criteres`) : le front n'en tient pas une
copie, il lit celle-ci.
"""

from __future__ import annotations

# Ordre d'affichage : du plus décisif (ce qu'on paie, ce qu'on habite) au plus contextuel.
FAMILLES = [
    ("prix", "Prix & budget"),
    ("bien", "Le bien"),
    ("cadre", "Cadre & nature"),
    ("village", "Village & services"),
    ("acces", "Accès"),
    ("calme", "Calme & nuisances"),
    ("risques", "Risques & santé"),
    ("rendement", "Rendement locatif"),
]

# id -> (famille, nom court canonique, ce qui est mesuré)
CRITERES: dict[str, tuple[str, str, str]] = {
    # --- prix ---
    "budget": ("prix", "Budget", "Le prix affiché contre la fourchette du set ; hors budget = forte décote."),
    "rapport_qualite_prix": ("prix", "Rapport qualité/prix",
                             "Le prix au m² habitable contre les ventes réelles du secteur (DVF)."),
    "prix_m2_terrain": ("prix", "Prix du terrain au m²", "Le prix au m² de terrain contre un repère bon marché / cher."),
    # --- le bien ---
    "chambres_min": ("bien", "Nombre de chambres", "Le nombre de chambres, avec repli sur les pièces puis la surface."),
    "logement_compact": ("bien", "Format du logement",
                         "Un plafond de taille : ne récompense jamais le grand, décote l'immense."),
    "espace_modulable": ("bien", "Espace modulable en dortoir",
                         "Les volumes convertibles en couchages décrits par l'annonce : grange, "
                         "combles et sous-sol aménageables, dépendance, atelier, mezzanine."),
    "surface_habitable": ("bien", "Surface habitable", "La surface bâtie contre un plancher."),
    "light_works": ("bien", "Volume de travaux", "L'état lu dans l'annonce : habitable, à rafraîchir, à rénover, ruine."),
    "dpe": ("bien", "DPE", "La classe énergie de l'annonce ; F et G sont des passoires, à rénover pour louer."),
    "cachet": ("bien", "Cachet", "Le caractère décrit dans l'annonce (pierre, poutres…), contre le pavillon."),
    "authentic": ("bien", "Charme / authenticité", "La mention d'un caractère ancien ou authentique."),
    "pas_pavillon": ("bien", "Pas un pavillon", "Écarte le pavillon de lotissement récent."),
    "jardin": ("bien", "Jardin", "Un extérieur prouvé, avec une surface minimale."),
    "has_terrain": ("bien", "Terrain", "La surface de terrain contre un souhait."),
    "constructible": ("bien", "Constructible", "Le zonage PLU/GPU : la parcelle est-elle constructible."),
    # --- cadre & nature ---
    "relief_mountain": ("cadre", "Montagne / relief", "L'altitude du bien contre une altitude de référence."),
    "coin_nature": ("cadre", "Coin de nature", "Eau, bois, vue dégagée, altitude : la nature autour du bien."),
    "ensoleillement": ("cadre", "Exposition / soleil d'hiver",
                       "Les heures de soleil direct au 21 décembre, calculées sur le relief IGN."),
    "nature_exception": ("cadre", "Nature d'exception", "Un cadre remarquable relevé à l'enrichissement."),
    "distance_mer": ("cadre", "Proximité de la mer", "La distance réelle au trait de côte."),
    "en_hauteur_geo": ("cadre", "Position dominante", "La proéminence du point sur le relief réel."),
    "hiking": ("cadre", "Randonnées", "Les sentiers et itinéraires relevés autour du bien (OSM)."),
    "ski": ("cadre", "Station de ski", "La distance à la remontée mécanique la plus proche."),
    "feature:bord_de_mer": ("cadre", "Bord de mer", "L'annonce annonce une première ligne."),
    "feature:bord_eau": ("cadre", "Bord d'eau", "Rivière, étang ou ria mentionnés dans l'annonce."),
    "feature:vue": ("cadre", "Vue", "Une vue mer ou dégagée mentionnée dans l'annonce."),
    "feature:en_hauteur": ("cadre", "En hauteur (annonce)", "L'annonce dit le bien surélevé — non vérifié sur le relief."),
    "feature:isole": ("cadre", "Isolé / sauvage", "L'annonce décrit un bien isolé."),
    # --- village & services ---
    "village_vivant": ("village", "Village vivant", "Le nombre de commerces et services de la commune : ni désert, ni ville."),
    "commerces": ("village", "Commerces à proximité", "Le nombre de commerces et services relevés autour du bien."),
    "fiber": ("village", "Fibre", "La part des locaux de la commune éligibles à la fibre (Arcep)."),
    # --- accès ---
    "temps_acces": ("acces", "Temps d'accès", "Le temps porte-à-porte depuis la ville de départ."),
    "rail_time_from": ("acces", "Trajet en train", "La durée de trajet ferroviaire depuis la ville de départ."),
    "near_gare": ("acces", "Proximité d'une gare", "La distance à la gare la plus proche."),
    "near_city": ("acces", "Proximité d'une ville", "La distance à une ville donnée."),
    "near_corridor": ("acces", "Sur un axe", "La distance à un axe entre deux villes."),
    # --- calme & nuisances ---
    "tranquillite": ("calme", "Calme", "Calme, vis-à-vis et lotissement, tels que l'annonce les décrit."),
    "nuisance_sonore": ("calme", "Bruit (route, rail)", "La distance aux routes passantes et aux voies ferrées (OSM)."),
    "no_vis_a_vis": ("calme", "Sans vis-à-vis", "L'absence de vis-à-vis mentionnée dans l'annonce."),
    # --- risques & santé ---
    "risques_naturels": ("risques", "Risques naturels",
                         "Les aléas Géorisques de la commune, pondérés par leur gravité pour un logement."),
    "qualite_eau": ("risques", "Qualité de l'eau",
                    "Les relevés Hub'Eau du réseau : pesticides, nitrates, PFAS, conformité."),
    # --- rendement locatif ---
    "attractivite_airbnb": ("rendement", "Attractivité saisonnière",
                            "Ski, lac, hébergements et restaurants relevés autour du bien (OSM)."),
    "tension_locative": ("rendement", "Tension locative", "La tension du marché locatif de la commune."),
}

_FAMILLE_DEFAUT = "bien"


def identifiant(kind: str | None, params: dict | None = None) -> str:
    """Identité stable d'un critère. `feature` est générique : son nom la porte."""
    k = (kind or "").strip()
    if k == "feature":
        nom = ((params or {}).get("name") or "").strip()
        return f"feature:{nom}" if nom else "feature"
    return k


def fiche(crit_id: str) -> dict:
    """Famille, nom court et description d'un critère. Un critère inconnu du registre
    n'est pas une erreur : il est rangé dans « Le bien » et garde son id pour nom."""
    famille, court, quoi = CRITERES.get(crit_id, (_FAMILLE_DEFAUT, crit_id, ""))
    return {"famille": famille, "court": court, "quoi": quoi}


def registre() -> dict:
    """Le registre tel qu'il part dans data.json."""
    return {
        "familles": [{"id": fid, "label": lab} for fid, lab in FAMILLES],
        "index": {cid: {"famille": f, "court": c, "quoi": q} for cid, (f, c, q) in CRITERES.items()},
    }
