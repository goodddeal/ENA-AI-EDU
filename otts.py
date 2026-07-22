"""OTT(보러가기) 디스크 캐시 — 목록 로딩 시 네트워크 폭주 방지."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "otts.json"
_lock = threading.Lock()

# 한 번의 화면 갱신에서 새로 가져올 최대 건수 (느림/멈춤 방지)
DEFAULT_MAX_FETCH = 8


def load_ott_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"version": 0, "items": {}}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 0, "items": {}}
    if not isinstance(data, dict):
        return {"version": 0, "items": {}}
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def save_ott_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def has_otts(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(item.get("otts"))


def get_cached_ott(title: str, cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cache = cache if cache is not None else load_ott_cache()
    item = (cache.get("items") or {}).get(title)
    if not isinstance(item, dict):
        return None
    return item


def set_cached_ott(title: str, result: dict[str, Any], cache: dict[str, Any] | None = None) -> dict[str, Any]:
    with _lock:
        cache = cache if cache is not None else load_ott_cache()
        items = dict(cache.get("items") or {})
        items[title] = {
            "otts": list(result.get("otts") or []),
            "ott_links": dict(result.get("ott_links") or {}),
            "source": result.get("source") or "none",
            "confirmed": bool(result.get("confirmed")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        cache = {
            "version": cache.get("version") or 0,
            "items": items,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
        save_ott_cache(cache)
        return cache


def ensure_ott_cache(
    contents: list[dict],
    *,
    logic_version: int,
    force: bool = False,
    max_fetch: int = DEFAULT_MAX_FETCH,
    refetch_empty: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """
    전달된 콘텐츠에 대해 OTT 캐시를 채운다.
    - force: 해당 목록을 다시 조회 (전체 캐시는 지우지 않음)
    - refetch_empty: otts 가 비어 있는 캐시도 다시 조회
    """
    from naver_api import resolve_otts_for_title

    with _lock:
        cache = load_ott_cache()
        if int(cache.get("version") or 0) != logic_version:
            cache = {"version": logic_version, "items": {}}

        items = dict(cache.get("items") or {})
        missing: list[dict] = []
        for c in contents:
            title = c["title"]
            existing = items.get(title)
            if force:
                missing.append(c)
            elif title not in items:
                missing.append(c)
            elif refetch_empty and not has_otts(existing):
                missing.append(c)

        if not missing:
            cache = {
                "version": logic_version,
                "items": items,
                "refreshed_at": cache.get("refreshed_at"),
            }
            return cache

        batch = missing[: max(0, max_fetch)]
        for i, c in enumerate(batch):
            title = c["title"]
            if progress:
                progress(i + 1, len(batch), title)
            try:
                result = resolve_otts_for_title(
                    title,
                    channel=c.get("channel") or "",
                    light=True,
                )
            except Exception:
                result = {
                    "otts": [],
                    "ott_links": {},
                    "source": "none",
                    "confirmed": False,
                }
            items[title] = {
                "otts": list(result.get("otts") or []),
                "ott_links": dict(result.get("ott_links") or {}),
                "source": result.get("source") or "none",
                "confirmed": bool(result.get("confirmed")),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        cache = {
            "version": logic_version,
            "items": items,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
        save_ott_cache(cache)
        return cache


def ott_cache_stats(titles: list[str], cache: dict[str, Any] | None = None) -> tuple[int, int, int]:
    """(조회됨, OTT확인됨, 전체) 반환."""
    cache = cache if cache is not None else load_ott_cache()
    items = cache.get("items") or {}
    looked = 0
    confirmed = 0
    for t in titles:
        item = items.get(t)
        if not isinstance(item, dict):
            continue
        looked += 1
        if has_otts(item):
            confirmed += 1
    return looked, confirmed, len(titles)
