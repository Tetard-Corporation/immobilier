"""Photos de l'export : réutilisation du disque, WebP, et seuil de publication.

Le passage au WebP a introduit un défaut qui a cassé un export en plein vol : la
recherche du fichier déjà présent testait sa TAILLE avant son EXISTENCE, donc
`os.path.getsize` levait `FileNotFoundError` sur la première photo absente. Un test qui
part d'un dossier vide l'aurait attrapé.
"""

import io
import os

import pytest

from app.services.export_static import _download_photos, _garde_detail, _optimize_jpeg

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


class _Row:
    """Le minimum que `_download_photos` lit sur une ligne."""

    def __init__(self, raw=None):
        self.source = "test"
        self.external_id = "x"
        self.url = None
        self.raw = raw or {}


def _dossier(tmp_path):
    d = tmp_path / "test_x"
    d.mkdir()
    return d


# --- Réutilisation de ce qui est déjà sur disque ---------------------------------------

def test_dossier_vide_ne_leve_pas(tmp_path):
    # Le cas qui a cassé l'export : aucun fichier, aucune URL.
    assert _download_photos(_Row(), str(tmp_path), "photos") == []


def test_jpeg_existant_reutilise(tmp_path):
    (_dossier(tmp_path) / "0.jpg").write_bytes(b"x")
    assert _download_photos(_Row(), str(tmp_path), "photos") == ["photos/test_x/0.jpg"]


def test_les_deux_extensions_cohabitent(tmp_path):
    # Les photos d'avant le passage au WebP restent valables : on ne les retélécharge pas.
    d = _dossier(tmp_path)
    (d / "0.jpg").write_bytes(b"x")
    (d / "1.webp").write_bytes(b"y")
    assert _download_photos(_Row(), str(tmp_path), "photos") == [
        "photos/test_x/0.jpg", "photos/test_x/1.webp"]


def test_webp_prime_sur_jpeg_pour_le_meme_indice(tmp_path):
    d = _dossier(tmp_path)
    (d / "0.jpg").write_bytes(b"x")
    (d / "0.webp").write_bytes(b"y")
    assert _download_photos(_Row(), str(tmp_path), "photos") == ["photos/test_x/0.webp"]


def test_fichier_vide_ignore(tmp_path):
    (_dossier(tmp_path) / "0.jpg").write_bytes(b"")
    assert _download_photos(_Row(), str(tmp_path), "photos") == []


# --- Encodage --------------------------------------------------------------------------

@pytest.mark.skipif(Image is None, reason="Pillow absent")
def test_encode_en_webp_et_allege():
    buf = io.BytesIO()
    # Un dégradé : compressible, mais pas au point de rendre la comparaison triviale.
    im = Image.new("RGB", (1600, 1200))
    im.putdata([((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
                for y in range(1200) for x in range(1600)])
    im.save(buf, format="JPEG", quality=95)
    brut = buf.getvalue()

    out, ext = _optimize_jpeg(brut)
    assert ext == "webp"
    assert len(out) < len(brut)
    # Redimensionné au plafond, ratio conservé.
    assert Image.open(io.BytesIO(out)).size[0] <= 1280


def test_donnees_illisibles_rendues_telles_quelles():
    # Une photo qu'on ne sait pas décoder vaut mieux qu'une photo perdue.
    assert _optimize_jpeg(b"pas une image") == (b"pas une image", "jpg")


# --- Seuil de publication ---------------------------------------------------------------

def test_sans_seuil_tout_passe():
    assert _garde_detail({"1": {"match_score": 3}}, None, None) is True


def test_le_seuil_porte_sur_le_match_pas_sur_le_score_invest():
    # Les deux ne sont pas sur la même échelle : un bien que le set écarte peut avoir un
    # score d'investissement élevé. L'inclure retenait 2 246 biens au lieu de 806.
    assert _garde_detail({"1": {"match_score": 60}}, 95, 70) is False


def test_un_seul_set_au_dessus_suffit():
    # Un bien peut être médiocre pour têtard et bon pour le littoral.
    assert _garde_detail({"1": {"match_score": 40}, "4": {"match_score": 80}}, None, 70) is True


def test_bien_sans_aucun_score():
    assert _garde_detail({"1": {"match_score": None}}, None, 70) is False


def test_un_bien_sans_droit_aux_photos_garde_celles_deja_publiees(tmp_path):
    # L'intention de `telecharger=False` : ne pas appeler le réseau, sans rien perdre de
    # ce qui était déjà publié.
    d = _dossier(tmp_path)
    (d / "0.jpg").write_bytes(b"x")
    (d / "1.jpg").write_bytes(b"y")
    perimetre = {"photos/test_x/0.jpg", "photos/test_x/1.jpg"}
    assert _download_photos(_Row(), str(tmp_path), "photos",
                            telecharger=False, publiees=perimetre) == [
        "photos/test_x/0.jpg", "photos/test_x/1.jpg"]


def test_mais_il_ne_se_sert_pas_dans_ce_que_personne_n_a_publie(tmp_path):
    # Le disque n'est PAS le périmètre de publication : il porte les photos de tous les
    # biens jamais collectés — 57 000 fichiers — dont le dépôt ne suit qu'une fraction.
    # Mesuré sans ce filtre : 49 734 photos citées pour 5 636 biens, donc autant d'images
    # cassées chez le visiteur.
    d = _dossier(tmp_path)
    (d / "0.jpg").write_bytes(b"x")
    (d / "1.jpg").write_bytes(b"y")
    perimetre = {"photos/test_x/0.jpg"}          # la seconde n'a jamais été publiée
    assert _download_photos(_Row(), str(tmp_path), "photos",
                            telecharger=False, publiees=perimetre) == ["photos/test_x/0.jpg"]


def test_sans_perimetre_connu_on_ne_filtre_pas(tmp_path):
    # Hors dépôt git, en test, sur une autre machine : mieux vaut publier une image de
    # trop que perdre le seul exemplaire d'une photo dans un contexte qu'on ne lit pas.
    (_dossier(tmp_path) / "0.jpg").write_bytes(b"x")
    assert _download_photos(_Row(), str(tmp_path), "photos",
                            telecharger=False, publiees=None) == ["photos/test_x/0.jpg"]
