"""AI 요약 스케줄러 - 5분 주기 분석 타이머

CaptionServer의 asyncio 이벤트 루프 위에서 동작한다.
메인 STT 처리 루프를 절대 차단하지 않는다.

타이머 흐름:
  1. 방송 시작 후 5분 경과 → 첫 분석
  2. 이후 5분마다 새 발언이 있으면 분석
  3. 15분마다(3회차) 전체 회의 요약도 갱신
  4. LLM 실패 시 기존 결과 유지
"""
import sys
import time
import asyncio
from typing import Callable, Optional

from summarizer import Summarizer, SummaryState


class SummaryScheduler:
    """5분 주기 AI 분석 스케줄러"""

    def __init__(
        self,
        summarizer: Summarizer,
        store,  # TranscriptStore
        publish_fn: Callable[[dict], None],
        interval_s: int = 300,
        full_interval_s: int = 900,
    ):
        self.summarizer = summarizer
        self.store = store
        self.publish_fn = publish_fn  # CaptionServer.publish_summary
        self.interval_s = interval_s
        self.full_interval_s = full_interval_s

        self.state = SummaryState()
        self._running = False
        self._analyzing = False  # 동시 실행 방지
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._supabase_sink = None  # SupabaseSink (main.py에서 설정)
        self._session_id: Optional[str] = None
        # 1시간 단위 요약용
        self._hour_window_sentences: list = []  # 현재 시간대 누적 발언
        self._current_hour: Optional[int] = None  # 현재 시간대 (10, 11, 14, ...)
        self._hour_count: int = 0  # 시간대별 요약 번호 (ai_summaries.window_num)
        self._hour_row_num: Optional[int] = None  # 현재 시간대 행의 window_num (None이면 아직 저장 안 함)

    def set_session(self, session_id: Optional[str]):
        """방송 세션 시작/종료 - 시간대 요약 행은 세션별로 새로 만든다"""
        self._session_id = session_id
        self._current_hour = None
        self._hour_row_num = None
        self._hour_window_sentences = []

    def get_current_summary(self) -> Optional[dict]:
        """현재 요약 상태 (새 WebSocket 연결 시 hello에 포함)"""
        if not self.state.updated_at:
            return None
        return self.state.to_dict()

    async def start(self):
        """이벤트 루프에서 첫 타이머 시작"""
        self._loop = asyncio.get_event_loop()
        self._running = True
        print(f"✅ AI 요약 스케줄러 시작 (요청감지 60초, 전체분석 {self.interval_s}초 주기)", file=sys.stderr)
        # 전체 분석은 interval_s 후
        self._schedule_next(self.interval_s)
        # 요청 감지는 60초 주기로 별도 실행
        self._schedule_request_detect(60)

    def stop(self):
        self._running = False

    def _schedule_next(self, delay: float):
        if self._running and self._loop:
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._tick()))

    def _schedule_request_detect(self, delay: float):
        if self._running and self._loop:
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._tick_requests()))

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
            if sentences:
                last_id = sentences[-1].get("id", 0)
                # 이미 분석한 문장이면 스킵
                if last_id != getattr(self, "_last_request_id", 0):
                    new_requests = await self.summarizer.detect_requests(sentences, self.state)
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
                                req["requested_at"] = now
                                self.state.requests.append(req)
                                # Supabase에 즉시 저장
                                if self._supabase_sink and self._session_id:
                                    self._supabase_sink.save_requests(self._session_id, [req])
                        # WebSocket으로 즉시 브로드캐스트
                        self.publish_fn(self.state.to_dict())
                    self._last_request_id = last_id
        except Exception as e:
            print(f"⚠️  요청 감지 오류: {e}", file=sys.stderr)
        finally:
            self._schedule_request_detect(60)

    async def _tick(self):
        """주기적 분석 실행"""
        if not self._running:
            return
        if self._analyzing:
            # 이전 분석이 아직 진행 중 → 다음 주기로 미룸
            self._schedule_next(self.interval_s)
            return

        self._analyzing = True
        try:
            await self._do_analysis()
        except Exception as e:
            print(f"❌ AI 분석 오류: {e}", file=sys.stderr)
        finally:
            self._analyzing = False
            self._schedule_next(self.interval_s)

    async def _do_analysis(self):
        """실제 분석 수행"""
        now = time.time()
        window_start = now - self.interval_s

        # TranscriptStore에서 최근 윈도우의 문장 수집
        sentences = [
            s for s in self.store.today
            if s.get("broadcast_time", 0) >= window_start
        ]

        # 새 문장이 없으면 skip
        if not sentences:
            self.state.next_update_at = now + self.interval_s
            return

        last_id = sentences[-1].get("id", 0)
        if last_id == self.state.last_sentence_id:
            self.state.next_update_at = now + self.interval_s
            return

        # 5분 윈도우 분석
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] 🤖 AI 분석 시작 ({len(sentences)}문장)", file=sys.stderr)

        result = await self.summarizer.analyze_window(sentences, self.state)
        if result:
            self._apply_window_result(result, last_id, now)
            print(f"[{stamp}] ✅ AI 분석 완료: {self.state.current_topic}", file=sys.stderr)

        # 15분 주기: 전체 회의 요약
        self.state.window_count += 1
        full_cycles = self.full_interval_s // max(self.interval_s, 1)
        if self.state.window_count % full_cycles == 0:
            full = await self.summarizer.summarize_full(self.state)
            if full:
                self.state.full_summary = full
                self.state.full_summary_at = now

        # 1시간 단위 요약: 발언을 시간대별로 누적하고, 시간대가 바뀌면 이전 시간대 요약을 저장
        self._accumulate_hourly(sentences, now)

        # 결과 브로드캐스트 (WebSocket)
        self.state.updated_at = now
        self.state.next_update_at = now + self.interval_s
        self.publish_fn(self.state.to_dict())

        # Supabase에 요청사항/지적사항 저장
        if self._supabase_sink and self._session_id and result:
            try:
                new_requests = result.get("requests", [])
                if new_requests:
                    self._supabase_sink.save_requests(self._session_id, new_requests)
                new_issues = result.get("issues", [])
                if new_issues:
                    self._supabase_sink.save_issues(self._session_id, new_issues)
            except Exception as e:
                print(f"⚠️  Supabase 저장 오류: {e}", file=sys.stderr)

    def _accumulate_hourly(self, sentences: list, now: float):
        """발언을 시간대별로 누적하고, 현재 시간대 요약을 Supabase에 5분마다 갱신한다.

        원격(Vercel/모바일) 뷰어는 WebSocket이 아니라 Supabase ai_summaries만 읽으므로,
        시간대가 끝날 때까지 기다리지 않고 매 분석마다 현재 시간대 행을 최신 상태로 유지한다.
        """
        if not sentences:
            return
        current_hour = time.localtime(now).tm_hour

        # 시간대가 바뀌었으면 이전 시간대 행을 마지막으로 갱신하고 새 행을 시작
        if self._current_hour is not None and current_hour != self._current_hour:
            self._persist_hour_summary(self._current_hour, now)
            self._hour_row_num = None
            self._hour_window_sentences = []

        self._current_hour = current_hour
        self._hour_window_sentences.extend(sentences)
        self._persist_hour_summary(current_hour, now)

    def _persist_hour_summary(self, hour: int, now: float):
        """현재 시간대 요약을 ai_summaries에 저장(처음) 또는 갱신(이후)"""
        if not self._supabase_sink or not self._session_id:
            return
        if not self._hour_window_sentences:
            return
        update = self._hour_row_num is not None
        if not update:
            self._hour_count += 1
            self._hour_row_num = self._hour_count

        period = "오전" if hour < 12 else "오후"
        hour_range = f"{hour:02d}:00 ~ {hour + 1:02d}:00"
        summary_data = {
            "hour": hour_range,
            "hour_label": period,
            "current_topic": self.state.current_topic,
            "recent": self.state.recent,
            "topics": self.state.topics,
            "past": self.state.past,
            "full_summary": self.state.full_summary or None,
            "updated_at": now,
            "next_update_at": now + self.interval_s,
            "sentence_count": len(self._hour_window_sentences),
        }
        try:
            self._supabase_sink.save_summary(
                session_id=self._session_id,
                window_num=self._hour_row_num,
                state_dict=summary_data,
                update=update,
            )
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] 📊 {hour_range} 시간대 요약 {'갱신' if update else '저장'} (Supabase)", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  시간대 요약 저장 오류: {e}", file=sys.stderr)

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

        # 요청자료 누적
        new_requests = result.get("requests", [])
        for req in new_requests:
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
