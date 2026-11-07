"""회의록 구간 분할 - 규칙 우선 (LLM은 UNKNOWN 보조에만)

입력: transcripts/<날짜>.jsonl (문장 id·시각 포함, 권장) 또는 회의록 txt
      ("[HH:MM:SS] 발언" / "[HH시 MM분 SS초] 발언", 줄 앞의 [화자] 태그는 힌트로만 쓴다)
출력: 위원별 질의 구간 목록. 각 구간은 speaker(명단 이름 | 위원장 | UNKNOWN), phase, type, 줄 목록을 가진다.

규칙 (config/roster.json 명단만 사용, 새 인물은 만들지 않는다)
- 구간 시작: 위원장 지명 "OOO 의원님 질의하시기 바랍니다" / "이어서 우리 OOO 의원님" / "질의하실 의원님 … OOO" / "다음은 OOO"
- 위원 확정: 구간 첫 3줄의 자기소개 "OO 출신 OOO입니다", "OOO 의원입니다" (지명과 다르면 자기소개를 따른다)
- 구간 종료: 다음 지명, "OOO 의원님 수고하셨습니다", 정회·중지·속개
- 위원장 구간: "수고하셨습니다" 뒤에 위원장이 덧붙이는 실질 발언(150자 이상)은 speaker=위원장, phase=위원장 보충
  짧은 진행 발언(지명·시간 안내·정회)은 type=진행
- 단계: 자료요구(개회 뒤 "자료 요구하실 의원님"부터) → 본질의("본질의" 안내부터) → 추가질의("추가 질의를 하도록" 뒤)
- 매칭 실패: speaker=UNKNOWN, candidates에 들린 이름을 남겨 리포트에서 "명단 추가 후보"로 보여 준다

사용: python -m lib.segment --date 2026-11-06 [--input 파일] [--expect "박준모,한영수,..."] [--json 출력파일]
"""
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.roster import Roster, UNKNOWN, get_roster  # noqa: E402

CHAIR_LABEL = "위원장"

_LINE_RE = re.compile(r"^\[(\d{1,2})[:시]\s?(\d{2})[:분]\s?(\d{2})초?\]\s*(.*)$")
_TAG_RE = re.compile(r"^\[([^\]]{2,24})\]\s*(?:\(계속\)\s*)?")
_HON = r"(?:의원님|위원님|부위원장님|부원장님|호원님|원님|의원|위원|부위원장)?"
_ASK = r"(?:질의하시기|질의해주시|질의하시죠|질의가있겠|발언해주시|질의하십시오|질의하시면|질의하도록|질의를하시)"
_FILLER = r"(?:이어서|먼저|또|도|먼|이|님)*"
_CLOSE = re.compile(r"수고하셨습니다|수고하셨고|잘하셨습니다|좋은질의해주셨습니다")
_PHASE_MAIN = re.compile(r"본질의")
_PHASE_ADD = re.compile(r"추가질의를하도록|추가질의하도록하겠|추가질의를통해")
_PHASE_DATA = re.compile(r"자료요구하실|자료요구하실의원|자료유")
_RECESS = re.compile(r"정회|속개|산회|중지를선포")
_NOT_NAME = {"우리", "저희", "다음", "이어", "질의", "존경", "그러", "그리", "이제", "지금", "오늘", "먼저", "자료", "추가"}
_NOT_NAME_END = re.compile(r"(?:하실|하신|하시|의원|위원|하는|있는|니다)$")


def _plausible_name(tok: str) -> bool:
    return bool(tok) and tok not in _NOT_NAME and not _NOT_NAME_END.search(tok)


@dataclass
class Line:
    ts: str                      # HH:MM:SS
    text: str
    epoch: Optional[float] = None
    id: Optional[int] = None
    hint: Optional[str] = None   # 원문의 [화자] 태그 (틀릴 수 있어 힌트로만)
    offset: int = 0              # 이 줄의 몇 번째 글자부터 이 구간 발언인지 (한 줄에 두 구간이 섞인 경우)


@dataclass
class Segment:
    idx: int
    speaker: str                 # 명단 이름 | 위원장 | UNKNOWN
    phase: str                   # 자료요구 | 본질의 | 추가질의 | 위원장 보충 | 개회 | 진행
    type: str                    # 질의 | 자료요구 | 진행 | 업무보고 | 정회
    start_ts: str
    end_ts: str
    by: str                      # 규칙 (지명 / 지명+자기소개 / 마무리 / 시작)
    lines: list = field(default_factory=list)
    candidates: list = field(default_factory=list)   # UNKNOWN일 때 들린 이름
    data_requests: list = field(default_factory=list)
    intro_fix: Optional[str] = None                   # 지명과 자기소개가 다를 때 "지명→자기소개"

    @property
    def start_id(self):
        return self.lines[0].id if self.lines else None

    @property
    def end_id(self):
        return self.lines[-1].id if self.lines else None

    @property
    def raw_text(self) -> str:
        return "\n".join(f"[{l.ts}] {l.text[l.offset:].strip()}" for l in self.lines if l.text[l.offset:].strip())

    @property
    def chars(self) -> int:
        return sum(len(l.text[l.offset:].replace(" ", "")) for l in self.lines)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_id"] = self.start_id
        d["end_id"] = self.end_id
        d["raw_text"] = self.raw_text
        d["chars"] = self.chars
        d["line_count"] = len(self.lines)
        d["sentence_ids"] = [l.id for l in self.lines if l.id is not None]
        return d


# ------------------------------------------------------------ 입력 파싱
def parse_txt(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = _LINE_RE.match(ln.rstrip("\n"))
            if not m:
                continue
            ts = f"{int(m[1]):02d}:{m[2]}:{m[3]}"
            text = m[4]
            hint = None
            t = _TAG_RE.match(text)
            if t:
                hint, text = t[1], text[t.end():]
            rows.append(Line(ts=ts, text=text, hint=hint))
    return rows


def parse_jsonl(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            bt = r.get("broadcast_time") or r.get("recognized_at")
            ts = time.strftime("%H:%M:%S", time.localtime(bt)) if bt else "00:00:00"
            sp = r.get("speaker") or {}
            hint = f"{sp.get('name', '')} {sp.get('title', '')}".strip() if sp else None
            rows.append(Line(ts=ts, text=r.get("text", ""), epoch=bt, id=r.get("id"), hint=hint))
    return rows


def load_lines(path: str) -> list:
    return parse_jsonl(path) if path.endswith(".jsonl") else parse_txt(path)


# ------------------------------------------------------------ 규칙
class Segmenter:
    def __init__(self, roster: Optional[Roster] = None, chair_min_chars: int = 150):
        self.roster = roster or get_roster()
        self.chair = self.roster.chair or CHAIR_LABEL
        self.chair_min_chars = chair_min_chars
        alias = self.roster.alias_regex(for_nomination=True)
        self.nominate = [
            re.compile(alias + _HON + _FILLER + _ASK),
            re.compile(r"이어서(?:또)?(?:우리)?" + alias + _HON),
            re.compile(r"질의하실(?:우리)?(?:의원님)?(?:우리)?\S{0,4}?" + alias + _HON),
            re.compile(r"(?:다음은|다음|또)(?:우리)?" + alias + _HON),
            re.compile(r"수고하셨습니다\.?(?:또)?(?:자료)?(?:요구하실)?(?:의원님)?(?:우리)?" + alias + _HON),
            # "추가 질의하실 의원님 질의해 주시기 바랍니다. 우리 OOO 의원님"
            re.compile(r"질의하실의원님(?:질의해주시기바랍니다|질의하시기바랍니다)?\.?(?:우리)?" + alias + r"(?:의원님|위원님|부위원장님)"),
            # 호명만 있는 짧은 줄: "우리 OOO 의원님"
            re.compile(r"^(?:네|예|자)?(?:그러면|그럼)?(?:우리)?" + alias + r"(?:의원님|위원님|부위원장님)\.?$"),
        ] if alias else []
        # 명단에 없는 이름이 지명되는 경우 → UNKNOWN 후보
        self.nominate_any = re.compile(r"([가-힣]{2,4})(?:의원님|위원님|부위원장님)" + _FILLER + _ASK)
        self.intro = re.compile(
            r"(?:출신|비례(?:대표)?(?:의원)?(?:의)?|비례의)" + alias + r"|" + alias + r"(?:의원|위원|부위원장)?입니다"
        ) if alias else None
        self.intro_any = re.compile(r"(?:출신|비례(?:대표)?(?:의원)?(?:의)?)([가-힣]{2,4})(?:의원)?입니다")

    # ---- 한 줄 안에서 지명이 시작되는 원문 위치 (flat 인덱스 → 원문 인덱스)
    @staticmethod
    def _flat_map(text: str):
        flat, idx = [], []
        for i, ch in enumerate(text):
            if ch != " ":
                flat.append(ch)
                idx.append(i)
        return "".join(flat), idx

    def _find_nomination(self, flat: str):
        """(위원 이름 또는 UNKNOWN, 후보 이름, flat 위치) / None"""
        for rx in self.nominate:
            m = rx.search(flat)
            if m:
                return self.roster.lookup_flat(m.group(1)), None, m.start()
        m = self.nominate_any.search(flat)
        if m and _plausible_name(m.group(1)):
            return UNKNOWN, m.group(1), m.start()
        return None

    def _find_intro(self, flat: str):
        if self.intro:
            m = self.intro.search(flat)
            if m:
                return self.roster.lookup_flat(m.group(1) or m.group(2)), None
        m = self.intro_any.search(flat)
        if m and _plausible_name(m.group(1)):
            return None, m.group(1)
        return None

    # ---- 본체
    def segment(self, lines: list) -> list:
        segs: list = []
        cur: Optional[Segment] = None
        phase = "개회"
        n_since_open = 0

        def close():
            nonlocal cur
            if cur and cur.lines:
                segs.append(cur)
            cur = None

        def open_seg(speaker, line: Line, kind, ph, by, candidates=None):
            nonlocal cur, n_since_open
            close()
            cur = Segment(idx=len(segs), speaker=speaker, phase=ph, type=kind, start_ts=line.ts, end_ts=line.ts, by=by,
                          candidates=list(candidates or []))
            n_since_open = 0

        for raw in lines:
            flat, idx = self._flat_map(raw.text)
            if phase == "개회" and _PHASE_DATA.search(flat):
                phase = "자료요구"
            if phase in ("개회", "자료요구") and _PHASE_MAIN.search(flat):
                phase = "본질의"
            if phase == "본질의" and _PHASE_ADD.search(flat):
                phase = "추가질의"

            nom = self._find_nomination(flat)
            cm = _CLOSE.search(flat)
            recess = _RECESS.search(flat)

            # 질의 중인 위원을 다시 부르는 말("박준모 의원님" 뒤에 "수고하셨습니다")은 지명이 아니다
            if nom and cur is not None and cur.type in ("질의", "자료요구") and nom[0] == cur.speaker:
                nom = None
            if nom:
                who, cand, pos = nom
                split_at = idx[pos] if pos < len(idx) else 0
                # 지명 앞부분(직전 위원 마무리)은 이전 구간에 남긴다
                if cur is not None and split_at > 0:
                    head = Line(ts=raw.ts, text=raw.text[:split_at], epoch=raw.epoch, id=raw.id, hint=raw.hint)
                    cur.lines.append(head)
                    cur.end_ts = raw.ts
                kind = "자료요구" if phase == "자료요구" else "질의"
                open_seg(who or UNKNOWN, raw, kind, phase, "지명", [cand] if cand else [])
                line = Line(ts=raw.ts, text=raw.text, epoch=raw.epoch, id=raw.id, hint=raw.hint, offset=split_at)
                cur.lines.append(line)
                cur.end_ts = raw.ts
                n_since_open = 1
                continue

            if cm or recess:
                # 마무리/정회 줄은 현재 구간에 붙이고, 다음 줄부터 위원장 구간
                if cur is None:
                    open_seg(self.chair, raw, "진행", phase, "시작")
                cur.lines.append(raw)
                cur.end_ts = raw.ts
                open_seg(self.chair, raw, "정회" if recess else "진행", phase, "마무리")
                cur.lines = []  # 다음 줄부터
                continue

            if cur is None:
                open_seg(self.chair, raw, "진행", phase, "시작")
            if not cur.lines:
                cur.start_ts = raw.ts
            # 자기소개로 확정/정정 (구간 첫 3줄). 위원장 구간(마무리 뒤)에서 위원 자기소개가 나오면
            # 지명 없이 시작한 위원 구간(자료요구 순서 등)이므로 그 위원 구간으로 바꾼다
            if cur.speaker == self.chair and cur.type in ("진행", "정회") and n_since_open < 3 and len(cur.lines) == 0:
                found = self._find_intro(flat)
                if found and found[0] and found[0] != self.chair:
                    cur.speaker = found[0]
                    cur.type = "자료요구" if phase == "자료요구" else "질의"
                    cur.by = "자기소개"
            if cur.type in ("질의", "자료요구") and n_since_open < 3:
                found = self._find_intro(flat)
                if found:
                    who, cand = found
                    if who and who != cur.speaker:
                        cur.intro_fix = f"{cur.speaker}→{who}"
                        cur.speaker = who
                        cur.candidates = []
                    elif who is None and cand:
                        cur.candidates.append(cand)
                    if who:
                        cur.by = "지명+자기소개" if cur.by == "지명" else cur.by
            cur.lines.append(raw)
            cur.end_ts = raw.ts
            n_since_open += 1
        close()

        # 위원장 구간 정리: 실질 발언이 길면 보충 질의, 아니면 진행. 개회 전 긴 발언은 업무보고
        for s in segs:
            if s.speaker == self.chair and s.type in ("진행", "정회"):
                if s.phase == "개회":
                    s.type = "업무보고" if s.chars >= self.chair_min_chars else "진행"
                elif s.type == "진행" and s.chars >= self.chair_min_chars:
                    s.type = "질의"
                    s.phase = "위원장 보충"
            if s.type == "자료요구":
                s.data_requests = [l.text[l.offset:].strip() for l in s.lines if l.text[l.offset:].strip()]
        for i, s in enumerate(segs):
            s.idx = i
        return segs


# ------------------------------------------------------------ 리포트
def report(segs: list, roster: Roster, expect: Optional[list] = None) -> str:
    out = []
    q = [s for s in segs if s.type in ("질의", "자료요구")]
    out.append(f"구간 {len(segs)}개, 질의 구간 {len(q)}개")
    main = [s for s in q if s.phase == "본질의"]
    out.append("\n[본질의 순서]\n" + " → ".join(f"{s.speaker}({s.start_ts[:5]})" for s in main))
    add = [s for s in q if s.phase == "추가질의"]
    out.append("\n[추가질의 순서]\n" + " → ".join(f"{s.speaker}({s.start_ts[:5]})" for s in add))
    out.append("\n[위원별 구간 수·시작 시각]")
    by = {}
    for s in q:
        by.setdefault(s.speaker, []).append(s)
    missing = []
    for name in roster.names + [UNKNOWN]:
        ss = by.get(name, [])
        if name != UNKNOWN and not ss:
            missing.append(name)
        out.append(f"  {name:5s} {len(ss):2d}  " + ", ".join(f"{s.start_ts[:5]}[{s.phase[:2]}]" for s in ss))
    if missing:
        out.append(f"\n⚠️  구간이 없는 위원: {', '.join(missing)}")
    unknown = [s for s in segs if s.speaker == UNKNOWN]
    out.append(f"\n[UNKNOWN 구간] {len(unknown)}건")
    for s in unknown:
        first = s.lines[0].text[s.lines[0].offset:][:60] if s.lines else ""
        out.append(f"  {s.start_ts} 첫 문장: {first} / 명단 추가 후보: {', '.join(s.candidates) or '-'}")
    fixes = [s for s in q if s.intro_fix]
    if fixes:
        out.append("\n[자기소개로 정정]\n" + "\n".join(f"  {s.start_ts} {s.intro_fix}" for s in fixes))
    if expect:
        got = [s.speaker for s in main]
        ok = got == expect
        out.append(f"\n[기대값 대조] {'일치 ✅' if ok else '불일치 ❌'}")
        if not ok:
            out.append("  기대: " + " → ".join(expect))
            out.append("  실제: " + " → ".join(got))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="회의록 구간 분할")
    ap.add_argument("--date", required=True, help="회의 날짜 YYYY-MM-DD")
    ap.add_argument("--input", help="입력 파일 (기본 transcripts/<날짜>.jsonl, 없으면 .txt)")
    ap.add_argument("--expect", help="본질의 기대 순서 (쉼표 구분, 명단 이름 또는 alias)")
    ap.add_argument("--json", help="구간 JSON 출력 파일")
    ap.add_argument("--all", action="store_true", help="진행·업무보고 구간까지 모두 출력")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = args.input
    if not path:
        for ext in (".jsonl", ".txt"):
            cand = os.path.join(root, "transcripts", args.date + ext)
            if os.path.exists(cand):
                path = cand
                break
    if not path or not os.path.exists(path):
        sys.exit(f"입력 파일이 없습니다: {path or args.date}")

    roster = get_roster()
    lines = load_lines(path)
    segs = Segmenter(roster).segment(lines)
    expect = [roster.resolve(x) for x in args.expect.split(",")] if args.expect else None
    print(f"입력 {path}: {len(lines)}줄")
    print(report(segs, roster, expect))
    if args.all:
        print("\n[전체 구간]")
        for s in segs:
            print(f"  {s.start_ts}~{s.end_ts} {s.type:4s} {s.phase:6s} {s.speaker:4s} {s.by} ({len(s.lines)}줄 {s.chars}자)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in segs], f, ensure_ascii=False, indent=1)
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
