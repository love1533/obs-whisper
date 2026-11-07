// 위원 성향 탭 - analyze_meeting.py 결과를 보여 준다
//   데이터: data/profiles.json (analyze_meeting.py --export) 를 기본으로 읽고,
//           Supabase에 member_profiles/segments/analyses 테이블이 있으면 그 값으로 바꾼다
//   보기: 위원별(카드 → 상세) / 기관별(선택한 기관에 대한 질의만 위원별로)
//   필터: 회의(날짜), 피감기관. 타임스탬프 클릭 → 실시간 탭의 해당 문장으로 이동(같은 날 회의록이 로드된 경우)
(() => {
  const $ = (id) => document.getElementById(id);
  const cfg = window.VIEWER_CONFIG || {};
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let DATA = null;            // {meetings, members, segments, profiles, unknown_segments, generated_at}
  let view = "member";        // member | agency
  let meetingFilter = "";     // "" = 전체
  let agencyFilter = "";      // "" = 전체 (기관별 보기에서는 선택 기관)
  let selected = null;        // 상세 보기 중인 위원
  let loaded = false;

  // ------------------------------------------------------------ 데이터 로드
  async function loadStatic() {
    try {
      const r = await fetch("data/profiles.json?v=" + Math.floor(Date.now() / 60000));
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  async function sbGet(path) {
    const r = await fetch(`${cfg.supabaseUrl.replace(/\/$/, "")}/rest/v1/${path}`, {
      headers: { apikey: cfg.supabaseAnonKey, Authorization: `Bearer ${cfg.supabaseAnonKey}` },
    });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  async function loadSupabase() {
    if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) return null;
    const [meetings, members, profiles, segments] = await Promise.all([
      sbGet("meetings?select=id,date,title,agencies,answerers(name,title,agency)&order=date"),
      sbGet("members?select=id,name,role,district,party"),
      sbGet("member_profiles?select=member_id,profile_json,updated_at,members(name)"),
      sbGet("segments?select=meeting_id,speaker_raw,phase,type,start_ts,end_ts,start_id,sentence_ids,candidates,analyses(json,model,created_at)&order=start_ts"),
    ]);
    if (!segments.length && !profiles.length) return null;
    const prof = {};
    for (const p of profiles) {
      const name = p.members && p.members.name;
      if (name) prof[name] = p.profile_json;
    }
    return {
      generated_at: Math.max(0, ...profiles.map((p) => Date.parse(p.updated_at) / 1000 || 0)),
      meetings: meetings.map((m) => ({ date: m.id, title: m.title, agencies: m.agencies || [], answerers: m.answerers || [] })),
      members: members.map((m) => ({ name: m.name, role: m.role, district: m.district, party: m.party })),
      segments: segments
        .filter((s) => s.analyses && s.analyses.length)
        .map((s) => ({
          meeting_date: s.meeting_id, speaker: s.speaker_raw, phase: s.phase, type: s.type,
          start_ts: s.start_ts, end_ts: s.end_ts, start_id: s.start_id, sentence_ids: s.sentence_ids || [],
          analysis: s.analyses[s.analyses.length - 1].json,
        })),
      profiles: prof,
      unknown_segments: segments.filter((s) => s.speaker_raw === "확인필요").map((s) => ({
        meeting_date: s.meeting_id, start_ts: s.start_ts, candidates: s.candidates || [], first_line: "",
      })),
      source: "supabase",
    };
  }

  async function load() {
    if (loaded) return;
    loaded = true;
    DATA = await loadStatic();
    try {
      const sb = await loadSupabase();
      if (sb && (!DATA || (sb.generated_at || 0) >= (DATA.generated_at || 0))) DATA = sb;
    } catch (e) {
      // 테이블이 아직 없으면 정적 데이터만 쓴다
    }
    render();
  }

  // ------------------------------------------------------------ 집계 (필터 적용 후 클라이언트에서 다시 센다)
  function filteredSegments() {
    if (!DATA) return [];
    return DATA.segments.filter((s) => !meetingFilter || s.meeting_date === meetingFilter);
  }

  function topicsOf(seg) {
    const topics = (seg.analysis && seg.analysis.topics) || [];
    return topics.filter((t) => !agencyFilter || t.target_agency === agencyFilter || t.target_agency === "공통");
  }

  function countBy(segs, pick) {
    const c = {};
    for (const s of segs) for (const t of topicsOf(s)) for (const x of pick(t) || []) c[x] = (c[x] || 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }

  function memberStats(name) {
    const segs = filteredSegments().filter((s) => s.speaker === name);
    const withTopics = segs.filter((s) => topicsOf(s).length);
    const tones = {};
    for (const s of withTopics) {
      const t = (s.analysis.style && s.analysis.style.tone) || "혼합";
      tones[t] = (tones[t] || 0) + 1;
    }
    return {
      segs: withTopics,
      topicCount: withTopics.reduce((n, s) => n + topicsOf(s).length, 0),
      tags: countBy(withTopics, (t) => t.interest_tags),
      types: countBy(withTopics, (t) => t.question_types),
      targets: countBy(withTopics, (t) => (t.target_agency && t.target_agency !== "불명" ? [t.target_agency] : [])),
      tone: Object.entries(tones).sort((a, b) => b[1] - a[1])[0]?.[0] || "",
      meetings: [...new Set(withTopics.map((s) => s.meeting_date))].sort(),
    };
  }

  function allAgencies() {
    const set = new Set();
    for (const m of DATA.meetings) if (!meetingFilter || m.date === meetingFilter) for (const a of m.agencies || []) set.add(a);
    return [...set];
  }

  // ------------------------------------------------------------ 렌더링
  function render() {
    const root = $("profileRoot");
    if (!root) return;
    if (!DATA || !DATA.segments || !DATA.segments.length) {
      root.innerHTML = "";
      $("profileEmpty").style.display = "";
      return;
    }
    $("profileEmpty").style.display = "none";
    const btn = document.querySelector('.tab[data-tab="profile"]');
    if (btn) btn.classList.add("has-data");

    const agencies = allAgencies();
    if (agencyFilter && !agencies.includes(agencyFilter)) agencyFilter = "";
    if (view === "agency" && !agencyFilter) agencyFilter = agencies[0] || "";

    let html = `<div class="profile-toolbar">
      <div class="profile-toggle">
        <button class="pf-toggle${view === "member" ? " active" : ""}" data-view="member">위원별</button>
        <button class="pf-toggle${view === "agency" ? " active" : ""}" data-view="agency">기관별</button>
      </div>
      <select id="pfMeeting" class="pf-select" title="회의">
        <option value="">전체 회의 (${DATA.meetings.length})</option>
        ${DATA.meetings.map((m) => `<option value="${esc(m.date)}"${m.date === meetingFilter ? " selected" : ""}>${esc(m.date)} ${esc(m.title || "")}</option>`).join("")}
      </select>
      <select id="pfAgency" class="pf-select" title="피감기관">
        ${view === "member" ? `<option value="">전체 기관</option>` : ""}
        ${agencies.map((a) => `<option value="${esc(a)}"${a === agencyFilter ? " selected" : ""}>${esc(a)}</option>`).join("")}
      </select>
    </div>`;

    if (view === "member") {
      html += selected ? renderDetail(selected) : renderCards();
    } else {
      html += renderByAgency();
    }
    html += renderUnknown();
    html += `<div class="pf-foot">분석 기준: ${DATA.generated_at ? new Date(DATA.generated_at * 1000).toLocaleString("ko-KR") : "-"} · 원문 근거가 있는 발언만 정리했으며 예상 질의는 만들지 않습니다.</div>`;
    root.innerHTML = html;

    root.querySelectorAll(".pf-toggle").forEach((b) => {
      b.onclick = () => { view = b.dataset.view; selected = null; if (view === "member") agencyFilter = ""; render(); };
    });
    $("pfMeeting").onchange = (e) => { meetingFilter = e.target.value; render(); };
    $("pfAgency").onchange = (e) => { agencyFilter = e.target.value; render(); };
    root.querySelectorAll("[data-member]").forEach((el) => {
      el.onclick = (e) => {
        if (e.target.closest(".summary-ref") || e.target.closest(".pf-topic")) return;
        selected = el.dataset.member; render();
        const scroll = root.closest(".tab-panel-scroll"); if (scroll) scroll.scrollTop = 0;
      };
    });
    const back = $("pfBack");
    if (back) back.onclick = () => { selected = null; render(); };
  }

  function avatar(name) {
    const info = (window.MEMBER_INFO || {})[name] || {};
    return info.photo
      ? `<img class="member-avatar" src="${info.photo}" alt="${esc(name)}">`
      : `<span class="member-avatar member-initial">${esc(name.charAt(0))}</span>`;
  }

  function chips(list, cls, max) {
    return list.slice(0, max).map(([k, n]) => `<span class="pf-chip ${cls}">${esc(k)}<b>${n}</b></span>`).join("");
  }

  function renderCards() {
    const members = DATA.members.map((m) => ({ ...m, st: memberStats(m.name) }));
    members.sort((a, b) => b.st.topicCount - a.st.topicCount || a.name.localeCompare(b.name));
    return `<div class="pf-cards">` + members.map((m) => {
      const st = m.st;
      const empty = !st.segs.length;
      return `<div class="pf-card${empty ? " pf-card-empty" : ""}" data-member="${esc(m.name)}">
        <div class="pf-card-head">
          ${avatar(m.name)}
          <div class="pf-card-name"><b>${esc(m.name)}</b> <span class="member-role">${esc(m.role === "위원" ? "" : m.role || "")}</span>
            <div class="member-sub">${esc(m.district || "")}</div></div>
          <div class="pf-card-count">${empty ? "-" : `질의 ${st.segs.length}회 · 주제 ${st.topicCount}`}</div>
        </div>
        ${empty ? `<div class="pf-card-none">이 조건의 질의 구간이 없습니다</div>` : `
        <div class="pf-card-tags">${chips(st.tags, "tag", 3)}</div>
        <div class="pf-card-types">${chips(st.types, "type", 3)}${st.tone ? `<span class="pf-chip tone">${esc(st.tone)}</span>` : ""}</div>`}
      </div>`;
    }).join("") + `</div>`;
  }

  function bars(list, cls) {
    if (!list.length) return `<div class="pf-none">없음</div>`;
    const max = list[0][1];
    return `<div class="pf-bars">` + list.map(([k, n]) => `<div class="pf-bar-row">
      <span class="pf-bar-label">${esc(k)}</span>
      <span class="pf-bar-track"><span class="pf-bar ${cls}" style="width:${Math.max(6, Math.round((n / max) * 100))}%"></span></span>
      <span class="pf-bar-n">${n}${n === 1 ? " <small>단발</small>" : ""}</span>
    </div>`).join("") + `</div>`;
  }

  function tsRef(seg, ts) {
    const label = (ts || seg.start_ts || "").replace(/[\[\]]/g, "");
    const id = seg.start_id;
    const sameDay = !!(window._appBridge && window._appBridge.items && window._appBridge.items.some((it) => it.rec.id === id));
    return sameDay && id
      ? `<span class="summary-ref pf-ts" data-ids="${id}" title="실시간 탭에서 원문 보기">${esc(seg.meeting_date)} ${esc(label)}</span>`
      : `<span class="pf-ts">${esc(seg.meeting_date)} ${esc(label)}</span>`;
  }

  function renderTopic(seg, t) {
    const nums = (t.key_numbers || []).filter((k) => k.value);
    return `<div class="pf-topic">
      <div class="pf-topic-head">
        ${tsRef(seg, (t.evidence_ts || [])[0])}
        <span class="pf-phase">${esc(seg.phase)}</span>
        ${t.target_agency && t.target_agency !== "불명" ? `<span class="pf-agency">${esc(t.target_agency)}</span>` : ""}
      </div>
      <div class="pf-topic-title">${esc(t.title)}</div>
      <div class="pf-topic-summary">${esc(t.summary)}</div>
      ${t.answerer && t.answerer !== "불명" ? `<div class="pf-kv"><b>답변</b> ${esc(t.answerer)}${t.agency_response ? ` · ${esc(t.agency_response)}` : ""}</div>` : (t.agency_response ? `<div class="pf-kv"><b>답변</b> ${esc(t.agency_response)}</div>` : "")}
      ${nums.length ? `<div class="pf-kv"><b>수치</b> ${nums.map((k) => `${esc(k.value)}${k.context ? ` (${esc(k.context)})` : ""}${k.suspected_stt_error ? ' <span class="pf-warn">STT 의심</span>' : ""}`).join(", ")}</div>` : ""}
      ${(t.commitments_extracted || []).length ? `<div class="pf-kv"><b>약속</b> ${t.commitments_extracted.map(esc).join(" / ")}</div>` : ""}
      ${(t.data_requested || []).length ? `<div class="pf-kv"><b>자료</b> ${t.data_requested.map(esc).join(" / ")}</div>` : ""}
      ${t.carried_over_from ? `<div class="pf-kv"><b>이전 회의 연결</b> ${esc(t.carried_over_from)}</div>` : ""}
      <div class="pf-topic-tags">${(t.interest_tags || []).map((x) => `<span class="pf-chip tag">${esc(x)}</span>`).join("")}${(t.question_types || []).map((x) => `<span class="pf-chip type">${esc(x)}</span>`).join("")}</div>
    </div>`;
  }

  function renderDetail(name) {
    const m = DATA.members.find((x) => x.name === name) || { name };
    const st = memberStats(name);
    const p = (DATA.profiles || {})[name];
    const showProfile = p && !meetingFilter && !agencyFilter;
    const segs = [...st.segs].sort((a, b) => (a.meeting_date + a.start_ts).localeCompare(b.meeting_date + b.start_ts));
    return `<button id="pfBack" class="pf-back" type="button">← 위원 목록</button>
    <div class="pf-detail-head">
      ${avatar(name)}
      <div><div class="pf-detail-name">${esc(name)} <span class="member-role">${esc(m.role || "")}</span></div>
      <div class="member-sub">${esc(m.district || "")}${m.party ? ` · ${esc(m.party)}` : ""} · 질의 ${st.segs.length}회 · 주제 ${st.topicCount} · 회의 ${st.meetings.join(", ") || "-"}${st.tone ? ` · 어조 ${esc(st.tone)}` : ""}</div></div>
    </div>
    <div class="pf-grid">
      <section class="pf-section"><h4>관심 태그 Top</h4>${bars(st.tags.slice(0, 8), "tag")}</section>
      <section class="pf-section"><h4>질의 유형 분포</h4>${bars(st.types, "type")}</section>
    </div>
    ${st.targets.length ? `<section class="pf-section"><h4>질의 대상 기관</h4><div class="pf-card-tags">${chips(st.targets, "agency", 6)}</div></section>` : ""}
    ${showProfile ? `<section class="pf-section"><h4>질의 방식 (전체 회의 기준)</h4>
      ${listOrNone(p.question_style)}
      ${(p.recurring_targets || []).length ? `<h5>반복 대상</h5>${listOrNone(p.recurring_targets)}` : ""}
      ${(p.open_commitments || []).length ? `<h5>기관 약속 (후속 확인 전)</h5>${listOrNone(p.open_commitments)}` : ""}
    </section>` : (p ? `<div class="pf-note">회의·기관 필터를 해제하면 전체 회의 기준 질의 방식 요약이 표시됩니다.</div>` : "")}
    <section class="pf-section"><h4>대표 질의 (${segs.reduce((n, s) => n + topicsOf(s).length, 0)})</h4>
      ${segs.map((s) => topicsOf(s).map((t) => renderTopic(s, t)).join("")).join("") || `<div class="pf-none">없음</div>`}
    </section>`;
  }

  function listOrNone(list) {
    return (list && list.length) ? `<ul class="pf-list">${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="pf-none">없음</div>`;
  }

  function renderByAgency() {
    if (!agencyFilter) return `<div class="pf-none">피감기관 정보가 없습니다</div>`;
    const rows = DATA.members.map((m) => {
      const segs = filteredSegments().filter((s) => s.speaker === m.name && topicsOf(s).length);
      return { m, segs, n: segs.reduce((k, s) => k + topicsOf(s).length, 0) };
    }).filter((r) => r.n).sort((a, b) => b.n - a.n);
    if (!rows.length) return `<div class="pf-none">${esc(agencyFilter)}에 대한 질의가 없습니다</div>`;
    return `<div class="pf-agency-title">${esc(agencyFilter)}에 대한 질의 · 위원 ${rows.length}명 · 주제 ${rows.reduce((n, r) => n + r.n, 0)}</div>` +
      rows.map((r) => `<details class="pf-agency-member" open>
        <summary>${avatar(r.m.name)} <b>${esc(r.m.name)}</b> <span class="member-sub">${esc(r.m.district || "")}</span> <span class="pf-card-count">주제 ${r.n}</span></summary>
        ${r.segs.map((s) => topicsOf(s).map((t) => renderTopic(s, t)).join("")).join("")}
      </details>`).join("");
  }

  function renderUnknown() {
    const u = (DATA.unknown_segments || []).filter((x) => !meetingFilter || x.meeting_date === meetingFilter);
    if (!u.length) return "";
    return `<section class="pf-section pf-unknown"><h4>위원 미확정 구간 · 명단 추가 후보</h4>
      ${u.map((x) => `<div class="pf-kv">${esc(x.meeting_date)} ${esc(x.start_ts)} · 후보: ${esc((x.candidates || []).join(", ") || "-")}${x.first_line ? ` · ${esc(x.first_line)}` : ""}</div>`).join("")}
    </section>`;
  }

  // ------------------------------------------------------------ 탭 진입 시 로드
  const tabBtn = document.querySelector('.tab[data-tab="profile"]');
  if (tabBtn) tabBtn.addEventListener("click", load);
  // 데이터가 있으면 탭 뱃지만 미리 켠다
  loadStatic().then((d) => {
    if (d && d.segments && d.segments.length) {
      DATA = d;
      if (tabBtn) tabBtn.classList.add("has-data");
      if (tabBtn && tabBtn.classList.contains("active")) load();
    }
  });
})();
