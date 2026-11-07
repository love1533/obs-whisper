"""위원별 질의 구간 LLM 분석 + 위원 프로필 집계

- analyze_segment(): 구간 하나를 prompts/member_segment.md로 분석. 출력은 pydantic으로 검증하고 실패하면 1회 재시도.
- build_profile():   한 위원의 여러 회의 analyses를 prompts/member_profile.md로 집계. 빈도는 코드가 센다.
모델 출력에 없는 내용을 코드가 추론해 채우지 않는다. 코드는 검증·정규화(기관명 alias)만 한다.
"""
import collections
import json
import re
import os
import sys
import time
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.roster import Answerers, Normalizer, Roster, get_roster  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(ROOT, "prompts")

INTEREST_TAGS = [
    "예산·재정효율", "성과지표", "조례·법령 이행", "전년 지적 후속", "자료 정합성",
    "계약·수의계약", "수수료·요율", "데이터·개인정보·보안", "정보화·디지털",
    "소상공인 현장", "청년·일자리", "지역현안", "북부 균형발전", "기관 통합·구조개편",
    "인력·노무", "투자유치", "공공플랫폼 운영", "홍보 실효성", "금융·보증",
]
QUESTION_TYPES = ["사실확인", "정합성검증", "이행추적", "법령근거", "효율성", "대안제시", "현장검증", "책임소재", "자료요구"]
TONES = {"온건", "추궁형", "제안형", "혼합"}


# ------------------------------------------------------------ 스키마
class KeyNumber(BaseModel):
    value: str = ""
    context: str = ""
    suspected_stt_error: bool = False


class Topic(BaseModel):
    title: str
    summary: str = ""
    target_agency: str = "불명"
    answerer: str = "불명"
    interest_tags: list[str] = Field(default_factory=list)
    question_types: list[str] = Field(default_factory=list)
    key_numbers: list[KeyNumber] = Field(default_factory=list)
    agency_response: str = ""
    commitments_extracted: list[str] = Field(default_factory=list)
    data_requested: list[str] = Field(default_factory=list)
    carried_over_from: Optional[str] = None
    evidence_ts: list[str] = Field(default_factory=list)

    @field_validator("key_numbers", mode="before")
    @classmethod
    def _numbers(cls, v):
        out = []
        for x in v or []:
            if isinstance(x, dict):
                out.append(x)
            elif isinstance(x, (str, int, float)):
                out.append({"value": str(x)})
        return out

    @field_validator("interest_tags", "question_types", "commitments_extracted", "data_requested", "evidence_ts", mode="before")
    @classmethod
    def _str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v if x is not None and str(x).strip()]

    @field_validator("carried_over_from", mode="before")
    @classmethod
    def _carried(cls, v):
        if v in (None, "", "null", "없음"):
            return None
        return str(v)


class Style(BaseModel):
    tone: str = "혼합"
    signature_patterns: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("tone", mode="before")
    @classmethod
    def _tone(cls, v):
        v = str(v or "혼합").strip()
        return v if v in TONES else "혼합"

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


class SegmentAnalysis(BaseModel):
    member: str
    agencies_questioned: list[str] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    new_tags: list[str] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)


class Profile(BaseModel):
    top_interests: list[str] = Field(default_factory=list)
    question_style: list[str] = Field(default_factory=list)
    recurring_targets: list[str] = Field(default_factory=list)
    open_commitments: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def _lists(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        out = []
        for x in v:
            if isinstance(x, dict):
                out.append(" · ".join(str(val) for val in x.values() if val not in (None, "")))
            elif x is not None:
                out.append(str(x))
        return out


# ------------------------------------------------------------ 프롬프트
def _read_prompt(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
        return f.read()


def build_segment_prompt(seg: dict, meeting: dict, answerers: Answerers, roster: Roster, prev_topics: list) -> str:
    hon = "; ".join(f'"{h}" → {ag}' for h, ag in answerers.honorific_agency.items()) or "(없음)"
    hon += "; 기관명 직접 언급 → 그 기관"
    topic_rules = "; ".join(f"{', '.join(ws[:6])} → {ag}" for ag, ws in answerers.topic_keywords.items()) or "(없음)"
    people = ", ".join(f"{p['name']} {p.get('title', '')}({p.get('agency', '')})".strip() for p in answerers.people) or "(없음)"
    choices = " | ".join(list(answerers.agencies) + ["공통", "불명"])
    return (_read_prompt("member_segment.md")
            .replace("{{DATE}}", meeting.get("date", ""))
            .replace("{{COMMITTEE}}", roster.committee or meeting.get("committee", ""))
            .replace("{{AGENCIES}}", ", ".join(answerers.agencies))
            .replace("{{MEMBER}}", seg.get("speaker", ""))
            .replace("{{PHASE}}", seg.get("phase", ""))
            .replace("{{ANSWERERS}}", people)
            .replace("{{PREV_TOPICS}}", "; ".join(prev_topics) if prev_topics else "(없음)")
            .replace("{{AGENCY_CHOICES}}", choices)
            .replace("{{HONORIFIC_RULES}}", hon)
            .replace("{{TOPIC_RULES}}", topic_rules))


def _normalize_agency(value: str, answerers: Answerers, normalizer: Normalizer) -> str:
    v = (value or "").strip()
    if not v:
        return "불명"
    if v in answerers.agencies or v in ("공통", "불명"):
        return v
    nv = normalizer.normalize(v, context="기관")
    for ag in answerers.agencies:
        if ag in nv or nv in ag:
            return ag
    flat = v.replace(" ", "")
    for ag in answerers.agencies:
        if flat and (flat in ag.replace(" ", "") or ag.replace(" ", "") in flat):
            return ag
    return "불명"


def _canon_tag(tag: str) -> Optional[str]:
    """목록 태그와 표기만 다른 경우("재정 효율"→"예산·재정효율", "규제·법령 이행"→"조례·법령 이행")를 맞춘다. 못 맞추면 None"""
    flat = re.sub(r"[\s·/]", "", tag)
    for t in INTEREST_TAGS:
        tf = re.sub(r"[\s·/]", "", t)
        if flat == tf or (len(flat) >= 2 and (flat in tf or tf in flat)):
            return t
        parts = [p for p in re.split(r"[·/]", t) if len(p) >= 2]
        if any(p in flat for p in parts) and len(flat) <= len(tf) + 2:
            return t
    return None


def normalize_topic_tags(t: dict) -> list:
    """interest_tags 에 섞인 질의 유형은 question_types 로 옮기고, 표기 변형은 목록 태그로 맞춘다. 남는 새 태그를 돌려준다"""
    tags, new_tags = [], []
    qtypes = list(t.get("question_types") or [])
    for x in t.get("interest_tags") or []:
        if x in INTEREST_TAGS:
            tags.append(x)
        elif x in QUESTION_TYPES:
            if x not in qtypes:
                qtypes.append(x)
        else:
            c = _canon_tag(x)
            if c:
                tags.append(c)
            else:
                tags.append(x)
                new_tags.append(x)
    t["interest_tags"] = list(dict.fromkeys(tags))
    t["question_types"] = [x for x in dict.fromkeys(qtypes) if x in QUESTION_TYPES] or qtypes
    return new_tags


def postprocess(analysis: SegmentAnalysis, seg: dict, answerers: Answerers, normalizer: Normalizer) -> dict:
    """검증된 결과를 정규화만 한다 (기관명 alias, 위원명 고정, 명단 밖 답변자 표시)."""
    d = analysis.model_dump()
    d["member"] = seg.get("speaker") or d.get("member")
    d["agencies_questioned"] = sorted({_normalize_agency(a, answerers, normalizer) for a in d.get("agencies_questioned", [])} - {"불명"})
    for t in d.get("topics", []):
        t["target_agency"] = _normalize_agency(t.get("target_agency"), answerers, normalizer)
        ans = t.get("answerer") or "불명"
        known = answerers.resolve(ans)
        if known:
            t["answerer"] = f"{known['name']} {known.get('title', '')}".strip()
            t["answerer_agency"] = known.get("agency")
        else:
            dyn = answerers.detect_dynamic(ans + "입니다") if ans != "불명" else None
            t["answerer_agency"] = dyn.get("agency") if dyn else None
            t["answerer_unlisted"] = ans != "불명"
        unknown_tags = normalize_topic_tags(t)
        if unknown_tags:
            d["new_tags"] = sorted(set(d.get("new_tags") or []) | set(unknown_tags))
    d["new_tags"] = [x for x in (d.get("new_tags") or []) if x not in INTEREST_TAGS and x not in QUESTION_TYPES and not _canon_tag(x)]
    return d


# ------------------------------------------------------------ LLM 호출
async def _call_json(summarizer, system: str, user: str, model_cls, label: str) -> Optional[BaseModel]:
    """LLM 호출 → JSON 파싱 → 스키마 검증. 실패하면 오류를 알려 주며 1회 재시도."""
    last_err = None
    for attempt in range(2):
        prompt = user if attempt == 0 else (
            user + f"\n\n[재시도] 직전 응답이 스키마를 통과하지 못했습니다: {last_err}\n지정된 JSON 형식만 출력하세요.")
        raw = await summarizer._call_llm(system, prompt)
        if raw is None:
            last_err = "응답 없음"
            continue
        data = summarizer._parse_json(raw)
        if not isinstance(data, dict):
            last_err = "JSON 아님"
            continue
        try:
            return model_cls.model_validate(data)
        except ValidationError as e:
            last_err = str(e)[:400]
    print(f"⚠️  {label}: 스키마 검증 실패 ({last_err})", file=sys.stderr)
    return None


async def analyze_segment(summarizer, seg: dict, meeting: dict, answerers: Answerers, roster: Roster,
                          normalizer: Normalizer, prev_topics: Optional[list] = None) -> Optional[dict]:
    system = build_segment_prompt(seg, meeting, answerers, roster, prev_topics or [])
    user = f"[{seg.get('speaker')} 위원 질의 구간 {seg.get('start_ts')}~{seg.get('end_ts')}]\n{seg.get('raw_text', '')}"
    t0 = time.time()
    result = await _call_json(summarizer, system, user, SegmentAnalysis, f"{seg.get('speaker')} {seg.get('start_ts')}")
    if result is None:
        return None
    out = postprocess(result, seg, answerers, normalizer)
    out["model"] = summarizer.model
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------ 프로필 집계
def tag_table(analyses: list) -> dict:
    """코드가 세는 빈도: interest_tags, question_types, target_agency, tone"""
    tags, types, targets, tones = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    tag_dates = collections.defaultdict(set)
    for a in analyses:
        date = a.get("meeting_date", "")
        an = a.get("analysis") or {}
        tones[an.get("style", {}).get("tone", "혼합")] += 1
        for t in an.get("topics", []):
            for x in t.get("interest_tags", []):
                tags[x] += 1
                tag_dates[x].add(date)
            for x in t.get("question_types", []):
                types[x] += 1
            if t.get("target_agency") and t["target_agency"] != "불명":
                targets[t["target_agency"]] += 1
    return {
        "interest_tags": [{"tag": k, "count": n, "dates": sorted(tag_dates[k]), "once": n == 1} for k, n in tags.most_common()],
        "question_types": [{"type": k, "count": n, "once": n == 1} for k, n in types.most_common()],
        "targets": [{"agency": k, "count": n} for k, n in targets.most_common()],
        "tones": dict(tones),
        "segment_count": len(analyses),
        "topic_count": sum(len((a.get("analysis") or {}).get("topics", [])) for a in analyses),
        "meetings": sorted({a.get("meeting_date", "") for a in analyses}),
    }


def _compact(analyses: list) -> list:
    out = []
    for a in analyses:
        an = a.get("analysis") or {}
        out.append({
            "date": a.get("meeting_date"), "phase": a.get("phase"), "start": a.get("start_ts"),
            "topics": [{k: t.get(k) for k in ("title", "summary", "target_agency", "answerer", "interest_tags",
                                                "question_types", "commitments_extracted", "data_requested",
                                                "carried_over_from", "evidence_ts")} for t in an.get("topics", [])],
            "style": an.get("style"),
        })
    return out


async def build_profile(summarizer, member: str, analyses: list) -> Optional[dict]:
    table = tag_table(analyses)
    lines = ["interest_tags: " + ", ".join(f"{x['tag']} {x['count']}회{'(단발)' if x['once'] else ''}" for x in table["interest_tags"]),
             "question_types: " + ", ".join(f"{x['type']} {x['count']}회{'(단발)' if x['once'] else ''}" for x in table["question_types"]),
             "target_agency: " + ", ".join(f"{x['agency']} {x['count']}회" for x in table["targets"]),
             "tone: " + ", ".join(f"{k} {n}회" for k, n in table["tones"].items())]
    system = _read_prompt("member_profile.md").replace("{{MEMBER}}", member).replace("{{TAG_TABLE}}", "\n".join(lines))
    user = json.dumps({"member": member, "analyses": _compact(analyses)}, ensure_ascii=False)
    result = await _call_json(summarizer, system, user, Profile, f"{member} 프로필")
    profile = result.model_dump() if result else {"top_interests": [], "question_style": [], "recurring_targets": [], "open_commitments": []}
    profile.update(table)
    profile["member"] = member
    profile["updated_at"] = time.time()
    profile["model"] = summarizer.model if result else None
    return profile
