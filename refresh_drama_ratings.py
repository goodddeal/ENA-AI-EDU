"""드라마 시청률을 우선 재수집해 seed_cache/ratings.json 을 갱신."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from cache_bootstrap import SEED_VERSION, ensure_runtime_caches
from data import get_sorted_contents
from ratings import (
    fetch_rating_for_title,
    get_rating,
    load_cache,
    save_cache,
)

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ensure_runtime_caches(force=True)
    dramas = get_sorted_contents("드라마")
    all_contents = get_sorted_contents("전체")
    print(f"dramas={len(dramas)} seed_version={SEED_VERSION}")

    cache = load_cache()
    items = dict(cache.get("items") or {})

    for i, c in enumerate(dramas, 1):
        title = c["title"]
        channel = c.get("channel") or ""
        print(f"[{i}/{len(dramas)}] {title} ({channel})")
        try:
            rating = fetch_rating_for_title(title, channel=channel)
        except Exception as exc:
            print("  ERR", exc)
            rating = None
        if rating and rating.get("view_rate") is not None:
            items[title] = rating
            print("  ->", rating.get("view_rate"), rating.get("episode"))
        else:
            print("  -> keep", (items.get(title) or {}).get("view_rate"))

    # load_cache 의 manual override 반영본을 저장
    from naver_api import apply_manual_rating_overrides

    items = apply_manual_rating_overrides(items)
    save_cache({**cache, "items": items, "source": "naver_viewRate+manual"})

    rc = load_cache()
    ok = sum(1 for c in dramas if get_rating(c["title"], rc))
    all_ok = sum(1 for c in all_contents if get_rating(c["title"], rc))
    print(f"drama ok {ok}/{len(dramas)} | all ok {all_ok}/{len(all_contents)}")

    seed = ROOT / "seed_cache" / "ratings.json"
    shutil.copy2(ROOT / ".cache" / "ratings.json", seed)
    # Cloud 강제 동기화
    (ROOT / ".cache" / "seed_version.txt").write_text(
        str(SEED_VERSION), encoding="utf-8"
    )
    print("seed updated", seed.stat().st_size)


if __name__ == "__main__":
    main()
