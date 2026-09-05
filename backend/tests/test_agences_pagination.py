"""Pagination des pages de liste d'agences.

Le collecteur ne lisait qu'une page par agence, et le plafond ne se voyait pas : un site
qui ne rend que sa première page ressemble à un petit site. Orpi Ain Agences était à
exactement 40 biens — la valeur du cap de récolte des liens, pas son stock — et Diois
Immobilier à 10 quand son site en annonce 45.

Rien n'est codé par agence : la page suivante se reconnaît à ce qu'elle est la MÊME URL
à un nombre près.
"""

from app.services.agences_parsers import pagination_links


def _a(*hrefs: str) -> str:
    return "".join(f'<a href="{h}">x</a>' for h in hrefs)


# --- Le cas courant : un numéro dans le chemin ----------------------------------------

def test_numero_dans_le_chemin():
    html = _a("/vente/2", "/vente/3", "/vente/4")
    assert pagination_links(html, "https://a.fr/vente/1") == [
        "https://a.fr/vente/2", "https://a.fr/vente/3", "https://a.fr/vente/4"]


def test_les_pages_precedentes_sont_ignorees():
    # On collecte vers l'avant : la page 1 est déjà lue, la 2 l'a été si on vient d'elle.
    html = _a("/vente/1", "/vente/2", "/vente/4")
    assert pagination_links(html, "https://a.fr/vente/3") == ["https://a.fr/vente/4"]


def test_un_autre_chemin_au_meme_gabarit_ne_compte_pas():
    # « /agence/2 » n'est pas la page 2 de « /vente/1 ».
    html = _a("/agence/2", "/actualites/5")
    assert pagination_links(html, "https://a.fr/vente/1") == []


# --- Le numéro en paramètre de requête ------------------------------------------------

def test_parametre_de_page_quand_l_url_n_a_pas_de_numero():
    html = _a("/nos-biens?page=2", "/nos-biens?page=3")
    assert pagination_links(html, "https://a.fr/nos-biens") == [
        "https://a.fr/nos-biens?page=2", "https://a.fr/nos-biens?page=3"]


def test_un_parametre_qui_n_est_pas_une_page_ne_compte_pas():
    # Un filtre de tri ou de prix porte aussi un nombre : il ne pagine rien.
    html = _a("/nos-biens?prix_max=250000", "/nos-biens?tri=3")
    assert pagination_links(html, "https://a.fr/nos-biens") == []


# --- Les pièges qui coûtent cher ------------------------------------------------------

def test_une_fiche_n_est_pas_une_page():
    # « /annonce/1234 » et « /annonce/1235 » ont le même gabarit : c'est la taille du
    # nombre qui les sépare d'une pagination. Sans ce garde-fou, le collecteur suivrait
    # les fiches en croyant tourner les pages.
    html = _a("/annonce/1235", "/annonce/9871")
    assert pagination_links(html, "https://a.fr/annonce/1234") == []


def test_un_autre_domaine_est_ecarte():
    html = _a("https://autre-site.fr/vente/2")
    assert pagination_links(html, "https://a.fr/vente/1") == []


def test_rel_next_est_suivi_meme_sans_numero():
    # Certains sites n'exposent que « page suivante », sans liste de numéros.
    html = '<link rel="next" href="/nos-biens/suite">'
    assert pagination_links(html, "https://a.fr/nos-biens") == ["https://a.fr/nos-biens/suite"]


def test_le_plafond_est_respecte():
    html = _a(*[f"/vente/{i}" for i in range(2, 20)])
    assert len(pagination_links(html, "https://a.fr/vente/1", max_pages=5)) == 5


def test_site_sans_pagination():
    html = _a("/contact", "/qui-sommes-nous", "/annonce/4821")
    assert pagination_links(html, "https://a.fr/nos-biens") == []
