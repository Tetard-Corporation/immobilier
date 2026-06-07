"""Export d'un instantané statique (JSON + photos) pour le front GitHub Pages.

GitHub Pages est un hébergement statique : il ne peut ni exécuter le moteur Python ni
scraper. Le front lit donc ce snapshot produit par le backend. On exporte :
- les sets de filtres (têtard + sous-sets) avec leurs préférences,
- le catalogue des biens réels rencontrés (dédoublonnés), avec photos téléchargées,
  le score d'investissement détaillé et le match_score recalculé pour CHAQUE set,
- l'historique systématique des recherches.
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
from datetime import datetime, timezone

try:  # optimisation des images (optionnelle : repli sur l'octet brut si absente)
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from ..models import FilterSet, Listing, SavedListing, SearchHistory
from .filtersets import resolve_criteria
from .preferences import evaluate

# Colonnes DB dont le nom correspond 1:1 aux clés `flags` consommées par evaluate().
# (mapping inverse de search.upsert_listing, qui écrit flags.get(<col>) -> colonne)
_FLAG_COLS = (
    "condition", "niveau_travaux", "features", "nuisances", "nature_score",
    "nature_exception", "score", "score_details", "constructible", "est_zone_au",
    "zone_urba", "altitude", "rail_time_min", "risques", "prix_m2_secteur",
    "ecart_prix_pct", "pollution_eau_score", "eau_potable_conforme", "pollutions",
    "age_median", "part_gauche", "population_commune", "isolement_score", "price_decreased",
)

_UA = "Mozilla/5.0 (compatible; immobilier-export/1.0)"
_MAX_PHOTOS = 10
# Optimisation : galerie web -> 1280 px max suffit ; JPEG progressif qualité 78.
_MAX_DIM = 1280
_JPEG_QUALITY = 78


def _optimize_jpeg(data: bytes) -> bytes:
    """Redimensionne (≤_MAX_DIM) et recompresse en JPEG ; renvoie l'original si échec."""
    if Image is None:
        return data
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")  # supprime alpha/EXIF, force JPEG-compatible
        im.thumbnail((_MAX_DIM, _MAX_DIM))  # garde le ratio, ne sur-échantillonne pas
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True, progressive=True)
        out = buf.getvalue()
        return out if out and len(out) < len(data) else data
    except Exception:
        return data


class _RowItem:
    """Adapte une ligne DB Listing à l'objet `item` attendu par evaluate() (.flags)."""

    def __init__(self, row: Listing):
        self.prix = row.prix
        self.nb_chambres = row.nb_chambres
        self.surface_terrain = row.surface_terrain
        self.latitude = row.latitude
        self.longitude = row.longitude
        self.flags = {c: getattr(row, c) for c in _FLAG_COLS}


def _pref_dump(pref) -> dict:
    if isinstance(pref, dict):
        return {"kind": pref.get("kind"), "label": pref.get("label") or pref.get("kind"),
                "weight": pref.get("weight", 1.0), "params": pref.get("params") or {}}
    return {"kind": getattr(pref, "kind", None), "label": getattr(pref, "label", None),
            "weight": getattr(pref, "weight", 1.0), "params": getattr(pref, "params", {}) or {}}


def _photo_urls(row: Listing) -> list[str]:
    """Extrait les URLs de photos du payload source (best-effort, multi-source)."""
    raw = row.raw if isinstance(row.raw, dict) else {}
    urls: list[str] = []
    for ph in raw.get("photos") or raw.get("images") or []:
        if isinstance(ph, str):
            urls.append(ph)
        elif isinstance(ph, dict):
            u = ph.get("url") or ph.get("url_photo") or ph.get("urlThumbnail") or ph.get("href")
            if u:
                urls.append(u)
    # dédoublonne en gardant l'ordre
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:_MAX_PHOTOS]


def _download_photos(row: Listing, photos_dir: str, rel_base: str) -> list[str]:
    """Télécharge les photos en local ; renvoie les chemins relatifs (depuis data.json)."""
    key = f"{row.source}_{row.external_id}".replace("/", "_")
    dest_dir = os.path.join(photos_dir, key)
    rels: list[str] = []
    for i, url in enumerate(_photo_urls(row)):
        rel = f"{rel_base}/{key}/{i}.jpg"
        path = os.path.join(dest_dir, f"{i}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            rels.append(rel)
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": row.url or ""})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if data:
                data = _optimize_jpeg(data)
                with open(path, "wb") as fh:
                    fh.write(data)
                rels.append(rel)
        except Exception:
            continue  # photo indisponible -> on saute, sans casser l'export
    return rels


def build_dataset(db, *, out_dir: str | None = None, download_photos: bool = False) -> dict:
    """Construit le dataset statique. Si download_photos, écrit les images sous out_dir."""
    sets = (
        db.query(FilterSet)
        .order_by(FilterSet.parent_id.isnot(None), FilterSet.id)
        .all()
    )
    set_prefs: dict[int, list] = {}
    sets_out = []
    for fs in sets:
        # Préférences RÉSOLUES : un sous-set hérite des préférences de son parent
        # (fusionnées par resolve_criteria), pour une comparaison set/sous-set fidèle.
        prefs = (resolve_criteria(fs) or {}).get("preferences") or []
        set_prefs[fs.id] = prefs
        sets_out.append({
            "id": fs.id, "name": fs.name, "parent_id": fs.parent_id,
            "description": fs.description,
            "preferences": [_pref_dump(p) for p in prefs],
        })

    saved = {(s.source, s.external_id): s for s in db.query(SavedListing).all()}

    photos_dir = os.path.join(out_dir, "photos") if out_dir else None
    if photos_dir and download_photos:
        os.makedirs(photos_dir, exist_ok=True)

    biens_out = []
    rows = (
        db.query(Listing)
        .filter(Listing.source != "mock")
        .order_by(Listing.score.isnot(None).desc(), Listing.score.desc())
        .all()
    )
    for row in rows:
        item = _RowItem(row)
        scores_by_set = {}
        for fs_id, prefs in set_prefs.items():
            if not prefs:
                continue
            match, details = evaluate(item, prefs)
            scores_by_set[str(fs_id)] = {"match_score": match, "details": details}

        sv = saved.get((row.source, row.external_id))
        photos = _download_photos(row, photos_dir, "photos") if (download_photos and photos_dir) else []
        biens_out.append({
            "id": row.id, "source": row.source, "external_id": row.external_id,
            "type_bien": row.type_bien, "prix": row.prix, "nb_chambres": row.nb_chambres,
            "nb_pieces": row.nb_pieces, "surface_terrain": row.surface_terrain,
            "surface_bati": row.surface_bati, "commune": row.commune,
            "code_postal": row.code_postal, "departement": row.departement,
            "latitude": row.latitude, "longitude": row.longitude,
            "url": row.url, "description": row.description, "dpe_classe": row.dpe_classe,
            "condition": row.condition, "features": row.features, "nuisances": row.nuisances,
            "altitude": row.altitude, "rail_time_min": row.rail_time_min,
            "isolement_score": row.isolement_score, "population_commune": row.population_commune,
            "risques": row.risques, "score": row.score, "score_details": row.score_details,
            "scores_by_set": scores_by_set,
            "is_favori": sv is not None,
            "favori_note": sv.note if sv else None,
            "n_photos_source": len(_photo_urls(row)),
            "photos": photos,
        })

    searches_out = [
        {
            "id": h.id, "source": h.source, "criteria": h.criteria,
            "filter_set_id": h.filter_set_id, "nb_results": h.nb_results,
            "enriched": h.enriched, "top_results": h.top_results,
            "ran_at": h.ran_at.isoformat() if h.ran_at else None,
        }
        for h in db.query(SearchHistory).order_by(SearchHistory.ran_at.desc()).all()
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sets": sets_out,
        "biens": biens_out,
        "searches": searches_out,
        "stats": {"n_biens": len(biens_out), "n_sets": len(sets_out), "n_searches": len(searches_out)},
    }


def export_to_dir(db, out_dir: str, *, download_photos: bool = True) -> dict:
    """Écrit out_dir/data.json (+ photos/) et renvoie les stats."""
    os.makedirs(out_dir, exist_ok=True)
    data = build_dataset(db, out_dir=out_dir, download_photos=download_photos)
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return data["stats"]


if __name__ == "__main__":  # python -m app.services.export_static [out_dir]
    import sys

    from ..db import SessionLocal

    out = sys.argv[1] if len(sys.argv) > 1 else "../docs/data"
    no_photos = "--no-photos" in sys.argv
    stats = export_to_dir(SessionLocal(), out, download_photos=not no_photos)
    print(f"Export -> {out}/data.json : {stats}")
