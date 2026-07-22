"""네이버 시청률 일일 캐시 — 매일 08:00(KST) 전일 기준으로 갱신."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cache_bootstrap import ensure_runtime_caches
from naver_api import fetch_view_rate_with_history

KST = ZoneInfo("Asia/Seoul")
REFRESH_HOUR = 8
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "ratings.json"

_lock = threading.Lock()


def now_kst() -> datetime:
    return datetime.now(KST)


def expected_refresh_date(now: datetime | None = None) -> date:
    """
    오늘 08:00 이전이면 '어제'가 마지막 갱신일이어야 하고,
    08:00 이후면 '오늘' 갱신이 완료되어야 한다.
    """
    now = now or now_kst()
    d = now.date()
    if now.time() < time(REFRESH_HOUR, 0):
        return d - timedelta(days=1)
    return d


def as_of_date_for(refresh_day: date) -> date:
    """갱신일 기준 '전일'."""
    return refresh_day - timedelta(days=1)


def load_cache() -> dict[str, Any]:
    ensure_runtime_caches()
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cache_is_fresh(
    cache: dict[str, Any] | None = None,
    now: datetime | None = None,
    titles: list[str] | None = None,
) -> bool:
    cache = cache if cache is not None else load_cache()
    expected = expected_refresh_date(now).isoformat()
    if not cache.get("items") or cache.get("refresh_date") != expected:
        return False
    if titles:
        items = cache.get("items") or {}
        # 신규 프로그램이 많이 빠졌으면 재갱신
        missing = sum(1 for t in titles if t not in items)
        if missing >= max(3, len(titles) // 5):
            return False
    return True


def get_rating(title: str, cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cache = cache if cache is not None else load_cache()
    item = (cache.get("items") or {}).get(title)
    if not isinstance(item, dict):
        return None
    # 조회했지만 네이버에 시청률이 없는 경우
    if item.get("looked_up") and item.get("view_rate") is None:
        return None
    if item.get("view_rate") is None:
        return None
    return item


def rating_lookup_done(title: str, cache: dict[str, Any] | None = None) -> bool:
    """시청률 조회를 이미 시도했는지 (성공/실패 포함)."""
    cache = cache if cache is not None else load_cache()
    item = (cache.get("items") or {}).get(title)
    if not isinstance(item, dict):
        return False
    return item.get("view_rate") is not None or bool(item.get("looked_up"))


def view_rate_value(title: str, cache: dict[str, Any] | None = None) -> float:
    """정렬용 시청률 숫자. 없으면 -1."""
    rating = get_rating(title, cache)
    if not rating or rating.get("view_rate") is None:
        return -1.0
    try:
        return float(rating["view_rate"])
    except (TypeError, ValueError):
        return -1.0


def sort_by_view_rate(
    items: list[dict],
    cache: dict[str, Any] | None = None,
) -> list[dict]:
    """시청률 높은 순. 동률·미확인은 최근 방영일 우선."""
    from datetime import date

    cache = cache if cache is not None else load_cache()

    def key(item: dict) -> tuple:
        rate = view_rate_value(item.get("title") or "", cache)
        aired = item.get("aired_at") or date.min
        return (rate, aired)

    return sorted(items, key=key, reverse=True)


def format_rate_percent(rate: float) -> str:
    rounded = round(float(rate), 2)
    if abs(rounded - round(rounded, 1)) < 1e-9:
        return f"{rounded:.1f}%"
    return f"{rounded:.2f}%"


def format_rating_label(rating: dict[str, Any] | None) -> str:
    if not rating or rating.get("view_rate") is None:
        return ""
    text = format_rate_percent(float(rating["view_rate"]))
    ep = (rating.get("episode") or "").strip()
    if ep:
        return f"시청률 {text} · {ep}"
    return f"시청률 {text}"


def get_episode_ratings(rating: dict[str, Any] | None) -> list[dict[str, Any]]:
    """회차별 시청률 목록. 없으면 빈 리스트."""
    if not rating:
        return []
    episodes = rating.get("episodes")
    if not isinstance(episodes, list):
        return []
    out: list[dict[str, Any]] = []
    for ep in episodes:
        if not isinstance(ep, dict) or ep.get("view_rate") is None:
            continue
        try:
            rate = float(ep["view_rate"])
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "episode": str(ep.get("episode") or "").strip(),
                "air_date": str(ep.get("air_date") or "").strip(),
                "view_rate": rate,
                "channel": str(ep.get("channel") or "").strip(),
            }
        )
    return out


def fetch_rating_for_title(title: str, channel: str = "") -> dict[str, Any] | None:
    parsed = fetch_view_rate_with_history(title, channel=channel)
    if not parsed:
        return None
    return {
        "view_rate": parsed["view_rate"],
        "episode": parsed.get("episode") or "",
        "air_date": parsed.get("air_date") or "",
        "channel": parsed.get("channel") or "",
        "type": parsed.get("type") or "LATEST",
        "episodes": list(parsed.get("episodes") or []),
    }


def refresh_ratings(
    titles: list[str],
    *,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    channels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    전체 프로그램 시청률을 네이버에서 다시 받아 캐시에 저장.
    force=False 이면 이미 오늘(08:00 기준) 갱신됐으면 스킵.
    """
    channels = channels or {}
    with _lock:
        now = now_kst()
        expected = expected_refresh_date(now)
        cache = load_cache()
        if not force and cache_is_fresh(cache, now, titles):
            return cache

        items: dict[str, Any] = {}
        total = len(titles)
        for i, title in enumerate(titles):
            if progress:
                progress(i + 1, total, title)
            try:
                rating = fetch_rating_for_title(title, channel=channels.get(title) or "")
            except Exception:
                rating = None
            if rating:
                items[title] = rating

        # 강제 갱신이 아니고 일부만 실패한 경우, 이전 값 유지
        prev_items = cache.get("items") or {}
        if isinstance(prev_items, dict):
            for title in titles:
                if title not in items and title in prev_items:
                    items[title] = prev_items[title]

        new_cache = {
            "refresh_date": expected.isoformat(),
            "as_of_date": as_of_date_for(expected).isoformat(),
            "refreshed_at": now.isoformat(),
            "source": "naver_viewRate",
            "items": items,
        }
        save_cache(new_cache)
        return new_cache


def fill_missing_ratings(
    contents: list[dict],
    *,
    max_fetch: int = 20,
    progress: Callable[[int, int, str], None] | None = None,
    retry_looked_up: bool = False,
) -> dict[str, Any]:
    """시청률이 없는 프로그램만 보강 (기존 캐시 유지)."""
    with _lock:
        cache = load_cache()
        items = dict(cache.get("items") or {})
        missing: list[dict] = []
        for c in contents:
            title = c.get("title") or ""
            existing = items.get(title)
            if isinstance(existing, dict):
                if existing.get("view_rate") is not None:
                    continue
                if existing.get("looked_up") and not retry_looked_up:
                    continue
            missing.append(c)
        batch = missing[: max(0, max_fetch)]
        if not batch:
            return cache

    updated: dict[str, Any] = {}
    for i, c in enumerate(batch):
        title = c.get("title") or ""
        if progress:
            progress(i + 1, len(batch), title)
        try:
            rating = fetch_rating_for_title(title, channel=c.get("channel") or "")
        except Exception:
            rating = None
        if rating and rating.get("view_rate") is not None:
            updated[title] = rating
        else:
            # 재조회 폭주 방지 — 네이버에 없음으로 표시
            updated[title] = {
                "looked_up": True,
                "view_rate": None,
                "episode": "",
                "air_date": "",
                "channel": "",
                "type": "",
                "episodes": [],
            }

    with _lock:
        cache = load_cache()
        items = dict(cache.get("items") or {})
        items.update(updated)
        now = now_kst()
        new_cache = {
            "refresh_date": cache.get("refresh_date") or expected_refresh_date(now).isoformat(),
            "as_of_date": cache.get("as_of_date") or as_of_date_for(expected_refresh_date(now)).isoformat(),
            "refreshed_at": now.isoformat(),
            "source": cache.get("source") or "naver_viewRate",
            "items": items,
        }
        save_cache(new_cache)
        return new_cache


def ensure_ratings(
    titles: list[str],
    *,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """앱 진입 시 호출 — 08:00 기준 전일 시청률이 없으면 갱신."""
    if force or not cache_is_fresh(titles=titles):
        return refresh_ratings(titles, force=force, progress=progress)
    return load_cache()


def cache_meta_label(cache: dict[str, Any] | None = None) -> str:
    cache = cache if cache is not None else load_cache()
    as_of = cache.get("as_of_date") or "-"
    refreshed = cache.get("refreshed_at") or ""
    if refreshed:
        try:
            dt = datetime.fromisoformat(refreshed)
            refreshed = dt.strftime("%m.%d %H:%M")
        except ValueError:
            pass
    return f"시청률 전일 기준({as_of}) · 매일 {REFRESH_HOUR:02d}:00 갱신" + (
        f" · 최근 {refreshed}" if refreshed else ""
    )
