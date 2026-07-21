"""라이브 오디오 파이프라인

스트림 캡처(ffmpeg) + VAD 발화 분절을 백그라운드 스레드에서 돌리고,
잘라낸 발화를 큐에 넣는다. 느린 전사(large-v3)와 분리되어 오디오 수집이
막히지 않는다. 방송이 꺼지거나 스트림이 끊기면 자동으로 재접속한다.
"""
import sys
import time
import queue
import threading
from typing import Optional, Callable

import numpy as np

from stream_recorder import StreamRecorder
from vad_segmenter import VADSegmenter


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
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self.url = url
        self.referer = referer
        self.segmenter = segmenter
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.retry_seconds = retry_seconds
        self.status_callback = status_callback

        self.utterance_queue: "queue.Queue[np.ndarray]" = queue.Queue()
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

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self._running:
            recorder = StreamRecorder(
                url=self.url,
                referer=self.referer,
                sample_rate=self.sample_rate,
                frame_samples=self.frame_samples,
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
                        self.connected = True
                        self._status(f"방송 수신 중 ({self.url.split('/')[-2]})")
                    utterance = self.segmenter.process_frame(frame)
                    if utterance is not None:
                        self.utterance_queue.put(utterance)
            except Exception as e:
                print(f"⚠️  스트림 파이프라인 오류: {e}", file=sys.stderr)
            finally:
                recorder.stop()
                self._recorder = None
                self.connected = False
                self.segmenter.reset()

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

    def get_utterance(self, timeout: float = 0.5) -> Optional[np.ndarray]:
        try:
            return self.utterance_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._running = False
        if self._recorder is not None:
            self._recorder.stop()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
