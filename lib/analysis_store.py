"""위원 성향 분석 결과 저장소 - 파일(transcripts/analysis), 뷰어 데이터(web/data/profiles.json), Supabase

analyze_meeting.py(회의 후 일괄)와 lib/live_profile.py(회의 중 30분 주기)가 같은 파일·형식을 쓴다.
  <날짜>.segments.json : 구간 분할 결과
  <날짜>.analyses.json : 구간별 LLM 분석 (speaker@start_ts 로 식별, 같은 구간은 다시 분석하지 않는다)
  profiles.json        : {"all": {위원: 프로필}, "by_meeting": {날짜: {위원: 프로필}}}
"""
import json
import os
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYZABLE_TYPES = ("질의", "자료요구")


class AnalysisStore:
    def __init__(self, analysis_dir: Optional[str] = None, web_export: Optional[str] = None):
        self.dir = analysis_dir or os.path.join(ROOT, "transcripts", "analysis")
        self.web_export = web_export or os.path.join(ROOT, "web", "data", "profiles.json")

    # ---- 파일
    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def load(self, name: str, default):
        p = self.path(name)
        if not os.path.exists(p):
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def save(self, name: str, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path(name))

    @staticmethod
    def seg_key(s: dict) -> str:
        return f"{s.get('speaker')}@{s.get('start_ts')}"

    def analyses(self, date: str) -> list:
        return self._normalized(self.load(f"{date}.analyses.json", []))

    def all_analyses(self, exclude_date: Optional[str] = None) -> list:
        out = []
        if not os.path.isdir(self.dir):
            return out
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".analyses.json") and fn[:10] != exclude_date:
                out.extend(self.load(fn, []))
        return self._normalized(out)

    @staticmethod
    def _normalized(items: list) -> list:
        from lib.member_analysis import normalize_topic_tags, INTEREST_TAGS, QUESTION_TYPES
        for a in items:
            an = a.get("analysis") or {}
            new_tags = []
            for t in an.get("topics", []):
                new_tags += normalize_topic_tags(t)
            an["new_tags"] = sorted(set(x for x in new_tags if x not in INTEREST_TAGS and x not in QUESTION_TYPES))
        return items

    def dates(self) -> list:
        if not os.path.isdir(self.dir):
            return []
        return sorted(fn[:10] for fn in os.listdir(self.dir) if fn.endswith(".segments.json"))

    def load_profiles(self) -> dict:
        p = self.load("profiles.json", {})
        if "all" not in p and "by_meeting" not in p:   # 예전 형식({위원: 프로필})
            p = {"all": p, "by_meeting": {}}
        p.setdefault("all", {})
        p.setdefault("by_meeting", {})
        return p

    def save_profiles(self, profiles: dict):
        self.save("profiles.json", profiles)

    # ---- 뷰어 데이터
    def export_web(self, profiles: dict, out_path: Optional[str] = None, live_date: Optional[str] = None,
                   live_updated_at: Optional[float] = None, live_next_at: Optional[float] = None) -> str:
        from lib.roster import Answerers, get_roster
        roster = get_roster()
        meetings = []
        for d in self.dates():
            try:
                a = Answerers(d)
                meetings.append({"date": d, "title": a.title, "agencies": a.agencies,
                                 "answerers": [{"name": p["name"], "title": p.get("title", ""), "agency": p.get("agency", "")} for p in a.people]})
            except FileNotFoundError:
                meetings.append({"date": d, "title": "", "agencies": [], "answerers": []})
        segments = [{k: a.get(k) for k in ("meeting_date", "speaker", "phase", "type", "start_ts", "end_ts", "start_id", "sentence_ids", "analysis")}
                    for a in self.all_analyses()]
        unknown = []
        for d in [m["date"] for m in meetings]:
            for s in self.load(f"{d}.segments.json", []):
                if s.get("speaker") == "확인필요":
                    unknown.append({"meeting_date": d, "start_ts": s["start_ts"], "candidates": s.get("candidates", []),
                                    "first_line": (s.get("raw_text") or "")[:80]})
        data = {
            "generated_at": time.time(),
            "committee": roster.committee,
            "meetings": meetings,
            "members": [{k: m.get(k, "") for k in ("name", "role", "district", "party")} for m in roster.members],
            "segments": segments,
            "profiles": profiles.get("all", {}),
            "profiles_by_meeting": profiles.get("by_meeting", {}),
            "unknown_segments": unknown,
            "live": {"date": live_date, "updated_at": live_updated_at, "next_at": live_next_at} if live_date else None,
        }
        out_path = out_path or self.web_export
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, out_path)
        return out_path

    # ---- Supabase (supabase/migrations/20261107_member_profiles.sql)
    def save_supabase(self, dates: list, profiles: dict, url: str, service_key: str, quiet: bool = False) -> bool:
        import httpx
        from lib.roster import Answerers, get_roster
        if not url or not service_key:
            return False
        roster = get_roster()
        headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
                   "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=representation"}
        c = httpx.Client(base_url=url.rstrip("/") + "/rest/v1", headers=headers, timeout=30.0)

        def upsert(table: str, rows: list, on_conflict: str):
            if not rows:
                return []
            r = c.post(f"/{table}?on_conflict={on_conflict}", json=rows)
            if r.status_code >= 400:
                raise RuntimeError(f"{table}: {r.status_code} {r.text[:200]}")
            return r.json()

        try:
            members = upsert("members", [{"name": m["name"], "role": m.get("role"), "district": m.get("district"),
                                          "party": m.get("party"), "aliases": m.get("aliases", [])} for m in roster.members], "name")
            member_id = {m["name"]: m["id"] for m in members}
            for d in dates:
                try:
                    a = Answerers(d)
                    agencies, title, people = a.agencies, a.title, a.people
                except FileNotFoundError:
                    agencies, title, people = [], "", []
                upsert("meetings", [{"id": d, "date": d, "committee": roster.committee, "agencies": agencies, "title": title}], "id")
                upsert("answerers", [{"meeting_id": d, "name": p["name"], "title": p.get("title"), "agency": p.get("agency"),
                                      "aliases": p.get("aliases", [])} for p in people], "meeting_id,name")
                segs = [s for s in self.load(f"{d}.segments.json", []) if s.get("type") in ANALYZABLE_TYPES]
                rows = upsert("segments", [{
                    "meeting_id": d, "member_id": member_id.get(s["speaker"]), "speaker_raw": s["speaker"], "phase": s["phase"],
                    "type": s["type"], "start_ts": s["start_ts"], "end_ts": s["end_ts"], "start_id": s.get("start_id"),
                    "sentence_ids": s.get("sentence_ids", []), "raw_text": s.get("raw_text", ""), "candidates": s.get("candidates", []),
                } for s in segs], "meeting_id,start_ts,speaker_raw")
                seg_id = {(r["start_ts"], r["speaker_raw"]): r["id"] for r in rows}
                an_rows = []
                for a_ in self.load(f"{d}.analyses.json", []):
                    sid = seg_id.get((a_["start_ts"], a_["speaker"]))
                    if sid:
                        an_rows.append({"segment_id": sid, "json": a_["analysis"], "model": a_.get("model") or ""})
                upsert("analyses", an_rows, "segment_id,model")
                if not quiet:
                    print(f"  Supabase {d}: 구간 {len(rows)} 분석 {len(an_rows)}", file=sys.stderr)
            prow = []
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            for n, p in profiles.get("all", {}).items():
                if n in member_id:
                    prow.append({"member_id": member_id[n], "meeting_id": "all", "profile_json": p, "updated_at": now})
            for d, byname in profiles.get("by_meeting", {}).items():
                for n, p in byname.items():
                    if n in member_id:
                        prow.append({"member_id": member_id[n], "meeting_id": d, "profile_json": p, "updated_at": now})
            upsert("member_profiles", prow, "member_id,meeting_id")
            if not quiet:
                print(f"  Supabase 프로필 {len(prow)}건 저장", file=sys.stderr)
            return True
        except Exception as e:
            print(f"⚠️  Supabase 위원 성향 저장 실패: {e}\n   supabase/migrations/20261107_member_profiles.sql 을 SQL Editor에서 먼저 실행했는지 확인하세요.", file=sys.stderr)
            return False
        finally:
            c.close()
