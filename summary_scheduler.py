"""AI 요약 스케줄러 - 5분 주기 분석 타이머 + 구간(기본 30분) 보고서 요약

CaptionServer의 asyncio 이벤트 루프 위에서 동작한다.
메인 STT 처리 루프를 절대 차단하지 않는다.

타이머 흐름:
  1. 방송 시작 후 5분 경과 → 첫 분석
  2. 이후 5분마다 새 발언이 있으면 분석
  3. 15분마다(3회차) 전체 회의 요약도 갱신
  4. 구간(SUMMARY_WINDOW_MIN, 기본 30분)이 끝나면 그 구간 발언 전체를 따로 요약해
     ai_summaries 행(summary_json.block)과 transcripts/날짜_보고서.md 에 남긴다
  5. LLM 실패 시 기존 결과 유지

요청자료는 집중 기관(config.AUDIT_FOCUS)이 발언에 명시적으로 언급된 것만 남긴다.
"""
import os
import sys
import time
import asyncio
from typing import Callable, Optional

import config
from summarizer import Summarizer, SummaryState
from ops_stats import stats as _ops
from speaker_tagger import normalize_items


class SummaryScheduler:
    """5분 주기 AI 분석 스케줄러"""

    def __init__(
        self,
        summarizer: Summarizer,
        store,  # TranscriptStore
        publish_fn: Callable[[dict], None],
        interval_s: int = 300,
        full_interval_s: int = 900,
        window_min: int = 30,
        report_dir: Optional[str] = None,
    ):
        self.summarizer = summarizer
        self.store = store
        self.publish_fn = publish_fn  # CaptionServer.publish_summary
        self.interval_s = interval_s
        self.full_interval_s = full_interval_s
        self.window_min = max(1, int(window_min))
        self.report_dir = report_dir

        self.state = SummaryState()
        self._running = False
        self._analyzing = False  # 동시 실행 방지
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._supabase_sink = None  # SupabaseSink (main.py에서 설정)
        self._session_id: Optional[str] = None
        # 구간(기본 30분) 단위 요약
        self._current_win: Optional[tuple] = None   # (시, 구간 번호)
        self._win_start_ts: Optional[float] = None  # 현재 구간 시작 시각(epoch)
        self._win_count: int = 0                    # 구간 행 번호 (ai_summaries.window_num)
        self._win_row_num: Optional[int] = None     # 현재 구간 행의 window_num (None이면 아직 저장 안 함)
        self._windows: list = []                    # 구간별 최신 요약 데이터 (뷰어 hourly, 보고서)
        self._blocks: list = []                     # 닫힌 구간의 보고서 요약
        self._last_new_requests: list = []          # 마지막 5분 분석에서 새로 남긴 요청 (Supabase 저장용)

    def set_session(self, session_id: Optional[str]):
        """방송 세션 시작/종료 - 구간 요약 행은 세션별로 새로 만든다 (구간 자체는 이어진다)"""
        self._session_id = session_id
        self._win_row_num = None

    # ------------------------------------------------------------ 뷰어용 데이터
    def _payload(self) -> dict:
        d = self.state.to_dict()
        d["hourly"] = list(self._windows)
        d["window_min"] = self.window_min
        return d

    def get_current_summary(self) -> Optional[dict]:
        """현재 요약 상태 (새 WebSocket 연결 시 hello에 포함)"""
        if not self.state.updated_at:
            return None
        return self._payload()

    async def start(self):
        """이벤트 루프에서 첫 타이머 시작"""
        self._loop = asyncio.get_event_loop()
        self._running = True
        print(f"✅ AI 요약 스케줄러 시작 (요청감지 60초, 전체분석 {self.interval_s}초 주기, {self.window_min}분 구간 보고서)", file=sys.stderr)
        # 전체 분석은 interval_s 후
        self._schedule_next(self.interval_s)
        # 요청 감지는 60초 주기로 별도 실행
        self._schedule_request_detect(60)

    def stop(self):
        self._running = False

    def _schedule_next(self, delay: float):
        if self._running and self._loop:
            _ops.job_next("5분 분석", time.time() + delay)
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._tick()))

    def _schedule_request_detect(self, delay: float):
        if self._running and self._loop:
            _ops.job_next("요청 감지", time.time() + delay)
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._tick_requests()))

    # ------------------------------------------------------------ 집중 기관 필터
    def _mentions_focus(self, req: dict) -> bool:
        """요청 발언 원문(또는 근거 문장)에 집중 기관이 명시적으로 언급됐는가. 집중 기관이 없으면 항상 True."""
        aliases = config.AUDIT_FOCUS_ALIASES
        if not aliases:
            return True
        texts = [req.get("original_text") or ""]
        ids = {i for i in (req.get("sentence_ids") or []) if isinstance(i, int)}
        if ids:
            texts += [s.get("text", "") for s in self.store.today if s.get("id") in ids]
        blob = " ".join(texts).replace(" ", "")
        return any(a in blob for a in aliases)

    def _focus_filter(self, requests: list) -> list:
        kept = []
        for req in requests or []:
            if self._mentions_focus(req):
                kept.append(req)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] 🔕 요청 제외({config.AUDIT_FOCUS} 미언급): {req.get('description', '')[:60]}", file=sys.stderr)
        return kept

    async def _tick_requests(self):
        """1분 주기: 최근 발언에서 자료 요청만 빠르게 감지"""
        if not self._running:
            return
        try:
            now = time.time()
            # 최근 60초 발언
            sentences = [
                s for s in self.store.today
                if s.get("broadcast_time", 0) >= now - 65  # 약간 여유
            ]
            if not sentences:
                _ops.job_skip("요청 감지", "최근 60초 발언 없음")
            if sentences:
                last_id = sentences[-1].get("id", 0)
                # 이미 분석한 문장이면 스킵
                if last_id == getattr(self, "_last_request_id", 0):
                    _ops.job_skip("요청 감지", "새 발언 없음")
                if last_id != getattr(self, "_last_request_id", 0):
                    _ops.job_start("요청 감지", f"{len(sentences)}문장")
                    new_requests = await self.summarizer.detect_requests(sentences, self.state)
                    new_requests = self._focus_filter(normalize_items(new_requests))
                    _ops.job_end("요청 감지", f"{len(sentences)}문장 → 요청 {len(new_requests or [])}건")
                    if new_requests:
                        stamp = time.strftime("%H:%M:%S")
                        print(f"[{stamp}] 🔍 요청 {len(new_requests)}건 감지", file=sys.stderr)
                        for req in new_requests:
                            # 중복 방지
                            existing = next(
                                (r for r in self.state.requests if r.get("description") == req.get("description")),
                                None,
                            )
                            if not existing:
                                req["requested_at"] = self._sentence_time(req.get("sentence_ids"), now)
                                self.state.requests.append(req)
                                # Supabase에 즉시 저장
                                if self._supabase_sink and self._session_id:
                                    self._supabase_sink.save_requests(self._session_id, [req])
                        # WebSocket으로 즉시 브로드캐스트
                        self.publish_fn(self._payload())
                    self._last_request_id = last_id
        except Exception as e:
            _ops.job_end("요청 감지", error=str(e))
            print(f"⚠️  요청 감지 오류: {e}", file=sys.stderr)
        finally:
            self._schedule_request_detect(60)

    async def _tick(self):
        """주기적 분석 실행"""
        if not self._running:
            return
        if self._analyzing:
            # 이전 분석이 아직 진행 중 → 다음 주기로 미룸
            _ops.job_skip("5분 분석", "이전 분석 진행 중")
            self._schedule_next(self.interval_s)
            return

        self._analyzing = True
        try:
            await self._do_analysis()
        except Exception as e:
            _ops.job_end("5분 분석", error=str(e))
            print(f"❌ AI 분석 오류: {e}", file=sys.stderr)
        finally:
            self._analyzing = False
            self._schedule_next(self.interval_s)

    async def _do_analysis(self):
        """실제 분석 수행"""
        now = time.time()
        window_start = now - self.interval_s

        # 구간이 끝났는데(정회 등으로 새 발언이 없어도) 아직 안 닫혔으면 먼저 닫는다
        await self._close_window_if_due(now)

        # TranscriptStore에서 최근 윈도우의 문장 수집
        sentences = [
            s for s in self.store.today
            if s.get("broadcast_time", 0) >= window_start
        ]

        # 새 문장이 없으면 skip
        if not sentences:
            _ops.job_skip("5분 분석", "최근 윈도우 발언 없음")
            self.state.next_update_at = now + self.interval_s
            return

        last_id = sentences[-1].get("id", 0)
        if last_id == self.state.last_sentence_id:
            _ops.job_skip("5분 분석", "새 발언 없음")
            self.state.next_update_at = now + self.interval_s
            return

        # 5분 윈도우 분석
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] 🤖 AI 분석 시작 ({len(sentences)}문장)", file=sys.stderr)

        _ops.job_start("5분 분석", f"{len(sentences)}문장")
        result = await self.summarizer.analyze_window(sentences, self.state)
        if result:
            self._apply_window_result(result, last_id, now)
            print(f"[{stamp}] ✅ AI 분석 완료: {self.state.current_topic}", file=sys.stderr)
            _ops.job_end("5분 분석", f"{len(sentences)}문장 → 주제 '{self.state.current_topic}', "
                                   f"요청 {len(result.get('requests') or [])}건, 지적 {len(result.get('issues') or [])}건")
        else:
            _ops.job_end("5분 분석", error="LLM 응답 없음(타임아웃/파싱 실패)")

        # 15분 주기: 전체 회의 요약
        self.state.window_count += 1
        full_cycles = self.full_interval_s // max(self.interval_s, 1)
        if self.state.window_count % full_cycles == 0:
            _ops.job_start("전체 요약")
            full = await self.summarizer.summarize_full(self.state)
            if full:
                self.state.full_summary = full
                self.state.full_summary_at = now
                _ops.job_end("전체 요약", f"{len(full)}자")
            else:
                _ops.job_end("전체 요약", error="LLM 응답 없음")

        # 구간 단위 요약: 발언을 구간별로 누적하고, 구간이 바뀌면 이전 구간을 닫는다
        await self._accumulate_window(sentences, now)

        # 결과 브로드캐스트 (WebSocket)
        self.state.updated_at = now
        self.state.next_update_at = now + self.interval_s
        self.publish_fn(self._payload())

        # Supabase에 요청사항/지적사항 저장
        if self._supabase_sink and self._session_id and result:
            try:
                new_requests = self._last_new_requests
                if new_requests:
                    self._supabase_sink.save_requests(self._session_id, new_requests)
                new_issues = normalize_items(result.get("issues", []))
                if new_issues:
                    self._supabase_sink.save_issues(self._session_id, new_issues)
            except Exception as e:
                print(f"⚠️  Supabase 저장 오류: {e}", file=sys.stderr)

    # ------------------------------------------------------------ 구간(30분) 요약
    def _win_key(self, t: float) -> tuple:
        lt = time.localtime(t)
        return (lt.tm_hour, lt.tm_min // self.window_min)

    def _win_label(self, key: tuple) -> str:
        h, slot = key
        start = h * 60 + slot * self.window_min
        end = start + self.window_min
        return f"{start // 60:02d}:{start % 60:02d} ~ {(end // 60) % 24:02d}:{end % 60:02d}"

    def _win_start(self, key: tuple, ref: float) -> float:
        lt = time.localtime(ref)
        h, slot = key
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, slot * self.window_min, 0, 0, 0, -1))

    def _win_sentences(self, start: float) -> list:
        end = start + self.window_min * 60
        return [s for s in self.store.today if start <= (s.get("broadcast_time") or 0) < end]

    async def _accumulate_window(self, sentences: list, now: float):
        """발언을 구간별로 배정하고, 구간이 넘어갔으면 이전 구간을 닫은 뒤 현재 구간 행을 갱신한다."""
        if not sentences:
            return
        latest = max(sentences, key=lambda s: s.get("broadcast_time") or 0)
        key = self._win_key(latest.get("broadcast_time") or now)
        if self._current_win is None:
            earliest = min(sentences, key=lambda s: s.get("broadcast_time") or 0)
            first = self._win_key(earliest.get("broadcast_time") or now)
            self._current_win = first
            self._win_start_ts = self._win_start(first, earliest.get("broadcast_time") or now)
        while key > self._current_win:
            await self._close_window(now)
            nxt = self._win_key(self._win_start_ts + self.window_min * 60)
            self._current_win = nxt
            self._win_start_ts = self._win_start_ts + self.window_min * 60
            if nxt == key:
                break
        self._persist_window(now)

    async def _close_window_if_due(self, now: float):
        """현재 구간이 지났으면 닫는다 (새 발언이 없어도 구간 요약과 보고서는 남긴다)"""
        if self._current_win is None or self._win_start_ts is None:
            return
        end = self._win_start_ts + self.window_min * 60
        if now < end + 60:  # 1분 여유: 구간 끝 직후 늦게 확정되는 문장을 기다린다
            return
        await self._close_window(now)
        self._current_win = None
        self._win_start_ts = None

    async def _close_window(self, now: float):
        """현재 구간의 발언 전체를 보고서용으로 요약하고 행을 마지막으로 갱신한다."""
        if self._current_win is None or self._win_start_ts is None:
            return
        label = self._win_label(self._current_win)
        sents = self._win_sentences(self._win_start_ts)
        block = None
        if sents:
            _ops.job_start("구간 요약", f"{label} {len(sents)}문장")
            try:
                block = await self.summarizer.summarize_block(sents, label, self.state)
            except Exception as e:
                print(f"⚠️  구간 요약 오류: {e}", file=sys.stderr)
            if block:
                _ops.job_end("구간 요약", f"{label} {len(sents)}문장 → '{block.get('title', '')}'")
                print(f"[{time.strftime('%H:%M:%S')}] 📘 {label} 구간 요약 완료: {block.get('title', '')}", file=sys.stderr)
            else:
                _ops.job_end("구간 요약", error="LLM 응답 없음")
            self._blocks.append({
                "label": label, "start": self._win_start_ts, "end": self._win_start_ts + self.window_min * 60,
                "sentence_count": len(sents), "block": block,
            })
        self._persist_window(now, block=block, closed=True)
        self._write_report(now)
        self._win_row_num = None

    def _persist_window(self, now: float, block: Optional[dict] = None, closed: bool = False):
        """현재 구간 요약 데이터를 뷰어 목록에 반영하고 ai_summaries에 저장(처음) 또는 갱신(이후)"""
        if self._current_win is None or self._win_start_ts is None:
            return
        sents = self._win_sentences(self._win_start_ts)
        if not sents:
            return
        label = self._win_label(self._current_win)
        hour = self._current_win[0]
        period = "오전" if hour < 12 else "오후"
        summary_data = {
            "hour": label,
            "hour_label": period,
            "window_min": self.window_min,
            "window_start": self._win_start_ts,
            "closed": closed,
            "block": block,
            "current_topic": self.state.current_topic,
            "recent": self.state.recent,
            "topics": self.state.topics,
            "past": self.state.past,
            "full_summary": self.state.full_summary or None,
            "updated_at": now,
            "next_update_at": now + self.interval_s,
            "sentence_count": len(sents),
        }
        # 뷰어/보고서용 목록: 같은 구간은 최신 데이터로 교체
        for i, w in enumerate(self._windows):
            if w.get("hour") == label:
                if block is None and w.get("block"):
                    summary_data["block"] = w["block"]
                self._windows[i] = summary_data
                break
        else:
            self._windows.append(summary_data)

        if not self._supabase_sink or not self._session_id:
            return
        update = self._win_row_num is not None
        if not update:
            self._win_count += 1
            self._win_row_num = self._win_count
        try:
            self._supabase_sink.save_summary(
                session_id=self._session_id,
                window_num=self._win_row_num,
                state_dict=summary_data,
                update=update,
            )
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] 📊 {label} 구간 요약 {'갱신' if update else '저장'}{' (마감)' if closed else ''} (Supabase)", file=sys.stderr)
            _ops.job_start("구간 저장")
            _ops.job_end("구간 저장", f"{label} {'갱신' if update else '저장'} ({len(sents)}문장)")
        except Exception as e:
            _ops.job_end("구간 저장", error=str(e))
            print(f"⚠️  구간 요약 저장 오류: {e}", file=sys.stderr)

    # ------------------------------------------------------------ 보고서 파일
    def build_report(self, now: Optional[float] = None) -> str:
        """구간 요약·요청자료·지적사항을 모아 마크다운 보고서 본문을 만든다."""
        now = now or time.time()
        focus = config.AUDIT_FOCUS
        date = time.strftime("%Y-%m-%d", time.localtime(now))
        lines = [f"# 경기도의회 {config.COMMITTEE_NAME} 행정사무감사 AI 요약 보고서", ""]
        lines.append(f"- 날짜: {date}")
        lines.append(f"- 생성 시각: {time.strftime('%H:%M', time.localtime(now))}")
        if config.AUDIT_AGENCIES:
            lines.append(f"- 감사 대상: {', '.join(config.AUDIT_AGENCIES)}")
        if focus:
            lines.append(f"- 집중 기관: {focus} (요약은 {focus} 관련 내용 중심, 요청자료는 {focus}이 명시된 건만)")
        lines.append("")

        lines.append(f"## {self.window_min}분 단위 구간 요약")
        lines.append("")
        if not self._blocks:
            lines.append("(아직 마감된 구간이 없습니다)")
            lines.append("")
        for b in self._blocks:
            blk = b.get("block") or {}
            title = blk.get("title") or ""
            lines.append(f"### {b['label']}" + (f" — {title}" if title else "") + f" ({b['sentence_count']}문장)")
            lines.append("")
            if blk.get("summary"):
                lines.append(blk["summary"].strip())
                lines.append("")
            if blk.get("key_points"):
                lines.append("**핵심 쟁점**")
                lines += [f"- {x}" for x in blk["key_points"]]
                lines.append("")
            if blk.get("answers"):
                lines.append("**기관 답변·약속**")
                lines += [f"- {x}" for x in blk["answers"]]
                lines.append("")
            if blk.get("requests"):
                lines.append("**이 구간의 자료 요청**")
                lines += [f"- {x}" for x in blk["requests"]]
                lines.append("")
            if blk.get("numbers"):
                lines.append("**주요 수치**: " + " · ".join(blk["numbers"]))
                lines.append("")
            if blk.get("other"):
                lines.append(f"**기타**: {blk['other']}")
                lines.append("")
            if not blk:
                lines.append("(요약 실패 - 발언 기록은 회의록 참고)")
                lines.append("")

        reqs = list(self.state.requests)
        lines.append(f"## {focus + ' ' if focus else ''}요청자료 ({len(reqs)}건)")
        lines.append("")
        for r in sorted(reqs, key=lambda r: r.get("requested_at") or 0):
            stamp = time.strftime("%H:%M", time.localtime(r["requested_at"])) if r.get("requested_at") else "--:--"
            extra = " / ".join(x for x in (r.get("type"), r.get("period"), (f"기한 {r['deadline']}" if r.get("deadline") else None)) if x)
            lines.append(f"- [{stamp}] {r.get('raised_by') or '확인필요'}: {r.get('description', '')}" + (f" ({extra})" if extra else ""))
        if not reqs:
            lines.append("(없음)")
        lines.append("")

        issues = list(self.state.issues)
        lines.append(f"## 지적사항·답변 ({len(issues)}건)")
        lines.append("")
        for i in issues:
            status = {"answered": "답변완료", "partial": "일부답변"}.get(i.get("status"), "미답변")
            lines.append(f"- [{status}] {i.get('raised_by') or '확인필요'}: {i.get('issue', '')}")
            if i.get("response"):
                lines.append(f"  - 답변({i.get('responded_by') or '기관'}): {i['response']}")
            if i.get("commitment"):
                lines.append(f"  - 조치/약속: {i['commitment']}")
        if not issues:
            lines.append("(없음)")
        lines.append("")

        if self.state.full_summary:
            lines.append("## 전체 회의 요약")
            lines.append("")
            lines.append(self.state.full_summary.strip())
            lines.append("")
        return "\n".join(lines)

    def _write_report(self, now: float):
        if not self.report_dir:
            return
        try:
            os.makedirs(self.report_dir, exist_ok=True)
            path = os.path.join(self.report_dir, f"{time.strftime('%Y-%m-%d', time.localtime(now))}_보고서.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.build_report(now))
            print(f"[{time.strftime('%H:%M:%S')}] 📄 보고서 갱신: {path}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  보고서 저장 오류: {e}", file=sys.stderr)

    # ------------------------------------------------------------ 결과 반영
    def _sentence_time(self, sentence_ids, default: float) -> float:
        """근거 문장 중 가장 이른 발언 시각. LLM이 적은 requested_at(예시값 복사 등)은 쓰지 않는다."""
        ids = {i for i in (sentence_ids or []) if isinstance(i, int)}
        if ids:
            times = [s.get("broadcast_time") for s in self.store.today if s.get("id") in ids and s.get("broadcast_time")]
            if times:
                return min(times)
        return default

    def _apply_window_result(self, result: dict, last_id: int, now: float):
        """LLM 분석 결과를 누적 상태에 반영"""
        self.state.last_sentence_id = last_id

        # 요약
        summary = result.get("summary", {})
        self.state.current_topic = summary.get("current_topic", self.state.current_topic)
        self.state.recent = summary

        # 주제 업데이트
        topic_update = summary.get("topic_update")
        if topic_update:
            title = topic_update.get("title", "")
            if topic_update.get("is_new", False) or not self.state.topics:
                # 기존 활성 주제가 있으면 past로 이동
                if self.state.topics and topic_update.get("is_new"):
                    ended = self.state.topics.pop()
                    ended["ended_at"] = now
                    self.state.past.insert(0, ended)
                self.state.topics.append({
                    "title": title,
                    "summary": topic_update.get("summary", ""),
                    "started_at": now,
                    "last_updated": now,
                    "active": True,
                })
            elif self.state.topics:
                # 기존 주제 업데이트
                self.state.topics[-1]["summary"] = topic_update.get("summary", "")
                self.state.topics[-1]["last_updated"] = now
            if topic_update.get("is_ended") and self.state.topics:
                ended = self.state.topics.pop()
                ended["ended_at"] = now
                ended["active"] = False
                self.state.past.insert(0, ended)

        # 요청자료 누적 (위원명은 명단 기준으로 정규화, 집중 기관 명시 건만, 시각은 LLM 값 대신 실제 발언 시각)
        new_requests = self._focus_filter(normalize_items(result.get("requests", [])))
        self._last_new_requests = new_requests
        for req in new_requests:
            req["requested_at"] = self._sentence_time(req.get("sentence_ids"), now)
            # 기존에 같은 설명의 요청이 있으면 업데이트
            existing = next(
                (r for r in self.state.requests if r.get("description") == req.get("description")),
                None,
            )
            if existing:
                existing.update(req)
            else:
                self.state.requests.append(req)

        # 지적/답변 누적
        new_issues = result.get("issues", [])
        for issue in new_issues:
            # 기존 미답변 지적에 답변이 온 경우 업데이트
            existing = next(
                (i for i in self.state.issues
                 if i.get("issue") == issue.get("issue") and i.get("status") != "answered"),
                None,
            )
            if existing:
                existing.update(issue)
            else:
                self.state.issues.append(issue)
