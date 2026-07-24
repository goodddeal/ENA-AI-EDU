"""OTT(보러가기) 미확인·누락분 재수집 → seed_cache/otts.json 갱신."""

from __future__ import annotations

import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from cache_bootstrap import SEED_VERSION, ensure_runtime_caches
from data import CONTENTS, OTT_META
from naver_api import resolve_otts_for_title
from otts import load_ott_cache, save_ott_cache

ROOT = Path(__file__).resolve().parent
SEED_OTTS = ROOT / "seed_cache" / "otts.json"
WORKERS = 3
# app.py OTT_LOGIC_VERSION 과 맞춤
LOGIC_VERSION = 16


def _need_refresh(title: str, items: dict) -> bool:
    row = items.get(title)
    if not isinstance(row, dict):
        return True
    otts = [o for o in (row.get("otts") or []) if o in OTT_META]
    return len(otts) == 0


def _fetch(c: dict) -> tuple[str, dict]:
    title = c["title"]
    try:
        # light=False: 보러가기 재시도 포함
        result = resolve_otts_for_title(
            title,
            channel=c.get("channel") or "",
            light=False,
        )
    except Exception as exc:
        print(f"  ERR {title}: {exc}")
        result = {
            "otts": [],
            "ott_links": {},
            "source": "none",
            "confirmed": False,
        }
    return title, result


def main() -> int:
    ensure_runtime_caches(force=True)
    cache = load_ott_cache()
    items = dict(cache.get("items") or {})

    targets = [c for c in CONTENTS if _need_refresh(c["title"], items)]
    print(
        f"seed={SEED_VERSION} logic={LOGIC_VERSION} "
        f"targets={len(targets)}/{len(CONTENTS)}"
    )
    if not targets:
        print("nothing to refresh")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    done = 0
    found = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_fetch, c): c["title"] for c in targets}
        for fut in as_completed(futs):
            title, result = fut.result()
            otts = [o for o in (result.get("otts") or []) if o in OTT_META]
            links = {
                k: v
                for k, v in (result.get("ott_links") or {}).items()
                if k in otts and v
            }
            items[title] = {
                "otts": otts,
                "ott_links": links,
                "source": result.get("source") or ("naver_borragi" if otts else "none"),
                "confirmed": bool(otts),
                "updated_at": now,
            }
            done += 1
            if otts:
                found += 1
            print(f"[{done}/{len(targets)}] {title} → {otts or '미편성'}")
            # 네이버 차단 완화
            time.sleep(0.35)

    # 기존 confirmed 유지 + 신규 병합
    confirmed = sum(1 for t in (c["title"] for c in CONTENTS) if items.get(t, {}).get("otts"))
    out = {
        "version": LOGIC_VERSION,
        "items": {c["title"]: items[c["title"]] for c in CONTENTS if c["title"] in items},
        "refreshed_at": now,
    }
    # CONTENTS 전체에 엔트리 보장 (조회 실패도 none 으로)
    for c in CONTENTS:
        if c["title"] not in out["items"]:
            out["items"][c["title"]] = {
                "otts": [],
                "ott_links": {},
                "source": "none",
                "confirmed": False,
                "updated_at": now,
            }

    save_ott_cache(out)
    SEED_OTTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / ".cache" / "otts.json", SEED_OTTS)
    (ROOT / ".cache" / "seed_version.txt").write_text(
        str(SEED_VERSION), encoding="utf-8"
    )

    confirmed = sum(
        1
        for c in CONTENTS
        if (out["items"].get(c["title"]) or {}).get("otts")
    )
    print(f"DONE refreshed={done} newly_found={found} confirmed={confirmed}/{len(CONTENTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
