"""Barème d'attractivité locative saisonnière (« Airbnb ») — hors ligne."""

from app.services.tourisme import noter, resumer

# Relevés réels des pivots, mesurés sur OpenStreetMap le 31 août 2026.
CHAMONIX = {"tour_checked": True, "tour_hebergements": 86, "tour_restos": 121,
            "tour_attractions": 108, "tour_dist_remontee_m": 500, "tour_dist_lac_m": 1700}
BEAUFORT = {"tour_checked": True, "tour_hebergements": 22, "tour_restos": 6,
            "tour_attractions": 55, "tour_dist_remontee_m": 3200, "tour_dist_lac_m": 3900}
HAUTEVILLE = {"tour_checked": True, "tour_hebergements": 4, "tour_restos": 8,
              "tour_attractions": 26, "tour_dist_remontee_m": None, "tour_dist_lac_m": None}
BARCELONNETTE = {"tour_checked": True, "tour_hebergements": 42, "tour_restos": 29,
                 "tour_attractions": 13, "tour_dist_remontee_m": 2900, "tour_dist_lac_m": None}


def test_point_non_mesure_reste_pending():
    # Pas de note par défaut : un point non mesuré doit rester `pending`, sinon il vaut
    # zéro et le critère punit l'absence de mesure au lieu de s'abstenir.
    assert noter(None) is None
    assert noter({}) is None
    assert noter({"tour_hebergements": 40}) is None  # sans tour_checked


def test_classement_des_pivots():
    notes = {n: noter(m)["note"] for n, m in
             (("chamonix", CHAMONIX), ("beaufort", BEAUFORT),
              ("barcelonnette", BARCELONNETTE), ("hauteville", HAUTEVILLE))}
    assert notes["chamonix"] > notes["beaufort"] > notes["barcelonnette"] > notes["hauteville"]
    assert notes["chamonix"] == 1.0
    assert notes["hauteville"] < 0.3   # ni ski, ni lac, ni marché : ce n'est pas une station


def test_le_ski_decroit_vite_avec_la_distance():
    def au_ski(m):
        return noter({**BEAUFORT, "tour_dist_remontee_m": m})["hiver"]
    assert au_ski(1000) == 1.0
    assert au_ski(10000) < 0.6      # une demi-heure de voiture, ça ne se loue pas pareil
    assert au_ski(25000) == 0.0
    assert au_ski(None) == 0.0


def test_ete_gagnable_sans_lac():
    # Bourg-d'Oisans n'a pas de plage et remplit juillet : les sites suffisent.
    sans_lac = noter({"tour_checked": True, "tour_hebergements": 39, "tour_restos": 15,
                      "tour_attractions": 90, "tour_dist_remontee_m": 3000,
                      "tour_dist_lac_m": None})
    assert sans_lac["ete"] >= 0.9


def test_prime_de_double_saison():
    # Deux points de marché et de vie identiques, l'un d'hiver seul, l'autre des deux.
    base = {"tour_checked": True, "tour_hebergements": 15, "tour_restos": 7,
            "tour_attractions": 0}
    hiver_seul = noter({**base, "tour_dist_remontee_m": 2000, "tour_dist_lac_m": None})
    deux_saisons = noter({**base, "tour_dist_remontee_m": 2000, "tour_dist_lac_m": 1000})
    assert deux_saisons["note"] - hiver_seul["note"] > 0.26  # l'été + la prime


def test_resume_lisible():
    texte = resumer(BEAUFORT, noter(BEAUFORT))
    assert "ski à 3.2 km" in texte
    assert "22 hébergements" in texte
    assert "quatre saisons" in texte
    assert "surtout l'hiver" in resumer(BARCELONNETTE, noter(BARCELONNETTE))


def test_branche_dans_le_moteur_de_preferences():
    from app.services.preferences import PREFERENCE_KINDS, _eval_one

    assert "attractivite_airbnb" in PREFERENCE_KINDS

    class _Item:
        flags = BEAUFORT

    note, statut, detail = _eval_one(_Item(), "attractivite_airbnb", {})
    assert statut == "ok" and 0.9 < note <= 1.0 and "ski" in detail

    class _Vide:
        flags = {}

    assert _eval_one(_Vide(), "attractivite_airbnb", {})[1] == "pending"
