"""운영자 대시보드(/ops)용 처리 지표 수집

앱 곳곳(스트림 수신, STT, AI 분석, Supabase 전송)에서 한 줄씩 호출해 상태를 모은다.
모든 메서드는 스레드 안전하며 절대 예외를 밖으로 내지 않는다(모니터링이 본 처리를
막으면 안 되므로). snapshot()이 /ops/api/state 로 그대로 내려간다.

stderr에 찍히는 기존 print 로그는 install_stderr_tee()로 가로채 최근 이벤트 링버퍼에
넣는다(Supabase httpx 'HTTP Request' 잡음은 제외). 로그 파일 자체는 그대로 쓰인다.
"""
import sys
import time
import threading
from collections import deque
from typing import Optional

_NOISE = ("HTTP Request:", "fork_posix.cc")


class OpsStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.config: dict = {}

        # 스트림
        self.stream = {
            "status": "방송 연결 대기 중...",
            "status_at": self.started_at,
            "attempts": 0,
            "last_attempt_at": None,
            "connected": False,
            "connected_at": None,
            "disconnected_at": None,
            "session_id": None,
            "connections": 0,          # 확정된 연결 횟수
            "audio_seconds": 0.0,      # 현재 연결에서 받은 오디오 길이
            "audio_seconds_total": 0.0,
            "last_error": None,
            "last_error_at": None,
        }
        # 음성 인식
        self.stt = {
            "backend": None,
            "sentences_total": 0,      # 앱 시작 이후
            "last_sentence_at": None,
            "partial": "",
            "partial_at": None,
            "reconnects": 0,
            "last_error": None,
            "last_error_at": None,
        }
        self._sentence_times: deque = deque(maxlen=2000)
        self._recent_sentences: deque = deque(maxlen=30)
        # AI 분석 작업별 상태
        self.jobs: dict = {}
        self.llm = {
            "calls": 0, "errors": 0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "last_latency_s": None, "last_model": None, "last_call_at": None,
        }
        # Supabase
        self.supabase = {
            "enabled": False,
            "sent": {}, "failed": {},
            "last_success_at": None, "last_error": None, "last_error_at": None,
        }
        self._queue_size_fn = None
        self.viewer_count_fn = None
        self.summary_state_fn = None
        self.store_today_fn = None

        self._events: deque = deque(maxlen=300)
        self._event_seq = 0

    # ------------------------------------------------------------ 이벤트 로그
    def event(self, text: str, level: str = "info"):
        text = (text or "").rstrip()
        if not text:
            return
        with self._lock:
            self._event_seq += 1
            self._events.append({"n": self._event_seq, "t": time.time(), "level": level, "text": text})

    def events(self, after: int = 0, limit: int = 200) -> list:
        with self._lock:
            out = [e for e in self._events if e["n"] > after]
        return out[-limit:]

    # ------------------------------------------------------------ 스트림
    def stream_attempt(self, url: str):
        with self._lock:
            self.stream["attempts"] += 1
            self.stream["last_attempt_at"] = time.time()

    def stream_status(self, msg: str):
        with self._lock:
            if msg != self.stream["status"]:
                self.stream["status"] = msg
                self.stream["status_at"] = time.time()

    def stream_connected(self, session_id: Optional[str]):
        with self._lock:
            now = time.time()
            self.stream.update(connected=True, connected_at=now, session_id=session_id,
                               audio_seconds=0.0)
            self.stream["connections"] += 1

    def stream_disconnected(self):
        with self._lock:
            if self.stream["connected"]:
                self.stream["disconnected_at"] = time.time()
            self.stream.update(connected=False, session_id=None)

    def stream_error(self, msg: str):
        with self._lock:
            self.stream["last_error"] = msg
            self.stream["last_error_at"] = time.time()

    def audio(self, seconds: float):
        with self._lock:
            self.stream["audio_seconds"] += seconds
            self.stream["audio_seconds_total"] += seconds

    # ------------------------------------------------------------ STT
    def sentence(self, rec: dict):
        now = time.time()
        with self._lock:
            self.stt["sentences_total"] += 1
            self.stt["last_sentence_at"] = now
            self.stt["partial"] = ""
            self._sentence_times.append(now)
            self._recent_sentences.append({
                "id": rec.get("id"), "t": rec.get("broadcast_time") or now,
                "recognized_at": rec.get("recognized_at") or now,
                "text": rec.get("text", ""),
            })

    def partial(self, text: str):
        with self._lock:
            self.stt["partial"] = text or ""
            self.stt["partial_at"] = time.time()

    def stt_reconnect(self, err: str):
        with self._lock:
            self.stt["reconnects"] += 1
            self.stt["last_error"] = err
            self.stt["last_error_at"] = time.time()

    # ------------------------------------------------------------ AI 작업
    def job_start(self, name: str, detail: str = ""):
        with self._lock:
            j = self.jobs.setdefault(name, {"runs": 0, "errors": 0, "running": False,
                                            "last_start": None, "last_end": None, "last_duration_s": None,
                                            "last_result": None, "last_error": None, "next_at": None,
                                            "skipped": 0})
            j.update(running=True, last_start=time.time(), runs=j["runs"] + 1, last_detail=detail)

    def job_end(self, name: str, result: str = "", error: Optional[str] = None):
        with self._lock:
            j = self.jobs.setdefault(name, {"runs": 0, "errors": 0, "skipped": 0})
            now = time.time()
            start = j.get("last_start") or now
            j.update(running=False, last_end=now, last_duration_s=round(now - start, 2))
            if error:
                j["errors"] = j.get("errors", 0) + 1
                j["last_error"] = error
                j["last_error_at"] = now
            else:
                j["last_result"] = result

    def job_skip(self, name: str, reason: str):
        with self._lock:
            j = self.jobs.setdefault(name, {"runs": 0, "errors": 0, "skipped": 0})
            j["skipped"] = j.get("skipped", 0) + 1
            j["last_skip"] = reason
            j["last_skip_at"] = time.time()

    def job_next(self, name: str, at: float):
        with self._lock:
            self.jobs.setdefault(name, {"runs": 0, "errors": 0, "skipped": 0})["next_at"] = at

    def llm_call(self, model: str, latency_s: float, usage: Optional[dict] = None, error: Optional[str] = None):
        with self._lock:
            self.llm["calls"] += 1
            self.llm["last_model"] = model
            self.llm["last_latency_s"] = round(latency_s, 2)
            self.llm["last_call_at"] = time.time()
            if error:
                self.llm["errors"] += 1
                self.llm["last_error"] = error
            if usage:
                self.llm["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                self.llm["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    # ------------------------------------------------------------ Supabase
    def supabase_sent(self, kind: str):
        with self._lock:
            self.supabase["sent"][kind] = self.supabase["sent"].get(kind, 0) + 1
            self.supabase["last_success_at"] = time.time()

    def supabase_failed(self, kind: str, err: str):
        with self._lock:
            self.supabase["failed"][kind] = self.supabase["failed"].get(kind, 0) + 1
            self.supabase["last_error"] = f"{kind}: {err}"
            self.supabase["last_error_at"] = time.time()

    def set_supabase_queue_fn(self, fn):
        self._queue_size_fn = fn
        self.supabase["enabled"] = True

    # ------------------------------------------------------------ 스냅샷
    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            cutoff1, cutoff5 = now - 60, now - 300
            per1 = sum(1 for t in self._sentence_times if t >= cutoff1)
            per5 = sum(1 for t in self._sentence_times if t >= cutoff5)
            stt = dict(self.stt, per_minute=per1, per_5min=per5)
            snap = {
                "now": now,
                "started_at": self.started_at,
                "uptime_s": round(now - self.started_at),
                "config": dict(self.config),
                "stream": dict(self.stream),
                "stt": stt,
                "recent_sentences": list(self._recent_sentences),
                "jobs": {k: dict(v) for k, v in self.jobs.items()},
                "llm": dict(self.llm),
                "supabase": dict(self.supabase, sent=dict(self.supabase["sent"]), failed=dict(self.supabase["failed"])),
            }
        # 락 밖에서 외부 상태 조회 (다른 락과 얽히지 않게)
        try:
            snap["supabase"]["queue"] = self._queue_size_fn() if self._queue_size_fn else 0
        except Exception:
            snap["supabase"]["queue"] = None
        try:
            snap["viewers"] = self.viewer_count_fn() if self.viewer_count_fn else 0
        except Exception:
            snap["viewers"] = None
        try:
            snap["sentences_today"] = self.store_today_fn() if self.store_today_fn else None
        except Exception:
            snap["sentences_today"] = None
        try:
            s = self.summary_state_fn() if self.summary_state_fn else None
            snap["summary"] = {
                "current_topic": s.get("current_topic") if s else None,
                "requests": len(s.get("requests") or []) if s else 0,
                "issues": len(s.get("issues") or []) if s else 0,
                "topics": len(s.get("topics") or []) if s else 0,
                "updated_at": s.get("updated_at") if s else None,
                "next_update_at": s.get("next_update_at") if s else None,
                "full_summary_at": s.get("full_summary_at") if s else None,
            }
        except Exception:
            snap["summary"] = None
        return snap


stats = OpsStats()


class _StderrTee:
    """sys.stderr 래퍼: 원래 stderr에 그대로 쓰고, 줄 단위로 이벤트 링버퍼에도 넣는다"""

    def __init__(self, orig):
        self._orig = orig
        self._buf = ""

    def write(self, s):
        try:
            self._orig.write(s)
        except Exception:
            pass
        try:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if not line or any(n in line for n in _NOISE):
                    continue
                level = "error" if ("❌" in line or "⚠️" in line or "rror" in line) else "info"
                stats.event(line, level)
                if "ffmpeg:" in line and "rror" in line:
                    stats.stream_error(line.replace("ffmpeg: ", "", 1))
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._orig, name)


def install_stderr_tee():
    if not isinstance(sys.stderr, _StderrTee):
        sys.stderr = _StderrTee(sys.stderr)
