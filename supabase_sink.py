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

from ops_stats import stats as _ops


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

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def save_summary(self, session_id: str, window_num: int, state_dict: dict, update: bool = False):
        """AI 요약 결과를 ai_summaries 테이블에 저장

        update=True면 같은 (session_id, window_num) 행을 갱신한다(시간대 요약을 5분마다 최신 상태로 유지).
        """
        import json
        self._queue.put(("ai_summary_update" if update else "ai_summary", {
            "session_id": session_id,
            "committee": self.committee,
            "window_num": window_num,
            "current_topic": state_dict.get("current_topic", ""),
            "summary_json": json.dumps(state_dict, ensure_ascii=False),
            "full_summary": state_dict.get("full_summary"),
        }))

    def save_requests(self, session_id: str, requests: list):
        """AI가 감지한 요청사항을 ai_requests 테이블에 저장 (upsert)"""
        for r in requests:
            self._queue.put(("ai_request", {
                "session_id": session_id,
                "committee": self.committee,
                "raised_by": r.get("raised_by"),
                "description": r.get("description", ""),
                "original_text": r.get("original_text"),
                "type": r.get("type"),
                "target": r.get("target"),
                "period": r.get("period"),
                "deadline": r.get("deadline"),
                "status": r.get("status", "new"),
                "requested_at": _iso(r.get("requested_at")),
                "sentence_ids": r.get("sentence_ids"),
            }))

    def save_issues(self, session_id: str, issues: list):
        """AI가 감지한 지적/답변을 ai_issues 테이블에 저장"""
        for i in issues:
            self._queue.put(("ai_issue", {
                "session_id": session_id,
                "committee": self.committee,
                "issue": i.get("issue", ""),
                "raised_by": i.get("raised_by"),
                "issue_ids": i.get("issue_ids"),
                "response": i.get("response"),
                "responded_by": i.get("responded_by"),
                "response_ids": i.get("response_ids"),
                "status": i.get("status", "unanswered"),
                "commitment": i.get("commitment"),
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
                    _ops.supabase_sent(kind)
                    break
                except Exception as e:
                    if attempt == 2:
                        _ops.supabase_failed(kind, str(e))
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
        elif kind == "ai_summary":
            r = c.post("/ai_summaries", json=payload, headers={"Prefer": "return=minimal"})
        elif kind == "ai_summary_update":
            r = c.patch(
                "/ai_summaries",
                params={"session_id": f"eq.{payload['session_id']}", "window_num": f"eq.{payload['window_num']}"},
                json=payload,
                headers={"Prefer": "return=representation"},
            )
            r.raise_for_status()
            if not r.json():  # 갱신할 행이 없으면(이전 저장 실패 등) 새로 넣는다
                r = c.post("/ai_summaries", json=payload, headers={"Prefer": "return=minimal"})
        elif kind == "ai_request":
            r = c.post("/ai_requests", json=payload, headers={"Prefer": "return=minimal"})
        elif kind == "ai_issue":
            r = c.post("/ai_issues", json=payload, headers={"Prefer": "return=minimal"})
        else:
            return
        r.raise_for_status()
