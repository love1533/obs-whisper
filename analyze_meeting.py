#!/usr/bin/env python3
"""회의록 → 위원별 질의 구간 → LLM 분석 → 위원 프로필 집계 → JSON / 뷰어 데이터 / Supabase

사용:
  python analyze_meeting.py --date 2026-11-06                 # 구간 분할 + 분석 + 프로필 + web/data/profiles.json
  python analyze_meeting.py --date 2026-11-06 --segments-only # 구간 분할만 (LLM 호출 없음)
  python analyze_meeting.py --date 2026-11-06 --limit 3       # 앞 3개 구간만 분석 (테스트)
  python analyze_meeting.py --date 2026-11-06 --supabase      # Supabase에도 저장 (supabase/migrations 먼저 실행)
  python analyze_meeting.py --profiles-only                   # 저장된 분석으로 프로필·뷰어 데이터만 다시 생성

입력은 transcripts/<날짜>.jsonl (문장 id 포함, 권장) 또는 .txt. 답변자 명단은 config/answerers/<날짜>.json 이 있어야 한다.
결과: transcripts/analysis/<날짜>.segments.json, <날짜>.analyses.json, profiles.json, web/data/profiles.json
이미 분석된 구간(같은 위원·시작 시각)은 다시 호출하지 않는다 (--force 로 재분석).
LLM 설정은 env 의 SUMMARY_PROVIDER / SUMMARY_API_KEY / SUMMARY_MODEL 을 쓴다.
"""
import argparse
import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
load_dotenv(os.path.join(ROOT, "env"))   # 실제 설정 (startup.sh가 export 하는 파일)
load_dotenv()                             # .env (있으면)

import config  # noqa: E402
from summarizer import Summarizer  # noqa: E402
from lib.roster import Answerers, Normalizer, UNKNOWN, get_roster, list_answerer_dates  # noqa: E402
from lib.segment import Segmenter, load_lines, report  # noqa: E402
from lib.member_analysis import analyze_segment, build_profile, normalize_topic_tags, INTEREST_TAGS, QUESTION_TYPES  # noqa: E402

ANALYSIS_DIR = os.path.join(ROOT, config.TRANSCRIPT_DIR, "analysis")
WEB_EXPORT = os.path.join(ROOT, "web", "data", "profiles.json")
ANALYZABLE_TYPES = ("질의", "자료요구")


def _path(name: str) -> str:
    return os.path.join(ANALYSIS_DIR, name)


def _load(name: str, default):
    p = _path(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save(name: str, data):
    os.makedirs(ANALYSIS_DIR, exist_ok=True)
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _seg_key(s: dict) -> str:
    return f"{s.get('speaker')}@{s.get('start_ts')}"


def _all_analyses(exclude_date: str = None) -> list:
    out = []
    if not os.path.isdir(ANALYSIS_DIR):
        return out
    for fn in sorted(os.listdir(ANALYSIS_DIR)):
        if fn.endswith(".analyses.json") and fn[:10] != exclude_date:
            out.extend(_load(fn, []))
    # 저장된 분석도 같은 규칙으로 태그를 정리한다 (목록 밖 표기 → 목록 태그, 질의 유형은 question_types 로)
    for a in out:
        an = a.get("analysis") or {}
        new_tags = []
        for t in an.get("topics", []):
            new_tags += normalize_topic_tags(t)
        an["new_tags"] = sorted(set(x for x in new_tags if x not in INTEREST_TAGS and x not in QUESTION_TYPES))
    return out


# ------------------------------------------------------------ 1. 구간 분할
def run_segments(date: str, input_path: str, expect: list) -> list:
    roster = get_roster()
    lines = load_lines(input_path)
    segs = Segmenter(roster).segment(lines)
    print(f"입력 {input_path}: {len(lines)}줄")
    print(report(segs, roster, expect))
    data = [s.to_dict() for s in segs]
    for d in data:
        d.pop("lines", None)          # 원문은 raw_text 로 충분
        d["meeting_date"] = date
    _save(f"{date}.segments.json", data)
    print(f"\n저장: {_path(f'{date}.segments.json')}")
    return data


# ------------------------------------------------------------ 2. 구간 분석
async def run_analysis(date: str, segments: list, limit: int, force: bool, concurrency: int) -> list:
    if not config.SUMMARY_API_KEY:
        sys.exit("SUMMARY_API_KEY 가 없습니다 (env 파일). --segments-only 로 구간 분할만 할 수 있습니다.")
    roster = get_roster()
    answerers = Answerers(date)
    normalizer = Normalizer()
    meeting = {"date": date, "committee": roster.committee, "agencies": answerers.agencies, "title": answerers.title}
    summarizer = Summarizer(config.SUMMARY_PROVIDER, config.SUMMARY_API_KEY, config.SUMMARY_MODEL)

    done = {} if force else {_seg_key(a): a for a in _load(f"{date}.analyses.json", [])}
    targets = [s for s in segments if s.get("type") in ANALYZABLE_TYPES and s.get("speaker") != UNKNOWN]
    todo = [s for s in targets if _seg_key(s) not in done]
    if limit:
        todo = todo[:limit]
    print(f"\n분석 대상 {len(targets)}구간 (이미 분석 {len(targets) - len([s for s in targets if _seg_key(s) not in done])}, 이번 {len(todo)}) "
          f"모델 {config.SUMMARY_PROVIDER}/{config.SUMMARY_MODEL}")

    # 이전 회의 topics (carried_over_from 후보)
    prev = {}
    for a in _all_analyses(exclude_date=date):
        if a.get("meeting_date", "") < date:
            for t in (a.get("analysis") or {}).get("topics", []):
                prev.setdefault(a.get("speaker"), []).append(f"{a.get('meeting_date')} {t.get('title')}")

    sem = asyncio.Semaphore(max(1, concurrency))
    results = dict(done)

    async def one(seg):
        async with sem:
            t0 = time.time()
            an = await analyze_segment(summarizer, seg, meeting, answerers, roster, normalizer, prev.get(seg["speaker"], [])[-20:])
            status = f"주제 {len(an.get('topics', []))}개" if an else "실패"
            print(f"  {seg['speaker']:4s} {seg['start_ts']} {seg['phase']:6s} {status} ({time.time() - t0:.0f}s)")
            if an:
                results[_seg_key(seg)] = {
                    "meeting_date": date, "speaker": seg["speaker"], "phase": seg["phase"], "type": seg["type"],
                    "start_ts": seg["start_ts"], "end_ts": seg["end_ts"], "start_id": seg.get("start_id"),
                    "sentence_ids": seg.get("sentence_ids", []), "raw_text": seg.get("raw_text", ""),
                    "analysis": an, "model": an.get("model"), "created_at": time.time(),
                }
                _save(f"{date}.analyses.json", _ordered(results))

    await asyncio.gather(*(one(s) for s in todo))
    await summarizer.close()
    out = _ordered(results)
    _save(f"{date}.analyses.json", out)
    print(f"저장: {_path(f'{date}.analyses.json')} ({len(out)}구간)")
    return out


def _ordered(results: dict) -> list:
    return sorted(results.values(), key=lambda a: (a.get("meeting_date", ""), a.get("start_ts", "")))


# ------------------------------------------------------------ 3. 프로필 집계
async def run_profiles(force_llm: bool = True) -> dict:
    roster = get_roster()
    analyses = _all_analyses()
    by_member = {}
    for a in analyses:
        by_member.setdefault(a.get("speaker"), []).append(a)
    summarizer = None
    if config.SUMMARY_API_KEY and force_llm:
        summarizer = Summarizer(config.SUMMARY_PROVIDER, config.SUMMARY_API_KEY, config.SUMMARY_MODEL)
    profiles = {}
    print(f"\n프로필 집계: 위원 {len([n for n in roster.names if n in by_member])}명")
    for name in roster.names:
        items = by_member.get(name)
        if not items:
            continue
        if summarizer:
            profiles[name] = await build_profile(summarizer, name, items)
        else:
            from lib.member_analysis import tag_table
            p = tag_table(items)
            p.update({"member": name, "top_interests": [], "question_style": [], "recurring_targets": [], "open_commitments": [], "updated_at": time.time()})
            profiles[name] = p
        p = profiles[name]
        print(f"  {name:4s} 구간 {p['segment_count']} 주제 {p['topic_count']} 관심 {', '.join(x['tag'] for x in p['interest_tags'][:3])}")
    if summarizer:
        await summarizer.close()
    _save("profiles.json", profiles)
    return profiles


# ------------------------------------------------------------ 4. 뷰어 데이터
def export_web(profiles: dict, out_path: str):
    roster = get_roster()
    meetings = []
    for d in list_answerer_dates():
        if os.path.exists(_path(f"{d}.analyses.json")) or os.path.exists(_path(f"{d}.segments.json")):
            a = Answerers(d)
            meetings.append({"date": d, "title": a.title, "agencies": a.agencies,
                             "answerers": [{"name": p["name"], "title": p.get("title", ""), "agency": p.get("agency", "")} for p in a.people]})
    segments = []
    for a in _all_analyses():
        segments.append({k: a.get(k) for k in ("meeting_date", "speaker", "phase", "type", "start_ts", "end_ts", "start_id", "sentence_ids", "analysis")})
    unknown = []
    for d in [m["date"] for m in meetings]:
        for s in _load(f"{d}.segments.json", []):
            if s.get("speaker") == UNKNOWN:
                unknown.append({"meeting_date": d, "start_ts": s["start_ts"], "candidates": s.get("candidates", []),
                                "first_line": (s.get("raw_text") or "")[:80]})
    data = {
        "generated_at": time.time(),
        "committee": roster.committee,
        "meetings": meetings,
        "members": [{k: m.get(k, "") for k in ("name", "role", "district", "party")} for m in roster.members],
        "segments": segments,
        "profiles": profiles,
        "unknown_segments": unknown,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"뷰어 데이터: {out_path} ({os.path.getsize(out_path) // 1024}KB, 회의 {len(meetings)} 구간 {len(segments)} 위원 {len(profiles)})")


# ------------------------------------------------------------ 5. Supabase
def save_supabase(dates: list, profiles: dict):
    import httpx
    if not config.SUPABASE_ENABLED:
        print("⚠️  SUPABASE_URL / SUPABASE_SERVICE_KEY 가 없어 Supabase 저장을 건너뜁니다.", file=sys.stderr)
        return
    roster = get_roster()
    headers = {"apikey": config.SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=representation"}
    c = httpx.Client(base_url=config.SUPABASE_URL.rstrip("/") + "/rest/v1", headers=headers, timeout=30.0)

    def upsert(table: str, rows: list, on_conflict: str):
        if not rows:
            return []
        r = c.post(f"/{table}?on_conflict={on_conflict}", json=rows)
        if r.status_code >= 400:
            raise RuntimeError(f"{table}: {r.status_code} {r.text[:300]}")
        return r.json()

    try:
        members = upsert("members", [{"name": m["name"], "role": m.get("role"), "district": m.get("district"),
                                      "party": m.get("party"), "aliases": m.get("aliases", [])} for m in roster.members], "name")
        member_id = {m["name"]: m["id"] for m in members}
        for d in dates:
            a = Answerers(d)
            upsert("meetings", [{"id": d, "date": d, "committee": roster.committee, "agencies": a.agencies, "title": a.title}], "id")
            upsert("answerers", [{"meeting_id": d, "name": p["name"], "title": p.get("title"), "agency": p.get("agency"),
                                  "aliases": p.get("aliases", [])} for p in a.people], "meeting_id,name")
            segs = [s for s in _load(f"{d}.segments.json", []) if s.get("type") in ANALYZABLE_TYPES]
            rows = upsert("segments", [{
                "meeting_id": d, "member_id": member_id.get(s["speaker"]), "speaker_raw": s["speaker"], "phase": s["phase"],
                "type": s["type"], "start_ts": s["start_ts"], "end_ts": s["end_ts"], "start_id": s.get("start_id"),
                "sentence_ids": s.get("sentence_ids", []), "raw_text": s.get("raw_text", ""), "candidates": s.get("candidates", []),
            } for s in segs], "meeting_id,start_ts,speaker_raw")
            seg_id = {(r["start_ts"], r["speaker_raw"]): r["id"] for r in rows}
            an_rows = []
            for a_ in _load(f"{d}.analyses.json", []):
                sid = seg_id.get((a_["start_ts"], a_["speaker"]))
                if sid:
                    an_rows.append({"segment_id": sid, "json": a_["analysis"], "model": a_.get("model") or ""})
            upsert("analyses", an_rows, "segment_id,model")
            print(f"  Supabase {d}: 구간 {len(rows)} 분석 {len(an_rows)}")
        upsert("member_profiles", [{"member_id": member_id[n], "profile_json": p, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                                   for n, p in profiles.items() if n in member_id], "member_id")
        print(f"  Supabase 프로필 {len(profiles)}명 저장")
    except Exception as e:
        print(f"⚠️  Supabase 저장 실패: {e}\n   supabase/migrations/20261107_member_profiles.sql 을 SQL Editor에서 먼저 실행했는지 확인하세요.", file=sys.stderr)
    finally:
        c.close()


# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="회의 날짜 YYYY-MM-DD")
    ap.add_argument("--input", help="입력 파일 (기본 transcripts/<날짜>.jsonl → .txt)")
    ap.add_argument("--expect", help="본질의 기대 순서 (쉼표 구분) - 구간 분할 검증")
    ap.add_argument("--segments-only", action="store_true", help="구간 분할만")
    ap.add_argument("--profiles-only", action="store_true", help="저장된 분석으로 프로필·뷰어 데이터만")
    ap.add_argument("--limit", type=int, default=0, help="분석할 구간 수 제한 (테스트)")
    ap.add_argument("--force", action="store_true", help="이미 분석된 구간도 다시 분석")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--no-profile-llm", action="store_true", help="프로필은 빈도표만 (LLM 호출 없음)")
    ap.add_argument("--export", default=WEB_EXPORT, help="뷰어 데이터 출력 경로")
    ap.add_argument("--supabase", action="store_true", help="Supabase 테이블에도 저장")
    ap.add_argument("--model", help="LLM 모델 (기본 env SUMMARY_MODEL)")
    args = ap.parse_args()
    if args.model:
        config.SUMMARY_MODEL = args.model

    if not args.profiles_only:
        if not args.date:
            sys.exit("--date 가 필요합니다")
        path = args.input
        if not path:
            for ext in (".jsonl", ".txt"):
                cand = os.path.join(ROOT, config.TRANSCRIPT_DIR, args.date + ext)
                if os.path.exists(cand):
                    path = cand
                    break
        if not path or not os.path.exists(path):
            sys.exit(f"입력 파일이 없습니다: {path or args.date}")
        roster = get_roster()
        expect = [roster.resolve(x) for x in args.expect.split(",")] if args.expect else None
        segments = run_segments(args.date, path, expect)
        if args.segments_only:
            return
        asyncio.run(run_analysis(args.date, segments, args.limit, args.force, args.concurrency))

    profiles = asyncio.run(run_profiles(force_llm=not args.no_profile_llm))
    export_web(profiles, args.export)
    if args.supabase:
        dates = [d for d in list_answerer_dates() if os.path.exists(_path(f"{d}.segments.json"))]
        save_supabase(dates, profiles)


if __name__ == "__main__":
    main()
