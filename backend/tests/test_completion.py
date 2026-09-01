"""Complétion des champs structurels depuis le texte de l'annonce.

Les cas viennent tous d'annonces réelles de la base (7 086 biens) : chaque test qui
ressemble à une bizarrerie a été payé par une valeur fausse ou absente sur le site.
"""

from app.services.completion import chambres, completer, lire, normalize, pieces, terrain
from app.services.completion import bati


def _t(texte: str) -> str:
    return normalize(texte)


# --- Chambres -------------------------------------------------------------------------

def test_compte_en_chiffres_et_en_lettres():
    assert chambres(_t("Maison comprenant 3 chambres")) == 3
    assert chambres(_t("À l'étage, on retrouve trois chambres, un bureau")) == 3
    assert chambres(_t("Une chambre, un séjour, une salle d'eau")) == 1


def test_adjectifs_intercales():
    assert chambres(_t("Enfin 3 grandes chambres dont une avec placard")) == 3
    assert chambres(_t("quatre belles chambres lumineuses")) == 4


def test_fiche_technique():
    assert chambres(_t("Chambres : 4")) == 4
    assert chambres(_t("chambre(s) : 2")) == 2


def test_texte_muet_ne_donne_rien():
    assert chambres(_t("Bel appartement lumineux proche commerces")) is None


# Le mode irréel : l'annonce vend un potentiel, pas des chambres.
def test_possibilite_de_chambre_ne_compte_pas():
    # « (possibilité d'une 4ème chambre) [...] et 3 chambres » : le bien en a 3.
    assert chambres(_t("séjour double (possibilité d'une 4ème chambre), WC séparé et 3 chambres")) == 3
    assert chambres(_t("combles aménageables en deux chambres")) is None
    assert chambres(_t("2 chambres d'hôtes exploitées à l'année")) is None


def test_niveaux_additionnes_maximum_dans_un_niveau():
    """« Au rdc trois chambres [...] à l'étage une chambre » : quatre, pas trois."""
    texte = _t("Au rez-de-chaussée, vous trouverez une entrée desservant trois chambres, "
               "une salle d'eau. À l'étage, une chambre supplémentaire et un coin bureau.")
    assert chambres(texte) == 4


def test_resume_puis_detail_ne_se_cumulent_pas():
    """« Comprend 3 chambres » puis « l'étage dispose de 2 chambres » : trois, pas cinq."""
    texte = _t("Cette maison de 2 étages comprend 3 chambres et 2 salles de bain. "
               "L'étage dispose de 2 chambres, une salle de bains à rafraîchir.")
    assert chambres(texte) == 3


def test_ordinal_fait_plancher():
    """« Une troisième chambre indépendante » prouve trois chambres."""
    texte = _t("une chambre d'enfant. Une troisième chambre indépendante avec salle d'eau")
    assert chambres(texte) == 3


def test_plafond_de_vraisemblance():
    assert chambres(_t("40 chambres")) is None


# --- Pièces ---------------------------------------------------------------------------

def test_pieces_le_premier_compte_gagne():
    """Le total est annoncé en tête ; le détail qui suit ne parle que d'un niveau."""
    assert pieces(_t("Maison 6 pièces 120 m2. À l'étage : 3 pièces")) == 6


def test_pieces_nomenclature_tf():
    assert pieces(_t("Bel appartement T3 rénové")) == 3
    assert pieces(_t("F4 avec balcon")) == 4


def test_piece_de_vie_nest_pas_un_compte():
    assert pieces(_t("une pièce de vie de 36 m2 et une cuisine")) is None


# --- Terrain --------------------------------------------------------------------------

def test_terrain_avant_et_apres():
    assert terrain(_t("Terrain clos de 788m2")) == 788
    assert terrain(_t("maison de plain pied de 98 m2 sur 1480m2 de terrain plat")) == 1480
    assert terrain(_t("implanté sur une parcelle cadastrale d'environ 755 m²")) == 755


def test_terrain_separateurs_de_milliers():
    # L'espace fine insécable (U+202F) des sites récents : sans elle, « 1 779 » vaut 1.
    assert terrain(_t("sur un terrain d'environ 1 779 m²")) == 1779
    assert terrain(_t("terrain de 1.200 m²")) == 1200


def test_terrain_en_hectares():
    assert terrain(_t("jolie ferme sur 1,8 ha de terrain")) == 18000


def test_terrain_le_plus_grand_gagne():
    """L'annonce cite le jardin (petit) puis le terrain (grand) : c'est le terrain."""
    assert terrain(_t("un jardin de 300 m² et un terrain attenant de 2 400 m²")) == 2400


def test_terrain_irreel_refuse():
    texte = _t("possibilité d'acquérir une dépendance et terrain en supplément pour "
               "porter la superficie du terrain à 3 765m2")
    assert terrain(texte) is None


def test_sans_jardin_est_une_donnee():
    """« Pas de jardin » n'est pas « on ne sait pas » : c'est zéro."""
    assert terrain(_t("salle de bains, wc avec lavabo. Pas de jardin.")) == 0.0


def test_terrain_trop_petit_ignore():
    assert terrain(_t("terrain de 4 m²")) is None


# --- Surface habitable ----------------------------------------------------------------

def test_bati_habitable_explicite():
    assert bati(_t("cette maison développe 76 m² habitables")) == 76
    assert bati(_t("sa surface habitable de 62 m2 offre un salon")) == 62


def test_bati_ne_prend_pas_le_terrain():
    """« Terrain de 450 m2 | maison de 83 m2 » : 83, pas 450."""
    assert bati(_t("terrain de 450 m2 viabilisé | maison de 83 m2 à étage + garage")) == 83


def test_bati_ignore_le_potentiel():
    assert bati(_t("potentiel de 200 m2 habitables, après rénovation")) is None


def test_ponctuation_arrete_le_nombre():
    """« 788 m². 120 m² habitables » ne se lit pas comme le nombre « 2. 120 »."""
    assert lire(_t("Terrain clos de 788 m2. 120 m² habitables.")) == {
        "surface_terrain": 788.0, "surface_bati": 120.0}


# --- Complétion d'un bien -------------------------------------------------------------

class _Bien:
    def __init__(self, **kw):
        self.adresse = kw.pop("adresse", None)
        self.description = kw.pop("description", None)
        for champ in ("nb_chambres", "nb_pieces", "surface_terrain", "surface_bati"):
            setattr(self, champ, kw.pop(champ, None))


def test_completer_remplit_les_trous():
    bien = _Bien(adresse="Maison 4 pièces",
                 description="À l'étage, trois chambres. Terrain de 788 m². 120 m² habitables.")
    ecrits = completer(bien)
    assert bien.nb_chambres == 3 and bien.surface_terrain == 788 and bien.surface_bati == 120
    assert set(ecrits) == {"nb_chambres", "nb_pieces", "surface_terrain", "surface_bati"}


def test_completer_ne_corrige_jamais_la_source():
    """Une donnée structurée du portail vaut mieux qu'une phrase de commercial."""
    bien = _Bien(nb_chambres=2, surface_terrain=1000.0,
                 description="À l'étage, trois chambres. Terrain de 788 m².")
    ecrits = completer(bien)
    assert bien.nb_chambres == 2 and bien.surface_terrain == 1000.0
    assert ecrits == {}


def test_completer_sans_texte():
    assert completer(_Bien()) == {}


def test_annotate_complete_avant_de_scorer():
    """Un terrain lu dans le texte doit compter dans le score, pas seulement s'afficher."""
    from app.services.enrich import annotate
    from app.sources.base import NormalizedListing

    item = annotate(NormalizedListing(
        source="test", external_id="1", type_bien="maison", prix=200000.0,
        adresse="Maison 4 pièces",
        description="À l'étage, trois chambres. Terrain clos de 1 200 m². 110 m² habitables."))
    assert item.nb_chambres == 3
    assert item.surface_terrain == 1200.0
    assert item.surface_bati == 110.0
    assert item.flags["champs_completes"] == ["nb_chambres", "nb_pieces",
                                              "surface_bati", "surface_terrain"]


def test_fiche_ne_lit_pas_une_surface_comme_un_compte():
    """« chambre : 12 m2 » détaille une pièce, il n'y en a pas douze."""
    texte = _t("composition actuelle : entrée : 15 m2 salon : 30 m2 chambre : 12 m2")
    # Sans compte devant le mot, on ne conclut rien : c'est une absence, pas un zéro.
    assert chambres(texte) is None
    assert pieces(_t("pièces : 40 m2 au sol")) is None


def test_le_compte_lu_est_plafonne_par_les_pieces():
    """Une annonce qui décrit la maison PUIS son annexe additionne deux logements."""
    bien = _Bien(nb_pieces=5,
                 description="Au rez-de-chaussée une chambre parentale. Au 1er, 2 chambres. "
                             "L'annexe louée comprend deux chambres.")
    completer(bien)
    assert bien.nb_chambres == 4          # 5 pièces -> 4 chambres au plus


def test_le_plafond_ne_touche_pas_une_valeur_de_la_source():
    bien = _Bien(nb_pieces=3, nb_chambres=5, description="peu importe")
    assert completer(bien) == {}
    assert bien.nb_chambres == 5
