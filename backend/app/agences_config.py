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


@dataclass
class AgencesConfig:
    imap: dict = field(default_factory=dict)
    agences: list[AgenceConfig] = field(default_factory=list)

    @property
    def all_site_urls(self) -> list[tuple[str, str]]:
        """Liste de (nom_agence, url) pour tous les sites configurés."""
        return [(a.nom, url) for a in self.agences for url in a.sites]


def load_agences_config(path: str) -> AgencesConfig:
    """Charge la config depuis un YAML. Renvoie une config vide si absent."""
    if not path or not os.path.exists(path):
        return AgencesConfig()
    import yaml

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    agences = [
        AgenceConfig(nom=str(a.get("nom") or "Agence"), sites=list(a.get("sites") or []))
        for a in (data.get("agences") or [])
    ]
    return AgencesConfig(imap=dict(data.get("imap") or {}), agences=agences)
