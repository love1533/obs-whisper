"""Supabase 저장/실시간 송출 모듈

인식된 문장을 Supabase transcripts 테이블에, 방송 연결 상태를 broadcast_sessions
테이블에 기록한다. 두 테이블은 Realtime(Postgres Changes)에 등록되어 있어서,
정적 호스팅한 뷰어(web/)가 INSERT/UPDATE를 실시간으로 받아 화면에 띄운다.
(테이블 생성: supabase/schema.sql)

네트워크 요청은 백그라운드 스레드에서 처리해 전사 루프를 막지 않는다. 실패해도
로컬 transcripts/*.jsonl에는 이미 저장되어 있으므로 기록이 사라지지 않는다.
"""
import sys
import time
import queue
import threading
from datetime import datetime, timezone
from typing import Optional

import httpx


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class SupabaseSink:
    """PostgREST(REST API)로 Supabase 테이블에 쓰는 클래스"""

    def __init__(self, url: str, service_key: str, committee: str, stream_url: str):
        self.committee = committee
        self.stream_url = stream_url
        headers = {"apikey": service_key, "Content-Type": "application/json"}
        if service_key.startswith("eyJ"):  # 레거시 service_role JWT
            headers["Authorization"] = f"Bearer {service_key}"
        self._client = httpx.Client(base_url=url.rstrip("/") + "/rest/v1", headers=headers, timeout=10.0)
        self._queue: "queue.Queue" = queue.Queue()
        self._session_id: Optional[str] = None
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 이전 실행이 비정상 종료되어 '방송 중'으로 남은 세션을 닫는다
        self._queue.put(("close_stale", None))

    # ------------------------------------------------------------ 메인 스레드 API
    def set_session(self, session_id: Optional[str], relay_path: Optional[str] = None):
        if session_id is None:
            if self._session_id:
                self._queue.put(("end_session", self._session_id))
            self._session_id = None
            return
        self._session_id = session_id
        self._queue.put(("start_session", {
            "id": session_id,
            "committee": self.committee,
            "stream_url": self.stream_url,
            "relay_path": relay_path,
            "status": "방송 수신 중",
            "started_at": _iso(time.time()),
        }))

    def set_status(self, status: str):
        if self._session_id:
            self._queue.put(("status", (self._session_id, status)))

    def add(self, rec: dict):
        self._queue.put(("transcript", {
            "session_id": rec["session"],
            "committee": self.committee,
            "seq": rec["id"],
            "start_s": rec["start"],
            "pts": rec["pts"],
            "broadcast_time": _iso(rec["broadcast_time"]),
            "recognized_at": _iso(rec["recognized_at"]),
            "text": rec["text"],
            "html": rec["html"],
        }))

    def stop(self, timeout: float = 10.0):
        """남은 요청을 보내고 종료"""
        self.set_session(None)
        self._running = False
        self._thread.join(timeout=timeout)
        self._client.close()

    # ---------------------------------------------------------------- 내부
    def _run(self):
        while self._running or not self._queue.empty():
            try:
                kind, payload = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            for attempt in range(3):
                try:
                    self._send(kind, payload)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️  Supabase {kind} 실패: {e}", file=sys.stderr)
                    else:
                        time.sleep(1.0 * (attempt + 1))

    def _send(self, kind: str, payload):
        c = self._client
        if kind == "transcript":
            r = c.post("/transcripts", json=payload, headers={"Prefer": "return=minimal"})
        elif kind == "start_session":
            r = c.post(
                "/broadcast_sessions",
                json=payload,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        elif kind == "status":
            session_id, status = payload
            r = c.patch(f"/broadcast_sessions?id=eq.{session_id}", json={"status": status})
        elif kind == "end_session":
            r = c.patch(
                f"/broadcast_sessions?id=eq.{payload}",
                json={"status": "방송 종료", "ended_at": _iso(time.time())},
            )
        elif kind == "close_stale":
            r = c.patch(
                "/broadcast_sessions",
                params={"committee": f"eq.{self.committee}", "ended_at": "is.null"},
                json={"status": "방송 종료", "ended_at": _iso(time.time())},
            )
        else:
            return
        r.raise_for_status()
