from app.services.classify import GROS_TRAVAUX, HABITABLE, RENOVER, RUINE, classify


def test_ruine_niveau_max():
    res = classify("Terrain", "Ancienne bâtisse en ruine, à reconstruire")
    assert res["condition"] == RUINE
    assert res["niveau_travaux"] == 4


def test_a_renover_distinct_de_ruine():
    res = classify("Maison", "Maison à rénover, travaux à prévoir")
    assert res["condition"] == RENOVER
    assert res["niveau_travaux"] == 2


def test_gros_travaux_plus_severe_que_renover():
    # Le niveau le plus sévère mentionné l'emporte.
    res = classify("Maison à rénover, prévoir de gros travaux et réhabilitation lourde")
    assert res["condition"] == GROS_TRAVAUX
    assert res["niveau_travaux"] == 3


def test_negation_aucun_travaux_non_classe_a_renover():
    res = classify("Maison", "Aucun travaux à prévoir, prête à habiter")
    assert res["condition"] == HABITABLE
    assert res["niveau_travaux"] == 0


def test_etat_inconnu():
    res = classify("Appartement", "Bel appartement lumineux proche commerces")
    assert res["condition"] is None
    assert res["niveau_travaux"] is None


def test_insensible_accents_casse():
    assert classify("RUINE À RECONSTRUIRE")["condition"] == RUINE


# --- Le bien de Jarrier (73) : une grange notée 89,4 et publiée comme pépite ----------
#
# « Grange à rénover entièrement » était classée `renover` (0,85 sur le barème travaux),
# c'est-à-dire juste au-dessus du palier du set têtard, qui accepte « à rénover » et
# refuse la rénovation complète. Deux corrections indépendantes la font redescendre :
# le mot-clé « à rénover entièrement », et la règle « coquille » (ce qu'on vend n'est
# pas un logement).

def test_a_renover_entierement_est_une_renovation_complete():
    for texte in ("Maison de ville à rénover entièrement",
                  "Studio entièrement à rénover, au centre",
                  "Maison à rénover intégralement",
                  "Bâtisse à rénover de fond en comble"):
        assert classify(texte)["condition"] == GROS_TRAVAUX, texte


def test_jarrier_grange_a_renover_entierement():
    res = classify("Grange 6 pièces 190 m²",
                   "Découvrez cette grange à rénover entièrement située au hameau de "
                   "Mollard-Rocher, sur la commune de Jarrier (73300).")
    assert res["condition"] == GROS_TRAVAUX


def test_coquille_grange_a_renover_sans_le_mot_entierement():
    # La règle seule, sans mot-clé de gros travaux : une grange qu'on aménage, c'est
    # créer planchers, isolation, réseaux et ouvertures.
    assert classify("Grange à rénover à École (grande surface aménageable)")["condition"] == GROS_TRAVAUX
    assert classify("Corps de ferme mitoyen à rénover, beau potentiel")["condition"] == GROS_TRAVAUX
    assert classify("Chalet d'alpage à rénover à proximité de Beaufort")["condition"] == GROS_TRAVAUX


def test_coquille_ne_se_declenche_pas_sur_une_annexe():
    # « Maison ... avec grange attenante » vend une maison : le mot maison vient avant.
    res = classify("Belle maison de village de 120 m² avec grange attenante, "
                   "quelques travaux de rafraîchissement à prévoir")
    assert res["condition"] != GROS_TRAVAUX


def test_coquille_ne_se_declenche_pas_sur_une_grange_deja_convertie():
    res = classify("Ancienne grange entièrement rénovée en maison de 140 m², habitable de suite")
    assert res["condition"] == HABITABLE


def test_coquille_exige_la_proximite_du_projet():
    # Une ferme habitable dont les DÉPENDANCES ont du potentiel n'est pas une coquille :
    # 500 signes séparent « ancienne ferme » de « à aménager ». (Saint-Martin-en-Vercors)
    res = classify("Venez découvrir cette ancienne ferme édifiée en 1871 pleine de charme. "
                   "La maison développe 164 m² habitables répartis sur deux niveaux, quatre "
                   "chambres, une cuisine de 21 m² et un séjour de 46 m² avec cheminée. "
                   "Le jardin arboré se déploie sur 1500 m². Les dépendances représentent un "
                   "véritable potentiel : l'ancienne écurie de 48 m² est à aménager.")
    assert res["condition"] != GROS_TRAVAUX


def test_coquille_ignore_le_patronyme_du_mandataire():
    # « iad France - Audrey Moulin vous propose » : ni le préambule d'agence ni un nom
    # de famille ne disent ce qu'on vend.
    res = classify("iad France - Audrey Moulin vous propose: Charmante Maison de Campagne "
                   "à rénover - Bourget-en-Huile")
    assert res["condition"] == RENOVER


def test_preambule_non_coupe_quand_il_nomme_le_bien():
    # « Maison 4 pièces 90 m² — Queige [...] je vous propose » : couper jusqu'à « vous
    # propose » effaçait le mot « maison » et la grange citée plus bas prenait sa place.
    res = classify("Maison 4 pièces 90 m²  Queige, à quelques kilomètres d'Albertville, "
                   "sur les hauteurs, je vous propose cette maison avec une grange à rénover")
    assert res["condition"] != GROS_TRAVAUX


def test_hors_deau_hors_dair_est_un_gros_oeuvre_a_finir():
    # L'enveloppe est close, tout le second œuvre reste à faire. Ces annonces étaient
    # classées « habitable » (« toiture et façade intégralement refaites »).
    res = classify("Maison de village vendue hors d'eau hors d'air. Toiture et façade "
                   "intégralement refaites. Menuiseries neuves PVC.")
    assert res["condition"] == GROS_TRAVAUX


def test_annonce_qui_se_declare_non_habitable():
    for texte in ("Bâtisse traditionnelle, des travaux sont à prévoir, ce bien n'est pas "
                  "habitable en l'état.",
                  "Ancienne remise, non habitable en l'état",
                  "Local impropre à l'habitation"):
        assert classify(texte)["condition"] == GROS_TRAVAUX, texte


def test_apostrophes_typographiques_lues_comme_les_droites():
    droite = classify("Ce bien n'est pas habitable en l'état")
    courbe = classify("Ce bien n’est pas habitable en l’état")
    assert droite == courbe == {"condition": GROS_TRAVAUX, "niveau_travaux": 3}


# --- Ugine, 180 000 € : « souhaite rénover e… » lu comme « rénové » ------------------
#
# Deux défauts se sont additionnés sur la même annonce, publiée comme pépite à 83,7 :
# les mots-clés se cherchaient en SOUS-CHAÎNE (« renove » se trouve dans « renover »),
# et la description était COUPÉE à 200 signes par la carte de la SERP SeLoger, juste
# avant le mot qui décide.

def test_renover_ne_se_lit_pas_comme_renove():
    # Le verbe à l'infinitif contient le participe passé. Sans frontières de mots, une
    # maison de 1920 « pour qui souhaite rénover » était déclarée habitable.
    assert classify("Cette maison de 1920 offre un beau potentiel pour qui souhaite "
                    "rénover et aménager")["condition"] == RENOVER
    # …sans casser le cas inverse, qui est bien un bien rénové.
    assert classify("Maison entièrement rénovée, habitable de suite")["condition"] == HABITABLE
    assert classify("Une maison mitoyenne rénovée récemment, belles prestations")["condition"] == HABITABLE


def test_texte_tronque_ne_prouve_pas_l_absence_de_travaux():
    """Une troncature ne peut pas inventer une mention de travaux, seulement en cacher
    une : un verdict léger rendu sur un texte coupé ne vaut donc rien."""
    complet = "Belle maison en très bon état, proche des commerces"
    assert classify(complet)["condition"] == HABITABLE
    assert classify(complet + "...")["condition"] is None
    assert classify(complet + "…")["condition"] is None
    assert classify("Maison à rénover, travaux à prévoir...")["condition"] is None


def test_les_verdicts_severes_survivent_a_la_troncature():
    # Eux sont déjà au bout de l'échelle : la suite du texte ne peut pas les aggraver.
    assert classify("Grange à rénover entièrement, hameau de Mollard-Rocher...")["condition"] == GROS_TRAVAUX
    assert classify("Ancienne bâtisse en ruine, à reconstruire…")["condition"] == RUINE


def test_ugine_bout_en_bout():
    res = classify("SOUS COMPROMIS Maison familiale à Ugine – 150 m² habitables + jardin "
                   "Située sur la commune d'Ugine, cette maison de ville des années 1920 "
                   "(type T6) offre un beau potentiel pour qui souhaite rénover e...",
                   "Ugine (73400)")
    assert res["condition"] is None   # et non « habitable »


def test_verbes_a_l_infinitif_comptent_comme_leur_niveau():
    # « afin de LA réhabiliter » : avec les frontières de mots, « a rehabiliter » ne
    # matche plus, et le bien serait repassé en état inconnu.
    assert classify("Un projet d'architecte afin de la réhabiliter en une seule "
                    "habitation")["condition"] == GROS_TRAVAUX


def test_negation_rien_a_renover():
    assert classify("Maison en parfait état, plus rien à rénover")["condition"] == HABITABLE
