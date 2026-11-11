import json
import unicodedata
from functools import lru_cache

from app.settings import settings


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(raw.casefold().split())


@lru_cache(maxsize=1)
def load_kb() -> dict:
    return json.loads(settings.kb_path.read_text(encoding="utf-8"))


def reload_kb() -> dict:
    load_kb.cache_clear()
    return load_kb()


def search_kb(text: str, hint_ids: list[str] | None = None, limit: int = 4) -> list[dict]:
    data = load_kb()
    facts = data.get("facts") or []
    by_id = {item["id"]: item for item in facts if isinstance(item, dict)}
    scored: list[tuple[int, dict]] = []
    folded = _fold(text)

    if hint_ids:
        for fact_id in hint_ids:
            fact = by_id.get(fact_id)
            if fact:
                scored.append((100, fact))

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if any(item["id"] == fact["id"] for _, item in scored):
            continue
        blob = _fold(
            " ".join(
                [
                    str(fact.get("title") or ""),
                    str(fact.get("claim") or ""),
                    str(fact.get("detail") or ""),
                    " ".join(fact.get("keywords") or []),
                ]
            )
        )
        score = 0
        for word in folded.split():
            if len(word) < 4:
                continue
            if word in blob:
                score += 2
        for key in fact.get("keywords") or []:
            key_fold = _fold(str(key))
            if key_fold and key_fold in folded:
                score += 6
        if score:
            scored.append((score, fact))

    scored.sort(key=lambda item: item[0], reverse=True)
    out = []
    for score, fact in scored[:limit]:
        out.append(
            {
                "id": fact["id"],
                "theme": fact.get("theme") or "",
                "title": fact.get("title") or "",
                "claim": fact.get("claim") or "",
                "detail": fact.get("detail") or "",
                "score": score,
                "source_title": fact.get("source_title") or "",
                "source_url": fact.get("source_url") or "",
                "source_kind": fact.get("source_kind") or "oficial",
            }
        )
    return out


def kb_sources(matches: list[dict]) -> list[dict]:
    sources = []
    seen = set()
    for item in matches:
        url = str(item.get("source_url") or "").strip()
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": item.get("source_title") or item.get("title") or url,
                "url": url,
                "kind": item.get("source_kind") or "oficial",
                "excerpt": str(item.get("detail") or item.get("claim") or "")[:280],
            }
        )
    return sources
