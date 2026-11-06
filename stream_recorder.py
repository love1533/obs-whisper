"""방송 스트림 오디오 수집 모듈

HLS 스트림에서 ffmpeg로 직접 16kHz 모노 PCM을 뽑아 프레임 단위로 공급한다.
YouTube 라이브 등 플랫폼 URL은 streamlink stdout → ffmpeg stdin 파이프로 연결한다.
"""
import os
import re
import subprocess
import shutil
import sys
import threading
import time
import numpy as np
from typing import Optional

from ops_stats import stats as _ops


_PTS_TIME_RE = re.compile(r"pts_time:(-?[\d.]+)")


def _is_platform_url(url: str) -> bool:
    return any(h in url for h in ("youtube.com/", "youtu.be/", "twitch.tv/"))


def _find_streamlink() -> str:
    venv_bin = os.path.join(os.path.dirname(sys.executable), "streamlink")
    if os.path.isfile(venv_bin):
        return venv_bin
    found = shutil.which("streamlink")
    if found:
        return found
    raise FileNotFoundError("streamlink이 설치되어 있지 않습니다. pip install streamlink")


class StreamRecorder:
    """HLS 스트림에서 ffmpeg로 오디오를 읽는 클래스"""

    def __init__(
        self,
        url: str,
        referer: str = "",
        sample_rate: int = 16000,
        frame_samples: int = 512,
        live_start_index: int = -1,
        hls_dir: Optional[str] = None,
        hls_window_s: int = 3600,
    ):
        self.url = url
        self.referer = referer
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.frame_bytes = frame_samples * 2
        self.live_start_index = live_start_index
        self.hls_dir = hls_dir
        self.hls_window_s = hls_window_s
        self.process: Optional[subprocess.Popen] = None
        self._streamlink_proc: Optional[subprocess.Popen] = None
        self.first_pts: Optional[float] = None

        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg가 설치되어 있지 않습니다. 'brew install ffmpeg'로 설치하세요.")

    def start(self):
        if _is_platform_url(self.url):
            self._start_via_streamlink()
        else:
            self._start_direct()

    def _start_via_streamlink(self):
        """YouTube 등: streamlink stdout → ffmpeg stdin (파이프)"""
        sl_bin = _find_streamlink()
        # HLS 중계가 필요하면 영상도 포함해야 하므로 480p, 아니면 오디오만으로 360p
        quality = "480p,360p,worst" if self.hls_dir else "360p,worst"
        sl_cmd = [
            sl_bin, "--stdout",
            "--hls-live-restart",
            "--retry-streams", "3",
            "--retry-max", "0",
            self.url, quality,
        ]
        _ops.stream_attempt(self.url)
        print(f"📡 streamlink 스트림 수신: {self.url} ({quality})", file=sys.stderr)
        self._streamlink_proc = subprocess.Popen(
            sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=1024 * 1024,
        )
        threading.Thread(target=self._drain_streamlink_stderr, daemon=True).start()

        cmd = [
            "ffmpeg",
            "-hide_banner", "-nostats",
            "-loglevel", "info",
            "-fflags", "+discardcorrupt",
            "-i", "pipe:0",
        ]
        # HLS 중계: 영상+음성을 그대로 복사해서 웹 뷰어에서 재생
        if self.hls_dir:
            os.makedirs(self.hls_dir, exist_ok=True)
            hls_time = 2
            cmd += [
                "-map", "0:v?", "-map", "0:a?",
                "-c", "copy",
                "-muxdelay", "0", "-muxpreload", "0",
                "-f", "hls",
                "-hls_time", str(hls_time),
                "-hls_list_size", str(max(30, self.hls_window_s // hls_time)),
                "-hls_flags", "delete_segments+independent_segments",
                "-hls_segment_filename", os.path.join(self.hls_dir, "seg%06d.ts"),
                os.path.join(self.hls_dir, "index.m3u8"),
            ]
        cmd += [
            # 오디오 → STT용 PCM + PTS 추출
            "-filter_complex",
            "[0:a:0]asplit=2[pcm][probe];[probe]atrim=end_sample=1,ashowinfo,anullsink",
            "-map", "[pcm]",
            "-ac", "1",
            "-ar", str(self.sample_rate),
            "-f", "s16le",
            "-",
        ]
        self.first_pts = None
        self.process = subprocess.Popen(
            cmd,
            stdin=self._streamlink_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.frame_bytes * 16,
        )
        threading.Thread(target=self._read_stderr, args=(self.process,), daemon=True).start()
        print(f"🎙️  streamlink → ffmpeg 파이프 연결 완료 (HLS중계: {'ON' if self.hls_dir else 'OFF'})", file=sys.stderr)

    def _start_direct(self):
        """일반 HLS: ffmpeg 직접 연결"""
        headers = f"Referer: {self.referer}\r\n" if self.referer else ""
        cmd = [
            "ffmpeg",
            "-hide_banner", "-nostats",
            "-loglevel", "info",
            "-copyts",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-live_start_index", str(self.live_start_index),
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
        if headers:
            cmd += ["-headers", headers]
        cmd += ["-i", self.url]
        if self.hls_dir:
            os.makedirs(self.hls_dir, exist_ok=True)
            hls_time = 2
            cmd += [
                "-map", "0:v?", "-map", "0:a?",
                "-c", "copy",
                "-muxdelay", "0", "-muxpreload", "0",
                "-f", "hls",
                "-hls_time", str(hls_time),
                "-hls_list_size", str(max(30, self.hls_window_s // hls_time)),
                "-hls_flags", "delete_segments+independent_segments",
                "-hls_segment_filename", os.path.join(self.hls_dir, "seg%06d.ts"),
                os.path.join(self.hls_dir, "index.m3u8"),
            ]
        cmd += [
            "-filter_complex",
            "[0:a:0]asplit=2[pcm][probe];[probe]atrim=end_sample=1,ashowinfo,anullsink",
            "-map", "[pcm]",
            "-ac", "1",
            "-ar", str(self.sample_rate),
            "-f", "s16le",
            "-",
        ]
        self.first_pts = None
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.frame_bytes * 8,
        )
        threading.Thread(target=self._read_stderr, args=(self.process,), daemon=True).start()
        _ops.stream_attempt(self.url)
        print(f"🎙️  스트림 연결 시도: {self.url}", file=sys.stderr)

    def _drain_streamlink_stderr(self):
        proc = self._streamlink_proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line and ("error" in line.lower() or "warning" in line.lower()):
                print(f"streamlink: {line}", file=sys.stderr)

    def _read_stderr(self, process: subprocess.Popen):
        for raw in iter(process.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if self.first_pts is None and "ashowinfo" in line:
                m = _PTS_TIME_RE.search(line)
                if m:
                    self.first_pts = float(m.group(1))
            elif "rror" in line:
                print(f"ffmpeg: {line}", file=sys.stderr)

    def read_frame(self) -> Optional[np.ndarray]:
        if self.process is None or self.process.stdout is None:
            return None
        buf = bytearray()
        while len(buf) < self.frame_bytes:
            chunk = self.process.stdout.read(self.frame_bytes - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return np.frombuffer(bytes(buf), dtype=np.int16)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self):
        for proc_attr in ("process", "_streamlink_proc"):
            proc = getattr(self, proc_attr, None)
            if proc is None:
                continue
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
            except Exception:
                pass
            finally:
                setattr(self, proc_attr, None)
