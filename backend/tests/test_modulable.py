from app.schemas import Preference
from app.services.modulable import detecter, noter, resumer
from app.services.preferences import evaluate
from app.sources.base import NormalizedListing


def _listing(**kw):
    base = dict(source="x", external_id="1", type_bien="maison")
    base.update(kw)
    return NormalizedListing(**base)


def test_detecte_les_volumes_convertibles():
    sig = detecter("Maison de village avec grange attenante de 120 m² et combles aménageables.")
    assert "grange" in sig and "combles_amenageables" in sig
    assert noter(sig) == 1.0


def test_un_volume_n_est_compte_qu_une_fois():
    # « Combles aménageables » déclenche aussi « combles » : sans absorption, un même
    # volume vaudrait 0,85 — plus qu'une grange.
    sig = detecter("Maison avec combles aménageables.")
    assert sig == ["combles_amenageables"]
    assert noter(sig) == noter(["grange"])
    # Le grenier suit la même règle.
    assert detecter("Grenier aménageable sur toute la surface.") == ["grenier_amenageable"]


def test_un_seul_signal_fort_ne_suffit_pas_a_la_note_pleine():
    # Une grange citée en passant n'est pas un dortoir prouvé : elle monte, elle ne sature pas.
    seule = noter(detecter("Maison avec grange."))
    deux = noter(detecter("Maison avec grange et dépendance à aménager."))
    assert 0.5 < seule < 0.8
    assert deux > seule


def test_silence_de_l_annonce_vaut_le_socle_et_non_n_a():
    # Le point du critère : une feature absente vaudrait `n/a` et ne ferait donc que
    # monter celui dont l'annonce a employé le mot. Ici l'annonce lue sans volume note bas.
    assert detecter("Villa de plain-pied construite en 2022, jardin clos.") == []
    assert noter([]) == 0.2


def test_combles_perdus_ne_comptent_pas():
    assert "combles" not in detecter("Maison rénovée, combles perdus isolés.")
    assert noter(detecter("Maison rénovée, combles perdus isolés.")) == 0.2
    # Mais la négation n'efface pas un volume cité ailleurs dans l'annonce.
    assert "grange" in detecter("Combles perdus, mais une grange de 80 m² attenante.")


def test_la_cave_n_est_pas_un_dortoir():
    assert detecter("Maison avec cave voûtée et garage.") == []


def test_le_critere_note_et_explique_dans_le_set():
    item = _listing(flags={"espace_modulable": ["grange", "mezzanine"]})
    score, details = evaluate(item, [Preference(kind="espace_modulable", weight=3)])
    ligne = next(d for d in details if d["kind"] == "espace_modulable")
    assert ligne["status"] == "ok" and score > 60
    assert "grange" in ligne["detail"] and "mezzanine" in ligne["detail"]


def test_pending_quand_l_annonce_n_a_pas_ete_analysee():
    item = _listing(flags={})
    _, details = evaluate(item, [Preference(kind="espace_modulable")])
    assert details[0]["status"] == "pending"


def test_annonce_sans_texte_est_ignoree_et_non_penalisee():
    # Rien à lire n'est pas rien à trouver : le bien est jugé sans ce critère.
    assert detecter(None) is None
    assert detecter("") is None
    item = _listing(flags={"espace_modulable": detecter(None)})
    _, details = evaluate(item, [Preference(kind="espace_modulable")])
    assert details[0]["status"] == "n/a" and details[0]["subscore"] is None


def test_resume_lisible():
    assert resumer([]) == "aucun volume convertible décrit dans l'annonce"
    assert resumer(["combles_amenageables"]) == "combles aménageables"
