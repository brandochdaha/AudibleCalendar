"""Audible is authoritative; supplementary metadata only fills missing fields."""


def enrich_missing_fields(audible, supplement):
    result = dict(audible)
    for key, value in supplement.items():
        if key in {"source_payload", "release_date_precision"} or value in (None, "", [], {}):
            continue
        missing = result.get(key) in (None, "", [], {})
        if key == "title" and result.get(key) == "Untitled audiobook":
            missing = True
        if key == "is_preorder" and isinstance(audible.get("source_payload"), dict):
            missing = not any(field in audible["source_payload"] for field in ("isPreorder", "is_preorder", "preorder"))
        if missing:
            result[key] = value
            if key == "release_date":
                result["release_date_precision"] = supplement.get("release_date_precision") or "unknown"
    return result
