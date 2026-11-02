"""웹 뷰어 서버 - 방송 영상 + 싱크 맞춘 발언 패널

시청자 브라우저는 도의회 원본 HLS를 직접 재생하고(막히면 이 서버의 중계 HLS로
전환), 인식된 문장은 WebSocket으로 받는다. 각 문장에는 원본 스트림 기준 PTS가
붙어 있어, 브라우저는 영상이 그 PTS를 재생할 때 문장을 오른쪽 패널에 띄운다.

전사 결과는 transcripts/YYYY-MM-DD.jsonl(기계용)과 .txt(회의록)로 저장되며,
서버를 다시 켜도 오늘 기록을 불러와 이어서 보여 준다. Supabase를 설정하면 같은
기록이 transcripts 테이블에도 저장된다(supabase_sink.py).
"""
import os
import sys
import json
import html
import time
import asyncio
import threading
from typing import Optional

from aiohttp import web, WSMsgType

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_WEB_FILES = {"index.html", "app.js", "style.css", "summary.js", "notify.js", "demo.js"}
# 정적 자원(마스코트·배경 이미지·파비콘)은 확장자로 허용
_WEB_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".ico", ".mp4")


class TranscriptStore:
    """날짜별 전사 기록 파일 (jsonl + 사람이 읽는 txt)"""

    def __init__(self, root: str, formatter=None):
        self.root = root
        self.formatter = formatter
        os.makedirs(root, exist_ok=True)
        self._lock = threading.Lock()
        self.today = self.load_today()
        self._seq = self.today[-1]["id"] if self.today else 0

    def _path(self, ext: str, day: Optional[str] = None) -> str:
        return os.path.join(self.root, f"{day or time.strftime('%Y-%m-%d')}.{ext}")

    def create(
        self,
        text: str,
        session_id: Optional[str],
        start_s: Optional[float],
        pts0: Optional[float],
        wall0: Optional[float],
    ) -> dict:
        """문장 하나를 기록으로 만들어 파일에 저장하고 반환"""
        now = time.time()
        with self._lock:
            self._seq += 1
            safe = html.escape(text)
            rec = {
                "id": self._seq,
                "session": session_id,
                "start": round(start_s, 3) if start_s is not None else None,
                # 원본 스트림 PTS(초): 시청자 영상과 싱크를 맞추는 기준
                "pts": round(pts0 + start_s, 3) if pts0 is not None and start_s is not None else None,
                # 발언 시각: 세션 시작 시각 + 위치 (모르면 인식된 시각)
                "broadcast_time": round(wall0 + start_s, 2) if wall0 and start_s is not None else round(now, 2),
                "recognized_at": round(now, 2),
                "text": text,
                "html": self.formatter.format_text(safe) if self.formatter else safe,
            }
            stamp = time.strftime("%H:%M:%S", time.localtime(rec["broadcast_time"]))
            with open(self._path("jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            with open(self._path("txt"), "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
            self.today.append(rec)
            if len(self.today) > 2000:
                self.today = self.today[-1000:]
        return rec

    def load_today(self, limit: int = 1000) -> list:
        path = self._path("jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out[-limit:]


class CaptionServer:
    """aiohttp 서버를 별도 스레드/이벤트 루프에서 실행"""

    def __init__(self, host: str, port: int, hls_root: Optional[str], store: TranscriptStore, viewer_config: dict):
        self.host = host
        self.port = port
        self.hls_root = os.path.abspath(hls_root) if hls_root else None
        self.store = store
        # 브라우저에 /config.js로 내려 주는 설정 (정적 호스팅 시에는 web/config.js 파일)
        self.viewer_config = viewer_config

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: set = set()

        # 방송 상태 (메인 스레드에서 갱신, 이벤트 루프에서 읽음)
        self.session: Optional[dict] = None   # {"id", "relay"}
        self.status = "방송 연결 대기 중..."
        self.summary_scheduler = None  # SummaryScheduler (main.py에서 설정)

    @property
    def viewer_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------ 메인 스레드 API
    def start(self):
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        ready.wait(timeout=10)
        print(f"✅ 웹 뷰어: http://{self.host}:{self.port}", file=sys.stderr)
        # AI 요약 스케줄러가 설정되어 있으면 시작
        if self.summary_scheduler:
            self._call(self.summary_scheduler.start)

    def set_session(self, session_id: Optional[str], hls_dir: Optional[str]):
        """새 연결(세션) 시작/종료 알림 - 파이프라인 스레드에서 호출"""
        if session_id is None:
            self.session = None
        else:
            relay = f"/live/{session_id}/index.m3u8" if hls_dir else None
            self.session = {"id": session_id, "relay": relay}
        self._call(self._broadcast, {"type": "session", "session": self.session})

    def set_status(self, status: str):
        if status == self.status:
            return
        self.status = status
        self._call(self._broadcast, {"type": "status", "status": status})

    def publish(self, rec: dict):
        """저장된 문장 기록을 접속 중인 뷰어에 보낸다"""
        self._call(self._broadcast, {"type": "sentence", "sentence": rec})

    def publish_partial(self, text: str):
        """중간 인식 결과를 실시간으로 뷰어에 보낸다"""
        self._call(self._broadcast, {"type": "partial", "text": text})

    def publish_summary(self, summary: dict):
        """AI 요약 결과를 접속 중인 뷰어에 보낸다"""
        self._call(self._broadcast, {"type": "summary", "summary": summary})

    # ---------------------------------------------------------------- 내부
    def _call(self, coro_fn, *args):
        """다른 스레드에서 이벤트 루프로 코루틴을 넘긴다"""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(coro_fn(*args), self._loop)

    async def _broadcast(self, msg: dict):
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_str(data)
            except Exception:
                self._clients.discard(ws)

    async def _serve(self):
        app = web.Application()
        app.router.add_get("/", self._web_file)
        app.router.add_get("/config.js", self._config_js)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/live/{session}/{name}", self._live_file)
        app.router.add_get("/{name}", self._web_file)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, self.host, self.port).start()

    async def _web_file(self, request):
        name = request.match_info.get("name", "index.html")
        is_asset = name.lower().endswith(_WEB_ASSET_EXTS) and "/" not in name and ".." not in name
        if name not in _WEB_FILES and not is_asset:
            raise web.HTTPNotFound()
        path = os.path.join(_WEB_DIR, name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        cache = "public, max-age=3600" if is_asset else "no-cache"
        return web.FileResponse(path, headers={"Cache-Control": cache})

    async def _config_js(self, request):
        body = "window.VIEWER_CONFIG = " + json.dumps(self.viewer_config, ensure_ascii=False) + ";\n"
        return web.Response(text=body, content_type="application/javascript", headers={"Cache-Control": "no-cache"})

    async def _live_file(self, request):
        session = request.match_info["session"]
        name = request.match_info["name"]
        if not self.hls_root or "/" in session or ".." in session or "/" in name or ".." in name:
            raise web.HTTPNotFound()
        path = os.path.join(self.hls_root, session, name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        headers = {"Cache-Control": "no-cache"} if name.endswith(".m3u8") else {}
        return web.FileResponse(path, headers=headers)

    async def _ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self._clients.add(ws)
        summary = self.summary_scheduler.get_current_summary() if self.summary_scheduler else None
        await ws.send_str(json.dumps({
            "type": "hello",
            "status": self.status,
            "session": self.session,
            "sentences": self.store.today[-500:],
            "summary": summary,
        }, ensure_ascii=False))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws
