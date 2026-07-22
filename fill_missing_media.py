"""누락·깨진 시청률/포스터를 채운 뒤 seed_cache 를 갱신."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from data import get_sorted_contents
from posters import fill_missing_posters, get_cached_poster, is_safe_poster_url, load_poster_cache
from ratings import fill_missing_ratings, get_rating, load_cache

ROOT = Path(__file__).resolve().parent
SEED = ROOT / "seed_cache"
RUNTIME = ROOT / ".cache"


def main() -> None:
    contents = get_sorted_contents("전체")
    print(f"programs={len(contents)}")

    def prog(i: int, n: int, title: str) -> None:
        print(f"  [{i}/{n}] {title}")

    print("fill ratings…")
    fill_missing_ratings(contents, max_fetch=len(contents), progress=prog)
    print("fill posters…")
    fill_missing_posters(contents, max_fetch=len(contents), progress=prog)

    rc = load_cache()
    pc = load_poster_cache()
    miss_r = [c["title"] for c in contents if not get_rating(c["title"], rc)]
    miss_p = [
        c["title"]
        for c in contents
        if not is_safe_poster_url(
            get_cached_poster(c["title"], pc) or (c.get("poster_url") or "")
        )
    ]
    print(f"still missing ratings ({len(miss_r)}): {miss_r}")
    print(f"still missing posters ({len(miss_p)}): {miss_p}")

    SEED.mkdir(parents=True, exist_ok=True)
    for name in ("ratings.json", "posters.json", "otts.json"):
        src = RUNTIME / name
        if src.is_file():
            shutil.copy2(src, SEED / name)
            print("seed updated", name, src.stat().st_size)


if __name__ == "__main__":
    main()
