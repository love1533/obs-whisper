"""발언자 태깅 - 발언 안의 명시적 자기소개로만 발언자를 붙인다

"장윤정 위원입니다" / "경제실장 OOO입니다" / "OOO 기획조정실장입니다" 처럼
이름이 또렷하게 말해진 문장에만 태그를 단다. 추측(이전 발언자 이어가기 등)은
하지 않는다 - 정확히 분류될 때만 표시해 달라는 요구.

- 위원: config.COMMITTEE_MEMBERS 명단에 있는 이름 + 위원/부위원장/위원장 + 입니다
- 기관 관계자: 성씨로 시작하는 2~4자 이름 + 직함(…장/…이사/…관) + 입니다 (양쪽 어순)
- 기관 답변자(config.AUDIT_WITNESSES, 예: 시석중 이사장): 이름 + 입니다 만으로도 인정
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
# STT 오기·호칭 변형 → 명단 이름. raised_by 정규화와 위원장 지명 감지에 쓴다 (명단에 없는 이름의 alias는 무시)
_MEMBER_ALIASES = {
    "장태환": ["장태원"],
    "장윤정": ["정윤정", "장윤정 부원장", "부회장님"],
    "최민": ["최민호원", "최민호 원", "최민부위원장", "최민 부원장"],
    "박준모": ["박종호", "박주호", "박준호"],
    "한영수": ["안영수"],
    "송연섭": ["송현섭", "송효섭", "정현섭"],
    "신명순": ["신명수", "신병순"],
    "유수연": ["유수현", "유승현"],
    "최혜경": ["최경호원", "조희경", "최 교수님", "최경"],
    "최보라": ["최구라", "최불라", "최고라", "최호라"],
    "최상규": ["최상기"],
    "송규근": ["송기근", "송규훈", "성기근", "정 교수님"],
    "이기대": ["이기 대원", "이기대원"],
    "정용한": ["정용환", "정용원"],
}
# 공백 제거 alias → 명단 이름 (긴 것 우선 매칭). config/roster.json이 있으면 그 alias를 우선 쓴다
try:
    from lib.roster import get_roster as _get_roster
    _ROSTER = _get_roster()
    for _m in _ROSTER.members:
        if _m["name"] in MEMBER_NAMES:
            _MEMBER_ALIASES[_m["name"]] = list(_m.get("aliases") or [])
except Exception as _e:  # roster.json이 없거나 깨져도 앱은 돌아야 한다
    print(f"⚠️  config/roster.json 로드 실패, 내장 alias 사용: {_e}", file=__import__("sys").stderr)
ALIAS_TO_NAME = {}
for _n in MEMBER_NAMES:
    for _a in [_n] + _MEMBER_ALIASES.get(_n, []):
        _fa = _a.replace(" ", "")
        if _fa and _fa not in ("위원장", "본위원장"):
            ALIAS_TO_NAME[_fa] = _n
_ALIAS_RE = "(" + "|".join(re.escape(a) for a in sorted(ALIAS_TO_NAME, key=len, reverse=True)) + ")" if ALIAS_TO_NAME else None
_HON = r"(?:의원님|위원님|부위원장님|부원장님|호원님|원님|의원|위원|부위원장)?"
_ASK = r"(?:질의하시기|질의해주시|질의하시죠|질의가있겠|발언해주시|질의하십시오|질의하시면|질의하도록|질의를하시)"
# 위원장 지명: "OOO 의원님 질의하시기 바랍니다" / "이어서 우리 OOO 의원님" / "질의하실 의원님 OOO" / "다음은 OOO 부위원장님"
_NOMINATE_RES = [
    re.compile(_ALIAS_RE + _HON + r"(?:이어서|먼저|또|도|먼|이|님)*" + _ASK),
    re.compile(r"이어서(?:또)?(?:우리)?" + _ALIAS_RE + _HON),
    re.compile(r"질의하실(?:우리)?(?:의원님)?(?:우리)?\S{0,4}?" + _ALIAS_RE + _HON),
    re.compile(r"(?:다음은|다음|또)(?:우리)?" + _ALIAS_RE + _HON),
    # 자료요구 순서: "OOO 의원님 수고하셨습니다. 또 자료 요구하실 의원님 OOO 의원님"
    re.compile(r"수고하셨습니다\.?(?:또)?(?:자료)?(?:요구하실)?(?:의원님)?(?:우리)?" + _ALIAS_RE + _HON),
] if _ALIAS_RE else []
# 기관 답변자(config.AUDIT_WITNESSES): 이름만 말해도("시석중입니다") 발언자로 인정하고 직함은 설정값을 쓴다
WITNESSES = {n: t for n, t in config.AUDIT_WITNESSES if n}

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
_WITNESS_RE = re.compile(
    r"(?:^|[\s.,])(" + "|".join(re.escape(n) for n in sorted(WITNESSES, key=len, reverse=True)) + r")\s*(?:" + _TITLE + r"\s*)?입니다"
) if WITNESSES else None
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
    if _WITNESS_RE:
        for m in _WITNESS_RE.finditer(text):
            name = m.group(1)
            found.append((m.start(), {"name": name, "kind": "official", "title": WITNESSES[name]}))
    if not found:
        return None
    names = {f[1]["name"] for f in found}
    if len(names) > 1:
        return None  # 여러 사람을 소개하는 문장(간부 소개 등) → 발언자 아님
    found.sort(key=lambda f: f[0])
    sp = found[0][1]
    if sp["kind"] == "official" and sp["name"] in WITNESSES:
        sp["title"] = WITNESSES[sp["name"]]  # 설정된 정식 직함으로 통일
    return sp


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
    flat = s.replace(" ", "")
    for alias in sorted(ALIAS_TO_NAME, key=len, reverse=True):
        if len(alias) >= 3 and alias in flat:
            return ALIAS_TO_NAME[alias]
    return UNKNOWN


def detect_nomination(text: str) -> Optional[str]:
    """위원장이 질의할 위원을 지명한 문장이면 그 위원 이름(명단 기준). 아니면 None.
    공백을 모두 지우고 비교해 STT 띄어쓰기 오류("이기 대원님")에 흔들리지 않게 한다."""
    if not text:
        return None
    flat = text.replace(" ", "")
    seg = _segmenter()
    if seg is not None:
        found = seg._find_nomination(flat)
        if found and found[0] and found[0] != UNKNOWN:
            return found[0]
        return None
    for rx in _NOMINATE_RES:
        m = rx.search(flat)
        if m:
            return ALIAS_TO_NAME.get(m.group(1))
    return None


_SEGMENTER = None
_SEGMENTER_TRIED = False


def _segmenter():
    """lib/segment.py의 지명 규칙(회의록 구간 분할과 같은 규칙)을 공유한다. 없으면 내장 규칙으로 폴백"""
    global _SEGMENTER, _SEGMENTER_TRIED
    if not _SEGMENTER_TRIED:
        _SEGMENTER_TRIED = True
        try:
            from lib.segment import Segmenter
            _SEGMENTER = Segmenter(_ROSTER)
        except Exception as e:
            print(f"⚠️  lib.segment 로드 실패, 내장 지명 규칙 사용: {e}", file=__import__("sys").stderr)
    return _SEGMENTER


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
