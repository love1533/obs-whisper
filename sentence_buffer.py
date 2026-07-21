"""문장 버퍼 모듈 - 문장 단위로 자연스럽게 끊어 송출

발화(utterance)는 사람이 쉬는 지점에서 잘리지만, 한 발화에 여러 문장이 들어
있거나 한 문장이 두 발화에 걸칠 수 있다. 이 버퍼는 전사된 텍스트를 누적하며
종결 부호(. ! ? 등)로 끝나는 '완성된 문장'만 골라 내보내고, 미완성 꼬리는
다음 발화와 이어 붙인다. 종결 부호 없이 일정 시간이 지나면 남은 조각을
그대로 내보낸다.
"""
import re
import time
from typing import List

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
        self.last_update = time.time()

    def add(self, text: str) -> List[str]:
        """새 전사 텍스트를 추가하고, 완성된 문장 목록을 반환"""
        text = (text or "").strip()
        if not text:
            return []

        self.buffer = (self.buffer + " " + text).strip() if self.buffer else text
        self.last_update = time.time()

        completed = self._extract_complete()

        # 버퍼가 비정상적으로 길어지면(종결 부호 없이 계속 이어짐) 강제로 내보냄
        if len(self.buffer) >= self.max_chars:
            remainder = self.buffer.strip()
            self.buffer = ""
            if remainder:
                completed.append(remainder)

        return completed

    def _extract_complete(self) -> List[str]:
        """버퍼에서 완성된 문장을 떼어 내고 미완성 꼬리는 남긴다"""
        buf = self.buffer.strip()
        if not buf:
            self.buffer = ""
            return []

        parts = _SPLIT_RE.split(buf)

        if _ENDS_TERMINAL_RE.search(buf):
            # 마지막 조각도 종결 부호로 끝남 → 전부 완성
            self.buffer = ""
            done = parts
        else:
            # 마지막 조각은 미완성 → 버퍼에 남김
            self.buffer = parts[-1].strip()
            done = parts[:-1]

        return [p.strip() for p in done if p.strip()]

    def flush_if_idle(self, now: float = None) -> List[str]:
        """종결 부호 없이 일정 시간 지나면 남은 조각을 그대로 내보낸다"""
        if not self.buffer:
            return []
        now = now if now is not None else time.time()
        if now - self.last_update >= self.flush_timeout:
            remainder = self.buffer.strip()
            self.buffer = ""
            return [remainder] if remainder else []
        return []

    def flush(self) -> List[str]:
        """남은 버퍼를 강제로 모두 내보낸다 (종료 시)"""
        remainder = self.buffer.strip()
        self.buffer = ""
        return [remainder] if remainder else []
