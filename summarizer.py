"""AI 요약 엔진 - LLM API 호출 + 프롬프트 관리

5분 단위 발언을 분석하여 세 가지 결과를 동시에 생성한다:
  1. AI 요약 - 현재 논의 주제, 핵심 쟁점, 주요 발언
  2. 요청자료 - 의회가 요구한 자료(종류, 기간, 대상기관, 제출기한)
  3. 지적/답변 - 문제 제기 + 집행부 답변 연결, 미답변 추적

하나의 LLM 호출로 세 탭의 데이터를 모두 추출하여 API 비용을 절감한다.
이전 분석의 맥락(현재 주제, 미완료 Q&A, 미답변 지적)을 다음 윈도우에 전달하여
시간 구간을 넘는 질의응답도 자연스럽게 연결한다.
"""
import sys
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ---------------------------------------------------------------- 상태
@dataclass
class SummaryState:
    """분석 윈도우 간 전달되는 누적 맥락"""
    # 최근 5분 분석 결과
    current_topic: str = ""
    recent: Optional[dict] = None
    # 요청자료 누적
    requests: list = field(default_factory=list)
    # 지적/답변 누적
    issues: list = field(default_factory=list)
    # 주제별 누적 요약
    topics: list = field(default_factory=list)
    # 종료된 주제
    past: list = field(default_factory=list)
    # 전체 회의 요약
    full_summary: str = ""
    full_summary_at: float = 0
    # 내부 추적
    last_sentence_id: int = 0
    window_count: int = 0
    updated_at: float = 0
    next_update_at: float = 0

    def to_dict(self) -> dict:
        return {
            "current_topic": self.current_topic,
            "recent": self.recent,
            "requests": self.requests,
            "issues": self.issues,
            "topics": self.topics,
            "past": self.past,
            "full_summary": self.full_summary,
            "full_summary_at": self.full_summary_at,
            "updated_at": self.updated_at,
            "next_update_at": self.next_update_at,
        }


# ---------------------------------------------------------------- 프롬프트

_SYSTEM_PROMPT = """\
당신은 대한민국 지방의회 회의를 분석하는 AI 전문가입니다.
실시간 음성인식(STT)으로 변환된 발언을 분석하여 아래 세 가지를 JSON으로 출력합니다.

## 분석 규칙

1. **정확성**: 실제 발언에 없는 내용을 절대 생성하지 마세요.
2. **구분**: 질문(위원)과 답변(집행부/증인)을 명확히 구분하세요.
3. **용어**: 검토/추진/확정/완료를 혼동하지 마세요.
4. **STT 오류**: 의미가 불분명한 부분은 "[STT 불명확]"으로 표기하세요.
5. **문장 ID**: 각 분석 항목에 근거가 되는 sentence_ids를 반드시 포함하세요.
6. **한국어**: 모든 출력은 한국어로 작성하세요.

## 출력 JSON 형식

```json
{
  "summary": {
    "current_topic": "현재 논의 중인 주제 (1줄)",
    "key_statements": [
      {"text": "주요 발언 요약", "sentence_ids": [1,2], "type": "question|answer|statement"}
    ],
    "key_issues": ["핵심 쟁점 1", "핵심 쟁점 2"],
    "policies": ["언급된 정책/사업명"],
    "numbers": ["예산/수치 관련 언급"],
    "topic_update": {
      "title": "주제 제목",
      "summary": "이 주제에 대한 누적 요약",
      "is_new": false,
      "is_ended": false
    }
  },
  "requests": [
    {
      "description": "요청 자료 설명",
      "type": "자료종류 (현황자료/통계/계획서/보고서 등)",
      "target": "대상기관/부서",
      "period": "자료 기간 (해당 시)",
      "deadline": "제출기한 (언급된 경우)",
      "sentence_ids": [3,4],
      "status": "new|ongoing|completed"
    }
  ],
  "issues": [
    {
      "issue": "지적사항 요약",
      "raised_by": "제기자 (위원명 또는 '위원')",
      "issue_ids": [5],
      "response": "답변 내용 (있는 경우)",
      "responded_by": "답변자",
      "response_ids": [6],
      "status": "unanswered|answered|partial",
      "commitment": "약속/조치사항 (있는 경우)"
    }
  ]
}
```
"""

def _build_window_prompt(sentences: list, state: SummaryState) -> str:
    """5분 윈도우 분석용 사용자 프롬프트 구성"""
    parts = []

    # 이전 맥락 전달
    if state.current_topic:
        parts.append(f"[이전 논의 주제]: {state.current_topic}")
    # 미답변 지적사항 전달
    unanswered = [i for i in state.issues if i.get("status") != "answered"]
    if unanswered:
        items = "; ".join(i["issue"] for i in unanswered[:5])
        parts.append(f"[미답변 지적사항]: {items}")
    # 진행 중 요청자료 전달
    ongoing = [r for r in state.requests if r.get("status") != "completed"]
    if ongoing:
        items = "; ".join(r["description"] for r in ongoing[:5])
        parts.append(f"[진행 중 요청자료]: {items}")

    if parts:
        parts.append("")  # 빈 줄

    # 발언 목록
    parts.append("[최근 발언 목록]")
    for s in sentences:
        stamp = time.strftime("%H:%M:%S", time.localtime(s["broadcast_time"]))
        parts.append(f"[ID:{s['id']}] [{stamp}] {s['text']}")

    parts.append("")
    parts.append("위 발언을 분석하여 지정된 JSON 형식으로 출력하세요.")
    return "\n".join(parts)


def _build_full_prompt(state: SummaryState) -> str:
    """전체 회의 요약용 사용자 프롬프트"""
    parts = ["[전체 회의 주제별 요약을 바탕으로 종합 회의 요약을 작성하세요]", ""]

    for t in state.topics + state.past:
        parts.append(f"## {t.get('title', '주제')}")
        parts.append(t.get("summary", ""))
        parts.append("")

    if state.requests:
        parts.append("## 요청자료 현황")
        for r in state.requests:
            parts.append(f"- {r['description']} ({r.get('status', 'new')})")
        parts.append("")

    if state.issues:
        parts.append("## 지적사항 현황")
        for i in state.issues:
            status = "답변완료" if i.get("status") == "answered" else "미답변"
            parts.append(f"- {i['issue']} ({status})")
        parts.append("")

    parts.append("위 내용을 바탕으로 전체 회의 종합 요약을 2~3 문단으로 작성하세요. JSON이 아닌 일반 텍스트로 응답하세요.")
    return "\n".join(parts)


# ---------------------------------------------------------------- LLM 호출

class Summarizer:
    """LLM API를 호출하여 발언을 분석하는 엔진"""

    def __init__(self, provider: str, api_key: str, model: str):
        self.provider = provider  # "anthropic" or "openai"
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self):
        await self._client.aclose()

    async def analyze_window(self, sentences: list, state: SummaryState) -> Optional[dict]:
        """5분 윈도우 발언을 분석하여 요약/요청자료/지적답변을 한번에 추출"""
        if not sentences:
            return None
        prompt = _build_window_prompt(sentences, state)
        raw = await self._call_llm(_SYSTEM_PROMPT, prompt)
        if raw is None:
            return None
        return self._parse_json(raw)

    async def summarize_full(self, state: SummaryState) -> Optional[str]:
        """전체 회의 종합 요약 (15분 주기)"""
        if not state.topics and not state.past:
            return None
        prompt = _build_full_prompt(state)
        system = "당신은 대한민국 지방의회 회의록 요약 전문가입니다. 주어진 주제별 요약을 바탕으로 전체 회의를 간결하게 종합하세요."
        return await self._call_llm(system, prompt)

    async def _call_llm(self, system: str, user: str) -> Optional[str]:
        """Anthropic 또는 OpenAI API 호출"""
        try:
            if self.provider == "anthropic":
                return await self._call_anthropic(system, user)
            else:
                return await self._call_openai(system, user)
        except httpx.TimeoutException:
            print(f"⚠️  AI 요약 타임아웃 (60초 초과)", file=sys.stderr)
            return None
        except Exception as e:
            print(f"⚠️  AI 요약 오류: {e}", file=sys.stderr)
            # 1회 재시도
            try:
                import asyncio
                await asyncio.sleep(5)
                if self.provider == "anthropic":
                    return await self._call_anthropic(system, user)
                else:
                    return await self._call_openai(system, user)
            except Exception as e2:
                print(f"❌ AI 요약 재시도 실패: {e2}", file=sys.stderr)
                return None

    async def _call_anthropic(self, system: str, user: str) -> str:
        resp = await self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    async def _call_openai(self, system: str, user: str) -> str:
        resp = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _parse_json(self, raw: str) -> Optional[dict]:
        """LLM 응답에서 JSON 추출"""
        # 코드 블록으로 감싸져 있을 수 있음
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 첫줄(```json)과 마지막줄(```) 제거
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # { 와 } 사이를 찾아서 시도
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            print(f"⚠️  AI 응답 JSON 파싱 실패", file=sys.stderr)
            return None
