"""메인 애플리케이션

경기도의회 라이브 방송(경제노동위원회 ch6) 스트림을 직접 받아 faster-whisper로
음성을 인식하고, 문장 단위로 자연스럽게 끊어 텔레그램으로 송출한다.
"""
import time
import signal
import sys
import asyncio
import threading

from audio_pipeline import LiveAudioPipeline
from vad_segmenter import VADSegmenter
from transcriber import Transcriber
from telegram_sender import TelegramSender
from text_formatter import TextFormatter
from sentence_buffer import SentenceBuffer
from user_manager import UserManager
from user_checker import UserChecker
from ui import AppUI
from rich.live import Live
import config


class OBSWhisperApp:
    """스트림 기반 실시간 음성 인식 및 텔레그램 전송 앱"""

    def __init__(self):
        self.ui = AppUI()

        # 키워드는 볼드 강조에만 사용한다.
        # (initial_prompt로 키워드를 넣으면 Whisper가 그 문구를 그대로 출력에
        #  되뱉는 echo 현상이 있어, 키워드 나열이 메시지로 새어 나온다. 그래서
        #  전사 프롬프트에는 키워드를 넣지 않는다.)
        self.text_formatter = TextFormatter()
        initial_prompt = None

        # 음성 인식 (기본: MLX Whisper large-v3, Apple GPU 가속)
        self.transcriber = Transcriber(
            backend=config.STT_BACKEND,
            model_size=config.STT_MODEL,
            mlx_repo=config.STT_MLX_REPO,
            device=config.STT_DEVICE,
            compute_type=config.STT_COMPUTE_TYPE,
            language=config.STT_LANGUAGE,
            beam_size=config.STT_BEAM_SIZE,
            initial_prompt=initial_prompt,
        )

        # VAD 발화 분절기
        self.segmenter = VADSegmenter(
            sample_rate=config.SAMPLE_RATE,
            threshold=config.VAD_THRESHOLD,
            min_speech_ms=config.VAD_MIN_SPEECH_MS,
            silence_ms=config.VAD_SILENCE_MS,
            max_utterance_s=config.VAD_MAX_UTTERANCE_S,
        )

        # 스트림 캡처 파이프라인
        self.pipeline = LiveAudioPipeline(
            url=config.STREAM_URL,
            referer=config.STREAM_REFERER,
            segmenter=self.segmenter,
            sample_rate=config.SAMPLE_RATE,
            retry_seconds=config.STREAM_RETRY_SECONDS,
            status_callback=self._on_stream_status,
        )

        # 문장 버퍼
        self.sentence_buffer = SentenceBuffer(flush_timeout=config.SENTENCE_FLUSH_TIMEOUT)

        # 사용자 / 텔레그램
        self.user_manager = UserManager()
        self.user_checker = UserChecker(
            check_interval_minutes=1,
            ui_callback=self._update_user_count_ui,
        )
        self.user_checker_thread = None

        user_ids = self.user_manager.get_all_users()
        if not user_ids:
            print("⚠️  등록된 사용자가 없습니다.", file=sys.stderr)
            print("   'python3 get_chat_id.py'를 실행하여 사용자를 등록하세요.", file=sys.stderr)

        self.telegram_sender = TelegramSender(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            chat_ids=user_ids,
        )

        self.is_running = False
        self._stream_status = "방송 연결 대기 중..."
        self._live = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ------------------------------------------------------------------ 콜백
    def _update_user_count_ui(self, user_count: int):
        self.ui.update_user_count(user_count)

    def _on_stream_status(self, msg: str):
        self._stream_status = msg

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
    def _dispatch(self, sentence: str):
        """문장 하나를 포맷팅해 텔레그램으로 브로드캐스트"""
        sentence = sentence.strip()
        if not sentence:
            return

        formatted = self.text_formatter.format_text(sentence)
        user_ids = self.user_manager.get_all_users()

        if not user_ids:
            self.ui.update_status("등록된 사용자 없음")
            self.ui.update_transcription(sentence, sent=False)
            return

        self.ui.update_status(f"텔레그램으로 전송 중... ({len(user_ids)}명)")
        self._refresh_ui()

        result = self.telegram_sender.broadcast_message_sync(formatted, user_ids)
        if result["success"] > 0:
            print(
                f"[{time.strftime('%H:%M:%S')}] ✅ 전송 {result['success']}/{result['total']}명 - '{sentence}'",
                file=sys.stderr,
            )
            self.ui.update_transcription(sentence, sent=True)
            self.ui.update_status(f"전송 완료! ({result['success']}/{result['total']}명)")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] ❌ 전송 실패: '{sentence}'", file=sys.stderr)
            self.ui.update_transcription(sentence, sent=False)
            self.ui.update_status("전송 실패")

    def _refresh_ui(self):
        if self._live is not None:
            self._live.update(self.ui.render())

    # ------------------------------------------------------------------ 실행
    def run(self):
        self.ui.show_startup()

        # 시작 안내 브로드캐스트
        user_ids = self.user_manager.get_all_users()
        if user_ids:
            start_message = f"{config.COMMITTEE_NAME} 자동텍스트 변환 시작합니다"
            print(f"[{time.strftime('%H:%M:%S')}] 📢 시작 안내 메시지 전송 중...", file=sys.stderr)
            result = self.telegram_sender.broadcast_message_sync(start_message, user_ids)
            if result["success"] > 0:
                print(
                    f"[{time.strftime('%H:%M:%S')}] ✅ 시작 안내 전송 완료: {result['success']}/{result['total']}명",
                    file=sys.stderr,
                )

        self.is_running = True
        self.ui.set_recording(True)
        self.ui.update_user_count(len(user_ids))

        self._start_user_checker()
        self.pipeline.start()

        last_ui = 0.0
        try:
            with Live(self.ui.render(), refresh_per_second=4, screen=True) as live:
                self._live = live
                while self.is_running:
                    # 발화 하나 대기 (없으면 0.5초 후 None)
                    utterance = self.pipeline.get_utterance(timeout=0.5)

                    if utterance is not None:
                        dur = len(utterance) / config.SAMPLE_RATE
                        self.ui.update_status(f"음성 인식 중... ({dur:.1f}초 발화)")
                        self._refresh_ui()

                        text = self.transcriber.transcribe(utterance, sample_rate=config.SAMPLE_RATE)
                        if text and self.text_formatter.is_keyword_dump(text):
                            # 키워드 나열(프롬프트 echo/환각)은 송출하지 않음
                            print(f"[{time.strftime('%H:%M:%S')}] 🚫 echo 필터: '{text}'", file=sys.stderr)
                            text = None
                        if text:
                            print(f"[{time.strftime('%H:%M:%S')}] 📝 인식: '{text}'", file=sys.stderr)
                            for sentence in self.sentence_buffer.add(text):
                                self._dispatch(sentence)

                    # 종결 부호 없이 멈춘 조각은 일정 시간 후 그대로 송출
                    for sentence in self.sentence_buffer.flush_if_idle():
                        self._dispatch(sentence)

                    # UI 주기적 갱신 (연결 상태 / 사용자 수)
                    now = time.time()
                    if now - last_ui >= 0.25:
                        if utterance is None:
                            self.ui.update_status(self._stream_status)
                        current_users = len(self.user_manager.get_all_users())
                        if current_users != self.ui.user_count:
                            self.ui.update_user_count(current_users)
                        self._refresh_ui()
                        last_ui = now

        except KeyboardInterrupt:
            self.ui.update_status("사용자에 의해 중단됨")
        except Exception as e:
            self.ui.show_error(str(e))
        finally:
            self.stop()

    def stop(self):
        if not self.is_running:
            return
        self.is_running = False

        # 버퍼에 남은 문장 마저 송출
        for sentence in self.sentence_buffer.flush():
            self._dispatch(sentence)

        # 종료 안내 브로드캐스트
        user_ids = self.user_manager.get_all_users()
        if user_ids:
            end_message = f"{config.COMMITTEE_NAME} 자동텍스트 변환을 종료합니다"
            print(f"[{time.strftime('%H:%M:%S')}] 📢 종료 안내 메시지 전송 중...", file=sys.stderr)
            self.telegram_sender.broadcast_message_sync(end_message, user_ids)

        self.user_checker.stop()
        self.pipeline.stop()
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
