"""Tests des scripts de données (logique pure, hors-ligne)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_age_dataset import median_age_from_brackets
from scripts.build_socio_dataset import _insee, _norm


def test_age_median_interpolation():
    assert median_age_from_brackets([100] * 7) == 52.5  # uniforme -> milieu
    assert median_age_from_brackets([0] * 7) is None
    jeune = median_age_from_brackets([3000, 5000, 4000, 2000, 1500, 800, 200])
    age = median_age_from_brackets([200, 400, 380, 250, 180, 90, 30])
    assert jeune < age  # commune jeune -> âge médian plus bas


def test_insee_reconstruction():
    assert _insee("01", "001") == "01001"
    assert _insee("2A", "004") == "2A004"
    assert _insee("971", "05") == "97105"  # DOM


def test_norm_candidats():
    assert _norm("Mélenchon") == "MELENCHON"
    assert _norm("  jadot ") == "JADOT"
