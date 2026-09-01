"""Le registre des critères : identité stable, familles, couverture des sets."""

from app.services.criteres import CRITERES, FAMILLES, fiche, identifiant, registre
from app.services.preferences import PREFERENCE_KINDS


def test_identite_stable_et_feature_discriminee():
    # Le libellé et les paramètres changent, l'identité non.
    assert identifiant("budget", {"budget_max": 250000}) == "budget"
    assert identifiant("budget", {"budget_max": 150000}) == "budget"
    # `feature` est générique : cinq préférences distinctes dans le set breton.
    assert identifiant("feature", {"name": "bord_de_mer"}) == "feature:bord_de_mer"
    assert identifiant("feature", {"name": "vue"}) == "feature:vue"
    assert identifiant("feature", {}) == "feature"


def test_tout_critere_evaluable_est_au_registre():
    # Un critère qu'on peut mettre dans un set mais que le registre ignore serait affiché
    # sans famille ni explication : c'est ce que la normalisation doit empêcher.
    generiques = {"feature"}          # décliné par nom (feature:bord_de_mer…)
    socio = {"population_jeune", "orientation_gauche"}   # données socio non peuplées
    manquants = [k for k in PREFERENCE_KINDS if k not in CRITERES and k not in generiques | socio]
    assert manquants == [], f"critères hors registre : {manquants}"


def test_familles_declarees_et_utilisees():
    ids = {f for f, _ in FAMILLES}
    assert all(fam in ids for fam, _, _ in CRITERES.values())
    # Chaque famille sert à quelque chose (une famille vide est une case décorative).
    utilisees = {fam for fam, _, _ in CRITERES.values()}
    assert utilisees == ids


def test_registre_exporte_est_lisible_par_le_front():
    reg = registre()
    assert [f["id"] for f in reg["familles"]] == [f for f, _ in FAMILLES]
    assert reg["index"]["dpe"]["famille"] == "bien"
    assert reg["index"]["dpe"]["court"] and reg["index"]["dpe"]["quoi"]
    # Un critère inconnu ne casse rien : il garde son id pour nom.
    assert fiche("inconnu")["court"] == "inconnu"
