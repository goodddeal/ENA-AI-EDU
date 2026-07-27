"""네이버 시청률 일일 캐시 — 매일 08:00(KST) 전일 기준으로 갱신."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cache_bootstrap import SEED_DIR, ensure_runtime_caches
from naver_api import apply_manual_rating_overrides, fetch_view_rate_with_history

KST = ZoneInfo("Asia/Seoul")
REFRESH_HOUR = 8
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "ratings.json"
SEED_PATH = SEED_DIR / "ratings.json"
DEFAULT_WORKERS = 5

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
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    items = data.get("items")
    if isinstance(items, dict):
        data = {**data, "items": apply_manual_rating_overrides(items)}
    return data


def save_cache(cache: dict[str, Any], *, sync_seed: bool = True) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cache, ensure_ascii=False, indent=2)
    CACHE_PATH.write_text(text, encoding="utf-8")
    if sync_seed:
        try:
            SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEED_PATH.write_text(text, encoding="utf-8")
        except OSError:
            pass


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
    from datetime import date as date_cls

    cache = cache if cache is not None else load_cache()

    def key(item: dict) -> tuple:
        rate = view_rate_value(item.get("title") or "", cache)
        aired = item.get("aired_at") or date_cls.min
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


def _rate_changed(prev: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    if not isinstance(prev, dict) or prev.get("view_rate") is None:
        return new.get("view_rate") is not None
    try:
        if abs(float(prev["view_rate"]) - float(new["view_rate"])) >= 0.005:
            return True
    except (TypeError, ValueError):
        return True
    if (prev.get("episode") or "") != (new.get("episode") or ""):
        return True
    if (prev.get("air_date") or "").rstrip(".") != (new.get("air_date") or "").rstrip("."):
        return True
    return False


def refresh_ratings(
    titles: list[str],
    *,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    channels: dict[str, str] | None = None,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """
    지정 프로그램 시청률을 네이버에서 다시 받아 캐시에 병합 저장.
    - 기존 캐시의 다른 타이틀은 유지 (부분 갱신 안전)
    - force=False 이면 이미 오늘(08:00 기준) 갱신됐으면 스킵
    """
    channels = channels or {}
    titles = [t for t in titles if t]
    with _lock:
        now = now_kst()
        expected = expected_refresh_date(now)
        cache = load_cache()
        if not force and cache_is_fresh(cache, now, titles):
            return cache

    fetched: dict[str, dict[str, Any]] = {}
    total = len(titles)
    if total == 0:
        return load_cache()

    def _one(title: str) -> tuple[str, dict[str, Any] | None]:
        try:
            return title, fetch_rating_for_title(title, channel=channels.get(title) or "")
        except Exception:
            return title, None

    workers = max(1, min(workers, total))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, t) for t in titles]
        for fut in as_completed(futs):
            title, rating = fut.result()
            if rating and rating.get("view_rate") is not None:
                fetched[title] = rating
            done += 1
            if progress:
                progress(done, total, title)

    changed = 0
    with _lock:
        cache = load_cache()
        items = dict(cache.get("items") or {})
        # 락 밖 prev 와 병합하되, 최신 디스크 기준 유지
        for title, rating in fetched.items():
            if _rate_changed(items.get(title), rating):
                changed += 1
            items[title] = rating
        # 조회 실패분은 이전 값 유지. 아예 없던 타이틀만 looked_up 표시
        for title in titles:
            if title in fetched:
                continue
            if title not in items:
                items[title] = {
                    "looked_up": True,
                    "view_rate": None,
                    "episode": "",
                    "air_date": "",
                    "channel": "",
                    "type": "",
                    "episodes": [],
                }

        items = apply_manual_rating_overrides(items)
        new_cache = {
            "refresh_date": expected.isoformat(),
            "as_of_date": as_of_date_for(expected).isoformat(),
            "refreshed_at": now_kst().isoformat(),
            "source": "naver_viewRate",
            "changed_count": changed,
            "fetched_count": len(fetched),
            "items": items,
        }
        save_cache(new_cache, sync_seed=True)
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

    titles = [c.get("title") or "" for c in batch]
    channels = {c.get("title") or "": c.get("channel") or "" for c in batch}
    return refresh_ratings(
        titles,
        force=True,
        progress=progress,
        channels=channels,
        workers=min(DEFAULT_WORKERS, max(1, len(titles))),
    )


def ensure_ratings(
    titles: list[str],
    *,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    channels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """앱 진입 시 호출 — 08:00 기준 전일 시청률이 없으면 갱신."""
    if force or not cache_is_fresh(titles=titles):
        return refresh_ratings(
            titles,
            force=True,
            progress=progress,
            channels=channels,
        )
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
    base = f"시청률 전일 기준({as_of}) · 매일 {REFRESH_HOUR:02d}:00 갱신"
    if refreshed:
        base += f" · 최근 {refreshed}"
    changed = cache.get("changed_count")
    fetched = cache.get("fetched_count")
    if isinstance(changed, int) and isinstance(fetched, int) and fetched:
        base += f" · 변동 {changed}/{fetched}"
    return base
