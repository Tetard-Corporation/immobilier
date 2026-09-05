"""Lecture du corps d'une fiche d'agence pour combler chambres et terrain.

97 % des biens d'agence arrivaient sans chambres, 95 % sans terrain — donc hors course
sur quatre critères du set têtard (capacité 4, format compact 4, jardin 4, terrain 3).
Mesuré sur des fiches réelles (Beaufortain, Diois, Orpi) : le titre et `og:description`
donnent les pièces au mieux, JAMAIS les chambres ni le terrain ; le corps les porte à
chaque fois. On le lit donc, sans le stocker : 12 800 caractères de navigation feraient
une description illisible sur le site.
"""

from app.services.agences_ingest import _texte_visible
from app.services.completion import completer, pieces, normalize


class _Bien:
    """Les seuls champs que `completer` regarde."""

    def __init__(self, **kw):
        for champ in ("description", "adresse", "nb_pieces", "nb_chambres",
                      "surface_terrain", "surface_bati"):
            setattr(self, champ, kw.get(champ))


# --- Extraction du texte lisible --------------------------------------------------------

def test_les_scripts_sont_retires_avant_le_texte():
    # Le piège : un script porte souvent du JSON plein de nombres. Laissé dans le texte,
    # il se ferait lire comme le contenu de l'annonce.
    html = '<script>var d = {"chambres": 99, "terrain": 8000};</script><p>2 chambres</p>'
    texte = _texte_visible(html)
    assert "99" not in texte
    assert completer(_Bien(), texte_source=texte) == {"nb_chambres": 2}


def test_styles_et_balises_disparaissent():
    html = "<style>.a{color:red}</style><div><b>Maison</b> de charme</div>"
    assert _texte_visible(html) == "Maison de charme"


def test_page_absente_ou_vide():
    assert _texte_visible(None) == ""
    assert _texte_visible("") == ""


# --- Ce que le corps apporte ------------------------------------------------------------

def test_chambres_et_terrain_viennent_du_corps():
    corps = _texte_visible(
        "<h1>Maison Brenod</h1><ul><li>6 pièces</li><li>5 chambres</li>"
        "<li>terrain de 1 200 m²</li></ul>")
    ecrits = completer(_Bien(), texte_source=corps)
    assert ecrits == {"nb_pieces": 6, "nb_chambres": 5, "surface_terrain": 1200.0}


def test_un_champ_deja_connu_n_est_jamais_ecrase():
    # La source fait autorité : le corps ne sert qu'à combler les trous.
    bien = _Bien(nb_chambres=3)
    ecrits = completer(bien, texte_source="la maison compte 5 chambres")
    assert bien.nb_chambres == 3
    assert "nb_chambres" not in ecrits


def test_le_texte_source_remplace_la_description_stockee():
    # `texte_source` est un texte À LIRE, pas à stocker : il prime sur la description du
    # bien, qui pour une fiche d'agence n'est qu'un titre.
    bien = _Bien(description="Maison Brenod - Vente 6 pièces")
    completer(bien, texte_source="4 chambres et un terrain de 300 m2")
    assert (bien.nb_chambres, bien.surface_terrain) == (4, 300.0)


# --- Le motif de pièces élargi ----------------------------------------------------------

def test_notation_orpi_avec_tiret_et_deux_chiffres():
    # « T-3 » et « T-17 » : le motif à un seul chiffre sans tiret les laissait passer, et
    # les titres Orpi n'annoncent le nombre de pièces que sous cette forme.
    assert pieces(normalize("Maison Tenay m² T-3 à vendre, 152 000 € | Orpi")) == 3
    assert pieces(normalize("Immeuble Hauteville-Lompnes m² T-17 à vendre")) == 17


def test_forme_des_url_de_recherche():
    # Certaines agences stockent l'URL de recherche comme texte de l'annonce.
    assert pieces(normalize("https://x.fr/vente-maisons-4pieces-26410--893.php")) == 4


def test_une_reference_n_est_pas_un_nombre_de_pieces():
    assert pieces(normalize("Réf: 2763 - Du Beaufortain")) is None
