#!/usr/bin/env python3
"""회의록 → 위원별 질의 구간 → LLM 분석 → 위원 프로필 집계 → JSON / 뷰어 데이터 / Supabase (회의 후 일괄)

사용:
  python analyze_meeting.py --date 2026-11-06                 # 구간 분할 + 분석 + 프로필 + web/data/profiles.json + Supabase
  python analyze_meeting.py --date 2026-11-06 --segments-only # 구간 분할만 (LLM 호출 없음)
  python analyze_meeting.py --date 2026-11-06 --limit 3       # 앞 3개 구간만 분석 (테스트)
  python analyze_meeting.py --profiles-only                   # 저장된 분석으로 프로필·뷰어 데이터만 다시 생성
  python analyze_meeting.py --date 2026-11-06 --no-supabase   # Supabase 저장 생략

입력은 transcripts/<날짜>.jsonl (문장 id 포함, 권장) 또는 .txt. 답변자 명단은 config/answerers/<날짜>.json 이 있어야 한다.
결과: transcripts/analysis/<날짜>.segments.json, <날짜>.analyses.json, profiles.json, web/data/profiles.json,
      Supabase meetings/members/answerers/segments/analyses/member_profiles (supabase/migrations/20261107_member_profiles.sql)
이미 분석된 구간(같은 위원·시작 시각)은 다시 호출하지 않는다 (--force 로 재분석).
회의 중에는 앱이 같은 파일·테이블을 30분마다 갱신한다 (lib/live_profile.py). 회의 후 이 스크립트를 돌리면 남은 구간을 채운다.
LLM 설정은 env 의 SUMMARY_PROVIDER / SUMMARY_API_KEY / SUMMARY_MODEL 을 쓴다.
"""
import argparse
import asyncio
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
from lib.roster import Answerers, Normalizer, UNKNOWN, get_roster  # noqa: E402
from lib.segment import Segmenter, load_lines, report  # noqa: E402
from lib.member_analysis import analyze_segment, build_profile, tag_table  # noqa: E402
from lib.analysis_store import AnalysisStore, ANALYZABLE_TYPES  # noqa: E402

STORE = AnalysisStore(os.path.join(ROOT, config.TRANSCRIPT_DIR, "analysis"))


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
    STORE.save(f"{date}.segments.json", data)
    print(f"\n저장: {STORE.path(f'{date}.segments.json')}")
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

    done = {} if force else {STORE.seg_key(a): a for a in STORE.load(f"{date}.analyses.json", [])}
    targets = [s for s in segments if s.get("type") in ANALYZABLE_TYPES and s.get("speaker") != UNKNOWN]
    todo = [s for s in targets if STORE.seg_key(s) not in done]
    if limit:
        todo = todo[:limit]
    print(f"\n분석 대상 {len(targets)}구간 (이미 분석 {len(targets) - len([s for s in targets if STORE.seg_key(s) not in done])}, 이번 {len(todo)}) "
          f"모델 {config.SUMMARY_PROVIDER}/{config.SUMMARY_MODEL}")

    # 이전 회의 topics (carried_over_from 후보)
    prev = {}
    for a in STORE.all_analyses(exclude_date=date):
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
                results[STORE.seg_key(seg)] = {
                    "meeting_date": date, "speaker": seg["speaker"], "phase": seg["phase"], "type": seg["type"],
                    "start_ts": seg["start_ts"], "end_ts": seg["end_ts"], "start_id": seg.get("start_id"),
                    "sentence_ids": seg.get("sentence_ids", []), "raw_text": seg.get("raw_text", ""),
                    "analysis": an, "model": an.get("model"), "created_at": time.time(),
                }
                STORE.save(f"{date}.analyses.json", _ordered(results))

    await asyncio.gather(*(one(s) for s in todo))
    await summarizer.close()
    out = _ordered(results)
    STORE.save(f"{date}.analyses.json", out)
    print(f"저장: {STORE.path(f'{date}.analyses.json')} ({len(out)}구간)")
    return out


def _ordered(results: dict) -> list:
    return sorted(results.values(), key=lambda a: (a.get("meeting_date", ""), a.get("start_ts", "")))


# ------------------------------------------------------------ 3. 프로필 집계 (전체 회의 + 회의별)
async def run_profiles(use_llm: bool = True) -> dict:
    roster = get_roster()
    analyses = STORE.all_analyses()
    summarizer = Summarizer(config.SUMMARY_PROVIDER, config.SUMMARY_API_KEY, config.SUMMARY_MODEL) if (config.SUMMARY_API_KEY and use_llm) else None

    async def profile(name, items, label):
        if summarizer:
            p = await build_profile(summarizer, name, items)
        else:
            p = tag_table(items)
            p.update({"member": name, "top_interests": [], "question_style": [], "recurring_targets": [], "open_commitments": [], "updated_at": time.time()})
        p["meeting_date"] = label
        return p

    groups = {"all": {}}
    for a in analyses:
        groups["all"].setdefault(a.get("speaker"), []).append(a)
        groups.setdefault(a.get("meeting_date"), {}).setdefault(a.get("speaker"), []).append(a)
    profiles = {"all": {}, "by_meeting": {}}
    for label, by_member in groups.items():
        print(f"\n프로필 집계 [{label}]: 위원 {len([n for n in roster.names if n in by_member])}명")
        for name in roster.names:
            items = by_member.get(name)
            if not items:
                continue
            p = await profile(name, items, label)
            (profiles["all"] if label == "all" else profiles["by_meeting"].setdefault(label, {}))[name] = p
            print(f"  {name:4s} 구간 {p['segment_count']} 주제 {p['topic_count']} 관심 {', '.join(x['tag'] for x in p['interest_tags'][:3])}")
    if summarizer:
        await summarizer.close()
    STORE.save_profiles(profiles)
    return profiles


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
    ap.add_argument("--export", default=STORE.web_export, help="뷰어 데이터 출력 경로")
    ap.add_argument("--no-supabase", action="store_true", help="Supabase 저장 생략")
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

    profiles = asyncio.run(run_profiles(use_llm=not args.no_profile_llm))
    out = STORE.export_web(profiles, args.export)
    print(f"뷰어 데이터: {out} ({os.path.getsize(out) // 1024}KB)")
    if not args.no_supabase:
        if config.SUPABASE_ENABLED:
            STORE.save_supabase(STORE.dates(), profiles, config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
        else:
            print("⚠️  SUPABASE_URL / SUPABASE_SERVICE_KEY 가 없어 Supabase 저장을 건너뜁니다.", file=sys.stderr)


if __name__ == "__main__":
    main()
