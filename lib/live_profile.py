"""회의 중 위원 성향 갱신 (기본 30분 주기) - 오늘 발언만으로 분석한다

SummaryScheduler 가 주기적으로 run_once() 를 부른다.
  1) 오늘 문장(store.today) → lib/segment 규칙으로 구간 분할
  2) 끝난 구간(마지막 구간은 진행 중이므로 제외) 중 아직 분석 안 한 것만 LLM 분석 (같은 구간은 두 번 분석하지 않는다)
  3) 오늘 분석만으로 위원별 프로필(빈도표 + LLM 요약)을 만든다 - 이전 회의 자료는 섞지 않는다
  4) transcripts/analysis/<오늘>.*.json, web/data/profiles.json(로컬 뷰어), Supabase(공개 뷰어) 에 저장
답변자 명단 config/answerers/<오늘>.json 이 없으면 경고만 하고 건너뛴다.
"""
import asyncio
import sys
import time
from typing import Optional

from lib.analysis_store import AnalysisStore, ANALYZABLE_TYPES
from lib.member_analysis import analyze_segment, build_profile
from lib.roster import Answerers, Normalizer, UNKNOWN, get_roster
from lib.segment import Line, Segmenter


class LiveProfiler:
    def __init__(self, summarizer, store, date: Optional[str] = None, concurrency: int = 2,
                 analysis_store: Optional[AnalysisStore] = None, supabase_url: str = "", supabase_key: str = "",
                 profile_llm: bool = True):
        self.summarizer = summarizer
        self.store = store                      # SentenceStore (today: 오늘 문장 목록)
        self.date = date or time.strftime("%Y-%m-%d")
        self.concurrency = max(1, concurrency)
        self.files = analysis_store or AnalysisStore()
        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        self.profile_llm = profile_llm
        self.roster = get_roster()
        self.normalizer = Normalizer()
        self.segmenter = Segmenter(self.roster)
        self._answerers: Optional[Answerers] = None
        self._warned_no_answerers = False
        self._running = False
        self.last_run_at: float = 0
        self.next_run_at: float = 0
        self.last_summary: str = ""

    # ---- 준비
    def _load_answerers(self) -> Optional[Answerers]:
        if self._answerers is None:
            try:
                self._answerers = Answerers(self.date)
            except FileNotFoundError as e:
                if not self._warned_no_answerers:
                    print(f"⚠️  위원 성향: {e} - 오늘 답변자 파일이 없어 분석을 건너뜁니다", file=sys.stderr)
                    self._warned_no_answerers = True
                return None
        return self._answerers

    def _lines(self) -> list:
        out = []
        for s in self.store.today:
            bt = s.get("broadcast_time") or s.get("recognized_at")
            if not bt:
                continue
            out.append(Line(ts=time.strftime("%H:%M:%S", time.localtime(bt)), text=s.get("text", ""), epoch=bt, id=s.get("id")))
        return out

    # ---- 본체
    async def run_once(self, now: Optional[float] = None) -> str:
        """한 번 갱신. 결과 요약 문자열을 돌려준다 (ops 로그용)."""
        if self._running:
            return "이전 갱신 진행 중"
        self._running = True
        try:
            return await self._run(now or time.time())
        finally:
            self._running = False

    async def _run(self, now: float) -> str:
        answerers = self._load_answerers()
        if answerers is None:
            return "답변자 파일 없음"
        lines = self._lines()
        if not lines:
            return "오늘 발언 없음"
        segs = self.segmenter.segment(lines)
        seg_dicts = []
        for s in segs:
            d = s.to_dict()
            d.pop("lines", None)
            d["meeting_date"] = self.date
            seg_dicts.append(d)
        self.files.save(f"{self.date}.segments.json", seg_dicts)

        # 끝난 구간만: 마지막 구간은 아직 말하는 중
        closed = seg_dicts[:-1] if seg_dicts else []
        targets = [s for s in closed if s.get("type") in ANALYZABLE_TYPES and s.get("speaker") != UNKNOWN]
        done = {self.files.seg_key(a): a for a in self.files.load(f"{self.date}.analyses.json", [])}
        todo = [s for s in targets if self.files.seg_key(s) not in done]
        meeting = {"date": self.date, "committee": self.roster.committee, "agencies": answerers.agencies, "title": answerers.title}

        sem = asyncio.Semaphore(self.concurrency)
        failed = 0

        async def one(seg):
            nonlocal failed
            async with sem:
                an = await analyze_segment(self.summarizer, seg, meeting, answerers, self.roster, self.normalizer, [])
                if not an:
                    failed += 1
                    return
                done[self.files.seg_key(seg)] = {
                    "meeting_date": self.date, "speaker": seg["speaker"], "phase": seg["phase"], "type": seg["type"],
                    "start_ts": seg["start_ts"], "end_ts": seg["end_ts"], "start_id": seg.get("start_id"),
                    "sentence_ids": seg.get("sentence_ids", []), "raw_text": seg.get("raw_text", ""),
                    "analysis": an, "model": an.get("model"), "created_at": time.time(),
                }
                print(f"[{time.strftime('%H:%M:%S')}] 👤 {seg['speaker']} {seg['start_ts']} {seg['phase']} 분석 완료 (주제 {len(an.get('topics', []))})", file=sys.stderr)

        if todo:
            await asyncio.gather(*(one(s) for s in todo))
        analyses = sorted(done.values(), key=lambda a: a.get("start_ts", ""))
        self.files.save(f"{self.date}.analyses.json", analyses)

        # 오늘 분석만으로 프로필
        by_member = {}
        for a in self.files.analyses(self.date):
            by_member.setdefault(a.get("speaker"), []).append(a)
        today_profiles = {}
        for name in self.roster.names:
            items = by_member.get(name)
            if not items:
                continue
            if self.profile_llm and self.summarizer:
                p = await build_profile(self.summarizer, name, items)
            else:
                from lib.member_analysis import tag_table
                p = tag_table(items)
                p.update({"member": name, "top_interests": [], "question_style": [], "recurring_targets": [], "open_commitments": [], "updated_at": time.time()})
            p["meeting_date"] = self.date
            today_profiles[name] = p
        profiles = self.files.load_profiles()
        profiles["by_meeting"][self.date] = today_profiles
        self.files.save_profiles(profiles)

        self.last_run_at = time.time()
        self.files.export_web(profiles, live_date=self.date, live_updated_at=self.last_run_at, live_next_at=self.next_run_at)
        if self.supabase_url and self.supabase_key:
            live_only = {"all": {}, "by_meeting": {self.date: today_profiles}}
            await asyncio.to_thread(self.files.save_supabase, [self.date], live_only, self.supabase_url, self.supabase_key, True)

        self.last_summary = (f"구간 {len(targets)} (새 분석 {len(todo) - failed}, 실패 {failed}) → 위원 {len(today_profiles)}명 프로필"
                             + (f", 진행 중 {seg_dicts[-1]['speaker']} {seg_dicts[-1]['start_ts']}" if seg_dicts and seg_dicts[-1].get('type') in ANALYZABLE_TYPES else ""))
        return self.last_summary
