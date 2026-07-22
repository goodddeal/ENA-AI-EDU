"""프로그램 포스터 URL 캐시 — 안전 URL만 사용."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from cache_bootstrap import ensure_runtime_caches

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
    blocked = (
        "img.extmovie.com",
        "extmovie.com",
        "i.namu.wiki",
        "namu.wiki",
    )
    lower = url.lower()
    if any(h in lower for h in blocked):
        return False
    return True


def load_poster_cache() -> dict[str, Any]:
    ensure_runtime_caches()
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


def fill_missing_posters(
    contents: list[dict],
    *,
    max_fetch: int = 20,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """안전 URL이 없는 포스터만 재조회 (차단 호스트·빈 값 포함)."""
    with _lock:
        cache = load_poster_cache()
        items = dict(cache.get("items") or {})
        need: list[dict] = []
        for c in contents:
            title = c["title"]
            fixed = (c.get("poster_url") or "").strip()
            if is_safe_poster_url(fixed):
                items[title] = {
                    "poster_url": fixed,
                    "id": c.get("id") or "",
                    "channel": c.get("channel") or "",
                    "source": "data",
                }
                continue
            existing = (items.get(title) or {}).get("poster_url") or ""
            if is_safe_poster_url(existing):
                continue
            need.append(c)
        if need:
            save_poster_cache({"source": "poster_cache", "items": items})
        batch = need[: max(0, max_fetch)]

    if not batch:
        return load_poster_cache()

    from naver_api import fetch_poster_url

    fetched_map: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(batch):
        title = c["title"]
        if progress:
            progress(i + 1, len(batch), title)
        try:
            url = fetch_poster_url(title, channel=c.get("channel") or "") or ""
        except Exception:
            url = ""
        if not is_safe_poster_url(url):
            url = ""
        fetched_map[title] = {
            "poster_url": url,
            "id": c.get("id") or "",
            "channel": c.get("channel") or "",
            "source": "naver" if url else "none",
        }

    with _lock:
        cache = load_poster_cache()
        items = dict(cache.get("items") or {})
        items.update(fetched_map)
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
    from urllib.parse import quote, urlsplit, urlunsplit

    def _encode(url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
        path = quote(parts.path, safe="/:@!$&'()*+,;=-._~")
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    for candidate in (
        (item.get("poster_url") or "").strip(),
        get_cached_poster(item.get("title") or "", cache),
        (fetched or "").strip(),
    ):
        encoded = _encode(candidate)
        if is_safe_poster_url(encoded):
            return encoded
    return ""
