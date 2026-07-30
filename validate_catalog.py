"""카탈로그 데이터 품질 점검 — 과거 재발 이슈 가드."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")

from data import CONTENTS
from posters import is_plausible_poster_url, is_safe_poster_url

ROOT = Path(__file__).resolve().parent
SEED_POSTERS = ROOT / "seed_cache" / "posters.json"
KST = ZoneInfo("Asia/Seoul")


def _check_seed_posters(errors: list[str], warnings: list[str]) -> None:
    if not SEED_POSTERS.is_file():
        warnings.append("seed_cache/posters.json 없음")
        return
    try:
        data = json.loads(SEED_POSTERS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"seed posters 파싱 실패: {exc}")
        return
    items = data.get("items") or {}
    by_title = {c["title"]: c for c in CONTENTS}
    for title, row in items.items():
        url = (row.get("poster_url") or "").strip()
        channel = row.get("channel") or (by_title.get(title) or {}).get("channel") or ""
        if url and not is_plausible_poster_url(url, title=title, channel=channel):
            errors.append(f"[seed implausible] {title}: {url[:90]}")
    norae = (items.get("전국노래자랑") or {}).get("poster_url") or ""
    if "tving.com" in norae.lower() or "namu.wiki" in norae.lower():
        errors.append("[회귀] seed 전국노래자랑 포스터가 오인 URL 입니다.")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    titles = [c["title"] for c in CONTENTS]
    if len(titles) != len(set(titles)):
        errors.append("중복 title 이 있습니다.")

    for c in CONTENTS:
        title = c["title"]
        ep = str(c.get("episode") or "")
        channel = c.get("channel") or ""
        poster = (c.get("poster_url") or "").strip()

        if c.get("poster_locked"):
            if not is_plausible_poster_url(poster, title=title, channel=channel):
                errors.append(f"[locked poster invalid] {title}: {poster[:80]}")

        if poster and not is_safe_poster_url(poster):
            errors.append(f"[unsafe data poster] {title}: {poster[:80]}")

        if "방영 중" in ep and ("종영" in ep or "방영 예정" in ep):
            errors.append(f"[status conflict] {title}: {ep}")

        ended = c.get("ended_at")
        today = datetime.now(KST).date()
        if isinstance(ended, date) and ended < today and "방영 중" in ep and "종영" not in ep:
            errors.append(
                f"[ended but airing] {title}: ended_at={ended.isoformat()} episode={ep}"
            )

        # 알려진 재발 케이스
        if title == "디어 마이 엑스" and "방영 중" in ep:
            errors.append("[회귀] 디어 마이 엑스는 종영이어야 합니다.")
        if title == "김부장":
            if "방영 중" in ep or "종영" not in ep:
                errors.append("[회귀] 김부장은 2026-07-25 종영이어야 합니다.")
            if c.get("ended_at") != date(2026, 7, 25):
                errors.append("[회귀] 김부장 ended_at 이 2026-07-25 이어야 합니다.")
        if title == "보검 매직컬":
            if "방영 중" in ep or "종영" not in ep:
                errors.append("[회귀] 보검 매직컬은 종영이어야 합니다.")
            if "32668767" in poster:
                errors.append("[회귀] 보검 매직컬 오인 포스터(32668767)입니다.")
            if not c.get("poster_locked"):
                warnings.append("보검 매직컬 poster_locked 권장")
        if title == "핸썸가이즈":
            if "방영 중" in ep or "종영" not in ep:
                errors.append("[회귀] 핸썸가이즈는 종영이어야 합니다.")
        if title == "쯔양몇끼":
            if "tingleroom" in poster.lower():
                errors.append("[회귀] 쯔양몇끼 팅글룸 오인 포스터입니다.")
            if not c.get("poster_locked"):
                warnings.append("쯔양몇끼 poster_locked 권장")
        if title == "왕자와 거지":
            if "방영 예정" in ep or "종영" in ep:
                errors.append("[회귀] 왕자와 거지는 방영 중이어야 합니다.")
        if title == "전국노래자랑":
            if "namu.wiki" in poster or "tving.com" in poster:
                errors.append("[회귀] 전국노래자랑 포스터가 오인 URL 입니다.")
            if not c.get("poster_locked"):
                warnings.append("전국노래자랑 poster_locked 권장")

    _check_seed_posters(errors, warnings)

    print(f"checked {len(CONTENTS)} programs")
    for w in warnings:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        print(f"FAILED ({len(errors)} errors)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
