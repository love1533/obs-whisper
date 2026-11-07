"""명단 로더 - config/roster.json (위원, 상임위 고정), config/answerers/<날짜>.json (회의별 답변자),
config/normalize.json (기관명 STT 정규화)

원칙: 새 인물을 임의로 만들지 않는다. 명단에 없으면 UNKNOWN("확인필요")으로 남긴다.
"""
import json
import os
import re
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
UNKNOWN = "확인필요"


def _flat(s: str) -> str:
    return (s or "").replace(" ", "")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ 위원
class Roster:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(CONFIG_DIR, "roster.json")
        data = _load_json(self.path)
        self.committee = data.get("committee", "")
        self.members = [m for m in data.get("members", []) if m.get("name")]
        self.names = [m["name"] for m in self.members]
        self.by_name = {m["name"]: m for m in self.members}
        # 공백 제거 alias → 정식 이름. "위원장"처럼 사람 이름이 아닌 alias는 지명 매칭에서 뺀다
        self.alias_to_name = {}
        self.nomination_alias_to_name = {}
        for m in self.members:
            for a in [m["name"]] + list(m.get("aliases") or []):
                fa = _flat(a)
                if not fa:
                    continue
                self.alias_to_name[fa] = m["name"]
                if fa not in ("위원장", "본위원장"):
                    self.nomination_alias_to_name[fa] = m["name"]
        self.chair = next((m["name"] for m in self.members if m.get("role") == "위원장"), None)

    def alias_regex(self, for_nomination: bool = True) -> Optional[str]:
        table = self.nomination_alias_to_name if for_nomination else self.alias_to_name
        if not table:
            return None
        return "(" + "|".join(re.escape(a) for a in sorted(table, key=len, reverse=True)) + ")"

    def resolve(self, raw: Optional[str], min_alias_len: int = 3) -> str:
        """AI가 적었거나 발언에 나온 이름 → 명단 이름. 없으면 UNKNOWN."""
        if not raw or not isinstance(raw, str):
            return UNKNOWN
        s = raw.strip()
        for name in sorted(self.names, key=len, reverse=True):
            if name in s:
                return name
        flat = _flat(s)
        for alias in sorted(self.alias_to_name, key=len, reverse=True):
            if len(alias) >= min_alias_len and alias in flat:
                return self.alias_to_name[alias]
        return UNKNOWN

    def lookup_flat(self, flat_alias: str) -> Optional[str]:
        return self.nomination_alias_to_name.get(flat_alias) or self.alias_to_name.get(flat_alias)


# ------------------------------------------------------------ 답변자 (회의별)
class Answerers:
    def __init__(self, date: str, path: Optional[str] = None):
        self.date = date
        self.path = path or os.path.join(CONFIG_DIR, "answerers", f"{date}.json")
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"답변자 파일이 없습니다: {self.path} (config/answerers/<회의일자>.json)")
        data = _load_json(self.path)
        self.title = data.get("title", "")
        self.agencies = list(data.get("agencies") or [])
        self.people = [p for p in data.get("answerers", []) if p.get("name")]
        self.by_name = {p["name"]: p for p in self.people}
        self.honorific_agency = dict(data.get("honorific_agency") or {})
        dyn = data.get("dynamic_titles") or {}
        self.dynamic_patterns = list(dyn.get("patterns") or [])
        self.dynamic_fixed_agency = dict(dyn.get("fixed_agency") or {})
        self.topic_keywords = {k: list(v) for k, v in (data.get("topic_keywords") or {}).items()}
        self.alias_to_name = {}
        for p in self.people:
            for a in [p["name"]] + list(p.get("aliases") or []):
                fa = _flat(a)
                if fa:
                    self.alias_to_name[fa] = p["name"]
        all_titles = set(self.dynamic_patterns) | set(self.dynamic_fixed_agency)
        titles = "|".join(re.escape(t) for t in sorted(all_titles, key=len, reverse=True)) or "본부장"
        # "○○본부장 ○○○입니다" / "○○○ ○○본부장입니다"
        self._dyn_title_first = re.compile(r"([가-힣A-Za-z0-9·]{0,12}(?:" + titles + r"))\s*([가-힣]{2,4})\s*입니다")
        self._dyn_name_first = re.compile(r"(?:^|[\s.,])([가-힣]{2,4})\s+([가-힣A-Za-z0-9·]{0,12}(?:" + titles + r"))\s*입니다")

    def resolve(self, raw: Optional[str]) -> Optional[dict]:
        """이름/별칭 → 답변자 dict. 명단에 없으면 None."""
        if not raw:
            return None
        flat = _flat(raw)
        for alias in sorted(self.alias_to_name, key=len, reverse=True):
            if len(alias) >= 2 and alias in flat:
                return self.by_name[self.alias_to_name[alias]]
        return None

    def detect_dynamic(self, text: str) -> Optional[dict]:
        """'○○본부장 ○○○입니다' 자기소개를 동적으로 인식. 소속 기관은 직함 규칙으로만 정하고 모르면 None."""
        m = self._dyn_title_first.search(text) or None
        if m:
            title, name = m.group(1).strip(), m.group(2)
        else:
            m2 = self._dyn_name_first.search(text)
            if not m2:
                return None
            name, title = m2.group(1), m2.group(2).strip()
        known = self.resolve(name)
        if known:
            return known
        # 직함에 기관 고유 단어가 있으면 그 기관("보증기획팀장"→신보), 아니면 직함 규칙("팀장"→경제실), 모르면 None
        agency = self.agency_from_topic(title)
        if not agency:
            for key, ag in self.dynamic_fixed_agency.items():
                if title.endswith(key):
                    agency = ag
                    break
        return {"name": name, "title": title, "agency": agency, "dynamic": True}

    def agency_from_honorific(self, text: str) -> Optional[str]:
        for hon, ag in sorted(self.honorific_agency.items(), key=lambda kv: -len(kv[0])):
            if hon in text:
                return ag
        return None

    def agency_from_topic(self, text: str) -> Optional[str]:
        best, best_n = None, 0
        for ag, words in self.topic_keywords.items():
            n = sum(text.count(w) for w in words)
            if n > best_n:
                best, best_n = ag, n
        return best


# ------------------------------------------------------------ 기관명 정규화
class Normalizer:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(CONFIG_DIR, "normalize.json")
        data = _load_json(self.path) if os.path.exists(self.path) else {}
        self.context_words = list(data.get("context_words") or [])
        self.rules = []
        for r in data.get("rules", []):
            ctx = set(r.get("context") or [])
            for src in r.get("from", []):
                self.rules.append((src, r["to"], src in ctx))
        # 긴 표기 먼저 치환
        self.rules.sort(key=lambda t: -len(t[0]))

    def normalize(self, text: str, context: str = "") -> str:
        """text 안의 STT 오기를 정규화한다. context 치환은 주변(context)에 기관 관련 단어가 있을 때만."""
        if not text:
            return text
        around = (context or "") + " " + text
        has_ctx = any(w in around for w in self.context_words)
        out = text
        for src, dst, needs_ctx in self.rules:
            if src == dst or src not in out:
                continue
            if needs_ctx and not has_ctx:
                continue
            out = out.replace(src, dst)
        return out


_roster: Optional[Roster] = None


def get_roster() -> Roster:
    global _roster
    if _roster is None:
        _roster = Roster()
    return _roster


def list_answerer_dates() -> list:
    d = os.path.join(CONFIG_DIR, "answerers")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
