"""발언자 태깅 - 발언 안의 명시적 자기소개로만 발언자를 붙인다

"장윤정 위원입니다" / "경제실장 OOO입니다" / "OOO 기획조정실장입니다" 처럼
이름이 또렷하게 말해진 문장에만 태그를 단다. 추측(이전 발언자 이어가기 등)은
하지 않는다 - 정확히 분류될 때만 표시해 달라는 요구.

- 위원: config.COMMITTEE_MEMBERS 명단에 있는 이름 + 위원/부위원장/위원장 + 입니다
- 기관 관계자: 성씨로 시작하는 2~4자 이름 + 직함(…장/…이사/…관) + 입니다 (양쪽 어순)
한 문장에 서로 다른 이름이 둘 이상 나오면(간부 소개 등) 발언자로 보지 않는다.

요청자료/지적사항의 raised_by 정규화(normalize_member)도 여기서 한다:
명단에 있는 위원이면 이름, 아니면 "확인필요".
"""
import html
import re
from typing import Optional

import config

MEMBERS = list(config.COMMITTEE_MEMBERS)            # [(이름, 직책)]
MEMBER_NAMES = [n for n, _ in MEMBERS]
MEMBER_ROLE = {n: r for n, r in MEMBERS}

UNKNOWN = "확인필요"

# 흔한 성씨(2~4자 토큰이 이름인지 가르는 1차 필터)
_SURNAMES = set("시피감빈태갈궁호상김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구나민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부")
# 이름처럼 생겼지만 이름이 아닌 흔한 단어
_NOT_NAMES = {
    "현황", "예산", "보고", "소개", "자료", "계획", "사항", "분야", "내용", "다음", "이상", "현재",
    "여기", "지원", "사업", "이쪽", "저희", "우리", "제가", "오늘", "조직", "정원", "규모", "순서",
    "위원", "의원", "질의", "답변", "질문", "경기도", "경제실", "기금", "실적", "성과", "추진",
    "운영", "정책", "금융", "조정", "기획", "본부", "대상", "기준", "결과", "마지막", "처음",
    "이번", "이제", "이것", "그것", "저것", "여러", "모든", "각각", "본인", "당시", "지금",
}
# 직함 어미(두 글자 이상이어야 "김정관" 같은 이름과 구분된다)
_TITLE_END = (r"(?:실장|국장|과장|본부장|단장|팀장|이사장|원장|청장|처장|담당관|사무관|주무관|부장|차장|"
              r"센터장|소장|대표|전무|상무|이사|위원장|의장|시장|지사|관장|총장|회장|사장|기관장|"
              r"비서관|수석|교수|파트장|지점장|지사장|부원장|부이사장)")
# 직함: 한글/영문/숫자 어절 최대 5개가 _TITLE_END 로 끝남 (예: 경기도 경제과학진흥원장, 기업 성장부문 상임이사)
_TITLE = r"(?:[가-힣A-Za-z0-9·]+\s*){0,5}?[가-힣A-Za-z0-9·]*" + _TITLE_END
_NAME = r"([가-힣]{2,4})"

_MEMBER_RE = None
_OFFICIAL_NAME_FIRST = re.compile(r"(?:^|[\s.,])" + _NAME + r"\s+(" + _TITLE + r")\s*입니다")
_OFFICIAL_TITLE_FIRST = re.compile(r"(?:^|[\s.,])(" + _TITLE + r")\s+" + _NAME + r"\s*입니다")


def _member_re():
    global _MEMBER_RE
    if _MEMBER_RE is None and MEMBER_NAMES:
        names = "|".join(re.escape(n) for n in sorted(MEMBER_NAMES, key=len, reverse=True))
        _MEMBER_RE = re.compile(r"(" + names + r")\s*(부위원장|위원장|위원|의원)\s*입니다")
    return _MEMBER_RE


def _looks_like_name(tok: str) -> bool:
    # "상대의" 처럼 조사로 끝나는 토큰은 이름이 아니다
    return (2 <= len(tok) <= 4 and tok[0] in _SURNAMES and tok not in _NOT_NAMES
            and tok[-1] not in "의은는을를도")


_JUNK_INSIDE = re.compile(r"는데|은데|니다|니까|겠|그래서|때문")
_PARTICLE_TOKEN = re.compile(r"(?:^|\s)\S+(?:은|는|을|를|의|이|가|에서|에게|도|께서|부터|까지|니까|니다|세요)(?=\s|$)")
_LEAD_JUNK = re.compile(r"^(?:안녕하십니까|안녕하세요|다음은|다음|그리고|이어서|네|예|저는|제가|저희|우리|그럼|자)\s+")


def _clean_title(title: str) -> Optional[str]:
    """직함 구절 정리. 조사가 붙은 어절이 섞여 있으면 직함이 아니므로 None."""
    title = title.strip()
    while True:
        t = _LEAD_JUNK.sub("", title)
        if t == title:
            break
        title = t
    if _PARTICLE_TOKEN.search(" " + title):
        return None
    if _JUNK_INSIDE.search(title):
        return None
    # 직함은 길어야 어절 3개(예: "기업 성장부문 상임이사"). 더 길면 앞쪽은 발언 내용이므로 버린다
    toks = title.split()
    title = " ".join(toks[-3:])
    return title or None


def detect_speaker(text: str) -> Optional[dict]:
    """문장에서 명시적 자기소개를 찾는다. 없으면 None.

    반환: {"name": 이름, "kind": "member"|"official", "title": 직책/직함}
    """
    if not text:
        return None
    found = []  # (위치, 정보)
    mre = _member_re()
    if mre:
        for m in mre.finditer(text):
            name = m.group(1)
            found.append((m.start(), {"name": name, "kind": "member",
                                      "title": MEMBER_ROLE.get(name) or m.group(2)}))
    for m in _OFFICIAL_NAME_FIRST.finditer(text):
        name, title = m.group(1), _clean_title(m.group(2))
        if title and _looks_like_name(name) and name not in MEMBER_NAMES:
            found.append((m.start(), {"name": name, "kind": "official", "title": title}))
    for m in _OFFICIAL_TITLE_FIRST.finditer(text):
        title, name = _clean_title(m.group(1)), m.group(2)
        if title and _looks_like_name(name) and name not in MEMBER_NAMES:
            found.append((m.start(), {"name": name, "kind": "official", "title": title}))
    if not found:
        return None
    names = {f[1]["name"] for f in found}
    if len(names) > 1:
        return None  # 여러 사람을 소개하는 문장(간부 소개 등) → 발언자 아님
    found.sort(key=lambda f: f[0])
    return found[0][1]


def speaker_html(sp: dict) -> str:
    """문장 앞에 붙일 발언자 배지 HTML"""
    name = html.escape(sp["name"])
    title = html.escape(sp.get("title") or "")
    cont = " cont" if sp.get("cont") else ""  # 길이 제한으로 끊긴 뒷조각 (같은 발언 계속)
    if sp["kind"] == "member":
        return (f'<span class="spk spk-member{cont}" data-name="{name}" title="{name} {title}">'
                f'<span class="spk-avatar">{name[0]}</span>{name} {title}</span> ')
    return f'<span class="spk spk-official{cont}" title="{title}">({name})</span> '


def speaker_label(sp: Optional[dict]) -> str:
    """회의록 txt 용 접두어"""
    if not sp:
        return ""
    cont = "(계속)" if sp.get("cont") else ""
    if sp["kind"] == "member":
        return f"[{sp['name']} {sp.get('title') or '위원'}{cont}] "
    return f"[{sp['name']} {sp.get('title') or ''}{cont}] ".replace(" ]", "]")


def normalize_member(raw) -> str:
    """AI가 적어 낸 위원명을 명단 기준으로 정규화. 명단에 없으면 '확인필요'."""
    if not raw or not isinstance(raw, str):
        return UNKNOWN
    s = raw.strip()
    for name in sorted(MEMBER_NAMES, key=len, reverse=True):
        if name in s:
            return name
    return UNKNOWN


def normalize_items(items, field: str = "raised_by") -> list:
    """요청/지적 목록의 raised_by를 정규화한다. 원래 값은 <field>_raw 에 남긴다."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        raw = it.get(field)
        norm = normalize_member(raw)
        if norm == UNKNOWN and raw and raw.strip() and raw.strip() != UNKNOWN:
            it[field + "_raw"] = raw.strip()
        it[field] = norm
        out.append(it)
    return out
