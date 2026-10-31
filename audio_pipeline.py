"""라이브 오디오 파이프라인

스트림 캡처(ffmpeg) + VAD 발화 분절을 백그라운드 스레드에서 돌리고,
잘라낸 발화를 큐에 넣는다. 느린 전사(large-v3)와 분리되어 오디오 수집이
막히지 않는다. 방송이 꺼지거나 스트림이 끊기면 자동으로 재접속한다.
"""
import os
import sys
import time
import queue
import shutil
import threading
from dataclasses import dataclass
from typing import Optional, Callable

import numpy as np

from stream_recorder import StreamRecorder
from vad_segmenter import VADSegmenter


@dataclass
class Utterance:
    """VAD로 잘라낸 발화 하나와 방송 내 위치"""
    audio: np.ndarray
    start_s: float      # 연결(세션) 시작 기준 발화 시작 시각(초)
    end_s: float
    session_id: str
    pts0: Optional[float] = None   # 세션 0초의 원본 스트림 PTS(초) - 시청자 싱크 기준
    wall0: float = 0.0             # 세션 0초의 실제 시각(epoch 초, 근사)


class LiveAudioPipeline:
    """스트림 → VAD → 발화 큐"""

    def __init__(
        self,
        url: str,
        referer: str,
        segmenter: VADSegmenter,
        sample_rate: int = 16000,
        frame_samples: int = 512,
        retry_seconds: int = 10,
        live_start_index: int = -1,
        status_callback: Optional[Callable[[str], None]] = None,
        hls_root: Optional[str] = None,
        hls_window_s: int = 3600,
        session_callback: Optional[Callable[[Optional[str], Optional[str]], None]] = None,
    ):
        self.url = url
        self.referer = referer
        self.segmenter = segmenter
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.retry_seconds = retry_seconds
        self.live_start_index = live_start_index
        self.status_callback = status_callback
        # 웹 뷰어용 영상 중계 (None이면 기존처럼 오디오만 수집)
        self.hls_root = hls_root
        self.hls_window_s = hls_window_s
        self.session_callback = session_callback
        self.session_id: Optional[str] = None

        self.utterance_queue: "queue.Queue[Utterance]" = queue.Queue()
        self._recorder: Optional[StreamRecorder] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.connected = False

    def _status(self, msg: str):
        if self.status_callback:
            try:
                self.status_callback(msg)
            except Exception:
                pass

    def _notify_session(self, session_id: Optional[str], hls_dir: Optional[str]):
        if self.session_callback:
            try:
                self.session_callback(session_id, hls_dir)
            except Exception as e:
                print(f"⚠️  세션 콜백 오류: {e}", file=sys.stderr)

    def _prepare_hls_dir(self, session_id: str) -> Optional[str]:
        """새 연결용 중계 폴더를 만들고 이전 연결의 세그먼트는 지운다"""
        if not self.hls_root:
            return None
        os.makedirs(self.hls_root, exist_ok=True)
        for name in os.listdir(self.hls_root):
            shutil.rmtree(os.path.join(self.hls_root, name), ignore_errors=True)
        return os.path.join(self.hls_root, session_id)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def start_recorder_only(self):
        """Clova 스트리밍용: recorder만 시작하고 VAD 루프는 안 돌린다.
        메인 스레드에서 read_raw_frame()으로 직접 프레임을 읽는다."""
        from stream_recorder import StreamRecorder
        self._running = True
        session_id = time.strftime("%Y%m%d-%H%M%S")
        hls_dir = self._prepare_hls_dir(session_id)
        self._recorder = StreamRecorder(
            url=self.url,
            referer=self.referer,
            sample_rate=self.sample_rate,
            frame_samples=self.frame_samples,
            live_start_index=self.live_start_index,
            hls_dir=hls_dir,
            hls_window_s=self.hls_window_s,
        )
        self._recorder.start()
        self.session_id = session_id
        self.connected = True
        self._status(f"방송 수신 중")
        self._notify_session(session_id, hls_dir)

    def _run(self):
        while self._running:
            # 연결(세션)마다 시간축이 0부터 다시 시작하므로 세션 ID로 구분한다
            session_id = time.strftime("%Y%m%d-%H%M%S")
            hls_dir = self._prepare_hls_dir(session_id)
            recorder = StreamRecorder(
                url=self.url,
                referer=self.referer,
                sample_rate=self.sample_rate,
                frame_samples=self.frame_samples,
                live_start_index=self.live_start_index,
                hls_dir=hls_dir,
                hls_window_s=self.hls_window_s,
            )
            self._recorder = recorder
            try:
                recorder.start()
                # 첫 프레임을 받을 때까지는 아직 연결 확정 아님
                got_data = False
                while self._running:
                    frame = recorder.read_frame()
                    if frame is None:
                        break  # EOF → 방송 종료/끊김 → 재접속
                    if not got_data:
                        got_data = True
                        wall0 = time.time() - len(frame) / self.sample_rate
                        self.connected = True
                        self.session_id = session_id
                        self._status(f"방송 수신 중 ({self.url.split('/')[-2]})")
                        self._notify_session(session_id, hls_dir)
                    audio = self.segmenter.process_frame(frame)
                    if audio is not None:
                        start_s = self.segmenter.last_start_s
                        self.utterance_queue.put(Utterance(
                            audio=audio,
                            start_s=start_s,
                            end_s=start_s + len(audio) / self.sample_rate,
                            session_id=session_id,
                            pts0=recorder.first_pts,
                            wall0=wall0,
                        ))
            except Exception as e:
                print(f"⚠️  스트림 파이프라인 오류: {e}", file=sys.stderr)
            finally:
                recorder.stop()
                self._recorder = None
                self.connected = False
                self.segmenter.reset()
                if self.session_id is not None:
                    self.session_id = None
                    self._notify_session(None, None)

            if not self._running:
                break

            self._status(f"방송 대기 중... {self.retry_seconds}초 후 재시도")
            print(
                f"📡 스트림 연결 종료(방송 꺼짐/끊김). {self.retry_seconds}초 후 재접속...",
                file=sys.stderr,
            )
            # 재시도 대기 (중간에 종료되면 즉시 빠져나옴)
            for _ in range(self.retry_seconds * 2):
                if not self._running:
                    break
                time.sleep(0.5)

    def get_utterance(self, timeout: float = 0.5) -> Optional[Utterance]:
        try:
            return self.utterance_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_raw_frame(self) -> Optional[np.ndarray]:
        """녹음기에서 원시 오디오 프레임을 직접 읽는다 (Clova 스트리밍용)"""
        if self._recorder is not None:
            return self._recorder.read_frame()
        return None

    def stop(self):
        self._running = False
        if self._recorder is not None:
            self._recorder.stop()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
