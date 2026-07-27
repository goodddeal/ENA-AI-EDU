"""시청률 캐시 강제 갱신 — 작업 스케줄러(매일 08:00)용 CLI.

기본: 방영 중 프로그램만 (전일 시청률). --all 이면 전체.
"""

from __future__ import annotations

import sys

from data import get_sorted_contents


def _is_airing(item: dict) -> bool:
    ep = str(item.get("episode") or "")
    if "방영 예정" in ep or "종영" in ep:
        return False
    return "방영 중" in ep


def main() -> int:
    from ratings import cache_meta_label, refresh_ratings

    all_contents = get_sorted_contents("전체")
    do_all = "--all" in sys.argv
    targets = all_contents if do_all else [c for c in all_contents if _is_airing(c)]
    if not targets:
        targets = all_contents
    titles = [c["title"] for c in targets]
    channels = {c["title"]: c.get("channel") or "" for c in all_contents}
    print(f"Refreshing ratings for {len(titles)} titles…")

    def progress(i: int, total: int, title: str) -> None:
        print(f"[{i}/{total}] {title}")

    cache = refresh_ratings(
        titles, force=True, progress=progress, channels=channels
    )
    print(cache_meta_label(cache))
    print(f"Saved {len(cache.get('items') or {})} ratings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
