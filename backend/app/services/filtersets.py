"""Résolution des sous-sets de filtres (héritage parent → enfant).

Un sous-set (ex. « Léo ») hérite des critères de son parent (« têtard ») et les
surcharge :
- les champs simples (property_types, prix_max, secteur...) de l'enfant remplacent
  ceux du parent s'ils sont définis ;
- les préférences sont fusionnées par `kind` : une préférence de l'enfant remplace
  (poids/params) celle du parent de même `kind`, les nouvelles s'ajoutent.
"""

from __future__ import annotations


def _merge_preferences(parent: list[dict], child: list[dict]) -> list[dict]:
    by_kind: dict[str, dict] = {}
    order: list[str] = []
    for p in parent + child:
        kind = p.get("kind")
        if kind is None:
            continue
        if kind not in by_kind:
            order.append(kind)
        by_kind[kind] = p  # l'enfant (passé en second) écrase le parent
    return [by_kind[k] for k in order]


def merge_criteria(parent: dict | None, child: dict) -> dict:
    """Fusionne les critères parent et enfant (l'enfant prime)."""
    if not parent:
        return dict(child or {})
    merged = dict(parent)
    for key, value in (child or {}).items():
        if key == "preferences":
            merged["preferences"] = _merge_preferences(
                parent.get("preferences") or [], value or []
            )
        elif value is not None:
            merged[key] = value
    # Préférences : si l'enfant n'en redéfinit pas, on garde celles du parent.
    if "preferences" not in (child or {}):
        merged["preferences"] = parent.get("preferences") or []
    return merged


def resolve_criteria(fs) -> dict:
    """Critères effectifs d'un FilterSet, en remontant la chaîne des parents."""
    chain = []
    node = fs
    seen = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append(node)
        node = node.parent
    # Du plus ancêtre vers l'enfant : on fusionne successivement.
    criteria: dict = {}
    for node in reversed(chain):
        criteria = merge_criteria(criteria, node.criteria or {})
    return criteria
