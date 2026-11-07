"""메인 애플리케이션

경기도의회 라이브 방송(경제노동위원회 ch6) 스트림을 직접 받아 MLX Whisper(GPU)로
음성을 인식하고, 문장 단위로 자연스럽게 끊어 텔레그램으로 동시 송출한다.
전송은 백그라운드 스레드에서 처리되어 오디오/전사 루프를 막지 않는다.
"""
import time
import signal
import sys
import asyncio
import threading
import queue

from audio_pipeline import LiveAudioPipeline
from vad_segmenter import VADSegmenter
from clova_streamer import ClovaStreamer
from transcriber import Transcriber
from telegram_sender import TelegramSender
from text_formatter import TextFormatter
from sentence_buffer import SentenceBuffer
from caption_server import CaptionServer, TranscriptStore
from supabase_sink import SupabaseSink
from user_manager import UserManager
from user_checker import UserChecker
from ui import AppUI
from rich.live import Live
import config
from ops_stats import stats as ops, install_stderr_tee


class OBSWhisperApp:
    """스트림 기반 실시간 음성 인식 및 텔레그램 전송 앱"""

    def __init__(self):
        # /ops 대시보드: stderr 로그를 이벤트 버퍼로도 보내고 기본 설정을 노출
        install_stderr_tee()
        ops.config = {
            "committee": config.COMMITTEE_NAME,
            "stream_url": config.STREAM_URL,
            "video_mode": config.VIDEO_MODE,
            "retry_seconds": config.STREAM_RETRY_SECONDS,
            "stt_backend": config.STT_BACKEND,
            "summary_enabled": config.SUMMARY_ENABLED,
            "summary_provider": config.SUMMARY_PROVIDER if config.SUMMARY_ENABLED else None,
            "summary_model": config.SUMMARY_MODEL if config.SUMMARY_ENABLED else None,
            "summary_interval_s": config.SUMMARY_INTERVAL_S,
            "supabase_enabled": config.SUPABASE_ENABLED,
            "web": f"http://{config.WEB_HOST}:{config.WEB_PORT}",
        }
        ops.stt["backend"] = config.STT_BACKEND
        self.ui = AppUI()

        # 키워드는 볼드 강조에만 사용한다.
        # (initial_prompt로 키워드를 넣으면 Whisper가 그 문구를 그대로 출력에
        #  되뱉는 echo 현상이 있어, 키워드 나열이 메시지로 새어 나온다. 그래서
        #  전사 프롬프트에는 키워드를 넣지 않는다.)
        self.text_formatter = TextFormatter()
        initial_prompt = None

        # 음성 인식
        self.transcriber = Transcriber(
            backend=config.STT_BACKEND,
            model_size=config.STT_MODEL,
            mlx_repo=config.STT_MLX_REPO,
            device=config.STT_DEVICE,
            compute_type=config.STT_COMPUTE_TYPE,
            language=config.STT_LANGUAGE,
            beam_size=config.STT_BEAM_SIZE,
            initial_prompt=initial_prompt,
            clova_url=config.CLOVA_SPEECH_URL,
            clova_secret=config.CLOVA_SPEECH_SECRET,
        )

        # VAD 발화 분절기
        self.segmenter = VADSegmenter(
            sample_rate=config.SAMPLE_RATE,
            threshold=config.VAD_THRESHOLD,
            min_speech_ms=config.VAD_MIN_SPEECH_MS,
            silence_ms=config.VAD_SILENCE_MS,
            max_utterance_s=config.VAD_MAX_UTTERANCE_S,
        )

        # 전사 기록 (transcripts/날짜.jsonl + .txt)
        self.store = TranscriptStore(config.TRANSCRIPT_DIR, formatter=self.text_formatter)
        ops.store_today_fn = lambda: len(self.store.today)
        relay_enabled = config.WEB_ENABLED and config.VIDEO_MODE in ("auto", "relay")

        # 웹 뷰어 (방송 영상 + 싱크 맞춘 발언 패널)
        self.caption_server = None
        if config.WEB_ENABLED:
            self.caption_server = CaptionServer(
                host=config.WEB_HOST,
                port=config.WEB_PORT,
                hls_root=config.HLS_DIR if relay_enabled else None,
                store=self.store,
                viewer_config={
                    "title": f"경기도의회 {config.COMMITTEE_NAME}",
                    "committee": config.COMMITTEE_NAME,
                    "streamUrl": config.VIEWER_STREAM_URL,
                    "videoMode": config.VIDEO_MODE,
                    "viewerDelay": config.VIEWER_DELAY_S,
                    # 앱이 직접 서빙할 때는 WebSocket이 더 빠르고 단순하므로 Supabase는
                    # 정적 호스팅한 뷰어(web/config.js)에서만 쓴다
                },
            )

        # AI 요약 (선택): 5분 단위 발언 분석
        if config.SUMMARY_ENABLED and self.caption_server:
            from summarizer import Summarizer
            from summary_scheduler import SummaryScheduler
            summarizer = Summarizer(
                provider=config.SUMMARY_PROVIDER,
                api_key=config.SUMMARY_API_KEY,
                model=config.SUMMARY_MODEL,
            )
            self.summary_scheduler = SummaryScheduler(
                summarizer=summarizer,
                store=self.store,
                publish_fn=self.caption_server.publish_summary,
                interval_s=config.SUMMARY_INTERVAL_S,
                full_interval_s=config.SUMMARY_FULL_INTERVAL_S,
                window_min=config.SUMMARY_WINDOW_MIN,
                report_dir=config.TRANSCRIPT_DIR,
            )
            self.caption_server.summary_scheduler = self.summary_scheduler
            ops.summary_state_fn = self.summary_scheduler.state.to_dict
            if config.PROFILE_ENABLED:
                try:
                    from lib.live_profile import LiveProfiler
                except Exception as e:  # pydantic 등 의존성이 없어도 앱 자체는 떠야 한다
                    LiveProfiler = None
                    print(f"⚠️  위원 성향 분석 비활성화 (모듈 로드 실패: {e}). venv/bin/pip install -r requirements.txt", file=sys.stderr)
            if config.PROFILE_ENABLED and LiveProfiler:
                self.summary_scheduler.set_profiler(
                    LiveProfiler(summarizer, self.store,
                                 supabase_url=config.SUPABASE_URL if config.SUPABASE_ENABLED else "",
                                 supabase_key=config.SUPABASE_SERVICE_KEY if config.SUPABASE_ENABLED else ""),
                    config.PROFILE_INTERVAL_MIN,
                )
                print(f"✅ 위원 성향 분석 {config.PROFILE_INTERVAL_MIN}분 주기 (오늘 발언만, config/answerers/{time.strftime('%Y-%m-%d')}.json 필요)", file=sys.stderr)
            print(f"✅ AI 요약 활성화 ({config.SUMMARY_INTERVAL_S}초 주기, {config.SUMMARY_WINDOW_MIN}분 구간 보고서, {config.SUMMARY_PROVIDER})", file=sys.stderr)
            if config.AUDIT_FOCUS:
                print(f"   집중 기관: {config.AUDIT_FOCUS} (요청자료는 명시 언급 건만) · 감사 대상: {', '.join(config.AUDIT_AGENCIES)}", file=sys.stderr)

        # Supabase (선택): 문장 저장 + Realtime 전달
        self.supabase = None
        if config.SUPABASE_ENABLED:
            self.supabase = SupabaseSink(
                url=config.SUPABASE_URL,
                service_key=config.SUPABASE_SERVICE_KEY,
                committee=config.COMMITTEE_NAME,
                stream_url=config.VIEWER_STREAM_URL,
            )
            # 스케줄러가 요약/요청사항을 Supabase에 쓰도록 연결 (이전에는 누락되어 ai_* 테이블이 비어 있었음)
            if getattr(self, "summary_scheduler", None):
                self.summary_scheduler._supabase_sink = self.supabase
            ops.set_supabase_queue_fn(self.supabase._queue.qsize)
            print("✅ Supabase 저장/실시간 전송 사용", file=sys.stderr)

        # 스트림 캡처 파이프라인
        self.pipeline = LiveAudioPipeline(
            url=config.STREAM_URL,
            referer=config.STREAM_REFERER,
            segmenter=self.segmenter,
            sample_rate=config.SAMPLE_RATE,
            retry_seconds=config.STREAM_RETRY_SECONDS,
            live_start_index=config.HLS_LIVE_START_INDEX,
            status_callback=self._on_stream_status,
            hls_root=config.HLS_DIR if relay_enabled else None,
            hls_window_s=config.HLS_WINDOW_S,
            session_callback=self._on_session,
        )

        # 문장 버퍼
        self.sentence_buffer = SentenceBuffer(flush_timeout=config.SENTENCE_FLUSH_TIMEOUT)

        # Clova 연속 스트리밍 (VAD+배치 대신)
        self.clova_streamer = None
        if config.STT_BACKEND == "clova":
            self.clova_streamer = ClovaStreamer(
                url=config.CLOVA_SPEECH_URL,
                secret=config.CLOVA_SPEECH_SECRET,
                language=config.STT_LANGUAGE,
                partial_callback=self._on_partial,
                soft_cut_s=config.CLOVA_SOFT_CUT_S,
                max_sentence_s=config.CLOVA_MAX_SENTENCE_S,
            )

        # 사용자 / 텔레그램
        self.telegram_enabled = config.TELEGRAM_ENABLED
        self.user_manager = None
        self.user_checker = None
        self.user_checker_thread = None
        self.telegram_sender = None

        if self.telegram_enabled:
            self.user_manager = UserManager()
            self.user_checker = UserChecker(
                check_interval_minutes=1,
                ui_callback=self._update_user_count_ui,
            )

            user_ids = self.user_manager.get_all_users()
            if not user_ids:
                print("⚠️  등록된 사용자가 없습니다.", file=sys.stderr)
                print("   'python3 get_chat_id.py'를 실행하여 사용자를 등록하세요.", file=sys.stderr)

            self.telegram_sender = TelegramSender(
                bot_token=config.TELEGRAM_BOT_TOKEN,
                chat_ids=user_ids,
            )
        else:
            print("ℹ️  텔레그램 발송 비활성화 (TELEGRAM_ENABLED=0)", file=sys.stderr)

        self.is_running = False
        self._buffer_session = None   # 문장 버퍼에 쌓인 조각이 속한 방송 세션
        self._session_timing = {}     # 세션 ID → (첫 음성 PTS, 세션 시작 시각)
        self._stream_status = "방송 연결 대기 중..."
        self._live = None
        self._last_ui_update = 0.0

        # 전송 전용 백그라운드 큐/스레드 (텔레그램 전송이 메인 루프를 막지 않도록)
        self.send_queue: "queue.Queue[str]" = queue.Queue()
        self.sender_thread = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ------------------------------------------------------------------ 콜백
    def _update_user_count_ui(self, user_count: int):
        self.ui.update_user_count(user_count)

    def _on_stream_status(self, msg: str):
        self._stream_status = msg
        ops.stream_status(msg)
        if self.caption_server:
            self.caption_server.set_status(msg)
        if self.supabase:
            self.supabase.set_status(msg)

    def _on_partial(self, text: str):
        """Clova 중간 인식 결과 → 웹 뷰어에 실시간 전송"""
        ops.partial(text)
        if self.caption_server:
            self.caption_server.publish_partial(text)

    def _on_session(self, session_id, hls_dir):
        """방송 연결(세션) 시작/종료 - 파이프라인 스레드에서 호출"""
        if self.caption_server:
            self.caption_server.set_session(session_id, hls_dir)
        if self.supabase:
            relay = f"/live/{session_id}/index.m3u8" if session_id and hls_dir else None
            self.supabase.set_session(session_id, relay)
        # AI 요약/요청사항을 Supabase에 저장할 때 쓸 세션 ID 전달
        if getattr(self, "summary_scheduler", None):
            self.summary_scheduler.set_session(session_id)

    def _signal_handler(self, signum, frame):
        print("\n종료 신호를 받았습니다. 정리 중...", file=sys.stderr)
        self.stop()
        sys.exit(0)

    def _start_user_checker(self):
        def run_checker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.user_checker.run_periodic_check())

        self.user_checker_thread = threading.Thread(target=run_checker, daemon=True)
        self.user_checker_thread.start()
        print("✅ 주기적 사용자 확인 시작 (1분마다)", file=sys.stderr)

    # ------------------------------------------------------------------ 송출
    def _dispatch(self, sentence: str, start_s=None, session_id=None, continued: bool = False):
        """문장을 전송 큐에 넣기만 한다 (네트워크는 백그라운드 스레드가 처리).

        메인 루프는 여기서 절대 블로킹되지 않으므로, 텔레그램이 느려도 오디오
        수집·전사 지연이 누적되지 않는다.
        """
        sentence = sentence.strip()
        if not sentence:
            return
        # 즉시 UI에 최근 인식 문장 반영
        self.ui.update_transcription(sentence, sent=True)
        pts0, wall0 = self._session_timing.get(session_id, (None, None))
        rec = self.store.create(sentence, session_id, start_s, pts0, wall0, continued=continued)
        if self.caption_server:
            self.caption_server.publish(rec)
        if self.supabase:
            self.supabase.add(rec)
        if self.telegram_enabled:
            self.send_queue.put(sentence)

    def _start_sender_thread(self):
        def run_sender():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._sender_loop())

        self.sender_thread = threading.Thread(target=run_sender, daemon=True)
        self.sender_thread.start()
        print("✅ 전송 스레드 시작 (동시 브로드캐스트)", file=sys.stderr)

    async def _sender_loop(self):
        """전송 큐를 소비하며 모든 사용자에게 동시 브로드캐스트하는 백그라운드 루프"""
        while self.is_running or not self.send_queue.empty():
            try:
                sentence = self.send_queue.get(timeout=0.3)
            except queue.Empty:
                continue

            formatted = self.text_formatter.format_text(sentence)
            user_ids = self.user_manager.get_all_users()  # 항상 최신(신규 등록자 포함)
            if not user_ids:
                continue

            try:
                result = await self.telegram_sender.broadcast_message(formatted, user_ids)
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] ❌ 전송 오류: {e}", file=sys.stderr)
                continue

            # 차단/탈퇴 사용자 자동 정리 (지연 누적 방지)
            for dead_id in result.get("dead", []):
                self.user_manager.remove_user(dead_id)
                print(f"[{time.strftime('%H:%M:%S')}] 🧹 도달불가 사용자 제거: {dead_id}", file=sys.stderr)

            if result["success"] > 0:
                print(
                    f"[{time.strftime('%H:%M:%S')}] ✅ 전송 {result['success']}/{result['total']}명 - '{sentence}'",
                    file=sys.stderr,
                )
                self.ui.update_transcription(sentence, sent=True)
                self.ui.update_status(f"전송 완료 ({result['success']}/{result['total']}명) · 대기 {self.send_queue.qsize()}건")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] ❌ 전송 실패: '{sentence}'", file=sys.stderr)

    def _refresh_ui(self):
        if self._live is not None:
            self._live.update(self.ui.render())

    # ------------------------------------------------------------------ 실행
    def run(self):
        self.ui.show_startup()

        self.is_running = True
        self.ui.set_recording(True)

        if self.telegram_enabled:
            user_ids = self.user_manager.get_all_users()
            self.ui.update_user_count(len(user_ids))
            self._start_user_checker()
            self._start_sender_thread()
            if user_ids:
                self.send_queue.put(f"{config.COMMITTEE_NAME} 자동텍스트 변환 시작합니다")
        else:
            self.ui.update_user_count(0)

        if self.caption_server:
            self.caption_server.start()
        if self.clova_streamer:
            self.clova_streamer.start()
            # Clova 스트리밍: pipeline 스레드 대신 직접 recorder 제어
            self.pipeline.start_recorder_only()
        else:
            self.pipeline.start()

        last_ui = 0.0
        try:
            with Live(self.ui.render(), refresh_per_second=4, screen=True) as live:
                self._live = live
                if self.clova_streamer:
                    self._run_clova_streaming(live)
                else:
                    self._run_vad_batch(live)

        except KeyboardInterrupt:
            self.ui.update_status("사용자에 의해 중단됨")
        except Exception as e:
            self.ui.show_error(str(e))
        finally:
            self.stop()

    def _run_clova_streaming(self, live):
        """Clova 연속 스트리밍 모드: 오디오 프레임을 그대로 Clova에 보내고 결과를 받음"""
        frames_fed = 0
        while self.is_running:
            # 1) 스트림에서 오디오 프레임 읽어서 Clova에 전달
            frame = self.pipeline.read_raw_frame()
            if frame is not None:
                self.clova_streamer.feed(frame)
                frames_fed += 1
                ops.audio(len(frame) / config.SAMPLE_RATE)
                if frames_fed == 1:
                    self.pipeline.confirm_connected()
                    self.ui.update_status("방송 수신 중 - 음성 인식 활성")
            elif frame is None:
                # 스트림 끊김, 또는 방송 전이라 연결 자체가 실패(ffmpeg 종료) → 재접속
                # (예전에는 frames_fed > 0 일 때만 재접속해서, 방송 전에 켜 두면 영원히 대기했다)
                if frames_fed > 0:
                    print(f"📡 스트림 끊김. {config.STREAM_RETRY_SECONDS}초 후 재접속...", file=sys.stderr)
                else:
                    print(f"📡 방송 전/연결 실패. {config.STREAM_RETRY_SECONDS}초 후 재접속...", file=sys.stderr)
                self._on_stream_status(f"방송 대기 중... {config.STREAM_RETRY_SECONDS}초 후 재시도")
                self.pipeline.stop()
                time.sleep(config.STREAM_RETRY_SECONDS)
                if not self.is_running:
                    break
                self.pipeline.start_recorder_only()
                frames_fed = 0
                continue

            # 2) Clova에서 완성된 문장 가져오기
            item = self.clova_streamer.get_sentence(timeout=0.01)
            if item:
                sentence, continued = item
                print(f"[{time.strftime('%H:%M:%S')}] 📝 인식{'(이어서)' if continued else ''}: '{sentence}'", file=sys.stderr)
                session_id = self.pipeline.session_id or "unknown"
                self._dispatch(sentence, None, session_id, continued=continued)

            # 3) UI 갱신
            now = time.time()
            if now - self._last_ui_update >= 0.25:
                if frames_fed == 0:
                    self.ui.update_status(self._stream_status)
                if self.caption_server:
                    viewers = self.caption_server.viewer_count
                    if viewers != self.ui.user_count:
                        self.ui.update_user_count(viewers)
                self._refresh_ui()
                self._last_ui_update = now

    def _run_vad_batch(self, live):
        """VAD + 배치 STT 모드 (Whisper 등)"""
        last_ui = 0.0
        while self.is_running:
            utterance = self.pipeline.get_utterance(timeout=0.5)

            if utterance is not None:
                dur = len(utterance.audio) / config.SAMPLE_RATE
                self.ui.update_status(f"음성 인식 중... ({dur:.1f}초 발화)")
                self._refresh_ui()

                if utterance.session_id != self._buffer_session:
                    for sentence, start in self.sentence_buffer.flush_timed():
                        self._dispatch(sentence, start, self._buffer_session)
                    self._buffer_session = utterance.session_id

                self._session_timing[utterance.session_id] = (utterance.pts0, utterance.wall0)
                text = self.transcriber.transcribe(utterance.audio, sample_rate=config.SAMPLE_RATE)
                if text and self.text_formatter.is_keyword_dump(text):
                    print(f"[{time.strftime('%H:%M:%S')}] 🚫 echo 필터: '{text}'", file=sys.stderr)
                    text = None
                if text:
                    print(f"[{time.strftime('%H:%M:%S')}] 📝 인식: '{text}'", file=sys.stderr)
                    for sentence, start in self.sentence_buffer.add_timed(
                        text, utterance.start_s, utterance.end_s
                    ):
                        self._dispatch(sentence, start, utterance.session_id)

            for sentence, start in self.sentence_buffer.flush_if_idle_timed():
                self._dispatch(sentence, start, self._buffer_session)

            now = time.time()
            if now - last_ui >= 0.25:
                if utterance is None:
                    self.ui.update_status(self._stream_status)
                if self.caption_server:
                    viewers = self.caption_server.viewer_count
                    if viewers != self.ui.user_count:
                        self.ui.update_user_count(viewers)
                elif self.telegram_enabled and self.user_manager:
                    current_users = len(self.user_manager.get_all_users())
                    if current_users != self.ui.user_count:
                        self.ui.update_user_count(current_users)
                self._refresh_ui()
                last_ui = now

    def stop(self):
        if not self.is_running:
            return

        # 버퍼에 남은 문장 + 종료 안내를 큐에 넣고(아직 is_running=True라 처리됨)
        for sentence, start in self.sentence_buffer.flush_timed():
            self._dispatch(sentence, start, self._buffer_session)
        if self.telegram_enabled and self.user_manager and self.user_manager.get_all_users():
            self.send_queue.put(f"{config.COMMITTEE_NAME} 자동텍스트 변환을 종료합니다")

        # 전송 스레드 종료 신호 및 큐 드레인 대기 (남은 메시지 모두 전송)
        self.is_running = False
        if self.sender_thread is not None:
            self.sender_thread.join(timeout=15.0)

        if self.clova_streamer:
            self.clova_streamer.stop()
        if self.user_checker:
            self.user_checker.stop()
        self.pipeline.stop()
        if self.supabase:
            self.supabase.stop()
        self.ui.set_recording(False)
        self.ui.update_status("종료됨")
        self.ui.console.print("\n[bold green]앱이 종료되었습니다.[/bold green]")


def main():
    try:
        app = OBSWhisperApp()
        app.run()
    except Exception as e:
        print(f"앱 시작 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
