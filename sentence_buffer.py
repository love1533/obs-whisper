"""문장 버퍼 모듈 - 문장 단위로 자연스럽게 끊어 송출

발화(utterance)는 사람이 쉬는 지점에서 잘리지만, 한 발화에 여러 문장이 들어
있거나 한 문장이 두 발화에 걸칠 수 있다. 이 버퍼는 전사된 텍스트를 누적하며
종결 부호(. ! ? 등)로 끝나는 '완성된 문장'만 골라 내보내고, 미완성 꼬리는
다음 발화와 이어 붙인다. 종결 부호 없이 일정 시간이 지나면 남은 조각을
그대로 내보낸다.
"""
import re
import time
from typing import List, Optional, Tuple

# 문장 종결로 볼 부호 (한국어/영문 공통)
_TERMINALS = ".!?。？！…"
# 종결 부호 뒤에서 문장을 분리 (뒤에 공백이 있거나 문자열 끝)
_SPLIT_RE = re.compile(rf"(?<=[{re.escape(_TERMINALS)}])\s+")
# 문자열이 종결 부호로 끝나는지
_ENDS_TERMINAL_RE = re.compile(rf"[{re.escape(_TERMINALS)}]\s*$")


class SentenceBuffer:
    """전사 텍스트를 문장 단위로 정리해 내보내는 버퍼"""

    def __init__(self, flush_timeout: float = 6.0, max_chars: int = 400):
        self.flush_timeout = flush_timeout
        self.max_chars = max_chars
        self.buffer = ""
        self.buffer_start: Optional[float] = None  # 버퍼 맨 앞 글자의 방송 시각(초)
        self.last_update = time.time()

    def add(self, text: str) -> List[str]:
        """새 전사 텍스트를 추가하고, 완성된 문장 목록을 반환"""
        return [t for t, _ in self.add_timed(text)]

    def add_timed(
        self, text: str, start: Optional[float] = None, end: Optional[float] = None
    ) -> List[Tuple[str, Optional[float]]]:
        """add()와 같지만 각 문장의 시작 시각(초)을 함께 반환.

        한 발화 안에 문장이 여럿이면 글자 위치 비율로 발화 구간 안의 시각을
        추정한다. 앞 발화에서 이어진 문장은 앞 발화의 시각을 그대로 쓴다.
        """
        text = (text or "").strip()
        if not text:
            return []

        if self.buffer:
            prefix_len = len(self.buffer) + 1
            self.buffer = self.buffer + " " + text
        else:
            prefix_len = 0
            self.buffer = text
            self.buffer_start = start
        self.last_update = time.time()

        prev_start = self.buffer_start

        def time_at(pos: int) -> Optional[float]:
            if pos < prefix_len or start is None:
                return prev_start if pos < prefix_len else start
            if end is None:
                return start
            return start + (end - start) * (pos - prefix_len) / max(1, len(text))

        completed = self._extract_complete(time_at)

        # 버퍼가 비정상적으로 길어지면(종결 부호 없이 계속 이어짐) 강제로 내보냄
        if len(self.buffer) >= self.max_chars:
            remainder = self.buffer.strip()
            self.buffer = ""
            if remainder:
                completed.append((remainder, self.buffer_start))

        return completed

    def _extract_complete(self, time_at) -> List[Tuple[str, Optional[float]]]:
        """버퍼에서 완성된 문장을 떼어 내고 미완성 꼬리는 남긴다"""
        buf = self.buffer
        parts = []
        pos = 0
        for m in _SPLIT_RE.finditer(buf):
            parts.append((buf[pos:m.start()], pos))
            pos = m.end()
        parts.append((buf[pos:], pos))

        if _ENDS_TERMINAL_RE.search(buf):
            # 마지막 조각도 종결 부호로 끝남 → 전부 완성
            self.buffer = ""
            done = parts
        else:
            # 마지막 조각은 미완성 → 버퍼에 남김
            tail, tail_pos = parts[-1]
            self.buffer = tail.strip()
            self.buffer_start = time_at(tail_pos)
            done = parts[:-1]

        return [(p.strip(), time_at(at)) for p, at in done if p.strip()]

    def flush_if_idle(self, now: float = None) -> List[str]:
        """종결 부호 없이 일정 시간 지나면 남은 조각을 그대로 내보낸다"""
        return [t for t, _ in self.flush_if_idle_timed(now)]

    def flush_if_idle_timed(self, now: float = None) -> List[Tuple[str, Optional[float]]]:
        if not self.buffer:
            return []
        now = now if now is not None else time.time()
        if now - self.last_update >= self.flush_timeout:
            return self.flush_timed()
        return []

    def flush(self) -> List[str]:
        """남은 버퍼를 강제로 모두 내보낸다 (종료 시)"""
        return [t for t, _ in self.flush_timed()]

    def flush_timed(self) -> List[Tuple[str, Optional[float]]]:
        remainder = self.buffer.strip()
        self.buffer = ""
        return [(remainder, self.buffer_start)] if remainder else []
