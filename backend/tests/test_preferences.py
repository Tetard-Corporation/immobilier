from app.schemas import Preference
from app.services.brief import _heuristic_parse, parse_brief
from app.services.gares import nearest_gare
from app.services.geo import distance_to_corridor_km, resolve_city
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="terrain")
    base.update(kw)
    return NormalizedListing(**base)


def test_geo_corridor_et_gare():
    paris, marseille = resolve_city("paris"), resolve_city("marseille")
    # Lyon est proche de l'axe Paris-Marseille
    d = distance_to_corridor_km(45.764, 4.835, [paris, marseille])
    assert d is not None and d < 60
    g = nearest_gare(45.764, 4.835)
    assert g is not None and g[1] < 10  # Lyon Part-Dieu tout proche


def test_budget_et_chambres():
    item = _listing(prix=120000, nb_chambres=5, surface_terrain=800, flags={})
    prefs = [
        Preference(kind="budget", weight=2, params={"apport": 150000, "levier": 1}),  # budget 150k
        Preference(kind="chambres_min", weight=2, params={"min": 5}),
        Preference(kind="has_terrain", weight=1),
    ]
    score, details = evaluate(item, prefs)
    assert score is not None and score >= 90
    assert all(d["status"] == "ok" for d in details)


def test_budget_depasse_baisse_le_score():
    cher = _listing(prix=400000, flags={})
    pas_cher = _listing(prix=100000, flags={})
    p = [Preference(kind="budget", params={"budget_max": 150000})]
    assert evaluate(cher, p)[0] < evaluate(pas_cher, p)[0]


def test_pending_quand_provider_absent():
    item = _listing(latitude=45.0, longitude=4.0, flags={})
    score, details = evaluate(item, [Preference(kind="fiber"), Preference(kind="rail_time_from", params={"ville": "Paris"})])
    statuses = {d["kind"]: d["status"] for d in details}
    assert statuses["fiber"] == "pending"
    assert statuses["rail_time_from"] == "pending"
    assert score is None  # aucune préférence évaluable -> pas de score


def test_feature_non_mentionnee_est_neutre():
    # Une feature non citée dans l'annonce ne doit PAS pénaliser (n/a, exclue du score).
    item = _listing(flags={"features": []})
    score, det = evaluate(item, [Preference(kind="feature", weight=2, params={"name": "cheminee"})])
    assert det[0]["status"] == "n/a"
    assert score is None
    item2 = _listing(flags={"features": ["cheminee"]})
    assert evaluate(item2, [Preference(kind="feature", params={"name": "cheminee"})])[0] == 100.0


def test_chambres_sous_minimum_degrade_lineaire():
    # Porte sur le SOUS-score : le match_score, lui, passe par l'étirement d'échelle.
    p = [Preference(kind="chambres_min", weight=1, params={"min": 4})]

    def sub(nb):
        return evaluate(_listing(nb_chambres=nb, flags={}), p)[1][0]["subscore"]

    assert sub(3) == 0.75   # 3/4
    assert sub(2) == 0.5    # 2/4
    assert sub(5) == 1.0    # >= min


def test_temps_acces_porte_a_porte():
    # Valence (sur l'axe, près du hub TGV) -> porte-à-porte court -> bon score.
    valence = _listing(latitude=44.93, longitude=4.89, flags={})
    p = [Preference(kind="temps_acces", params={"max_minutes": 240})]
    score, details = evaluate(valence, p)
    assert score is not None and details[0]["status"] == "ok"
    assert "porte-à-porte" in details[0]["detail"]
    # un point très loin de tout hub -> score plus faible
    brest = _listing(latitude=48.39, longitude=-4.48, flags={})
    assert evaluate(valence, p)[0] > evaluate(brest, p)[0]


def test_isole_renforce_par_densite():
    from app.enrichment.densite import isolement_score
    assert isolement_score(150) == 1.0
    assert isolement_score(20000) == 0.0
    assert 0 < isolement_score(2000) < 1
    # préférence feature=isole : commune peu peuplée -> bon score même sans mot-clé
    item = _listing(flags={"features": [], "isolement_score": 0.9, "population_commune": 180})
    score, det = evaluate(item, [Preference(kind="feature", params={"name": "isole"})])
    assert score >= 80 and "180 hab" in det[0]["detail"]


def test_brief_detecte_temps_acces():
    kinds = {p["kind"] for p in _heuristic_parse("Maison à 4h porte à porte de Paris, au calme")}
    assert "temps_acces" in kinds


def test_socio_preferences():
    item_data = _listing(latitude=48.85, longitude=2.35, flags={"pop_jeune_score": 0.8, "orientation_gauche_score": 0.6})
    p = [Preference(kind="population_jeune"), Preference(kind="orientation_gauche")]
    score, details = evaluate(item_data, p)
    assert score is not None and all(d["status"] == "ok" for d in details)
    # sans données socio -> pending
    item_vide = _listing(latitude=48.85, longitude=2.35, flags={})
    _, det = evaluate(item_vide, p)
    assert all(d["status"] == "pending" for d in det)


def test_brief_jeune_gauche():
    kinds = {p["kind"] for p in _heuristic_parse("commune jeune et à gauche, proche gare")}
    assert {"population_jeune", "orientation_gauche", "near_gare"} <= kinds


def test_corridor_paris_marseille_suit_la_vallee_du_rhone():
    # Un bien en vallée du Rhône (Valence) doit être bien plus proche de l'axe réel
    # qu'avec la ligne droite Paris-Marseille (qui passe par le Massif Central).
    from app.services.preferences import _corridor_points
    from app.services.geo import distance_to_corridor_km
    pts = _corridor_points({"villes": ["Paris", "Marseille"]})
    # Lyon, Valence, Avignon insérés -> au moins 6 points.
    assert len(pts) >= 6
    valence = (44.93, 4.89)
    d = distance_to_corridor_km(valence[0], valence[1], pts)
    assert d < 15  # sur l'axe rhodanien


def test_corridor_preference_score():
    lyon = _listing(latitude=45.764, longitude=4.835, flags={})
    brest = _listing(latitude=48.39, longitude=-4.48, flags={})
    p = [Preference(kind="near_corridor", params={"villes": ["Paris", "Marseille"], "max_km": 80})]
    assert evaluate(lyon, p)[0] > evaluate(brest, p)[0]


def test_brief_heuristique_exemple():
    brief = (
        "Sur l'axe Paris Marseille, plutôt isolé dans un coin nature montagneux pour des "
        "randonnées. À proximité d'une gare. Légers travaux possibles, avec du terrain. "
        "Sans vis à vis. Pour au moins 6 personnes en chambre. La fibre pour télétravailler. "
        "Le temps de trajet en train depuis Paris doit être raisonnable. Un terrain "
        "d'exception authentique. Budget via SCI avec 150000€ d'apports."
    )
    kinds = {p["kind"] for p in _heuristic_parse(brief)}
    assert {
        "budget",
        "chambres_min",
        "has_terrain",
        "light_works",
        "no_vis_a_vis",
        "nature_exception",
        "near_gare",
        "fiber",
        "relief_mountain",
        "hiking",
        "rail_time_from",
        "near_corridor",
    } <= kinds


def test_parse_brief_sans_cle_est_heuristique():
    res = parse_brief("Terrain avec fibre proche gare")
    assert res["parser"] == "heuristic"
    assert any(p["kind"] == "fiber" for p in res["preferences"])


def test_constructible():
    pref = [Preference(kind="constructible", weight=5)]

    def sub(flags):
        _, details = evaluate(_listing(prix=100000, surface_terrain=800, flags=flags), pref)
        return details[0]["subscore"], details[0]["status"]

    assert sub({"constructible": True, "zone_urba": "U"}) == (1.0, "ok")
    assert sub({"constructible": False, "est_zone_au": True}) == (0.6, "ok")   # zone AU
    assert sub({"constructible": False, "zone_urba": "N"}) == (0.1, "ok")      # non constructible
    assert sub({})[1] == "n/a"                                                  # zonage inconnu


def test_prix_m2_terrain():
    pref = [Preference(kind="prix_m2_terrain", weight=4, params={"bon": 80, "cher": 400})]

    def sub(prix, st):
        _, details = evaluate(_listing(prix=prix, surface_terrain=st, flags={}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 3) if ss is not None else None), details[0]["status"]

    assert sub(40000, 1000) == (1.0, "ok")       # 40 €/m² -> excellent
    assert sub(200000, 500) == (0.25, "ok")      # 400 €/m² -> seuil « cher »
    assert sub(120000, 800)[0] < 1.0             # 150 €/m² -> intermédiaire
    assert sub(100000, None) == (None, "n/a")    # surface terrain inconnue


def test_prix_m2_terrain_discrimine_au_dela_du_seuil_cher():
    """Au-delà de « cher », l'ancien barème plafonnait : un terrain au tarif parisien
    obtenait la même note qu'un terrain simplement cher, et le critère ne triait plus."""
    pref = [Preference(kind="prix_m2_terrain", params={"bon": 80, "cher": 400})]

    def sub(ppm):
        _, details = evaluate(_listing(prix=ppm * 1000, surface_terrain=1000, flags={}), pref)
        return details[0]["subscore"]

    cher, tres_cher, parisien = sub(400), sub(600), sub(1000)
    assert cher > tres_cher > parisien
    assert parisien < 0.15  # prix parisien -> quasi éliminatoire


def test_budget_recompense_la_marge_sous_le_plafond():
    """« Je vise la bonne affaire » : rentrer tout juste dans le budget ne vaut pas
    autant que laisser de la marge."""
    pref = [Preference(kind="budget", params={"budget_max": 400_000})]

    def sub(prix):
        _, details = evaluate(_listing(prix=prix, flags={}), pref)
        return details[0]["subscore"]

    assert sub(200_000) == 1.0          # 50 % du budget -> pleine note
    assert sub(280_000) == 1.0          # 70 % -> encore pleine note
    assert sub(400_000) < sub(320_000)  # au plafond < avec de la marge
    assert sub(400_000) > sub(440_000)  # mais reste au-dessus du hors budget
    assert sub(560_000) == 0.0          # +40 % -> éliminé


def test_tranquillite_monte_et_descend():
    """Pris séparément, « sans vis-à-vis » / « isolé » ne sont cités que par 4 à 15 % des
    annonces : en `n/a` ils ne servaient que de bonus et aucun bien ne pouvait mal noter.
    Le critère composite doit être toujours évaluable et aller dans les deux sens."""
    pref = [Preference(kind="tranquillite")]

    def sub(flags):
        _, d = evaluate(_listing(flags=flags), pref)
        return d[0]["subscore"], d[0]["status"]

    neutre = sub({})[0]
    assert sub({})[1] == "ok"                                    # jamais n/a
    assert sub({"features": ["sans_vis_a_vis", "isole"]})[0] > neutre
    assert sub({"nuisances": ["vis_a_vis"]})[0] < neutre
    assert sub({"pavillon_neuf": True})[0] < neutre
    # Le pire cas (lotissement + vis-à-vis) doit vraiment tomber bas.
    assert sub({"pavillon_neuf": True, "nuisances": ["vis_a_vis"]})[0] == 0.0
    # Le meilleur cas doit atteindre le haut de l'échelle.
    assert sub({"features": ["sans_vis_a_vis", "isole", "calme"], "isolement_score": 1.0})[0] == 1.0


def test_coin_nature_priorise_l_eau():
    """« Une petite rivière c'est le must » : l'eau doit peser plus que les autres
    signaux, et deux bons signaux suffire pour la note pleine."""
    pref = [Preference(kind="coin_nature")]

    def sub(feats, alt=None):
        flags = {"features": feats}
        if alt is not None:
            flags["altitude"] = alt
        _, d = evaluate(_listing(flags=flags), pref)
        return d[0]["subscore"]

    assert sub([]) == 0.0                          # rien de cité -> 0, pas n/a
    assert sub(["eau"]) > sub(["arbore"])          # la rivière prime
    assert sub(["eau"]) > sub(["foret"])
    assert sub(["eau", "vue_panoramique"]) == 1.0  # rivière + vue dégagée = le cas idéal
    # « Vue dégagée car en hauteur » : l'altitude compte, plafonnée au point haut local.
    assert sub(["arbore"], alt=100) > sub(["arbore"], alt=10)
    assert sub([], alt=10) == 0.0                  # 10 m n'est pas une hauteur ici


def test_logement_compact_penalise_le_trop_grand():
    """« De la tiny house jusqu'à 3/4 chambres » : jamais pénaliser le petit, et
    continuer de décroître au-delà de la limite (5 et 8 chambres ne se valent pas)."""
    pref = [Preference(kind="logement_compact", params={"ideal": 3, "max": 4})]

    def sub(ch=None, pi=None, sb=None, type_bien="maison"):
        it = _listing(type_bien=type_bien, nb_chambres=ch, nb_pieces=pi, surface_bati=sb, flags={})
        _, d = evaluate(it, pref)
        return d[0]["subscore"], d[0]["status"], d[0]["detail"]

    assert sub(ch=1)[0] == 1.0 and sub(ch=3)[0] == 1.0     # tiny house = plein score
    assert sub(ch=4)[0] == 0.75                            # limite haute acceptée
    assert sub(ch=8)[0] < sub(ch=6)[0] < sub(ch=5)[0] < 0.75
    assert sub(ch=8)[0] > 0                                # décroissance, pas un mur à 0
    # Repli sur les pièces : mesuré sur 456 annonces, chambres ≈ pièces - 1.
    assert sub(pi=4)[0] == 1.0
    assert "pièces - 1" in sub(pi=4)[2]
    assert sub(pi=5)[0] == 0.75                            # T5 -> ~4 chambres
    assert sub(sb=90)[0] == 1.0                            # repli surface : compact
    assert sub(sb=300)[0] == 0.0
    assert sub(type_bien="terrain")[1] == "n/a"             # un terrain nu n'a pas de logement


def test_score_utilise_toute_l_echelle():
    """La moyenne pondérée d'une douzaine de critères se concentre au centre : sans
    étirement, 90 % des biens tombaient entre 50 et 79 et les pépites ne se
    détachaient pas. L'étirement est monotone : il étale sans changer le classement."""
    from app.services.preferences import _contraste

    assert _contraste(0.20) == 0.0 and _contraste(0.90) == 1.0
    assert _contraste(0.10) == 0.0 and _contraste(1.0) == 1.0   # borné
    # strictement croissant sur la plage utile
    vals = [_contraste(i / 100) for i in range(101)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert round(_contraste(0.55), 6) == 0.5                     # milieu conservé au centre
    # un bien médiocre et un bien excellent doivent vraiment s'écarter
    pref = [Preference(kind="has_terrain", params={"min_surface": 1000})]
    petit = evaluate(_listing(surface_terrain=200, flags={}), pref)[0]
    grand = evaluate(_listing(surface_terrain=2000, flags={}), pref)[0]
    assert petit == 0.0 and grand == 100.0


def test_en_hauteur_geo():
    pref = [Preference(kind="en_hauteur_geo", weight=4)]

    def sub(prom):
        _, details = evaluate(_listing(prix=100000, flags={"prominence_m": prom} if prom is not None else {}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 2) if ss is not None else None), details[0]["status"]

    assert sub(9)[0] == 1.0            # très dominant
    assert sub(6)[0] > sub(0)[0]      # surélevé mieux que plat
    assert sub(0) == (0.3, "ok")      # plat
    assert sub(-5)[0] < 0.1 or sub(-5)[0] == 0.0  # en creux -> bas
    assert sub(None) == (None, "n/a")  # relief non calculé


def test_distance_mer():
    pref = [Preference(kind="distance_mer", weight=5, params={"proche": 300, "loin": 3000})]

    def sub(dm):
        _, details = evaluate(_listing(prix=100000, flags={"dist_mer_m": dm} if dm is not None else {}), pref)
        ss = details[0]["subscore"]
        return (round(ss, 2) if ss is not None else None), details[0]["status"]

    assert sub(150) == (1.0, "ok")     # pieds dans l'eau
    assert sub(3500) == (0.1, "ok")    # loin
    assert sub(1000)[0] < 1.0 and sub(1000)[0] > 0.1
    assert sub(None) == (None, "n/a")  # non calculé


# --------------------------------------------------------------------------- #
# Ce qui remplace les paliers, retirés le 5 septembre 2026.
#
# Un palier plafonnait le score tant qu'une exigence n'était pas remplie. Il faisait deux
# choses très différentes : (1) empêcher un bien mal mesuré de monter par accident, et
# (2) dire « hors budget, c'est non ». La première est reprise par l'a priori, la seconde
# par la note du critère lui-même. Les tests ci-dessous vérifient que l'INTENTION tient
# toujours, sans plafond — donc sans écraser le classement de qui pondère autrement.
# --------------------------------------------------------------------------- #


def _prefs_haut(poids_mer=5):
    return [Preference(kind="budget", weight=5, params={"budget_max": 400000}),
            Preference(kind="has_terrain", weight=3),
            Preference(kind="distance_mer", weight=poids_mer,
                       params={"proche": 300, "loin": 3000})]


def _score_haut(flags, poids_mer=5, apriori=None):
    item = _listing(prix=50000, surface_terrain=2000, flags=flags)
    return evaluate(item, _prefs_haut(poids_mer), apriori)


def test_plus_aucun_plafond_dans_le_detail():
    """Le score n'est plus ramené à un palier : il n'y a plus de ligne « exigence »."""
    score, details = _score_haut({"dist_mer_m": 3500}, poids_mer=1)
    assert not [d for d in details if d["kind"] == "exigence"]
    assert score > 90   # ce bien était plafonné à 90 ; il ne l'est plus


def test_le_critere_seul_departage_le_bien_loin_de_l_eau():
    """Sans plafond, c'est le POIDS du critère qui fait la différence — et il la fait."""
    proche, _ = _score_haut({"dist_mer_m": 200}, poids_mer=5)
    loin, _ = _score_haut({"dist_mer_m": 3500}, poids_mer=5)
    assert loin < proche
    # Et quelqu'un qui ne pondère presque pas la mer obtient un classement différent :
    # c'est précisément ce que le plafond interdisait à tout le monde.
    loin_indifferent, _ = _score_haut({"dist_mer_m": 3500}, poids_mer=1)
    assert loin_indifferent > loin


def test_un_critere_non_mesure_vaut_la_moyenne_du_catalogue():
    """Le job structurel des paliers « mesuré » : ne pas monter parce qu'on ignore.

    Sans a priori, le bien dont la mer n'est pas mesurée est jugé sans elle — donc sur
    ses seuls bons critères, et il monte. Avec, l'inconnu vaut la moyenne du catalogue.
    """
    sans, details_sans = _score_haut({})            # dist_mer_m absent -> n/a
    avec, details_avec = _score_haut({}, apriori={"distance_mer": 0.3})
    assert avec < sans
    ligne = [d for d in details_avec if d["kind"] == "distance_mer"][0]
    assert ligne["status"] != "ok" and ligne["apriori"] == 0.3
    # Un bien qui MESURE une mer lointaine (0,1) reste en dessous de l'inconnu (0,3) :
    # l'a priori ne récompense pas l'ignorance, il la met à la moyenne.
    mesure_mauvaise, _ = _score_haut({"dist_mer_m": 3500}, apriori={"distance_mer": 0.3})
    assert mesure_mauvaise < avec


def test_hors_budget_tombe_a_zero_des_quinze_pour_cent():
    """« Hors budget, c'est non » était un palier ; c'est maintenant la note elle-même."""
    p = [Preference(kind="budget", params={"budget_max": 200000})]
    sous = lambda prix: evaluate(_listing(prix=prix, flags={}), p)[1][0]["subscore"]  # noqa: E731
    assert sous(140000) == 1.0            # bien en dessous : plein score
    assert sous(200000) == 0.80           # pile au plafond
    assert sous(215000) == 0.4            # +7,5 % : la moitié du chemin vers zéro
    assert sous(230000) == 0.0            # +15 % : la note est à zéro
    assert sous(300000) == 0.0


def test_gros_travaux_et_ruine_restent_derriere_sans_plafond():
    p = [Preference(kind="light_works")]
    sous = lambda cond: evaluate(_listing(flags={"condition": cond}), p)[1][0]["subscore"]  # noqa: E731
    assert sous("ruine") < sous("gros_travaux") < sous("renover") < sous("habitable")


def test_chambres_min_toujours_mesure():
    """Le critère capacité doit s'appliquer à TOUTES les annonces. Sans repli il était
    `n/a` — donc neutre — sur la moitié du lot têtard, et une maison d'une pièce a fini
    deuxième d'un classement qui exigeait quatre chambres."""
    pref = [Preference(kind="chambres_min", params={"min": 3})]

    def sub(**kw):
        _, d = evaluate(_listing(type_bien="maison", flags={}, **kw), pref)
        return d[0]["subscore"], d[0]["status"], d[0]["detail"]

    assert sub(nb_chambres=4)[0] == 1.0                  # au-dessus du minimum
    assert sub(nb_chambres=3)[0] == 1.0                  # pile au minimum
    assert sub(nb_chambres=1)[0] < sub(nb_chambres=2)[0] < 1.0  # dégradé, pas un mur
    # Repli 1 : les pièces (91 % des annonces, contre 51 % pour les chambres).
    assert sub(nb_pieces=5)[0] == 1.0 and "pièces - 1" in sub(nb_pieces=5)[2]
    assert sub(nb_pieces=1)[0] < 1.0                     # une pièce n'est pas une pépite
    # Repli 2 : la surface habitable, quand l'annonce ne donne ni chambres ni pièces.
    assert sub(surface_bati=140)[0] == 1.0
    assert sub(surface_bati=45)[0] < 1.0
    assert sub()[1] == "n/a"                             # plus rien à quoi se raccrocher


def test_rapport_qualite_prix_compare_au_secteur():
    """« Le rapport qualité/prix c'est essentiel » : le prix au m² du bien contre celui
    du secteur (DVF). C'est le ratio qui parle — 2 000 €/m² est cher en Ardèche."""
    pref = [Preference(kind="rapport_qualite_prix", params={"bon": 0.75, "cher": 1.7})]

    def sub(prix=None, bati=None, secteur=None):
        it = _listing(type_bien="maison", prix=prix, surface_bati=bati,
                      flags={"prix_m2_secteur": secteur} if secteur else {})
        _, d = evaluate(it, pref)
        return d[0]["subscore"], d[0]["status"]

    # 1 000 €/m² dans un secteur à 2 000 -> moitié prix -> plein score.
    assert sub(prix=100_000, bati=100, secteur=2000) == (1.0, "ok")
    # Au prix du secteur : note intermédiaire, ni bonne affaire ni excès.
    milieu = sub(prix=200_000, bati=100, secteur=2000)[0]
    assert 0.2 < milieu < 1.0
    # Deux fois le secteur, puis quatre fois : la note continue de descendre.
    assert sub(prix=400_000, bati=100, secteur=2000)[0] > sub(prix=800_000, bati=100, secteur=2000)[0]
    assert sub(prix=800_000, bati=100, secteur=2000)[0] > 0
    assert sub(prix=200_000, bati=100)[1] == "n/a"        # pas de référence DVF
    assert sub(prix=200_000, secteur=2000)[1] == "n/a"    # surface bâtie inconnue


def test_tranquillite_sans_isolement():
    """Le set têtard veut le calme SANS l'isolement (« pas isolé »). Poids nul = le
    signal sort du calcul, il ne compte pas pour zéro non plus."""
    sans_iso = [Preference(kind="tranquillite", params={"poids_isolement": 0, "poids_densite": 0})]
    defaut = [Preference(kind="tranquillite")]
    flags = {"features": ["isole"], "isolement_score": 1.0}

    def sub(pref, fl):
        _, d = evaluate(_listing(flags=fl), pref)
        return d[0]["subscore"], d[0]["detail"]

    assert sub(sans_iso, flags)[0] < sub(defaut, flags)[0]   # l'isolement ne rapporte plus
    assert "isolé" not in sub(sans_iso, flags)[1]            # ni ne s'affiche comme un plus
    # Le reste du critère continue de fonctionner dans les deux sens.
    assert sub(sans_iso, {"features": ["sans_vis_a_vis"]})[0] > sub(sans_iso, {})[0]
    assert sub(sans_iso, {"pavillon_neuf": True})[0] < sub(sans_iso, {})[0]


def test_etat_inconnu_ne_vaut_plus_mieux_que_mesure():
    """« Pas de gros travaux » était un plancher tenu par un palier. Sans palier, deux
    choses le portent : la note (ruine 0,1, gros travaux 0,4) et surtout l'a priori —
    l'état n'est renseigné que sur 59 % des annonces, et ne pas le connaître ne doit pas
    valoir mieux que le connaître mauvais."""
    prefs = [Preference(kind="light_works", weight=4),
             Preference(kind="budget", weight=4, params={"budget_max": 250000})]
    ap = {"light_works": 0.75}          # moyenne du catalogue
    note = lambda cond: evaluate(_listing(type_bien="maison", prix=150000,  # noqa: E731
                                          flags={"condition": cond} if cond else {}), prefs, ap)[0]
    assert note("habitable") > note(None) > note("gros_travaux") > note("ruine")
    # Sans a priori, l'état inconnu sortait du calcul : le bien était jugé sur son seul
    # budget et montait au niveau d'un bien habitable. C'est ce trou qu'on ferme.
    sans_ap = evaluate(_listing(type_bien="maison", prix=150000, flags={}), prefs)[0]
    assert sans_ap > note(None)


def test_village_vivant_penalise_la_ville_autant_que_le_desert():
    """« Trop en ville » : deux biens notés 1★ pour cette seule raison marquaient pourtant
    le maximum sur `commerces`, qui sature à 15. Un lieu de retrait entre copains n'est ni
    un hameau sans boulangerie ni un centre-ville — d'où une cloche."""
    pref = [Preference(kind="village_vivant", params={"vivant": 8, "ideal": 25, "ville": 120})]

    def sub(n):
        _, d = evaluate(_listing(flags={"n_commerces": n} if n is not None else {}), pref)
        return d[0]["subscore"], d[0]["status"]

    assert sub(20)[0] == 1.0 and sub(25)[0] == 1.0   # bourg pourvu : l'optimum
    assert sub(0)[0] < 0.5                            # désert commercial
    assert sub(258)[0] < sub(20)[0]                   # Chambéry redescend
    assert sub(258)[0] < sub(60)[0]                   # et plus bas qu'un gros bourg
    assert sub(3)[0] < sub(20)[0]                     # le hameau aussi
    assert sub(None)[1] == "pending"


def test_cachet_monte_et_descend():
    """« Pas de charme » trois fois en reproche, « charme de la bâtisse » une fois en
    éloge : le cachet compte. Cité par 38 % des annonces seulement, il doit être un
    composite toujours évaluable — sinon il ne sert que de bonus."""
    pref = [Preference(kind="cachet")]

    def sub(flags):
        _, d = evaluate(_listing(flags=flags), pref)
        return d[0]["subscore"], d[0]["status"]

    neutre = sub({})[0]
    assert sub({})[1] == "ok"                                   # jamais n/a
    assert sub({"features": ["authentique"]})[0] > neutre
    assert sub({"pavillon_neuf": True})[0] < neutre
    # « Trop ancien, trop rustique » (1★) : le cachet ne rachète pas une ruine.
    assert sub({"features": ["authentique"], "condition": "ruine"})[0] < \
           sub({"features": ["authentique"]})[0]


def test_bruit_compte_les_routes_passantes():
    """« Le long d'une route nationale », « le long d'une route » : deux 1★. Le critère
    ne regardait qu'autoroutes et voies ferrées, donc une départementale devant la porte
    lui était invisible."""
    pref = [Preference(kind="nuisance_sonore",
                       params={"min_m": 200, "ref_m": 1000, "poids_route": 0.45})]

    def sub(flags):
        _, d = evaluate(_listing(flags={"infra_checked": True, **flags}), pref)
        return d[0]["subscore"], d[0]["detail"]

    au_calme = sub({})[0]
    assert au_calme == 1.0
    bord_de_route = sub({"dist_route_m": 60})[0]
    assert bord_de_route < au_calme
    assert "route passante" in sub({"dist_route_m": 60})[1]
    # Le bruit d'une nationale porte moins loin que celui d'une autoroute : à distance
    # égale, elle pénalise moins.
    assert sub({"dist_route_m": 400})[0] > sub({"dist_autoroute_m": 400})[0]


def test_ensoleillement_separe_l_adret_de_l_ubac():
    """Le critère que l'annonce ne donne pas : deux biens de même altitude, l'un au
    soleil tout l'hiver, l'autre à l'ombre du versant d'en face."""
    adret = _listing(type_bien="maison", flags={
        "soleil_hiver_h": 7.2, "exposition_deg": 180, "exposition": "sud",
        "pente_deg": 18, "masque_sud_deg": 5.0})
    ubac = _listing(type_bien="maison", flags={
        "soleil_hiver_h": 0.0, "exposition_deg": 0, "exposition": "nord",
        "pente_deg": 18, "masque_sud_deg": 28.0})
    p = [Preference(kind="ensoleillement", params={"heures_faibles": 1.5, "heures_bonnes": 6.0})]
    note_adret, det_adret = evaluate(adret, p)
    assert note_adret > evaluate(ubac, p)[0]
    assert "21 décembre" in det_adret[0]["detail"]


def test_ensoleillement_repli_sur_l_annonce_puis_pending():
    """Tant que le relief n'est pas échantillonné (cache réchauffé à part), le critère
    reste `pending` — sauf si l'annonce revendique l'exposition, ce qui vaut moins qu'une
    mesure et doit le rester."""
    non_mesure = _listing(flags={"features": []})
    assert evaluate(non_mesure, [Preference(kind="ensoleillement")])[1][0]["status"] == "pending"

    revendique = _listing(flags={"features": ["ensoleille"]})
    sub = evaluate(revendique, [Preference(kind="ensoleillement")])[1][0]["subscore"]
    mesure = _listing(flags={"soleil_hiver_h": 7.5, "pente_deg": 15, "exposition_deg": 180})
    assert sub < evaluate(mesure, [Preference(kind="ensoleillement")])[1][0]["subscore"]


def test_jardin_exige_l_exterieur_sans_punir_l_annonce_muette():
    """« Jardin requis » : la surface prime, la mention du texte dépanne, et l'absence
    des deux vaut `n/a` — donc un palier non rempli, pas une note inventée."""
    mesure = _listing(type_bien="maison", surface_terrain=800)
    petit = _listing(type_bien="maison", surface_terrain=60)
    mention = _listing(type_bien="maison", flags={"features": ["jardin"]})
    muet = _listing(type_bien="maison", flags={"features": []})
    p = [Preference(kind="jardin", params={"min_surface": 300})]
    assert evaluate(mesure, p)[0] == 100.0
    assert evaluate(petit, p)[0] < evaluate(mention, p)[0] < evaluate(mesure, p)[0]
    assert evaluate(muet, p)[1][0]["status"] == "n/a"


def test_sans_exterieur_prouve_le_bien_passe_derriere():
    """« Jardin requis » ne plafonne plus : le bien sans extérieur prouvé prend la valeur
    moyenne du catalogue sur ce critère, ce qui le place derrière celui qui l'a."""
    prefs = [Preference(kind="jardin", weight=4, params={"min_surface": 300}),
             Preference(kind="budget", weight=4, params={"budget_max": 250000})]
    ap = {"jardin": 0.6}
    avec = _listing(type_bien="maison", prix=150000, surface_terrain=900, flags={})
    sans = _listing(type_bien="maison", prix=150000, flags={"features": []})
    assert evaluate(sans, prefs, ap)[0] < evaluate(avec, prefs, ap)[0]
    # Et celui qui ne veut pas de jardin peut le dire : son classement, lui, change.
    indifferent = [Preference(kind="jardin", weight=0, params={"min_surface": 300}),
                   Preference(kind="budget", weight=4, params={"budget_max": 250000})]
    assert evaluate(sans, indifferent, ap)[0] == evaluate(avec, indifferent, ap)[0]


def _tetard():
    """Le set têtard réel (critères + paliers), pour vérifier des arbitrages de groupe
    sur la définition publiée plutôt que sur une copie qui vieillirait à côté."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import collect_tetard

    return collect_tetard.PREFERENCES, None


def test_tetard_prefere_le_petit_bien_place_au_grand_mal_place():
    """« Un bien plus petit avec 3 chambres bien placé, c'est mieux qu'un bien grand mal
    placé » — et « 5 chambres ça reste ok » : le grand n'est pas exclu, il est départagé
    par le placement (soleil, nature, relief, calme), pas par sa taille."""
    prefs, exigences = _tetard()
    # Les deux biens sont au MÊME PRIX et au même prix au m² que LEUR secteur : sans ça,
    # `budget` (le set préfère le milieu de sa fourchette) ou `rapport_qualite_prix`
    # (poids 5) décideraient à la place du placement, et le test ne parlerait plus de ce
    # qu'il annonce. Ne restent en jeu que la taille et le cadre.
    PRIX = 200_000
    commun = dict(type_bien="maison", surface_terrain=900, prix=PRIX)
    etat = {"condition": "habitable"}
    bien_place = {"soleil_hiver_h": 7.5, "exposition_deg": 180, "pente_deg": 20,
                  "exposition": "sud", "altitude": 850,
                  "features": ["eau", "vue_panoramique"]}
    mal_place = {"soleil_hiver_h": 0.5, "exposition_deg": 0, "pente_deg": 20,
                 "exposition": "nord", "altitude": 300, "features": []}

    petit_bien_place = _listing(**commun, nb_chambres=3, surface_bati=95,
                                flags={**etat, "prix_m2_secteur": PRIX / 95})
    petit_bien_place.flags |= bien_place
    grand_mal_place = _listing(**commun, nb_chambres=5, surface_bati=190,
                               flags={**etat, "prix_m2_secteur": PRIX / 190})
    grand_mal_place.flags |= mal_place

    petit = evaluate(petit_bien_place, prefs, exigences)[0]
    grand = evaluate(grand_mal_place, prefs, exigences)[0]
    assert petit > grand

    # …et le grand n'est pas écarté pour sa taille : à placement égal, cinq chambres
    # restent recevables (aucun palier ne le plafonne).
    grand_bien_place = _listing(**commun, nb_chambres=5, surface_bati=190,
                                flags={**etat, "prix_m2_secteur": PRIX / 190})
    grand_bien_place.flags |= bien_place
    note, details = evaluate(grand_bien_place, prefs, exigences)
    plafonne = [d for d in details if d.get("status") == "ko"
                and "Format maison de retrait" in (d.get("label") or "")]
    assert not plafonne


# --- Le prix plancher : « pas de secret quand un bien est à 100 ou 150 k€ » -----------

class _Bien:
    """Objet minimal accepté par `_eval_one`."""

    def __init__(self, **kw):
        self.prix = self.nb_chambres = self.nb_pieces = self.surface_bati = None
        self.flags = {}
        self.__dict__.update(kw)


_FOURCHETTE = {"budget_max": 250000, "budget_min": 180000}


def test_budget_cesse_de_recompenser_le_bon_marche():
    from app.services.preferences import _eval_one

    def note(prix):
        return _eval_one(_Bien(prix=float(prix)), "budget", _FOURCHETTE)[0]

    # Dans la fourchette : plein score. En dessous : la note redescend.
    assert note(200000) > 0.9
    assert note(180000) > 0.9
    assert note(170000) < 0.79        # sous le plancher, le palier de prix mord
    assert note(135000) < note(170000)
    assert note(75000) < note(135000)
    # Non nulle : c'est un a priori très fort, pas une preuve.
    assert note(75000) > 0.1


def test_le_plancher_est_un_seuil_pas_une_pente():
    """Sans cran net, un bien à 179 000 € notait encore 1,0 et le palier de prix ne
    mordait qu'à partir de 158 000 € — pas là où il est écrit."""
    from app.services.preferences import _eval_one

    juste_dessous = _eval_one(_Bien(prix=179999.0), "budget", _FOURCHETTE)[0]
    juste_dessus = _eval_one(_Bien(prix=180001.0), "budget", _FOURCHETTE)[0]
    assert juste_dessous < 0.79 <= juste_dessus


def test_sans_plancher_le_comportement_historique_est_preserve():
    from app.services.preferences import _eval_one

    assert _eval_one(_Bien(prix=75000.0), "budget", {"budget_max": 250000})[0] == 1.0


def test_les_pieces_annoncees_sont_recoupees_par_la_surface():
    """« Chalet de montagne de 4 pièces de 35 m² situé au camping » : le repli
    « pièces - 1 » lui accordait 3 chambres, donc le palier de capacité d'accueil."""
    from app.services.preferences import _eval_one

    params = {"min": 3, "m2_min_par_piece": 20}
    mobilhome = _Bien(nb_pieces=4, surface_bati=35.0)
    note, statut, detail = _eval_one(mobilhome, "chambres_min", params)
    assert note < 0.5 and "tenables" in detail

    # Une vraie maison de 4 pièces n'est pas touchée.
    vraie = _Bien(nb_pieces=4, surface_bati=95.0)
    assert _eval_one(vraie, "chambres_min", params)[0] == 1.0
    # Ni celle qui donne ses chambres directement.
    assert _eval_one(_Bien(nb_chambres=3, surface_bati=60.0), "chambres_min", params)[0] == 1.0


def test_dpe_passoire_note_moins_quun_bien_performant():
    # Le DPE dit ce que l'état du bâti ne dit pas : deux maisons habitables, l'une G.
    p = [Preference(kind="dpe")]
    passoire = _listing(type_bien="maison", dpe_classe="G", flags={})
    correcte = _listing(type_bien="maison", dpe_classe="C", flags={})
    assert evaluate(passoire, p)[0] < evaluate(correcte, p)[0]
    assert "passoire" in evaluate(passoire, p)[1][0]["detail"]


def test_dpe_absent_est_neutre():
    # 19 % des annonces ne le donnent pas : le critère sort du calcul, il ne pénalise pas.
    score, det = evaluate(_listing(type_bien="maison", flags={}), [Preference(kind="dpe")])
    assert det[0]["status"] == "n/a"
    assert score is None


def test_risques_et_eau_reprennent_la_mesure_du_score_investissement():
    # Même barème que le pilier « Risques » : une inondation pèse plus qu'un radon.
    p = [Preference(kind="risques_naturels")]
    sous = lambda item: evaluate(item, p)[1][0]["subscore"]   # noqa: E731 — le score agrégé
    # est étiré entre deux ancres et sature : c'est le sous-score qui porte la mesure.
    inonde = _listing(flags={"risques": ["inondation", "mouvementTerrain"]})
    radon = _listing(flags={"risques": ["radon"]})
    sain = _listing(flags={"risques": []})
    assert sous(inonde) < sous(radon) < sous(sain)
    # Sans relevé Géorisques, le critère est `pending` (mesure à faire), pas zéro.
    assert evaluate(_listing(flags={}), p)[1][0]["status"] == "pending"

    q = [Preference(kind="qualite_eau")]
    sale = _listing(flags={"pollution_eau_score": 0.2, "eau_potable_conforme": False,
                           "pollutions": ["nitrates"]})
    propre = _listing(flags={"pollution_eau_score": 1.0, "eau_potable_conforme": True})
    assert evaluate(sale, q)[1][0]["subscore"] < evaluate(propre, q)[1][0]["subscore"]
    assert "NON conforme" in evaluate(sale, q)[1][0]["detail"]


def test_hiking_note_la_densite_de_sentiers_pas_leur_presence():
    # Avant : 1,00 dès qu'il y avait un sentier -> 94 % des biens à égalité.
    p = [Preference(kind="hiking", params={"peu": 10, "beaucoup": 200})]
    sous = lambda item: evaluate(item, p)[1][0]["subscore"]  # noqa: E731
    rare = _listing(flags={"randonnee": True, "rando_count": 15})
    dense = _listing(flags={"randonnee": True, "rando_count": 190})
    assert sous(rare) < 0.2 < sous(dense)
    # Sans comptage, la donnée ne sait dire que oui/non : on garde le repli.
    assert sous(_listing(flags={"randonnee": True})) == 1.0
    assert sous(_listing(flags={"randonnee": False})) == 0.3
    # Les repères appartiennent au set : le littoral compte plus de sentiers.
    cotier = [Preference(kind="hiking", params={"peu": 20, "beaucoup": 250})]
    assert evaluate(dense, cotier)[1][0]["subscore"] < sous(dense)


def test_a_renover_ne_vaut_plus_presque_habitable():
    p = [Preference(kind="light_works")]
    sous = lambda cond: evaluate(_listing(flags={"condition": cond}), p)[1][0]["subscore"]  # noqa: E731
    assert sous("habitable") == 1.0
    assert sous("rafraichir") == 1.0
    # 0,65 : une rénovation coûte. Et surtout la note ne colle plus au seuil du palier
    # (0,6), donc une erreur de classement ne le traverse plus d'un centième.
    assert sous("renover") == 0.65
    assert sous("gros_travaux") < sous("renover")
    assert sous("ruine") < sous("gros_travaux")
