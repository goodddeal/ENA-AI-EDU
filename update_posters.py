"""전체 프로그램 포스터 캐시 강제 갱신 CLI."""

from __future__ import annotations

from data import get_sorted_contents
from posters import cache_coverage, ensure_poster_cache


def main() -> int:
    contents = get_sorted_contents("전체")
    print(f"Refreshing posters for {len(contents)} titles…")

    def progress(i: int, total: int, title: str) -> None:
        print(f"[{i}/{total}] {title}")

    cache = ensure_poster_cache(contents, force=True, progress=progress)
    ok, total = cache_coverage([c["title"] for c in contents], cache)
    print(f"Saved {ok}/{total} posters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
