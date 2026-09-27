"""Confirmed pen-name identities, separate from heuristic name matching."""

from .author_sources import source_asins


# Explicitly confirmed identities, not inferred from shared books or series.
# Include Audible's stable IDs and the generated IDs used when credits omit one.
CONFIRMED_IDENTITIES = (
    ("B08VWFRTMS", "B0HF6MXJXD", "name:shirtaloon", "name:travis-deverell"),
)
_GROUP_BY_SOURCE = {
    source: index for index, group in enumerate(CONFIRMED_IDENTITIES) for source in group
}


def confirmed_identity_key(author):
    groups = {_GROUP_BY_SOURCE[source] for source in source_asins(author) if source in _GROUP_BY_SOURCE}
    return next(iter(groups)) if len(groups) == 1 else None


def confirmed_author_match(left, right):
    if left.get("marketplace") and right.get("marketplace") and left["marketplace"] != right["marketplace"]:
        return False
    group = confirmed_identity_key(left)
    return group is not None and group == confirmed_identity_key(right)
