"""시청률 캐시 강제 갱신 — 작업 스케줄러(매일 08:00)용 CLI."""

from __future__ import annotations

import sys

from data import get_sorted_contents
from ratings import cache_meta_label, refresh_ratings


def main() -> int:
    titles = [c["title"] for c in get_sorted_contents("전체")]
    print(f"Refreshing ratings for {len(titles)} titles…")

    def progress(i: int, total: int, title: str) -> None:
        print(f"[{i}/{total}] {title}")

    cache = refresh_ratings(titles, force=True, progress=progress)
    print(cache_meta_label(cache))
    print(f"Saved {len(cache.get('items') or {})} ratings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
