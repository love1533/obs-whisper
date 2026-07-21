"""방송 스트림 오디오 수집 모듈

BlackHole/스피커 대신 경기도의회 라이브 HLS 스트림(경제노동위원회 ch6)에서
ffmpeg로 직접 16kHz 모노 PCM을 뽑아 프레임 단위로 공급한다.
"""
import subprocess
import shutil
import sys
import numpy as np
from typing import Optional


class StreamRecorder:
    """HLS 스트림에서 ffmpeg로 오디오를 읽는 클래스"""

    def __init__(
        self,
        url: str,
        referer: str = "",
        sample_rate: int = 16000,
        frame_samples: int = 512,  # Silero VAD는 16kHz에서 512 샘플 단위를 요구
    ):
        self.url = url
        self.referer = referer
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.frame_bytes = frame_samples * 2  # int16 = 2 bytes
        self.process: Optional[subprocess.Popen] = None

        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                "ffmpeg가 설치되어 있지 않습니다. 'brew install ffmpeg'로 설치하세요."
            )

    def start(self):
        """ffmpeg 프로세스 시작 (스트림 열기)"""
        headers = f"Referer: {self.referer}\r\n" if self.referer else ""
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            # 네트워크 끊김 시 ffmpeg 자체 재접속
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
        if headers:
            cmd += ["-headers", headers]
        cmd += [
            "-i", self.url,
            "-vn",                 # 비디오 제거
            "-ac", "1",            # 모노
            "-ar", str(self.sample_rate),
            "-f", "s16le",         # raw PCM
            "-",                   # stdout
        ]
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self.frame_bytes * 8,
        )
        print(f"🎙️  스트림 연결 시도: {self.url}", file=sys.stderr)

    def read_frame(self) -> Optional[np.ndarray]:
        """정확히 frame_samples 개의 int16 샘플을 읽어 반환.

        스트림이 끝나거나 오류로 데이터가 더 없으면 None 반환.
        """
        if self.process is None or self.process.stdout is None:
            return None

        buf = bytearray()
        while len(buf) < self.frame_bytes:
            chunk = self.process.stdout.read(self.frame_bytes - len(buf))
            if not chunk:  # EOF (스트림 종료/방송 꺼짐)
                return None
            buf.extend(chunk)

        return np.frombuffer(bytes(buf), dtype=np.int16)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """ffmpeg 프로세스 종료 및 정리"""
        if self.process is None:
            return
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2.0)
        except Exception:
            pass
        finally:
            self.process = None
