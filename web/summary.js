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
      $("count").style.display = isLive ? "" : "none";
      $("searchToggle").style.display = isLive ? "" : "none";
      $("downloadBtn").style.display = isLive ? "" : "none";
      if (!isLive) {
        const bar = $("searchBar");
        if (bar) bar.hidden = true;
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

    // 최근 5분 핵심
    const recent = data.recent;
    const recentSec = $("summaryRecent");
    if (recent) {
      $("summaryRecentBody").innerHTML = renderRecent(recent);
      recentSec.hidden = false;
    } else {
      recentSec.hidden = true;
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
      $("summaryFullBody").innerHTML = `<div class="full-summary-text">${escHtml(data.full_summary)}</div>`;
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
      html += '<div style="margin-top:10px"><strong style="font-size:12px;color:var(--muted)">핵심 쟁점</strong>';
      html += '<ul class="summary-list">';
      for (const i of r.key_issues) html += `<li>${escHtml(i)}</li>`;
      html += "</ul></div>";
    }

    // 정책/사업명
    if (r.policies && r.policies.length) {
      html += '<div style="margin-top:8px">';
      for (const p of r.policies) html += `<span class="summary-tag policy">${escHtml(p)}</span> `;
      html += "</div>";
    }

    // 예산/수치
    if (r.numbers && r.numbers.length) {
      html += '<div style="margin-top:8px">';
      for (const n of r.numbers) html += `<span class="summary-tag number">${escHtml(n)}</span> `;
      html += "</div>";
    }

    // Q&A
    if (r.qa_pairs && r.qa_pairs.length) {
      html += '<div style="margin-top:12px"><strong style="font-size:12px;color:var(--muted)">질의응답</strong>';
      for (const qa of r.qa_pairs) {
        html += '<div style="margin:6px 0">';
        html += `<div class="issue-raised"><strong>Q.</strong> ${escHtml(qa.question)}${refSpan(qa.q_ids)}</div>`;
        if (qa.answer) {
          html += `<div class="issue-response"><strong>A.</strong> ${escHtml(qa.answer)}${refSpan(qa.a_ids)}</div>`;
        } else {
          html += '<div style="font-size:12px;color:var(--live);margin-top:4px">답변 대기 중...</div>';
        }
        html += "</div>";
      }
      html += "</div>";
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

  // ------------------------------------------------------------ 요청자료 탭 렌더링
  function renderRequests(requests) {
    const el = $("requestsList");
    if (!requests || !requests.length) {
      el.innerHTML = "";
      $("requestsEmpty").style.display = "";
      updateTabBadge("requests", false);
      return;
    }
    $("requestsEmpty").style.display = "none";
    updateTabBadge("requests", true);

    el.innerHTML = requests
      .map((r) => {
        const statusLabel = { new: "신규", ongoing: "진행", completed: "완료" }[r.status] || r.status || "신규";
        const statusCls = r.status || "new";
        return `<div class="request-card">
          <div class="issue-header">
            <div class="request-card-title">${escHtml(r.description || "")}</div>
            <span class="request-status ${statusCls}">${statusLabel}</span>
          </div>
          <div class="request-meta">
            ${r.type ? `<span class="request-meta-item"><strong>종류</strong> ${escHtml(r.type)}</span>` : ""}
            ${r.target ? `<span class="request-meta-item"><strong>대상</strong> ${escHtml(r.target)}</span>` : ""}
            ${r.period ? `<span class="request-meta-item"><strong>기간</strong> ${escHtml(r.period)}</span>` : ""}
            ${r.deadline ? `<span class="request-meta-item"><strong>기한</strong> ${escHtml(r.deadline)}</span>` : ""}
          </div>
          ${r.sentence_ids ? `<div style="margin-top:6px">${refSpan(r.sentence_ids)}</div>` : ""}
        </div>`;
      })
      .join("");
  }

  // ------------------------------------------------------------ 지적/답변 탭 렌더링
  function renderIssues(issues) {
    const el = $("issuesList");
    if (!issues || !issues.length) {
      el.innerHTML = "";
      $("issuesEmpty").style.display = "";
      updateTabBadge("issues", false);
      return;
    }
    $("issuesEmpty").style.display = "none";

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
        return `<div class="issue-card ${status}">
          <div class="issue-header">
            <div class="issue-title">${escHtml(i.issue || "")}</div>
            <span class="issue-status-badge ${status}">${statusLabel}</span>
          </div>
          <div class="issue-raised">
            ${escHtml(i.raised_by ? `${i.raised_by} 위원` : "위원")} 지적${refSpan(i.issue_ids)}
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

  // 전역 브릿지 등록
  window._summaryBridge = { handleSummary };
})();
