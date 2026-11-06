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

from ops_stats import stats as _ops
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
이 시스템은 경기신용보증재단(경기신보) 직원들이 행정사무감사 중 위원들의 질의·요청에
실시간으로 대응하기 위해 사용합니다. 경기신보 관련 발언을 특히 정확하게 분류하고,
답변 준비에 필요한 정보(요청 자료, 언급 수치, 작년 감사 연결)를 구조화하세요.
실시간 음성인식(STT)으로 변환된 발언을 분석하여 아래 세 가지를 JSON으로 출력합니다.

## 분석 규칙

1. **정확성**: 실제 발언에 없는 내용을 절대 생성하지 마세요.
2. **구분**: 질문(위원)과 답변(집행부/증인)을 명확히 구분하세요.
3. **용어**: 검토/추진/확정/완료를 혼동하지 마세요.
4. **STT 오류**: 의미가 불분명한 부분은 "[STT 불명확]"으로 표기하세요.
5. **문장 ID**: 각 분석 항목에 근거가 되는 sentence_ids를 반드시 포함하세요.
6. **한국어**: 모든 출력은 한국어로 작성하세요.

## 경제노동위원회 자료 요청 패턴

위원들은 다음과 같은 표현으로 자료를 요청합니다:
- "자료 요구하겠습니다", "자료 요청드리겠습니다", "자료 제출해 주시기 바랍니다"
- "자료 좀 받아달라", "상세 자료", "현황 자료"
- "답변해 주십시오", "설명 좀 해주세요", "어떻게 되는 겁니까"
- 위원장이 "거수로 신청해 주시기 바랍니다" 후 위원이 순차 요청
- 위원은 보통 "OO 출신 OOO 위원"으로 자기소개 후 요청

### 대상기관 분류 (중요!)
요청의 대상기관(target)을 정확히 분류하세요:
- **"경기신용보증재단"** (= 경기신보, 신보, 신용보증재단): 보증, 대위변제, 구상권, 출연금, 보증잔액, 보증사고, 소상공인 보증 관련
- "경제실": 경기도 경제정책 일반
- "경자청"(경기경제자유구역청): 경제자유구역 관련
- 기타: "산업입지과", "소상공인과" 등

### 주요 감시 대상: 경기신용보증재단
경기신보 관련 발언은 특히 주의 깊게 분류하세요:
- 보증 정책, 부실률, 부실채권, 대위변제, 구상권, 출연금, 재산 운영, 경영평가
- 불승인율, 보증신청 승인·불승인, 손실보전
- 특례보증, 영세 소상공인 보증, 청년 기회사다리 금융
- 위탁사업, 차세대 시스템, 전산 사업
- 부채 중점기관 지정, 재정 건전성
- "신보"라는 약칭도 경기신용보증재단을 가리킵니다

참고: 작년(2025) 행정사무감사에서 주요 쟁점이었던 사항:
- 2년 연속 부채 중점기관 지정
- 불승인율 2023년 9.9% → 2025년 22%로 급증
- 부실채권 30% 이상
- 코로나19 특례보증 부실률 문제
- 차세대 시스템 사업 좌초
- 47개 위탁사업(566억) 관리 부실

현재 경제노동위원회 위원:
{{MEMBERS}}
위원명은 반드시 이 명단의 이름을 그대로 쓰고, 발언에서 누가 말했는지 확실하지 않거나 명단에 없는 이름이면 "확인필요"라고 적으세요.

{{AUDIT}}

**요청자료 추출 시 반드시 포함할 것:**
- **raised_by**: 위원명
- **original_text**: 위원의 실제 발언 원문 (해당 sentence_ids의 텍스트를 그대로 포함)
- **target**: 대상기관 (위 분류 기준에 따라)

## 이슈 정리 스타일 (잘하나 참고)

각 쟁점을 정리할 때 다음 구조를 따르세요:
- **양측 입장 균형 제시**: 위원의 지적(비판)과 집행부/재단의 답변(해명)을 동등하게
- **금액/수치 강조**: 예산액, 비율, 건수 등 구체적 숫자를 반드시 포함
- **맥락 연결**: 이전 감사(2025년)에서 지적된 사항과 올해 후속 상황을 연결
- **조치사항 추적**: 약속된 사항, 이행 여부, 기한 등

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
    "numbers": ["예산/수치 관련 언급 (금액, 비율, 건수 등 구체적으로)"],
    "topic_update": {
      "title": "주제 제목",
      "summary": "이 주제에 대한 누적 요약 (양측 입장 균형 포함)",
      "is_new": false,
      "is_ended": false
    },
    "qa_pairs": [
      {
        "question": "위원의 질의 요약",
        "q_ids": [1],
        "answer": "집행부/재단 답변 요약 (없으면 null)",
        "a_ids": [2],
        "follow_up": "후속 쟁점이 되는 부분 (있으면)"
      }
    ]
  },
  "requests": [
    {
      "description": "요청 자료 설명 (핵심 요약)",
      "original_text": "위원의 실제 발언 원문 (sentence_ids에 해당하는 텍스트를 그대로 이어붙여 포함)",
      "raised_by": "요청 위원명 (위원 목록에서 매칭)",
      "type": "자료종류 (현황자료/통계/계획서/보고서 등)",
      "target": "대상기관/부서 (경기신용보증재단/경제실/경자청 등)",
      "period": "자료 기간 (해당 시)",
      "deadline": "제출기한 (언급된 경우)",
      "requested_at": 1730456700.0,
      "sentence_ids": [3,4],
      "status": "new|ongoing|completed"
    }
  ],
  "issues": [
    {
      "issue": "지적사항 요약 (구체적 수치 포함)",
      "context": "배경 설명 (작년 감사와의 연결, 사업 경과 등)",
      "raised_by": "제기 위원명",
      "issue_ids": [5],
      "amounts": ["관련 금액/수치 (예: '55억 원', '불승인율 22%')"],
      "response": "답변 내용 (있는 경우, 원문에 가깝게)",
      "responded_by": "답변자 (직함 포함: 경제실장, 재단 이사장 등)",
      "response_ids": [6],
      "status": "unanswered|answered|partial",
      "commitment": "약속/조치사항 (기한 포함)",
      "prior_issue": "작년 감사 관련 사항 연결 (해당 시)"
    }
  ]
}
```
"""

_REQUEST_DETECT_PROMPT = """\
당신은 지방의회 회의 발언에서 자료 요청을 감지하는 분류기입니다.
아래 발언 중 자료 요청/제출 요구가 있는 것만 추출하세요.
자료 요청이 없으면 빈 배열을 반환하세요.

자료 요청 패턴:
- "자료 제출해 주십시오", "자료 요청드립니다", "자료 좀 주세요"
- "현황 자료", "상세 자료", "보고서", "명세", "실적"
- "제출해 주시기 바랍니다", "정리해서 주세요"

대상기관 분류:
- "경기신용보증재단" (= 경기신보, 신보): 보증, 대위변제, 부실, 출연금, 전산시스템 관련
- "경제실": 경기도 경제정책 일반
- 기타

위원 목록: {{MEMBERS}}
raised_by는 이 목록의 이름만 쓰고, 발언자가 불확실하거나 목록에 없으면 "확인필요"로 적으세요.

{{AUDIT}}

JSON 형식으로만 응답:
{"requests": [{"description": "요약", "original_text": "원문 그대로", "raised_by": "위원명", "target": "대상기관", "type": "종류", "sentence_ids": [ID], "status": "new"}]}
요청이 없으면: {"requests": []}
"""


_BLOCK_PROMPT = """\
당신은 대한민국 지방의회 행정사무감사 회의록을 요약하는 전문가입니다.
아래는 {{LABEL}} 구간에 있었던 발언(실시간 음성인식 결과) 전체입니다. 이 구간을 보고서에 들어갈 요약으로 정리하세요.

위원 목록: {{MEMBERS}}
위원명은 이 목록의 이름만 쓰고, 불확실하거나 목록에 없으면 "확인필요"로 적으세요.

{{AUDIT}}

규칙:
1. 발언에 없는 내용을 만들지 마세요. 수치·금액·기한·건수는 발언 그대로 적으세요.
2. 질의(위원)와 답변(기관)을 구분하고, 누가 무엇을 지적했고 기관이 어떻게 답했는지가 드러나게 쓰세요.
3. 의미가 불분명한 음성인식 오류는 "[STT 불명확]"으로 표기하세요.
4. 모든 출력은 한국어, JSON으로만 응답하세요.

출력 JSON:
{
  "title": "이 구간의 제목 (1줄)",
  "summary": "구간 요약 2~4문단 (문단은 빈 줄로 구분, 집중 기관 관련 내용 중심)",
  "key_points": ["핵심 쟁점·지적사항 (수치 포함)"],
  "answers": ["기관 답변·약속·조치 요지 (답변자 직함 포함)"],
  "requests": ["이 구간에서 요청된 자료 (요청 위원명 포함)"],
  "numbers": ["언급된 주요 수치"],
  "other": "집중 기관 외 다른 기관·기타 내용 (1~2줄, 없으면 빈 문자열)"
}
"""


def _audit_text() -> str:
    """오늘 감사 대상 기관·집중 기관 안내문 (config.AUDIT_*)"""
    import config
    agencies = list(config.AUDIT_AGENCIES)
    focus = config.AUDIT_FOCUS
    if not agencies and not focus:
        return ""
    lines = ["## 오늘 감사 대상 기관"]
    for a in agencies:
        who = [f"{n} {t}".strip() for n, t in config.AUDIT_WITNESSES if a in (t or "")]
        lines.append(f"- {a}" + (f" (답변자: {', '.join(who)})" if who else ""))
    if len(agencies) > 1:
        lines.append("두 기관이 공동으로 감사를 받으므로 같은 시간대에 두 기관 발언이 섞여 나옵니다. 어느 기관에 대한 내용인지 구분하세요.")
    if focus:
        aliases = ", ".join(a for a in config.AUDIT_FOCUS_ALIASES if a != focus) or focus
        lines.append(f"집중 기관: **{focus}** (약칭: {aliases}).")
        lines.append(f"- 요약(summary, key_statements, key_issues, qa_pairs, topic_update)은 {focus} 관련 발언을 중심으로 작성하고, "
                     f"다른 기관만 관련된 내용은 한두 줄로만 다루세요.")
        lines.append(f"- 요청자료(requests)는 발언에 {focus}(또는 약칭)이 명시적으로 언급된 것만 추출하세요. "
                     f"다른 기관만 대상인 요청이나 어느 기관인지 말하지 않은 요청은 넣지 마세요.")
    return "\n".join(lines)


def _with_members(prompt: str) -> str:
    """프롬프트의 {{MEMBERS}} 를 config 위원 명단으로, {{AUDIT}} 를 감사 대상 기관 안내로 채운다"""
    import config
    names = ", ".join(f"{n}({r})" if r != "위원" else n for n, r in config.COMMITTEE_MEMBERS)
    return prompt.replace("{{MEMBERS}}", names).replace("{{AUDIT}}", _audit_text())


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
        raw = await self._call_llm(_with_members(_SYSTEM_PROMPT), prompt)
        if raw is None:
            return None
        return self._parse_json(raw)

    async def detect_requests(self, sentences: list, state: SummaryState) -> Optional[list]:
        """1분 주기: 최근 발언에서 자료 요청만 빠르게 감지 (가벼운 모델)"""
        if not sentences:
            return None
        system = _with_members(_REQUEST_DETECT_PROMPT)
        parts = []
        for s in sentences:
            stamp = time.strftime("%H:%M:%S", time.localtime(s["broadcast_time"]))
            parts.append(f"[ID:{s['id']}] [{stamp}] {s['text']}")
        user = "\n".join(parts)
        raw = await self._call_llm_fast(system, user)
        if raw is None:
            return None
        result = self._parse_json(raw)
        if result and isinstance(result, dict):
            return result.get("requests", [])
        if result and isinstance(result, list):
            return result
        return None

    async def summarize_block(self, sentences: list, label: str, state: SummaryState) -> Optional[dict]:
        """구간(기본 30분) 발언 전체를 보고서용으로 요약한다. 구간이 끝날 때 한 번 호출."""
        if not sentences:
            return None
        system = _with_members(_BLOCK_PROMPT).replace("{{LABEL}}", label)
        parts = [f"[{label} 발언 목록]"]
        for s in sentences:
            stamp = time.strftime("%H:%M:%S", time.localtime(s["broadcast_time"]))
            parts.append(f"[ID:{s['id']}] [{stamp}] {s['text']}")
        parts.append("")
        parts.append("위 발언을 지정된 JSON 형식으로 요약하세요.")
        raw = await self._call_llm(system, "\n".join(parts))
        if raw is None:
            return None
        result = self._parse_json(raw)
        if not isinstance(result, dict) or not result.get("summary"):
            return None
        for key in ("key_points", "answers", "requests", "numbers"):
            v = result.get(key)
            result[key] = [str(x) for x in v if x] if isinstance(v, list) else []
        result["other"] = str(result.get("other") or "")
        return result

    async def summarize_full(self, state: SummaryState) -> Optional[str]:
        """전체 회의 종합 요약 (15분 주기)"""
        if not state.topics and not state.past:
            return None
        prompt = _build_full_prompt(state)
        system = "당신은 대한민국 지방의회 회의록 요약 전문가입니다. 주어진 주제별 요약을 바탕으로 전체 회의를 간결하게 종합하세요."
        return await self._call_llm(system, prompt)

    async def _call_llm_fast(self, system: str, user: str) -> Optional[str]:
        """가벼운 모델로 빠르게 호출 (요청 감지용)"""
        try:
            if self.provider == "anthropic":
                return await self._call_anthropic(system, user, model="claude-haiku-4-5-20251001", max_tokens=1024)
            else:
                return await self._call_openai(system, user, model="gpt-4o-mini", max_tokens=1024)
        except Exception as e:
            print(f"⚠️  요청 감지 오류: {e}", file=sys.stderr)
            return None

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

    async def _call_anthropic(self, system: str, user: str, model: str = None, max_tokens: int = 4096) -> str:
        t0 = time.time()
        try:
            return await self._call_anthropic_raw(system, user, model, max_tokens, t0)
        except Exception as e:
            _ops.llm_call(model or self.model, time.time() - t0, None, error=str(e))
            raise

    async def _call_anthropic_raw(self, system, user, model, max_tokens, t0) -> str:
        resp = await self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model or self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _ops.llm_call(model or self.model, time.time() - t0, data.get("usage"))
        return data["content"][0]["text"]

    async def _call_openai(self, system: str, user: str, model: str = None, max_tokens: int = 4096) -> str:
        t0 = time.time()
        try:
            return await self._call_openai_raw(system, user, model, max_tokens, t0)
        except Exception as e:
            _ops.llm_call(model or self.model, time.time() - t0, None, error=str(e))
            raise

    async def _call_openai_raw(self, system, user, model, max_tokens, t0) -> str:
        resp = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or self.model,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _ops.llm_call(model or self.model, time.time() - t0, data.get("usage"))
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
