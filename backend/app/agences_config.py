"""Chargement de la configuration des agences (fichier YAML versionnable).

Exemple `agences.yaml` :

    # Surcharge optionnelle de la boîte IMAP (sinon variables d'environnement)
    imap:
      host: imap.exemple.fr
      user: prospection@exemple.fr
      folder: INBOX

    agences:
      - nom: "Agence du Coin"
        # URLs de pages d'annonces à scraper (sites peu/pas protégés)
        sites:
          - https://agence-du-coin.fr/nos-biens
        # Set auquel rattacher ses biens (optionnel). Sans lui, un bien d'agence est
        # noté pour TOUS les sets : une agence bretonne polluerait le set Drôme/Ardèche.
        set_id: 4
        # Départements attendus (optionnel). Un réseau national partage un seul domaine :
        # la récolte de liens sort de la zone et ramène des biens d'ailleurs.
        departements: [22, 29, 56]
      - nom: "Terres & Demeures"
        sites: []
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AgenceConfig:
    nom: str
    sites: list[str] = field(default_factory=list)
    set_id: int | None = None
    # Un set ET son sous-set : un bien rattaché au seul set parent disparaît quand on
    # bascule sur le sous-set dans le front, alors qu'il le concerne tout autant.
    set_ids: list[int] = field(default_factory=list)
    departements: list[str] = field(default_factory=list)


@dataclass
class AgencesConfig:
    imap: dict = field(default_factory=dict)
    agences: list[AgenceConfig] = field(default_factory=list)

    @property
    def all_site_urls(self) -> list[tuple[str, str]]:
        """Liste de (nom_agence, url) pour tous les sites configurés."""
        return [(a.nom, url) for a in self.agences for url in a.sites]

    @property
    def set_par_agence(self) -> dict[str, list[int]]:
        """{nom_agence: [set_ids]} pour les agences qui en déclarent."""
        out = {}
        for a in self.agences:
            ids = list(a.set_ids) or ([a.set_id] if a.set_id is not None else [])
            if ids:
                out[a.nom] = sorted({int(i) for i in ids})
        return out

    @property
    def departements_par_agence(self) -> dict[str, list[str]]:
        """{nom_agence: [départements]} pour les agences qui en déclarent."""
        return {a.nom: a.departements for a in self.agences if a.departements}


def load_agences_config(path: str) -> AgencesConfig:
    """Charge la config depuis un YAML. Renvoie une config vide si absent."""
    if not path or not os.path.exists(path):
        return AgencesConfig()
    import yaml

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    agences = [
        AgenceConfig(nom=str(a.get("nom") or "Agence"), sites=list(a.get("sites") or []),
                     set_id=int(a["set_id"]) if a.get("set_id") is not None else None,
                     set_ids=[int(i) for i in (a.get("set_ids") or [])],
                     departements=[str(d).zfill(2) for d in (a.get("departements") or [])])
        for a in (data.get("agences") or [])
    ]
    return AgencesConfig(imap=dict(data.get("imap") or {}), agences=agences)
