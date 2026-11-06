// AI 요약 / 요청자료 / 지적답변 탭 렌더링
//
// app.js의 handlers 객체에 summary 핸들러를 추가하고,
// 탭 전환 로직과 각 분석 탭의 렌더링을 담당한다.
// summary.js는 app.js 뒤에 로드되므로 window._appBridge를 통해 연동한다.

(() => {
  const $ = (id) => document.getElementById(id);
  const fmtTime = (epochSec) =>
    new Date(epochSec * 1000).toLocaleTimeString("ko-KR", { hour12: false });
  const fmtDate = (epochSec) => {
    const d = new Date(epochSec * 1000);
    return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  };

  let currentFilter = "all";
  let summaryData = null;

  // ------------------------------------------------------------ 탭 전환
  const tabs = document.querySelectorAll(".tab-bar .tab");
  const panels = document.querySelectorAll(".tab-panel");

  tabs.forEach((btn) => {
    btn.onclick = () => {
      tabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      panels.forEach((p) => {
        p.classList.toggle("active", p.dataset.tab === btn.dataset.tab);
      });
      // 실시간 탭에서만 검색/문장수 표시
      const isLive = btn.dataset.tab === "live";
      const countEl = $("count");
      const searchEl = $("searchToggle");
      const dlEl = $("downloadBtn");
      if (countEl) countEl.style.display = isLive ? "" : "none";
      if (searchEl) searchEl.style.display = isLive ? "" : "none";
      if (dlEl) dlEl.style.display = isLive ? "" : "none";
      if (!isLive) {
        const bar = $("searchBar");
        if (bar) bar.hidden = true;
      }
      // 탭 전환 시 패널 상단으로 스크롤
      const activePanel = document.querySelector(`.tab-panel[data-tab="${btn.dataset.tab}"]`);
      if (activePanel) {
        const scrollEl = activePanel.querySelector(".tab-panel-scroll") || activePanel.querySelector(".transcript-body");
        if (scrollEl) scrollEl.scrollTop = 0;
      }
    };
  });

  // ------------------------------------------------------------ 원문 이동
  function goToSentence(ids) {
    if (!ids || !ids.length) return;
    // 실시간 탭으로 전환
    tabs.forEach((b) => b.classList.toggle("active", b.dataset.tab === "live"));
    panels.forEach((p) => p.classList.toggle("active", p.dataset.tab === "live"));
    // app.js에서 노출한 items 배열 사용
    const items = window._appBridge && window._appBridge.items;
    if (!items) return;
    const id = ids[0];
    const item = items.find((it) => it.rec.id === id);
    if (!item) return;
    item.el.scrollIntoView({ behavior: "smooth", block: "center" });
    item.el.classList.add("search-hit");
    setTimeout(() => item.el.classList.remove("search-hit"), 3000);
  }

  function refSpan(ids) {
    if (!ids || !ids.length) return "";
    return ` <span class="summary-ref" data-ids="${ids.join(",")}">#${ids[0]}</span>`;
  }

  // ------------------------------------------------------------ 요약 탭 렌더링
  function renderSummary(data) {
    if (!data) return;
    summaryData = data;

    // 상태 바
    if (data.updated_at) {
      $("summaryLastUpdate").textContent = `마지막 갱신: ${fmtTime(data.updated_at)}`;
    }
    if (data.next_update_at) {
      $("summaryNextUpdate").textContent = `다음 갱신: ${fmtTime(data.next_update_at)}`;
    }

    // 현재 논의 주제
    const topicSec = $("summaryCurrentTopic");
    if (data.current_topic) {
      $("summaryTopicText").textContent = data.current_topic;
      topicSec.hidden = false;
    } else {
      topicSec.hidden = true;
    }

    // 대시보드: 지표 카드 + 레이더 차트
    // When multiple hourly summaries exist, aggregate dashboard data from all hours
    // 구간(기본 30분)이 둘 이상이거나, 하나뿐이어도 보고서 요약(block)이 있으면 구간 목록으로 보여 준다
    const hasHourly = !!(data.hourly && (data.hourly.length > 1 || (data.hourly.length === 1 && data.hourly[0].block)));
    const recent = hasHourly ? aggregateRecent(data.hourly) : data.recent;
    const dashSec = $("summaryDashboard");
    if (recent) {
      renderDashboard(recent, data);
      dashSec.hidden = false;
    } else {
      dashSec.hidden = true;
    }

    // 시간대별 타임라인
    const hourlyEl = $("summaryHourly");
    if (hasHourly) {
      const wm = data.window_min || (data.hourly[0] && data.hourly[0].window_min) || 60;
      const heading = wm === 60 ? "시간대별 진행" : `${wm}분 단위 요약`;
      hourlyEl.innerHTML = `<h3>${heading}</h3>` + data.hourly.map((h, i) => renderHourBlock(h, i === data.hourly.length - 1)).join("");
      hourlyEl.hidden = false;
      // Hide the old "recent" section since hourly replaces it
      $("summaryRecent").hidden = true;
    } else {
      hourlyEl.hidden = true;
      // Show recent section as before for single-summary mode
      const recentSec = $("summaryRecent");
      if (data.recent) {
        $("summaryRecentBody").innerHTML = renderRecent(data.recent);
        recentSec.hidden = false;
      } else {
        recentSec.hidden = true;
      }
    }

    // Click delegation for hour block toggle (kept outside to avoid duplicate listeners)
    if (!hourlyEl._clickBound) {
      hourlyEl.addEventListener("click", (e) => {
        const head = e.target.closest(".hour-block-head");
        if (head) head.parentElement.classList.toggle("collapsed");
      });
      hourlyEl._clickBound = true;
    }

    // 주제별 누적
    const topicsSec = $("summaryTopics");
    if (data.topics && data.topics.length) {
      $("summaryTopicsBody").innerHTML = data.topics.map(renderTopicCard).join("");
      topicsSec.hidden = false;
    } else {
      topicsSec.hidden = true;
    }

    // 전체 회의 요약
    const fullSec = $("summaryFull");
    if (data.full_summary) {
      const paragraphs = data.full_summary.split(/\n\n+/).filter(p => p.trim());
      const paraHtml = paragraphs.map(p => `<p class="full-summary-para">${escHtml(p.trim())}</p>`).join("");
      $("summaryFullBody").innerHTML = `<div class="full-summary-text">${paraHtml}</div>`;
      fullSec.hidden = false;
    } else {
      fullSec.hidden = true;
    }

    // 이전 논의
    const pastSec = $("summaryPast");
    if (data.past && data.past.length) {
      $("summaryPastBody").innerHTML = data.past.map((t) => renderTopicCard(t, true)).join("");
      pastSec.hidden = false;
    } else {
      pastSec.hidden = true;
    }

    // 빈 상태 숨김
    const hasData = data.current_topic || recent || (data.topics && data.topics.length);
    $("summaryEmpty").style.display = hasData ? "none" : "";

    // 탭에 데이터 표시
    updateTabBadge("summary", hasData);
  }

  // ------------------------------------------------------------ 시간대별 블록 렌더링
  function renderHourBlock(h, isLast) {
    const cls = isLast ? "" : " collapsed";
    const hourTime = h.hour || "";
    const hourLabel = h.hour_label || "";
    const topic = h.current_topic || "";
    const r = h.recent || {};

    let bodyHtml = "";

    // 구간이 끝날 때 만든 보고서용 요약 (구간 발언 전체 기준). 진행 중인 구간에는 아직 없다.
    const b = h.block;
    if (b && b.summary) {
      bodyHtml += '<div class="block-summary">';
      if (b.title) bodyHtml += `<div class="block-title">${escHtml(b.title)}</div>`;
      bodyHtml += b.summary.split(/\n\n+/).filter(p => p.trim()).map(p => `<p>${escHtml(p.trim())}</p>`).join("");
      const sub = (title, arr) => (arr && arr.length)
        ? `<div class="recent-subsection"><h4 class="recent-subsection-title">${title}</h4><ul class="summary-list">${arr.map(x => `<li>${escHtml(x)}</li>`).join("")}</ul></div>` : "";
      bodyHtml += sub("핵심 쟁점", b.key_points) + sub("기관 답변·약속", b.answers) + sub("자료 요청", b.requests);
      if (b.numbers && b.numbers.length) {
        bodyHtml += '<div class="recent-subsection"><h4 class="recent-subsection-title">주요 수치</h4><div class="numbers-row">'
          + b.numbers.map(n => `<span class="number-chip">${escHtml(n)}</span>`).join("") + "</div></div>";
      }
      if (b.other) bodyHtml += `<p class="block-other">기타: ${escHtml(b.other)}</p>`;
      bodyHtml += "</div>";
    } else if (h.closed === false || (!b && isLast)) {
      bodyHtml += '<p class="block-pending">구간이 끝나면 보고서 요약이 만들어집니다. 아래는 5분 분석 기준 진행 상황입니다.</p>';
    }

    // key_statements
    if (r.key_statements && r.key_statements.length) {
      bodyHtml += '<ul class="summary-list">';
      for (const s of r.key_statements) {
        const tag = s.type || "statement";
        const tagLabel = { question: "질의", answer: "답변", statement: "발언" }[tag] || tag;
        bodyHtml += `<li><span class="summary-tag ${tag}">${tagLabel}</span>${escHtml(s.text)}${refSpan(s.sentence_ids)}</li>`;
      }
      bodyHtml += "</ul>";
    }

    // key_issues
    if (r.key_issues && r.key_issues.length) {
      bodyHtml += '<div class="recent-subsection">';
      bodyHtml += '<h4 class="recent-subsection-title">핵심 쟁점</h4>';
      bodyHtml += '<div class="issues-grid">';
      for (const i of r.key_issues) {
        bodyHtml += `<div class="issue-chip">${escHtml(i)}</div>`;
      }
      bodyHtml += "</div></div>";
    }

    // qa_pairs
    if (r.qa_pairs && r.qa_pairs.length) {
      bodyHtml += '<div class="recent-subsection">';
      bodyHtml += '<h4 class="recent-subsection-title">질의응답</h4>';
      bodyHtml += '<div class="qa-list">';
      for (const qa of r.qa_pairs) {
        bodyHtml += '<div class="qa-pair">';
        bodyHtml += `<div class="qa-question"><span class="qa-label">Q</span><span class="qa-text">${escHtml(qa.question)}${refSpan(qa.q_ids)}</span></div>`;
        if (qa.answer) {
          bodyHtml += `<div class="qa-answer"><span class="qa-label qa-label-a">A</span><span class="qa-text">${escHtml(qa.answer)}${refSpan(qa.a_ids)}</span></div>`;
        } else {
          bodyHtml += '<div class="qa-answer qa-pending"><span class="qa-label qa-label-pending">A</span><span class="qa-text qa-text-pending">답변 대기 중</span></div>';
        }
        bodyHtml += "</div>";
      }
      bodyHtml += "</div></div>";
    }

    // policies
    if (r.policies && r.policies.length) {
      bodyHtml += '<div class="recent-subsection">';
      bodyHtml += '<h4 class="recent-subsection-title">관련 정책</h4>';
      bodyHtml += '<div class="policy-tags">';
      bodyHtml += r.policies.map(p => `<span class="policy-tag">${escHtml(p)}</span>`).join("");
      bodyHtml += "</div></div>";
    }

    // numbers
    if (r.numbers && r.numbers.length) {
      bodyHtml += '<div class="recent-subsection">';
      bodyHtml += '<h4 class="recent-subsection-title">주요 수치</h4>';
      bodyHtml += '<div class="numbers-row">';
      bodyHtml += r.numbers.map(n => `<span class="number-chip">${escHtml(n)}</span>`).join("");
      bodyHtml += "</div></div>";
    }

    if (!bodyHtml) {
      bodyHtml = '<p style="color:var(--muted);font-size:13px">이 시간대의 상세 내용이 없습니다.</p>';
    }

    return `<div class="hour-block${cls}">
      <div class="hour-block-head">
        <span class="hour-time">${escHtml(hourTime)}</span>
        <span class="hour-label">${escHtml(hourLabel)}</span>
        <span class="hour-topic">${escHtml(topic)}</span>
      </div>
      <div class="hour-block-body">${bodyHtml}</div>
    </div>`;
  }

  // Aggregate recent data from all hourly summaries for the dashboard
  function aggregateRecent(hourlyArr) {
    const merged = {
      key_statements: [],
      key_issues: [],
      numbers: [],
      policies: [],
      qa_pairs: [],
    };
    const seenIssues = new Set();
    const seenNumbers = new Set();
    const seenPolicies = new Set();

    for (const h of hourlyArr) {
      const r = h.recent || {};
      if (r.key_statements) merged.key_statements.push(...r.key_statements);
      if (r.key_issues) {
        for (const i of r.key_issues) {
          if (!seenIssues.has(i)) { seenIssues.add(i); merged.key_issues.push(i); }
        }
      }
      if (r.numbers) {
        for (const n of r.numbers) {
          if (!seenNumbers.has(n)) { seenNumbers.add(n); merged.numbers.push(n); }
        }
      }
      if (r.policies) {
        for (const p of r.policies) {
          if (!seenPolicies.has(p)) { seenPolicies.add(p); merged.policies.push(p); }
        }
      }
      if (r.qa_pairs) merged.qa_pairs.push(...r.qa_pairs);
    }
    // Return null if nothing was aggregated
    if (!merged.key_statements.length && !merged.key_issues.length && !merged.numbers.length) return null;
    return merged;
  }

  // ------------------------------------------------------------ 대시보드 렌더링
  function parseStatNumber(n) {
    const isWarn = /부실|미회수|불승인|반송|좌초|감소|적자/.test(n);
    // Pattern 1: "라벨 숫자단위" e.g. "비정규직 75%", "정규직 377명", "서울 9.1배"
    const m1 = n.match(/^(.+?)\s+([\d,.]+(?:\.\d+)?(?:%|배|명|원|억 원|만 원|만|건|개|곳|억)?)$/);
    if (m1) return { value: m1[2], label: m1[1], warn: isWarn };
    // Pattern 2: "숫자단위 라벨" e.g. "55억 원 좌초", "30% 초과"
    const m2 = n.match(/^([\d,.]+(?:\.\d+)?(?:억 원|만 원|억|만|%|배|명|원|건|개|곳))\s+(.+)$/);
    if (m2) return { value: m2[1], label: m2[2], warn: isWarn };
    // Pattern 3: just a number with unit somewhere in the string
    const m3 = n.match(/([\d,.]+(?:\.\d+)?(?:%|배|명|원|억 원|만 원|억|만|건|개|곳)?)/);
    if (m3) {
      const rest = n.replace(m3[0], "").trim();
      return { value: m3[1], label: rest || "", warn: isWarn };
    }
    return { value: n, label: "", warn: isWarn };
  }

  function renderDashboard(recent, data) {
    // 1. 핵심 수치 카드
    const numbers = recent.numbers || [];
    const cardsEl = $("statCards");
    if (numbers.length) {
      cardsEl.innerHTML = numbers.slice(0, 6).map((n) => {
        const parsed = parseStatNumber(n);
        const cls = parsed.warn ? "warn" : "";
        const labelHtml = parsed.label ? `<span class="stat-card-label">${escHtml(parsed.label)}</span>` : "";
        return `<div class="stat-card"><span class="stat-card-value ${cls}">${escHtml(parsed.value)}</span>${labelHtml}</div>`;
      }).join("");
    } else {
      cardsEl.innerHTML = "";
    }

    // 2. 레이더 차트
    const issues = recent.key_issues || [];
    const statements = recent.key_statements || [];
    const questionCount = statements.filter(s => s.type === "question").length;
    const answerCount = statements.filter(s => s.type === "answer").length;
    const requestCount = (data.requests || []).length;

    // 6축: 질의 강도, 답변 충실도, 쟁점 수, 요청자료 수, 수치 언급, 정책 언급
    const axes = [
      { label: "질의", value: Math.min(questionCount / 5, 1) },
      { label: "답변", value: Math.min(answerCount / 3, 1) },
      { label: "쟁점", value: Math.min(issues.length / 5, 1) },
      { label: "자료요청", value: Math.min(requestCount / 5, 1) },
      { label: "수치", value: Math.min(numbers.length / 6, 1) },
      { label: "정책", value: Math.min((recent.policies || []).length / 4, 1) },
    ];
    renderRadar(axes);
  }

  function renderRadar(axes) {
    const svg = $("radarChart");
    if (!svg) return;
    const cx = 120, cy = 120, R = 85;
    const n = axes.length;
    const levels = [0.25, 0.5, 0.75, 1.0];

    function polar(i, r) {
      const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
      return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
    }
    function polygon(r) {
      return axes.map((_, i) => polar(i, r).join(",")).join(" ");
    }

    let html = "";
    // 격자
    for (const lv of levels) {
      html += `<polygon class="radar-grid" points="${polygon(R * lv)}"/>`;
    }
    // 축선
    for (let i = 0; i < n; i++) {
      const [x, y] = polar(i, R);
      html += `<line class="radar-axis" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}"/>`;
    }
    // 데이터 영역
    const dataPoints = axes.map((a, i) => polar(i, R * Math.max(a.value, 0.08)));
    html += `<polygon class="radar-area" points="${dataPoints.map(p => p.join(",")).join(" ")}"/>`;
    // 점 + 라벨
    for (let i = 0; i < n; i++) {
      const [dx, dy] = dataPoints[i];
      html += `<circle class="radar-dot" cx="${dx}" cy="${dy}" r="3"/>`;
      const [lx, ly] = polar(i, R + 14);
      html += `<text class="radar-label" x="${lx}" y="${ly + 3}">${axes[i].label}</text>`;
    }
    svg.innerHTML = html;
  }

  function renderRecent(r) {
    let html = "";

    // 주요 발언
    if (r.key_statements && r.key_statements.length) {
      html += '<ul class="summary-list">';
      for (const s of r.key_statements) {
        const tag = s.type || "statement";
        const tagLabel = { question: "질의", answer: "답변", statement: "발언" }[tag] || tag;
        html += `<li><span class="summary-tag ${tag}">${tagLabel}</span>${escHtml(s.text)}${refSpan(s.sentence_ids)}</li>`;
      }
      html += "</ul>";
    }

    // 핵심 쟁점
    if (r.key_issues && r.key_issues.length) {
      html += '<div class="recent-subsection">';
      html += '<h4 class="recent-subsection-title">핵심 쟁점</h4>';
      html += '<div class="issues-grid">';
      for (const i of r.key_issues) {
        html += `<div class="issue-chip">${escHtml(i)}</div>`;
      }
      html += "</div></div>";
    }

    // 정책/사업명
    if (r.policies && r.policies.length) {
      html += '<div class="recent-subsection">';
      html += '<h4 class="recent-subsection-title">관련 정책</h4>';
      html += '<div class="policy-tags">';
      html += r.policies.map(p => `<span class="policy-tag">${escHtml(p)}</span>`).join("");
      html += "</div></div>";
    }

    // 예산/수치
    if (r.numbers && r.numbers.length) {
      html += '<div class="recent-subsection">';
      html += '<h4 class="recent-subsection-title">주요 수치</h4>';
      html += '<div class="numbers-row">';
      html += r.numbers.map(n => `<span class="number-chip">${escHtml(n)}</span>`).join("");
      html += "</div></div>";
    }

    // Q&A
    if (r.qa_pairs && r.qa_pairs.length) {
      html += '<div class="recent-subsection">';
      html += '<h4 class="recent-subsection-title">질의응답</h4>';
      html += '<div class="qa-list">';
      for (const qa of r.qa_pairs) {
        html += '<div class="qa-pair">';
        html += `<div class="qa-question"><span class="qa-label">Q</span><span class="qa-text">${escHtml(qa.question)}${refSpan(qa.q_ids)}</span></div>`;
        if (qa.answer) {
          html += `<div class="qa-answer"><span class="qa-label qa-label-a">A</span><span class="qa-text">${escHtml(qa.answer)}${refSpan(qa.a_ids)}</span></div>`;
        } else {
          html += '<div class="qa-answer qa-pending"><span class="qa-label qa-label-pending">A</span><span class="qa-text qa-text-pending">답변 대기 중</span></div>';
        }
        html += "</div>";
      }
      html += "</div></div>";
    }

    return html;
  }

  function renderTopicCard(t, collapsed) {
    const cls = collapsed ? " collapsed" : "";
    const timeStr = t.started_at ? fmtDate(t.started_at) : "";
    const endStr = t.ended_at ? ` ~ ${fmtDate(t.ended_at)}` : " ~ 진행 중";
    return `<div class="topic-card${cls}">
      <div class="topic-card-head" onclick="this.parentElement.classList.toggle('collapsed')">
        <span>${escHtml(t.title || "주제")}</span>
        <span class="topic-time">${timeStr}${endStr}</span>
      </div>
      <div class="topic-card-body">${escHtml(t.summary || "")}</div>
    </div>`;
  }

  // ------------------------------------------------------------ 요청자료(위원별) 탭 렌더링

  // 경제노동위원회 위원 명단 (이름 매칭 + 사진 + 소속)
  // photo: 경기도의회 사이트 복구 후 실제 URL로 교체
  const _P = "https://www.ggc.go.kr/site/main/gwstorage/PhotoPath/";
  const MEMBER_INFO = {
    "장태환": { party: "더불어민주당", district: "의왕시2",   photo: _P + "jangt776_org^jpg" },
    "최민":   { party: "더불어민주당", district: "광명시2",   photo: _P + "ntop17_org^jpg" },
    "장윤정": { party: "더불어민주당", district: "안산시3", role: "부위원장", photo: _P + "jyj2195_org^jpg" },
    "박준모": { party: "더불어민주당", district: "안양시4",   photo: _P + "junmo_org^jpg" },
    "송규근": { party: "더불어민주당", district: "고양시6",   photo: _P + "cjsql00_org^jpg" },
    "송연섭": { party: "더불어민주당", district: "비례대표",  photo: _P + "fivewhys_org^jpg" },
    "신명순": { party: "더불어민주당", district: "김포시3",   photo: _P + "92free_org^jpg" },
    "오현정": { party: "더불어민주당", district: "화성시2",   photo: _P + "sweatkids_org^jpg" },
    "유수연": { party: "더불어민주당", district: "의정부시1", photo: _P + "glenaa_org^jpg" },
    "이기대": { party: "더불어민주당", district: "고양시9",   photo: _P + "leekd03_org^jpg" },
    "최보라": { party: "더불어민주당", district: "광주시1",   photo: _P + "now4060_org^jpg" },
    "최상규": { party: "더불어민주당", district: "수원시1",   photo: _P + "tkdrb1504_org^jpg" },
    "최혜경": { party: "더불어민주당", district: "비례대표",  photo: _P + "chk7430_org^jpg" },
    "한영수": { party: "더불어민주당", district: "용인시1",   photo: _P + "hysboy_org^jpg" },
    "정용한": { party: "국민의힘",     district: "성남시8",   photo: _P + "cjdan_org^jpg" },
  };
  const MEMBERS = Object.keys(MEMBER_INFO);
  window.MEMBER_INFO = MEMBER_INFO;  // app.js 발언자 배지 사진용

  // AI가 적은 위원명을 명단 기준으로 정리: 명단 이름이 들어 있으면 그 이름, 아니면 null(확인필요)
  function resolveMember(raw) {
    if (!raw || typeof raw !== "string") return null;
    for (const name of MEMBERS) {
      if (raw.includes(name)) return name;
    }
    return null;
  }

  // 페이지 로드 시 전체 위원 목록을 바로 렌더링
  function renderMemberList() {
    const el = $("requestsList");
    $("requestsEmpty").style.display = "none";
    el.innerHTML = MEMBERS.map((name) => renderMemberRow(name, [])).join("");
  }

  function renderRequests(requests) {
    const el = $("requestsList");
    const summaryEl = $("requestsSummary");

    // 위원별로 그룹핑
    const byMember = {};
    const unassigned = [];
    if (requests && requests.length) {
      for (const r of requests) {
        // 명단에 있는 위원만 위원 행에 넣고, 나머지("확인필요", 잘못 인식된 이름 등)는 확인필요 행으로
        const member = resolveMember(r.raised_by) || resolveMember(r.member);
        if (member) {
          if (!byMember[member]) byMember[member] = [];
          byMember[member].push(r);
        } else {
          unassigned.push(r);
        }
      }
    }

    const totalCount = requests ? requests.length : 0;
    const activeCount = Object.keys(byMember).length;
    if (summaryEl) {
      summaryEl.innerHTML = totalCount
        ? `<span class="requests-stat">총 <strong>${totalCount}건</strong></span> <span class="requests-stat">위원 <strong>${activeCount}명</strong></span>`
        : "";
    }
    updateTabBadge("requests", totalCount > 0);

    // 위원을 요청 있는 순 → 없는 순으로 정렬
    const sorted = [...MEMBERS].sort((a, b) => {
      const ac = (byMember[a] || []).length;
      const bc = (byMember[b] || []).length;
      if (ac !== bc) return bc - ac;  // 요청 많은 위원 먼저
      return 0;
    });

    let html = sorted.map((name) => renderMemberRow(name, byMember[name] || [])).join("");

    // 위원 확인필요 건 (명단에 없는 이름·불확실한 발언자) - 맨 위에 두어 사람이 바로 확인하게
    if (unassigned.length) {
      html = renderMemberRow(null, unassigned) + html;
    }

    el.innerHTML = html;
  }

  function renderMemberRow(name, items) {
    const info = name ? (MEMBER_INFO[name] || {}) : {};
    const hasItems = items.length > 0;
    const activeCls = (hasItems ? " member-row-active" : "") + (name ? "" : " member-row-unknown");

    // 아바타
    let avatar;
    if (name && info.photo) {
      avatar = `<img class="member-avatar" src="${info.photo}" alt="${escHtml(name)}">`;
    } else if (name) {
      avatar = `<span class="member-avatar member-initial">${escHtml(name.charAt(0))}</span>`;
    } else {
      avatar = `<span class="member-avatar member-initial" style="background:var(--muted)">?</span>`;
    }

    const displayName = name ? `${escHtml(name)} 위원` : "(확인필요) 위원 미확정";
    const role = info.role ? `<span class="member-role">${escHtml(info.role)}</span>` : "";
    const district = info.district || "";
    const party = info.party || "";
    const partyClass = party.includes("민주") ? "dem" : party.includes("국민") ? "ppp" : "";
    const countBadge = hasItems
      ? `<span class="member-count">${items.length}건</span>`
      : `<span class="member-count member-count-zero">-</span>`;

    const requestsHtml = hasItems
      ? `<div class="member-requests">${items.map(renderRequestItem).join("")}</div>`
      : "";

    return `<div class="member-row${activeCls}">
      <div class="member-row-head">
        ${avatar}
        <div class="member-info">
          <span class="member-name">${displayName}${role}</span>
          <span class="member-sub">${escHtml(district)}${partyClass ? ` <span class="member-party ${partyClass}">${escHtml(party)}</span>` : ""}</span>
        </div>
        ${countBadge}
      </div>
      ${requestsHtml}
    </div>`;
  }

  function renderRequestItem(r) {
    const statusLabel = { new: "신규", ongoing: "진행", completed: "완료" }[r.status] || r.status || "신규";
    const statusCls = r.status || "new";
    const timeStr = r.requested_at ? fmtTime(r.requested_at) : (r.broadcast_time ? fmtTime(r.broadcast_time) : "");
    const quoteHtml = r.original_text
      ? `<div class="request-quote">${escHtml(r.original_text)}</div>`
      : "";
    return `<div class="request-card">
      <div class="request-card-top">
        ${timeStr ? `<span class="request-time">${timeStr}</span>` : ""}
        <span class="request-status ${statusCls}">${statusLabel}</span>
      </div>
      <div class="request-card-title">${escHtml(r.description || "")}</div>
      ${r.raised_by_raw ? `<div class="request-raw-name">AI 인식 발언자: ${escHtml(r.raised_by_raw)} (확인필요)</div>` : ""}
      ${quoteHtml}
      <div class="request-meta">
        ${r.type ? `<span class="request-meta-item"><strong>종류</strong> ${escHtml(r.type)}</span>` : ""}
        ${r.target ? `<span class="request-meta-item"><strong>대상</strong> ${escHtml(r.target)}</span>` : ""}
        ${r.period ? `<span class="request-meta-item"><strong>기간</strong> ${escHtml(r.period)}</span>` : ""}
        ${r.deadline ? `<span class="request-meta-item"><strong>기한</strong> ${escHtml(r.deadline)}</span>` : ""}
      </div>
      ${r.sentence_ids ? `<div style="margin-top:4px">${refSpan(r.sentence_ids)}</div>` : ""}
    </div>`;
  }

  function detectMember(text) {
    if (!text) return null;
    for (const name of MEMBERS) {
      if (text.includes(name)) return name;
    }
    return null;
  }

  // ------------------------------------------------------------ 지적/답변 탭 렌더링
  function renderIssues(issues) {
    const el = $("issuesList");
    if (!el) return;  // issues 탭이 HTML에 없으면 스킵
    if (!issues || !issues.length) {
      el.innerHTML = "";
      const empty = $("issuesEmpty");
      if (empty) empty.style.display = "";
      updateTabBadge("issues", false);
      return;
    }
    const empty = $("issuesEmpty");
    if (empty) empty.style.display = "none";

    const unansweredCount = issues.filter((i) => i.status !== "answered").length;
    updateTabBadge("issues", unansweredCount > 0);

    const filtered =
      currentFilter === "all"
        ? issues
        : issues.filter((i) => i.status === currentFilter);

    el.innerHTML = filtered
      .map((i) => {
        const status = i.status || "unanswered";
        const statusLabel = { unanswered: "미답변", answered: "답변완료", partial: "일부답변" }[status] || status;
        const amountsHtml = i.amounts && i.amounts.length
          ? `<div class="issue-amounts">${i.amounts.map(a => `<span class="summary-tag number">${escHtml(a)}</span>`).join(" ")}</div>`
          : "";
        const contextHtml = i.context
          ? `<div class="issue-context">${escHtml(i.context)}</div>`
          : "";
        const priorHtml = i.prior_issue
          ? `<div class="issue-prior"><strong>작년 감사 연결:</strong> ${escHtml(i.prior_issue)}</div>`
          : "";
        return `<div class="issue-card ${status}">
          <div class="issue-header">
            <div class="issue-title">${escHtml(i.issue || "")}</div>
            <span class="issue-status-badge ${status}">${statusLabel}</span>
          </div>
          ${amountsHtml}
          ${contextHtml}
          <div class="issue-raised">
            ${escHtml(resolveMember(i.raised_by) ? `${resolveMember(i.raised_by)} 위원` : "(확인필요) 위원")} 지적${refSpan(i.issue_ids)}
          </div>
          ${
            i.response
              ? `<div class="issue-response">
                  <span class="issue-who">${escHtml(i.responded_by || "집행부")}</span><br>
                  ${escHtml(i.response)}${refSpan(i.response_ids)}
                </div>`
              : ""
          }
          ${i.commitment ? `<div class="issue-commitment">${escHtml(i.commitment)}</div>` : ""}
          ${priorHtml}
        </div>`;
      })
      .join("");
  }

  // 필터 버튼
  const filterBtns = document.querySelectorAll(".filter-btn");
  filterBtns.forEach((btn) => {
    btn.onclick = () => {
      filterBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentFilter = btn.dataset.filter;
      if (summaryData) renderIssues(summaryData.issues);
    };
  });

  // ------------------------------------------------------------ 탭 뱃지
  function updateTabBadge(tabName, hasData) {
    const btn = document.querySelector(`.tab[data-tab="${tabName}"]`);
    if (btn) btn.classList.toggle("has-data", !!hasData);
  }

  // ------------------------------------------------------------ 클릭 이벤트 위임
  document.addEventListener("click", (e) => {
    const ref = e.target.closest(".summary-ref");
    if (ref) {
      const ids = ref.dataset.ids.split(",").map(Number);
      goToSentence(ids);
    }
  });

  // ------------------------------------------------------------ 토글: 주제 카드
  document.addEventListener("click", (e) => {
    const head = e.target.closest(".topic-card-head");
    if (head) {
      head.parentElement.classList.toggle("collapsed");
    }
  });

  // ------------------------------------------------------------ AI 요약 보고서 다운로드 (마크다운)
  function buildReport(data) {
    const cfg = window.VIEWER_CONFIG || {};
    const esc = (s) => String(s || "").trim();
    const kst = new Date(Date.now() + 9 * 60 * 60 * 1000);
    const date = `${kst.getUTCFullYear()}-${String(kst.getUTCMonth() + 1).padStart(2, "0")}-${String(kst.getUTCDate()).padStart(2, "0")}`;
    const wm = data.window_min || 60;
    const L = [];
    L.push(`# ${cfg.title || "경기도의회 행정사무감사"} AI 요약 보고서`, "");
    L.push(`- 날짜: ${date}`, `- 생성 시각: ${new Date().toLocaleTimeString("ko-KR", { hour12: false })}`);
    L.push("- 집중 기관: 경기신용보증재단 (요약은 경기신보 관련 내용 중심, 요청자료는 경기신보가 명시된 건만)", "");
    L.push(`## ${wm}분 단위 구간 요약`, "");
    const hourly = data.hourly || [];
    if (!hourly.length) L.push("(아직 구간 요약이 없습니다)", "");
    for (const h of hourly) {
      const b = h.block || null;
      L.push(`### ${h.hour || ""}${b && b.title ? " — " + esc(b.title) : (h.current_topic ? " — " + esc(h.current_topic) : "")}` + (h.sentence_count ? ` (${h.sentence_count}문장)` : ""), "");
      if (b && b.summary) {
        L.push(esc(b.summary), "");
        const sub = (t, arr) => { if (arr && arr.length) { L.push(`**${t}**`); arr.forEach(x => L.push(`- ${esc(x)}`)); L.push(""); } };
        sub("핵심 쟁점", b.key_points); sub("기관 답변·약속", b.answers); sub("자료 요청", b.requests);
        if (b.numbers && b.numbers.length) L.push("**주요 수치**: " + b.numbers.map(esc).join(" · "), "");
        if (b.other) L.push(`**기타**: ${esc(b.other)}`, "");
      } else {
        const r = h.recent || {};
        L.push(h.closed === false ? "(구간 진행 중 - 5분 분석 기준)" : "(5분 분석 기준 요약)", "");
        (r.key_statements || []).forEach(s => L.push(`- [${{ question: "질의", answer: "답변" }[s.type] || "발언"}] ${esc(s.text)}`));
        if (r.key_issues && r.key_issues.length) { L.push("", "**핵심 쟁점**"); r.key_issues.forEach(x => L.push(`- ${esc(x)}`)); }
        if (r.numbers && r.numbers.length) L.push("", "**주요 수치**: " + r.numbers.map(esc).join(" · "));
        L.push("");
      }
    }
    const reqs = [...(data.requests || [])].sort((a, b) => (a.requested_at || 0) - (b.requested_at || 0));
    L.push(`## 경기신용보증재단 요청자료 (${reqs.length}건)`, "");
    for (const r of reqs) {
      const stamp = r.requested_at ? fmtDate(r.requested_at) : "--:--";
      const extra = [r.type, r.period, r.deadline ? `기한 ${r.deadline}` : null].filter(Boolean).join(" / ");
      L.push(`- [${stamp}] ${r.raised_by || "확인필요"}: ${esc(r.description)}${extra ? ` (${extra})` : ""}`);
    }
    if (!reqs.length) L.push("(없음)");
    L.push("");
    const issues = data.issues || [];
    L.push(`## 지적사항·답변 (${issues.length}건)`, "");
    for (const i of issues) {
      const st = { answered: "답변완료", partial: "일부답변" }[i.status] || "미답변";
      L.push(`- [${st}] ${i.raised_by || "확인필요"}: ${esc(i.issue)}`);
      if (i.response) L.push(`  - 답변(${i.responded_by || "기관"}): ${esc(i.response)}`);
      if (i.commitment) L.push(`  - 조치/약속: ${esc(i.commitment)}`);
    }
    if (!issues.length) L.push("(없음)");
    L.push("");
    if (data.full_summary) L.push("## 전체 회의 요약", "", esc(data.full_summary), "");
    return L.join("\n");
  }

  const reportBtn = $("reportBtn");
  if (reportBtn) {
    reportBtn.onclick = () => {
      if (!summaryData) return;
      const blob = new Blob([buildReport(summaryData)], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const kst = new Date(Date.now() + 9 * 60 * 60 * 1000);
      a.download = `경기신보_AI요약보고서_${kst.toISOString().slice(0, 10)}.md`;
      a.click();
      URL.revokeObjectURL(url);
    };
  }

  // ------------------------------------------------------------ 엑셀(CSV) 다운로드
  const excelBtn = $("requestsExcelBtn");
  if (excelBtn) {
    excelBtn.onclick = () => {
      if (!_liveRequests.length) return;
      const BOM = "\uFEFF";  // Excel에서 한글 깨짐 방지
      const header = ["위원명", "요청 시각", "요청 내용", "종류", "대상기관", "상태", "발언 원문"];
      const rows = _liveRequests.map(r => [
        r.raised_by || "",
        r.requested_at ? new Date(r.requested_at * 1000).toLocaleString("ko-KR") : "",
        (r.description || "").replace(/"/g, '""'),
        r.type || "",
        r.target || "",
        { new: "신규", ongoing: "진행", completed: "완료" }[r.status] || r.status || "",
        (r.original_text || "").replace(/"/g, '""'),
      ]);
      const csv = BOM + [header, ...rows].map(row =>
        row.map(cell => `"${cell}"`).join(",")
      ).join("\n");
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const today = new Date().toISOString().slice(0, 10);
      a.download = `경기신보_요청사항_${today}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    };
  }

  // ------------------------------------------------------------ 유틸
  function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ------------------------------------------------------------ app.js 연동
  // app.js에서 window._appBridge.onSummary를 호출
  function handleSummary(data) {
    summaryData = data;
    renderSummary(data);
    renderRequests(data.requests);
    renderIssues(data.issues);
  }

  // Supabase Realtime에서 개별 INSERT를 받을 때 사용
  let _liveRequests = [];
  let _liveIssues = [];

  function addRequest(req) {
    _liveRequests.push(req);
    renderRequests(_liveRequests);
    if (window._notify) window._notify.request(req);
  }

  function addIssue(issue) {
    _liveIssues.push(issue);
    renderIssues(_liveIssues);
  }

  // handleSummary가 호출되면 라이브 목록도 갱신
  const _origHandleSummary = handleSummary;
  let _summarySeen = false;  // 첫 로드(과거 목록)는 알리지 않고, 그 뒤 새로 생긴 요청만 알린다
  function handleSummaryWrapped(data) {
    if (data.requests) {
      if (_summarySeen && window._notify) {
        const known = new Set(_liveRequests.map((r) => r.description));
        data.requests.filter((r) => r.description && !known.has(r.description)).forEach((r) => window._notify.request(r));
      }
      _liveRequests = data.requests;
    }
    if (data.issues) _liveIssues = data.issues;
    _summarySeen = true;
    _origHandleSummary(data);
  }

  // 전역 브릿지 등록
  window._summaryBridge = { handleSummary: handleSummaryWrapped, addRequest, addIssue };

  // 페이지 로드 시 위원 목록 + 예시 데이터 표시
  // (실제 운영 시 SAMPLE_REQUESTS를 빈 배열로 바꾸면 됨)
  // 2025년 행정사무감사 실제 질의 패턴 기반 샘플
  const SAMPLE_REQUESTS = [
    {
      description: "주요 정책 보증 사업별 부실 현황 상세 명세, 불승인율 추이(2023~2026년)",
      original_text: "2년 연속 부채 중점기관으로 지정됐는데, 주요 정책별로 보증 사업 부실이 얼마나 되는지 상세 명세를 좀 주세요. 불승인율이 2023년 9.9%에서 2025년 22%로 2배 이상 증가했는데 올해는 어떤지, 보증신청 승인·불승인 건수하고 불승인율도 같이 정리해서 제출해 주시기 바랍니다.",
      raised_by: "한영수",
      type: "통계/현황",
      target: "경기신용보증재단",
      requested_at: 1730350800,
      sentence_ids: [124, 125],
      status: "new",
    },
    {
      description: "코로나19 특례보증·신용업 보증 부실률 현황, 부실채권 30% 관련 상세 자료",
      original_text: "부실채권이 30% 넘는다고 하는데 코로나19 극복 특례보증하고 신용업 보증에서 특히 높은 부실률이라고 알고 있어요. 이 부분 사업별로 상세하게 정리해서 자료 제출해 주십시오.",
      raised_by: "한영수",
      type: "통계",
      target: "경기신용보증재단",
      requested_at: 1730353400,
      sentence_ids: [160, 161],
      status: "new",
    },
    {
      description: "2026년 불승인 사유별 상세 자료, 취약 소상공인 특례보증 부실률",
      original_text: "올해 불승인 사유를 유형별로 분류한 상세 자료 주시고, 취약 소상공인 특례보증, 영세 소상공인 특별보증 이런 것들 부실률이 각각 어떻게 되는지 정리해 주세요.",
      raised_by: "한영수",
      type: "통계",
      target: "경기신용보증재단",
      requested_at: 1730355000,
      sentence_ids: [185],
      status: "new",
    },
    {
      description: "경기청년 기회사다리 금융 대출금·상환자 수·손실보전액 현황",
      original_text: "경기청년 기회사다리 금융 관련해서 대출금이 얼마나 되는지, 상환자 수, 손실보전액 이런 것들 현황 자료를 제출해 주시기 바랍니다.",
      raised_by: "최상규",
      type: "현황",
      target: "경기신용보증재단",
      requested_at: 1730352600,
      sentence_ids: [156],
      status: "new",
    },
    {
      description: "차세대 시스템 사업 진행 경과, 업체 선정 과정 자료, 내부감사 결과",
      original_text: "경기신용보증재단 차세대 시스템 사업이 어떻게 돼 가고 있는지 파악하고 계십니까? 사실은 업체 선정 때부터 잘못돼서 지금 이런 일이 벌어졌다고 생각합니다. 2회 유찰 후 가격이 싸서 1개 업체와만 합의했는데 금융 관련 전산 작업 경험이 부족한 업체였어요. 업체 선정 과정 자료하고 내부감사 결과 제출해 주십시오.",
      raised_by: "최민",
      type: "경과보고/감사결과",
      target: "경기신용보증재단",
      requested_at: 1730347200,
      sentence_ids: [42, 43, 44],
      status: "new",
    },
    {
      description: "47개 위탁사업(566억) 회의록·관리 현황, 수수료율 자료",
      original_text: "47개의 위탁사업 566억 5,000만 원 규모인데 회의자료가 57개밖에 없다고요. 회의록 작성이 기본적인 행정 기록이어야 합니다. 위탁사업별 관리 현황하고 수수료율 자료 제출해 주세요.",
      raised_by: "정용한",
      type: "관리현황/회의록",
      target: "경기신용보증재단",
      requested_at: 1730349000,
      sentence_ids: [87, 88],
      status: "new",
    },
    {
      description: "신보 출연금 집행 현황 및 향후 출연 계획, 재단 경영평가 결과",
      original_text: "경기도에서 신보에 출연한 출연금이 어떻게 집행이 되고 있는지, 앞으로 출연 계획은 어떤지 자료 주시고요. 재단 경영평가 결과도 같이 제출해 주십시오.",
      raised_by: "이기대",
      type: "현황/계획",
      target: "경기신용보증재단",
      requested_at: 1730356200,
      sentence_ids: [203],
      status: "new",
    },
    {
      description: "중앙신보 시스템 연계 현황, 새로운 시스템 사업 추진 계획",
      original_text: "이번 신보 시스템 개선 사업은 경기신보와 중앙신보 시스템 연계를 위한 것이라고 하셨는데, 현재 연계 현황이 어떻게 되는지, 새로운 시스템 사업은 언제 추진할 계획인지 자료 제출 바랍니다.",
      raised_by: "장윤정",
      type: "계획/현황",
      target: "경기신용보증재단",
      requested_at: 1730354400,
      sentence_ids: [178, 179],
      status: "new",
    },
  ];
  // Supabase 모드면 DB에서 로드하므로 샘플 스킵, 로컬(WebSocket)이면 샘플 표시
  const cfg = window.VIEWER_CONFIG || {};
  if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) {
    renderRequests(SAMPLE_REQUESTS);
  } else {
    renderMemberList();  // Supabase 모드: 빈 위원 목록만 먼저 표시, DB 로드 후 갱신됨
  }
})();
