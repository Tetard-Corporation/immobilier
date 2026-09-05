"""Demandes d'achat et prix invraisemblables : ce ne sont pas des biens à vendre.

Leboncoin ne sépare pas les offres des demandes. Une demande porte un prix symbolique —
1 €, 200 €, 5 000 € — que le critère budget lit comme une affaire exceptionnelle : c'est
PRÉCISÉMENT parce que le bien n'est pas à vendre qu'il remontait dans le classement.
Vécu : « Cherche maison sur Veynes..habitable. 70 m2 » à 1 €, et « Agréable appartement »
à 800 € (un loyer mensuel lu comme un prix), classé dans le premier tiers du set têtard.
"""

from app.services.export_static import _detect_demande_achat as demande


# --- Ce qui doit être écarté ------------------------------------------------------------

def test_particulier_qui_cherche():
    assert demande(1, "maison", "Particulier recherche chalet sur notre dame de bellecombe")
    assert demande(1, "maison", "Bonjour,\nJe recherche une maison sur le secteur d'Albens")
    assert demande(200, "maison", "Cherche à acheter petit chalet individuel dans le Champsaur")


def test_une_demande_au_prix_credible_est_quand_meme_une_demande():
    # 160 000 € est un prix plausible : seul le texte trahit qu'il n'y a rien à vendre.
    assert demande(160_000, "maison", "Bonjour ,\nJe cherche un chalet à rénover")
    assert demande(35_000, "maison", "Recherche grangeon avec terrain à rénover")


def test_un_bati_sous_le_plancher():
    # Un loyer mensuel lu comme un prix de vente.
    assert demande(800, "appartement", "Au centre de Bourg d'Oisans. Agréable appartement")
    assert demande(4000, "maison", "Vends 4 chalets savoyard en madriers")


def test_type_inconnu_traite_comme_du_bati():
    # Les biens d'agence n'ont pas de type renseigné. Une liste positive de types bâtis
    # les laissait passer : c'est ce qui gardait un bien à 800 € et un autre à 0 €.
    assert demande(800, None, "Agréable appartement composé d'une cuisine")
    assert demande(0, None, "Situés à Oz, sur le grand domaine")


# --- Ce qui doit rester publié ----------------------------------------------------------

def test_le_demarchage_d_agence_n_est_pas_une_demande():
    # « NOUS recherchons » en tête d'une VRAIE annonce : le vendeur parle à la première
    # personne du pluriel, l'acheteur au singulier. Trois biens de la base sont dans ce cas.
    assert not demande(140_000, "maison",
                       "NOUS RECHERCHONS ACTIVEMENT DES BIENS SUR CE SECTEUR POUR NOS CLIENTS")
    assert not demande(192_000, "maison",
                       "Nous recherchons les futurs habitants d'un appartement à vendre")


def test_recherche_au_fil_du_texte_ne_declenche_rien():
    # 1 393 annonces de la base emploient « recherche » comme un mot ordinaire de vente.
    assert not demande(250_000, "maison", "Maison idéale pour qui recherche le calme")
    assert not demande(250_000, "maison",
                       "Que vous recherchiez une résidence principale ou un investissement")


def test_un_terrain_bon_marche_est_une_vraie_vente():
    # 6 500 € pour 4 650 m² en zone naturelle : le plancher ne s'applique pas au terrain.
    assert not demande(6500, "terrain", "NESTENN vous propose à la vente des terrains")
    assert not demande(7500, "terrain", "Terrain nature de loisir - 4 650 m²")


def test_sans_prix_ni_texte_on_ne_conclut_pas():
    assert not demande(None, "maison", None)
    assert not demande(None, None, "")
