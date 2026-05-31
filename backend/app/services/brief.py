"""Conversion d'un brief en langage naturel en préférences structurées.

Deux voies : parseur LLM (API Claude) si une clé est configurée, sinon repli
heuristique par mots-clés. Le résultat (liste de préférences pondérées) est
directement éditable et utilisable comme `criteria.preferences`.
"""

from __future__ import annotations

import json
import re

from ..config import get_settings
from .geo import CITY_COORDS
from .preferences import PREFERENCE_KINDS

_SYSTEM = f"""Tu convertis un brief immobilier en français en préférences de recherche pondérées.

Régime : RANKING (aucune exclusion). Chaque préférence a:
- "kind" parmi: {", ".join(PREFERENCE_KINDS)}
- "weight" (0.5 = secondaire, 1 = normal, 2 = prioritaire/"privilégié/au moins")
- "params" (objet, selon le kind)
- "label" (libellé court en français)

Paramètres par kind:
- budget: {{"apport": <€>, "levier": <x, défaut 4>}} ou {{"budget_max": <€>}}
- chambres_min: {{"min": <int>}}
- has_terrain: {{"min_surface": <m², optionnel>}}
- light_works: {{}}  (travaux légers acceptés/souhaités)
- no_vis_a_vis: {{}}
- nature_exception: {{}}
- authentic: {{}}
- near_corridor: {{"villes": ["Paris","Marseille"], "max_km": <km, défaut 40>}}
- near_gare: {{"max_km": <km, défaut 10>}}
- near_city: {{"ville": "Paris", "max_km": <km>}}
- rail_time_from: {{"ville": "Paris", "max_minutes": <int>}}
- fiber: {{}}
- relief_mountain: {{}}
- hiking: {{}}

Villes géocodées connues: {", ".join(sorted(CITY_COORDS))}.
Réponds UNIQUEMENT par un tableau JSON de préférences, sans texte autour."""


def _llm_parse(text: str) -> list[dict] | None:
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.extract_model,
        max_tokens=2048,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": text}],
    )
    out = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    start, end = out.find("["), out.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(out[start : end + 1])
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def _heuristic_parse(text: str) -> list[dict]:
    t = text.lower()
    prefs: list[dict] = []

    m = re.search(r"(\d[\d  .]{3,})\s*€?\s*d['’\s]*apport", t) or re.search(
        r"apports?[^\d]{0,15}(\d[\d  .]{3,})", t
    )
    if m:
        apport = int(re.sub(r"[\s .€]", "", m.group(1)))
        prefs.append({"kind": "budget", "weight": 2, "params": {"apport": apport, "levier": 4}, "label": f"Budget (apport {apport}€)"})

    m = re.search(r"(\d+)\s*(?:/\s*\d+\s*)?(?:personnes|chambres)", t)
    if m:
        prefs.append({"kind": "chambres_min", "weight": 2, "params": {"min": int(m.group(1))}, "label": f"≥ {m.group(1)} chambres"})

    if "terrain" in t:
        prefs.append({"kind": "has_terrain", "weight": 1, "params": {}, "label": "Avec terrain"})
    if "travaux" in t or "rénover" in t or "renover" in t:
        prefs.append({"kind": "light_works", "weight": 1, "params": {}, "label": "Travaux légers"})
    if "vis-à-vis" in t or "vis a vis" in t or "vis à vis" in t:
        prefs.append({"kind": "no_vis_a_vis", "weight": 1.5, "params": {}, "label": "Sans vis-à-vis"})
    if "exception" in t or "authentique" in t or "cachet" in t:
        prefs.append({"kind": "nature_exception", "weight": 2, "params": {}, "label": "Nature d'exception"})
        prefs.append({"kind": "authentic", "weight": 1.5, "params": {}, "label": "Authentique / cachet"})
    if "gare" in t:
        prefs.append({"kind": "near_gare", "weight": 1, "params": {"max_km": 15}, "label": "Proche d'une gare"})
    if "fibre" in t or "télétravail" in t or "teletravail" in t:
        prefs.append({"kind": "fiber", "weight": 1, "params": {}, "label": "Fibre (télétravail)"})
    if "montagn" in t or "relief" in t:
        prefs.append({"kind": "relief_mountain", "weight": 1, "params": {}, "label": "Relief / montagne"})
    if "randonn" in t:
        prefs.append({"kind": "hiking", "weight": 1, "params": {}, "label": "Randonnées"})
    if "train" in t and ("paris" in t):
        prefs.append({"kind": "rail_time_from", "weight": 1.5, "params": {"ville": "Paris", "max_minutes": 180}, "label": "Trajet train depuis Paris"})

    # Corridor : deux villes connues mentionnées (ex. "axe Paris Marseille").
    cited = [c for c in CITY_COORDS if c in t]
    if len(cited) >= 2:
        prefs.append({"kind": "near_corridor", "weight": 1.5, "params": {"villes": [c.title() for c in cited[:2]], "max_km": 40}, "label": f"Axe {cited[0].title()}-{cited[1].title()}"})

    return prefs


def parse_brief(text: str) -> dict:
    """Renvoie {'preferences': [...], 'parser': 'llm'|'heuristic'}."""
    settings = get_settings()
    if settings.llm_extract_available:
        try:
            import anthropic  # noqa: F401

            parsed = _llm_parse(text)
            if parsed is not None:
                return {"preferences": parsed, "parser": "llm"}
        except Exception:
            pass
    return {"preferences": _heuristic_parse(text), "parser": "heuristic"}
