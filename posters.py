"""프로그램 포스터 URL 캐시 — 안전 URL만 사용."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "posters.json"
_lock = threading.Lock()


def is_safe_poster_url(url: str) -> bool:
    """HTML src 에 넣어도 레이아웃이 깨지지 않는 URL인지 검사."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    if any(ch in url for ch in ('"', "'", "<", ">", "`", "\n", "\r")):
        return False
    if "dthumb-phinf.pstatic.net" in url:
        return False
    if "%22" in url or "%27" in url:
        return False
    return True


def load_poster_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"items": {}}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": {}}
    if not isinstance(data, dict):
        return {"items": {}}
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def save_poster_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_cached_poster(title: str, cache: dict[str, Any] | None = None) -> str:
    cache = cache if cache is not None else load_poster_cache()
    item = (cache.get("items") or {}).get(title) or {}
    url = (item.get("poster_url") or "").strip()
    return url if is_safe_poster_url(url) else ""


def cache_coverage(titles: list[str], cache: dict[str, Any] | None = None) -> tuple[int, int]:
    cache = cache if cache is not None else load_poster_cache()
    items = cache.get("items") or {}
    ok = sum(1 for t in titles if is_safe_poster_url((items.get(t) or {}).get("poster_url") or ""))
    return ok, len(titles)


def ensure_poster_cache(
    contents: list[dict],
    *,
    force: bool = False,
    max_fetch: int | None = 8,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """
    포스터 캐시 보장.
    force=False 이고 커버리지가 충분하면 기존 캐시 사용.
    max_fetch: 한 번에 새로 가져올 최대 건수 (None 이면 제한 없음).
    """
    titles = [c["title"] for c in contents]
    with _lock:
        cache = load_poster_cache()
        ok, total = cache_coverage(titles, cache)
        if not force and total and ok >= max(1, int(total * 0.85)):
            return cache

        items = dict(cache.get("items") or {})
        fetched = 0
        for i, c in enumerate(contents):
            title = c["title"]
            if progress:
                progress(i + 1, len(contents), title)

            fixed = (c.get("poster_url") or "").strip()
            if is_safe_poster_url(fixed):
                items[title] = {
                    "poster_url": fixed,
                    "id": c.get("id") or "",
                    "channel": c.get("channel") or "",
                    "source": "data",
                }
                continue

            if not force:
                existing = (items.get(title) or {}).get("poster_url") or ""
                if is_safe_poster_url(existing):
                    continue

            if max_fetch is not None and fetched >= max_fetch:
                continue

            try:
                # 순환 import 방지: 필요 시점에만 로드
                from naver_api import fetch_poster_url

                url = fetch_poster_url(title, channel=c.get("channel") or "") or ""
            except Exception:
                url = ""
            if not is_safe_poster_url(url):
                url = ""
            items[title] = {
                "poster_url": url,
                "id": c.get("id") or "",
                "channel": c.get("channel") or "",
                "source": "naver" if url else "none",
            }
            fetched += 1

        cache = {"source": "poster_cache", "items": items}
        save_poster_cache(cache)
        return cache


def resolve_poster_url(
    item: dict,
    *,
    fetched: str = "",
    cache: dict[str, Any] | None = None,
) -> str:
    """우선순위: data 고정 → 캐시 → 실시간 fetch 결과."""
    for candidate in (
        (item.get("poster_url") or "").strip(),
        get_cached_poster(item.get("title") or "", cache),
        (fetched or "").strip(),
    ):
        if is_safe_poster_url(candidate):
            return candidate
    return ""
