// 위원 성향 탭 - 위원별 질의 구간 분석 결과를 보여 준다
//   데이터: Supabase member_profiles / segments / analyses (앱이 회의 중 30분마다, 회의 후 analyze_meeting.py 가 저장)
//           Supabase 에 테이블이 없거나 비어 있으면 data/profiles.json (정적 내보내기 / 로컬 앱이 갱신) 을 쓴다
//   기본 보기: 가장 최근 회의(회의 중이면 오늘)만. "전체 회의"를 고르면 누적
//   보기: 위원별(카드 → 상세) / 기관별(선택한 기관에 대한 질의만 위원별로)
//   타임스탬프 클릭 → 실시간 탭의 해당 문장으로 이동(같은 날 회의록이 로드된 경우)
(() => {
  const $ = (id) => document.getElementById(id);
  const cfg = window.VIEWER_CONFIG || {};
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const POLL_MS = 60 * 1000;

  let DATA = null;            // {meetings, members, segments, profiles, profiles_by_meeting, unknown_segments, generated_at, live}
  let view = "member";        // member | agency
  let meetingFilter = null;   // null = 아직 안 정함 (최근 회의로), "" = 전체
  let agencyFilter = "";
  let selected = null;        // 상세 보기 중인 위원
  let loading = false;
  let pollTimer = null;

  // ------------------------------------------------------------ 데이터 로드
  async function loadStatic() {
    try {
      const r = await fetch("data/profiles.json?v=" + Date.now(), { cache: "no-store" });
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
      sbGet("member_profiles?select=member_id,meeting_id,profile_json,updated_at,members(name)"),
      sbGet("segments?select=meeting_id,speaker_raw,phase,type,start_ts,end_ts,start_id,sentence_ids,candidates,analyses(json,model,created_at)&order=start_ts"),
    ]);
    if (!segments.length && !profiles.length) return null;
    const all = {}, byMeeting = {};
    let latest = 0;
    for (const p of profiles) {
      const name = p.members && p.members.name;
      if (!name) continue;
      latest = Math.max(latest, Date.parse(p.updated_at) / 1000 || 0);
      if (!p.meeting_id || p.meeting_id === "all") all[name] = p.profile_json;
      else (byMeeting[p.meeting_id] = byMeeting[p.meeting_id] || {})[name] = p.profile_json;
    }
    const segs = segments.filter((s) => s.analyses && s.analyses.length).map((s) => ({
      meeting_date: s.meeting_id, speaker: s.speaker_raw, phase: s.phase, type: s.type,
      start_ts: s.start_ts, end_ts: s.end_ts, start_id: s.start_id, sentence_ids: s.sentence_ids || [],
      analysis: s.analyses[s.analyses.length - 1].json,
    }));
    const dates = meetings.map((m) => m.id).sort();
    const today = localDate(new Date());
    return {
      generated_at: latest,
      meetings: meetings.map((m) => ({ date: m.id, title: m.title, agencies: m.agencies || [], answerers: m.answerers || [] })),
      members: members.map((m) => ({ name: m.name, role: m.role, district: m.district, party: m.party })),
      segments: segs,
      profiles: all,
      profiles_by_meeting: byMeeting,
      unknown_segments: segments.filter((s) => s.speaker_raw === "확인필요").map((s) => ({
        meeting_date: s.meeting_id, start_ts: s.start_ts, candidates: s.candidates || [], first_line: "",
      })),
      live: dates.includes(today) ? { date: today, updated_at: latest } : null,
      source: "supabase",
    };
  }

  function localDate(d) {
    const z = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      let data = null;
      try { data = await loadSupabase(); } catch (e) { /* 테이블이 없으면 정적 데이터 */ }
      const st = await loadStatic();
      if (!data || (st && (st.generated_at || 0) > (data.generated_at || 0) + 5)) data = st || data;
      if (data) DATA = data;
    } finally {
      loading = false;
    }
    render();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      const active = document.querySelector('.tab[data-tab="profile"]');
      if (active && active.classList.contains("active") && !document.hidden) load();
    }, POLL_MS);
  }

  // ------------------------------------------------------------ 집계 (필터 적용 후 클라이언트에서 다시 센다)
  // 기본은 항상 오늘. 오늘 회의가 없으면 질의 목록은 비고 누적 태그·유형만 보인다 (과거 회의는 목록에서 직접 고른다)
  function currentMeeting() {
    if (meetingFilter === null) meetingFilter = (DATA.live && DATA.live.date) || localDate(new Date());
    return meetingFilter;
  }

  function meetingOptions() {
    const today = localDate(new Date());
    const list = [...(DATA.meetings || [])].sort((a, b) => b.date.localeCompare(a.date));
    if (!list.some((m) => m.date === today)) list.unshift({ date: today, title: "", today: true });
    return list.map((m) => ({ ...m, today: m.date === today }));
  }

  function filteredSegments() {
    if (!DATA) return [];
    const mf = currentMeeting();
    return DATA.segments.filter((s) => !mf || s.meeting_date === mf);
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

  // 선택한 회의(기본 오늘)의 질의만 (대표 질의·질의 횟수·대상 기관)
  function memberStats(name, segsSource) {
    const segs = (segsSource || filteredSegments()).filter((s) => s.speaker === name);
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

  // 모든 회의 누적 (관심 태그·질의 유형은 과거 회의까지 합쳐서 본다)
  function cumStats(name) {
    return memberStats(name, DATA.segments);
  }

  function meetingCount() {
    return new Set((DATA.segments || []).map((s) => s.meeting_date)).size;
  }

  function profileFor(name) {
    const mf = currentMeeting();
    const today = mf ? ((DATA.profiles_by_meeting || {})[mf] || {})[name] : null;
    return today || (DATA.profiles || {})[name] || null;
  }

  function allAgencies() {
    const set = new Set();
    const mf = currentMeeting();
    const has = mf && DATA.meetings.some((m) => m.date === mf);
    for (const m of DATA.meetings) if (!mf || !has || m.date === mf) for (const a of m.agencies || []) set.add(a);
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

    const mf = currentMeeting();
    const agencies = allAgencies();
    if (agencyFilter && !agencies.includes(agencyFilter)) agencyFilter = "";
    if (view === "agency" && !agencyFilter) agencyFilter = agencies[0] || "";
    const live = DATA.live && DATA.live.date === mf;
    const updated = live && DATA.live.updated_at ? new Date(DATA.live.updated_at * 1000) : (DATA.generated_at ? new Date(DATA.generated_at * 1000) : null);

    let html = `<div class="profile-toolbar">
      <div class="profile-toggle">
        <button class="pf-toggle${view === "member" ? " active" : ""}" data-view="member">위원별</button>
        <button class="pf-toggle${view === "agency" ? " active" : ""}" data-view="agency">기관별</button>
      </div>
      <select id="pfMeeting" class="pf-select" title="회의">
        ${meetingOptions().map((m) => `<option value="${esc(m.date)}"${m.date === mf ? " selected" : ""}>${m.today ? "오늘 " : ""}${esc(m.date)} ${esc(m.title || "")}${DATA.live && DATA.live.date === m.date ? " (진행 중)" : ""}</option>`).join("")}
        <option value=""${mf === "" ? " selected" : ""}>전체 회의 누적 (${DATA.meetings.length})</option>
      </select>
      <select id="pfAgency" class="pf-select" title="피감기관">
        ${view === "member" ? `<option value="">전체 기관</option>` : ""}
        ${agencies.map((a) => `<option value="${esc(a)}"${a === agencyFilter ? " selected" : ""}>${esc(a)}</option>`).join("")}
      </select>
      <span class="pf-updated">${live ? "🔴 회의 중 · 30분마다 갱신 · " : ""}${updated ? `갱신 ${updated.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}` : ""}</span>
    </div>`;

    if (view === "member") {
      html += selected ? renderDetail(selected) : renderCards();
    } else {
      html += renderByAgency();
    }
    html += renderUnknown();
    html += `<div class="pf-foot">원문 근거가 있는 발언만 정리했으며 예상 질의는 만들지 않습니다. 질의 중인 위원의 구간은 발언이 끝난 뒤 다음 갱신에 반영됩니다.</div>`;
    root.innerHTML = html;

    root.querySelectorAll(".pf-toggle").forEach((b) => {
      b.onclick = () => { view = b.dataset.view; selected = null; if (view === "member") agencyFilter = ""; render(); };
    });
    $("pfMeeting").onchange = (e) => { meetingFilter = e.target.value; selected = null; render(); };
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
    const mf = currentMeeting();
    const members = DATA.members.map((m) => ({ ...m, st: memberStats(m.name), cum: cumStats(m.name) }));
    // 선택한 회의에서 질의가 많은 순 → 누적 순
    members.sort((a, b) => b.st.topicCount - a.st.topicCount || b.cum.topicCount - a.cum.topicCount || a.name.localeCompare(b.name));
    return `<div class="pf-cards">` + members.map((m) => {
      const st = m.st, cum = m.cum;
      const noToday = !st.segs.length;
      const noCum = !cum.segs.length;
      const label = mf ? (mf === localDate(new Date()) ? "오늘" : mf.slice(5)) : "누적";
      return `<div class="pf-card${noCum ? " pf-card-empty" : ""}" data-member="${esc(m.name)}">
        <div class="pf-card-head">
          ${avatar(m.name)}
          <div class="pf-card-name"><b>${esc(m.name)}</b> <span class="member-role">${esc(m.role === "위원" ? "" : m.role || "")}</span>
            <div class="member-sub">${esc(m.district || "")}</div></div>
          <div class="pf-card-count">${noToday ? `${esc(label)} 질의 없음` : `${esc(label)} 질의 ${st.segs.length}회 · 주제 ${st.topicCount}`}${mf && !noCum ? `<br><span class="pf-cum">누적 ${cum.segs.length}회</span>` : ""}</div>
        </div>
        ${noCum ? `<div class="pf-card-none">아직 분석된 질의 구간이 없습니다</div>` : `
        <div class="pf-card-tags">${chips(cum.tags, "tag", 3)}</div>
        <div class="pf-card-types">${chips(cum.types, "type", 3)}${cum.tone ? `<span class="pf-chip tone">${esc(cum.tone)}</span>` : ""}</div>`}
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
    const st = memberStats(name);          // 선택한 회의(오늘)
    const cum = cumStats(name);            // 모든 회의 누적
    const p = profileFor(name);
    const showProfile = p && !agencyFilter;
    const mf = currentMeeting();
    const isLive = !!(mf && DATA.live && DATA.live.date === mf);
    const isToday = mf === localDate(new Date());
    const dayLabel = mf ? (isToday ? "오늘" : mf) : "전체 회의";
    const nMeetings = meetingCount();
    const segs = [...st.segs].sort((a, b) => (a.meeting_date + a.start_ts).localeCompare(b.meeting_date + b.start_ts));
    const profileLabel = p && p.meeting_date && p.meeting_date !== "all" ? `${p.meeting_date === mf && isToday ? "오늘" : p.meeting_date} 발언 기준` : "전체 회의 기준";
    return `<button id="pfBack" class="pf-back" type="button">← 위원 목록</button>
    <div class="pf-detail-head">
      ${avatar(name)}
      <div><div class="pf-detail-name">${esc(name)} <span class="member-role">${esc(m.role || "")}</span></div>
      <div class="member-sub">${esc(m.district || "")}${m.party ? ` · ${esc(m.party)}` : ""} · ${esc(dayLabel)} 질의 ${st.segs.length}회 · 주제 ${st.topicCount}${mf ? ` · 누적 ${cum.segs.length}회 (회의 ${cum.meetings.length})` : ""}${st.tone ? ` · 어조 ${esc(st.tone)}` : ""}</div></div>
    </div>
    <div class="pf-grid">
      <section class="pf-section"><h4>관심 태그 Top <small>누적 · 회의 ${nMeetings}</small></h4>${bars(cum.tags.slice(0, 8), "tag")}</section>
      <section class="pf-section"><h4>질의 유형 분포 <small>누적 · 회의 ${nMeetings}</small></h4>${bars(cum.types, "type")}</section>
    </div>
    ${st.targets.length ? `<section class="pf-section"><h4>${esc(dayLabel)} 질의 대상 기관</h4><div class="pf-card-tags">${chips(st.targets, "agency", 6)}</div></section>` : ""}
    ${showProfile ? `<section class="pf-section"><h4>질의 방식 <small>${esc(profileLabel)}</small></h4>
      ${listOrNone(p.question_style)}
      ${(p.recurring_targets || []).length ? `<h5>반복 대상</h5>${listOrNone(p.recurring_targets)}` : ""}
      ${(p.open_commitments || []).length ? `<h5>기관 약속 (후속 확인 전)</h5>${listOrNone(p.open_commitments)}` : ""}
    </section>` : (p ? `<div class="pf-note">기관 필터를 해제하면 질의 방식 요약이 표시됩니다.</div>` : "")}
    <section class="pf-section"><h4>${esc(dayLabel)} 질의 (${segs.reduce((n, s) => n + topicsOf(s).length, 0)})</h4>
      ${segs.map((s) => topicsOf(s).map((t) => renderTopic(s, t)).join("")).join("") || `<div class="pf-none">${isLive ? "아직 분석된 질의가 없습니다. 발언이 끝나면 다음 갱신(30분)에 올라옵니다." : (isToday ? "오늘 분석된 질의가 없습니다. 회의가 시작되면 30분마다 올라옵니다." : "없음")}</div>`}
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
    const mf = currentMeeting();
    const u = (DATA.unknown_segments || []).filter((x) => !mf || x.meeting_date === mf);
    if (!u.length) return "";
    return `<section class="pf-section pf-unknown"><h4>위원 미확정 구간 · 명단 추가 후보</h4>
      ${u.map((x) => `<div class="pf-kv">${esc(x.meeting_date)} ${esc(x.start_ts)} · 후보: ${esc((x.candidates || []).join(", ") || "-")}${x.first_line ? ` · ${esc(x.first_line)}` : ""}</div>`).join("")}
    </section>`;
  }

  // ------------------------------------------------------------ 탭 진입 시 로드, 1분마다 갱신
  const tabBtn = document.querySelector('.tab[data-tab="profile"]');
  if (tabBtn) tabBtn.addEventListener("click", () => { load(); startPolling(); });
  load().then(startPolling);
})();
