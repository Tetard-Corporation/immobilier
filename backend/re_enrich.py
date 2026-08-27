"""Re-enrichit les biens qui ont perdu leurs flags d'enrichissement (DVF, pollution,
GPU, socio, densité, relief) — typiquement les biens re-seedés depuis data.json.
Enrichissement parallèle (I/O), écriture DB mono-thread. Skippe géorisques (lent,
traité par re_risques.py) et hiking (Overpass, traité par warm.py).

Usage :
    python re_enrich.py               # comble les flags manquants (tous providers)
    python re_enrich.py --force-dvf   # recalcule les comparables DVF de TOUS les biens

`--force-dvf` sert quand la méthode de calcul des comparables change (et non quand la
donnée manque) : le mode par défaut saute les biens qui ont déjà un `prix_m2_secteur`,
donc il ne corrigerait jamais une référence déjà écrite mais fausse. Ce mode ne fait
tourner QUE le provider DVF, et repart des flags SANS l'ancienne référence : si le
recalcul échoue, le bien se retrouve sans comparables plutôt qu'avec une valeur périmée.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import SessionLocal
from app.enrichment import enrich_listing, reset_providers
from app.enrichment.densite import DensiteProvider
from app.enrichment.dvf import DvfComparablesProvider
from app.enrichment.fibre import FibreProvider
from app.enrichment.gpu import GpuZonageProvider
from app.enrichment.pollution import PollutionProvider
from app.enrichment.relief import ReliefProvider
from app.enrichment.socio import SocioProvider
from app.models import Listing
from app.services.export_static import _FLAG_COLS
from app.sources.base import NormalizedListing

WORKERS = 6
# Colonnes d'enrichissement à (ré)écrire depuis les flags calculés.
ENRICH_COLS = ("constructible", "est_zone_au", "zone_urba", "altitude", "pollution_eau_score",
               "eau_potable_conforme", "pollutions", "age_median", "part_gauche",
               "population_commune", "isolement_score", "prix_m2_secteur", "ecart_prix_pct")

FORCE_DVF = "--force-dvf" in sys.argv
# Colonnes de comparables : réécrites même à None en mode --force-dvf (voir docstring).
DVF_COLS = ("prix_m2_secteur", "ecart_prix_pct")

if FORCE_DVF:
    reset_providers([DvfComparablesProvider()])
else:
    reset_providers([GpuZonageProvider(), ReliefProvider(), PollutionProvider(),
                     SocioProvider(), DensiteProvider(), DvfComparablesProvider(), FibreProvider()])


def _item(r):
    skip = {"score", "score_details"} | (set(DVF_COLS) if FORCE_DVF else set())
    flags = {c: getattr(r, c) for c in _FLAG_COLS if getattr(r, c, None) is not None
             and c not in skip}
    return NormalizedListing(
        source=r.source, external_id=r.external_id, type_bien=r.type_bien, prix=r.prix,
        surface_terrain=r.surface_terrain, surface_bati=r.surface_bati, commune=r.commune,
        code_postal=r.code_postal, code_commune=r.code_commune, departement=r.departement,
        latitude=r.latitude, longitude=r.longitude, description=r.description, flags=flags)


def main():
    db = SessionLocal()
    rows = db.query(Listing).filter(Listing.source != "mock").all()

    def missing(r):
        return r.latitude is not None and (
            r.prix_m2_secteur is None or r.pollution_eau_score is None or r.zone_urba is None)

    if FORCE_DVF:
        todo = [r for r in rows if r.latitude is not None]
        print(f"{len(todo)}/{len(rows)} biens : recalcul FORCÉ des comparables DVF", flush=True)
    else:
        todo = [r for r in rows if missing(r)]
        print(f"{len(todo)}/{len(rows)} biens à re-enrichir (DVF/pollution/GPU/...)", flush=True)

    def work(r):
        try:
            return r.id, enrich_listing(_item(r)).flags
        except Exception:
            return r.id, None

    t0 = time.time()
    out = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for i, f in enumerate(as_completed(futs), 1):
            rid, flags = f.result()
            if flags is not None:
                out[rid] = flags
            if i % 40 == 0:
                print(f"  {i}/{len(todo)} ({time.time()-t0:.0f}s)", flush=True)

    by_id = {r.id: r for r in rows}
    n = 0
    for rid, flags in out.items():
        row = by_id[rid]
        cols = DVF_COLS if FORCE_DVF else ENRICH_COLS
        for c in cols:
            # En mode forcé on écrit aussi les None : une référence devenue incalculable
            # doit disparaître, pas survivre avec son ancienne valeur.
            if FORCE_DVF or flags.get(c) is not None:
                setattr(row, c, flags.get(c)); n += 1
    db.commit()
    print(f"OK : {len(out)}/{len(todo)} enrichis, {n} colonnes écrites, en {time.time()-t0:.0f}s", flush=True)
    print("RE_ENRICH TERMINÉ.", flush=True)


if __name__ == "__main__":
    main()
