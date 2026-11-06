"""Clova Speech Live 연속 스트리밍 모듈

오디오 프레임을 연속으로 받아 Clova gRPC 세션에 실시간 전송하고,
Clova가 보내는 단어 조각을 모아 문장 단위로 끊어 큐에 넣는다.

Clova Live 응답 특성:
- 단어 조각 단위로 text가 옴 (final=False)
- 무음 구간에서는 빈 text("")가 계속 옴
- epdType/final이 실질적으로 안 옴
→ 빈 응답이 일정 횟수 연속되면 문장 완성으로 판단
"""
import sys
import json
import time
import queue

from ops_stats import stats as _ops
import threading
from typing import Optional

import numpy as np
import grpc

import nest_pb2
import nest_pb2_grpc

# 빈 응답 연속 N회 → 문장 완성 (0.4초 × 3 = 약 1.2초 무음)
_EMPTY_THRESHOLD = 3
# 오디오를 이만큼 모아서 전송 (너무 작으면 gRPC 오버헤드, 너무 크면 지연)
_AUDIO_BATCH_MS = 500  # 0.5초


class ClovaStreamer:
    """Clova Speech Live 연속 스트리밍 전사기"""

    def __init__(self, url: str, secret: str, language: str = "ko",
                 partial_callback=None):
        self._url = url
        self._secret = secret
        self._language = language
        self._partial_callback = partial_callback  # fn(text: str) - 중간 결과 콜백

        self._audio_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=300)
        self._result_q: "queue.Queue[str]" = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 프레임 배치 버퍼
        self._batch_buf = bytearray()
        self._batch_bytes = int(16000 * 2 * _AUDIO_BATCH_MS / 1000)  # 0.5초 = 16000 bytes

        channel = grpc.secure_channel(url, grpc.ssl_channel_credentials())
        self._stub = nest_pb2_grpc.NestServiceStub(channel)
        print(f"✅ CLOVA Speech Live 스트리밍 준비: {url}", file=sys.stderr)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._audio_q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def feed(self, audio_int16: np.ndarray):
        """오디오 프레임 전달 (int16, 16kHz). 배치로 모아서 전송."""
        if not self._running:
            return
        self._batch_buf.extend(audio_int16.tobytes())
        if len(self._batch_buf) >= self._batch_bytes:
            try:
                self._audio_q.put_nowait(bytes(self._batch_buf))
            except queue.Full:
                pass  # 지연 방지: 처리보다 빠르면 버림
            self._batch_buf.clear()

    def get_sentence(self, timeout: float = 0.01) -> Optional[str]:
        try:
            return self._result_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run(self):
        while self._running:
            try:
                self._stream_session()
            except grpc.RpcError as e:
                if not self._running:
                    break
                _ops.stt_reconnect(f"gRPC {e.code()}")
                print(f"⚠️  Clova gRPC 오류: {e.code()} - 2초 후 재연결", file=sys.stderr)
                time.sleep(2)
            except Exception as e:
                if not self._running:
                    break
                _ops.stt_reconnect(str(e))
                print(f"⚠️  Clova 스트리밍 오류: {e} - 2초 후 재연결", file=sys.stderr)
                time.sleep(2)

    def _stream_session(self):
        metadata = (("authorization", f"Bearer {self._secret}"),)

        def request_gen():
            yield nest_pb2.NestRequest(
                type=nest_pb2.CONFIG,
                config=nest_pb2.NestConfig(config=json.dumps({
                    "transcription": {"language": self._language},
                }))
            )
            seq = 0
            while self._running:
                try:
                    data = self._audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if data is None:
                    break
                for i in range(0, len(data), 32000):
                    yield nest_pb2.NestRequest(
                        type=nest_pb2.DATA,
                        data=nest_pb2.NestData(
                            chunk=data[i:i + 32000],
                            extra_contents=json.dumps({"seqId": seq, "epFlag": False}),
                        )
                    )
                    seq += 1
            yield nest_pb2.NestRequest(
                type=nest_pb2.DATA,
                data=nest_pb2.NestData(
                    chunk=b"",
                    extra_contents=json.dumps({"seqId": seq, "epFlag": True}),
                )
            )

        responses = self._stub.recognize(request_gen(), metadata=metadata)

        sentence_parts = []  # 단어 조각 모음
        empty_count = 0      # 연속 빈 응답 횟수

        for resp in responses:
            if not self._running:
                break
            result = json.loads(resp.contents)
            resp_types = result.get("responseType", [])

            if "transcription" not in resp_types:
                continue

            text = result.get("transcription", {}).get("text", "")

            if text.strip():
                # 텍스트가 있으면 조각 추가, 빈 카운터 리셋
                sentence_parts.append(text)
                empty_count = 0
                # 중간 결과를 실시간으로 콜백
                if self._partial_callback:
                    partial = "".join(sentence_parts).strip()
                    if partial:
                        self._partial_callback(partial)
            else:
                # 빈 응답
                if sentence_parts:
                    empty_count += 1
                    # 빈 응답이 N회 연속 → 문장 완성
                    if empty_count >= _EMPTY_THRESHOLD:
                        sentence = "".join(sentence_parts).strip()
                        if len(sentence) >= 2:
                            self._result_q.put(sentence)
                        sentence_parts.clear()
                        empty_count = 0
                        # 문장 확정 → partial 초기화
                        if self._partial_callback:
                            self._partial_callback("")

        # 세션 종료 시 잔여
        if sentence_parts:
            sentence = "".join(sentence_parts).strip()
            if len(sentence) >= 2:
                self._result_q.put(sentence)
