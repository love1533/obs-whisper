// 방송 영상 + 싱크 맞춘 발언 패널
//
// 영상: 도의회 원본 HLS를 브라우저가 직접 재생한다(videoMode=auto/direct).
//       원본 재생이 막히면(CORS 등) 앱이 중계한 HLS로 자동 전환한다(auto).
// 싱크: 서버는 각 문장에 "원본 스트림 PTS(초)"를 붙여 보낸다. hls.js가 알려 주는
//       initPTS로 현재 재생 위치도 원본 PTS로 바꿀 수 있으므로, 재생 위치가 문장의
//       PTS를 지날 때 그 문장을 띄운다. 영상은 인식 시간만큼(viewerDelay) 라이브보다
//       늦게 재생해서 문장이 영상보다 먼저 도착하도록 한다.
// 문장 수신: 앱이 직접 띄운 페이지면 WebSocket, config.js에 Supabase가 설정되어
//       있으면(정적 호스팅) Supabase Realtime으로 받는다.

(() => {
  const cfg = window.VIEWER_CONFIG || {};
  const PARA_GAP_S = 1.5;       // 이만큼 쉬었다가 다시 말하면 새 문단
  const PARA_MAX_SENTENCES = 5; // 문단이 너무 길어지지 않도록
  const CHARS_PER_S = 6;        // 한국어 발화 속도 추정 (앞 문장이 끝난 시각 계산용)
  const LOWER_THIRD_HOLD_S = 9; // 하단 자막 유지 시간
  const PTS_WRAP_S = 2 ** 33 / 90000; // MPEG-TS PTS는 약 26.5시간마다 0으로 돌아감

  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const list = $("list");

  // ------------------------------------------------------------ PIP 영상 창
  (() => {
    const stage = $("pipStage");
    const bar = $("pipBar");
    const sizeBtn = $("pipSizeBtn");
    const minBtn = $("pipMinBtn");
    if (!stage || !bar) return;

    const SIZES = [380, 520, 280];
    let sizeIdx = 0;
    let minimized = false;

    // localStorage 복원
    try {
      const s = JSON.parse(localStorage.getItem("pip") || "{}");
      if (s.x != null) { stage.style.left = s.x + "px"; stage.style.top = s.y + "px"; stage.style.right = "auto"; stage.style.bottom = "auto"; }
      if (s.size != null) { sizeIdx = SIZES.indexOf(s.size); if (sizeIdx < 0) sizeIdx = 0; stage.style.width = SIZES[sizeIdx] + "px"; }
      if (s.min) { minimized = true; stage.classList.add("pip-min"); minBtn.textContent = "펼치기"; }
    } catch (e) {}

    function savePip() {
      try {
        const r = stage.getBoundingClientRect();
        localStorage.setItem("pip", JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top), size: SIZES[sizeIdx], min: minimized }));
      } catch (e) {}
    }

    function clampPos() {
      const r = stage.getBoundingClientRect();
      const x = Math.max(0, Math.min(r.left, window.innerWidth - r.width));
      const y = Math.max(0, Math.min(r.top, window.innerHeight - r.height));
      stage.style.left = x + "px";
      stage.style.top = y + "px";
      stage.style.right = "auto";
      stage.style.bottom = "auto";
    }

    // 크기 순환
    sizeBtn.onclick = () => {
      sizeIdx = (sizeIdx + 1) % SIZES.length;
      stage.style.width = SIZES[sizeIdx] + "px";
      clampPos();
      savePip();
    };

    // 접기/펼치기
    minBtn.onclick = () => {
      minimized = !minimized;
      stage.classList.toggle("pip-min", minimized);
      minBtn.textContent = minimized ? "펼치기" : "접기";
      if (!minimized) stage.style.width = SIZES[sizeIdx] + "px";
      clampPos();
      savePip();
    };

    // 드래그 이동
    let dragX = 0, dragY = 0;
    bar.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".pip-btn")) return;
      e.preventDefault();
      const r = stage.getBoundingClientRect();
      dragX = e.clientX - r.left;
      dragY = e.clientY - r.top;
      stage.style.left = r.left + "px";
      stage.style.top = r.top + "px";
      stage.style.right = "auto";
      stage.style.bottom = "auto";
      bar.setPointerCapture(e.pointerId);
    });
    bar.addEventListener("pointermove", (e) => {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      const x = Math.max(0, Math.min(e.clientX - dragX, window.innerWidth - stage.offsetWidth));
      const y = Math.max(0, Math.min(e.clientY - dragY, window.innerHeight - stage.offsetHeight));
      stage.style.left = x + "px";
      stage.style.top = y + "px";
    });
    bar.addEventListener("pointerup", (e) => {
      if (bar.hasPointerCapture(e.pointerId)) {
        bar.releasePointerCapture(e.pointerId);
        savePip();
      }
    });

    // 창 리사이즈 시 화면 밖 방지
    window.addEventListener("resize", clampPos);
  })();

  const viewerDelay = cfg.viewerDelay || 15;
  const videoMode = cfg.videoMode || "auto";
  let hls = null;
  let session = null;          // {id, relay}
  let source = null;           // "direct" | "relay"
  let fragLoaded = false;      // 현재 소스에서 영상 조각을 하나라도 받았는지
  const initPts = {};          // 불연속 구간(cc)별 hls.js initPTS
  let curCc = 0;
  let ccOn = true;
  let playedOnce = false;
  let followLatest = true;
  let autoScroll = false;      // 코드가 스크롤 중일 때는 사용자 스크롤로 보지 않음
  let sessionLoadedAt = 0;
  let current = null;          // 현재 하이라이트된 문장 엘리먼트
  let lastPara = null;         // {el, session, lastEnd, lastTime, count}
  const items = [];            // {rec, el}
  const seen = new Set();      // 중복 수신 방지 (재연결 시 다시 불러올 때)
  let searchQuery = "";        // 현재 검색어

  // ------------------------------------------------------------ 시각 계산
  const fmtTime = (epochSec) =>
    new Date(epochSec * 1000).toLocaleTimeString("ko-KR", { hour12: false });

  // 현재 재생 위치를 원본 스트림 PTS(초)로 (모르면 null)
  function streamPts() {
    const base = initPts[curCc];
    if (!hls || !base || video.readyState < 2) return null;
    return video.currentTime + base.baseTime / base.timescale;
  }

  // a - b (PTS 순환을 고려한 차이, 초)
  function ptsDiff(a, b) {
    let d = (a - b) % PTS_WRAP_S;
    if (d > PTS_WRAP_S / 2) d -= PTS_WRAP_S;
    if (d < -PTS_WRAP_S / 2) d += PTS_WRAP_S;
    return d;
  }

  // 이 문장을 영상에 맞춰 늦게 보여줘야 하는지
  const isSynced = (rec) => session && rec.session === session.id && rec.pts != null;

  // ------------------------------------------------------------ 플레이어
  function relayUrl() {
    return session && session.relay ? (cfg.relayBase || "") + session.relay : null;
  }

  function loadSession(s) {
    session = s && s.id ? s : null;
    sessionLoadedAt = Date.now();
    const on = !!session;
    $("offair").hidden = on;
    $("liveBadge").classList.toggle("on", on);
    $("liveBadge").textContent = on ? "LIVE" : "OFF AIR";
    if (!on) { stopVideo(); return; }
    startVideo(videoMode === "relay" ? "relay" : "direct");
  }

  function stopVideo() {
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute("src");
    video.load();
    source = null;
  }

  function startVideo(kind) {
    const url = kind === "relay" ? relayUrl() : cfg.streamUrl;
    stopVideo();
    if (!url) return;
    source = kind;
    playedOnce = false;
    fragLoaded = false;
    for (const k of Object.keys(initPts)) delete initPts[k];
    $("sourceHint").textContent = kind === "relay" ? "앱 중계 영상" : "도의회 원본 영상";

    if (!(window.Hls && Hls.isSupported())) {
      video.src = url;
      video.play().catch(() => {});
      return;
    }
    hls = new Hls({
      liveSyncDuration: viewerDelay,
      liveMaxLatencyDuration: viewerDelay + 30,
      maxLiveSyncPlaybackRate: 1,
      manifestLoadingMaxRetry: kind === "direct" && videoMode === "auto" ? 1 : 20,
      levelLoadingMaxRetry: 20,
    });
    hls.on(Hls.Events.INIT_PTS_FOUND, (_e, d) => {
      initPts[d.frag.cc] = { baseTime: d.initPTS, timescale: d.timescale };
    });
    hls.on(Hls.Events.FRAG_CHANGED, (_e, d) => { curCc = d.frag.cc; });
    hls.on(Hls.Events.FRAG_LOADED, () => { fragLoaded = true; });
    const player = hls;
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return;
      const restart = (nextKind, delay) => setTimeout(() => {
        if (hls === player && session) startVideo(nextKind);
      }, delay);
      if (source === "direct" && !fragLoaded && videoMode === "auto" && relayUrl()) {
        console.warn("원본 재생 실패, 앱 중계로 전환:", data.details);
        restart("relay", 0);
      } else if (data.details && data.details.startsWith("manifest")) {
        restart(source, 3000);
      } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad();
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
      }
    });
    hls.loadSource(url);
    hls.attachMedia(video);
    video.play().catch(() => {});
  }

  video.addEventListener("playing", () => { playedOnce = true; });

  // ------------------------------------------------------------ 컨트롤
  $("soundBtn").onclick = () => {
    video.muted = !video.muted;
    if (!video.muted) video.play().catch(() => {});
    const iconPath = video.muted
      ? "M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"
      : "M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z";
    $("soundIcon").setAttribute("d", iconPath);
    $("soundBtn").querySelector("span").textContent = video.muted ? "소리 켜기" : "소리 끄기";
  };

  $("ccBtn").onclick = () => {
    ccOn = !ccOn;
    $("ccBtn").setAttribute("aria-pressed", String(ccOn));
    $("ccBtn").querySelector("span").textContent = ccOn ? "자막" : "자막 꺼짐";
    tick();
  };

  $("liveBtn").onclick = () => {
    if (hls && hls.liveSyncPosition != null) video.currentTime = hls.liveSyncPosition;
    else if (video.seekable.length) video.currentTime = video.seekable.end(0) - viewerDelay;
    video.play().catch(() => {});
    followLatest = true;
  };

  // 전체화면
  let isFullscreen = false;
  $("fullscreenBtn").onclick = () => {
    isFullscreen = !isFullscreen;
    document.body.classList.toggle("fs-mode", isFullscreen);
    $("fullscreenBtn").querySelector("span").textContent = isFullscreen ? "창 모드" : "전체화면";
    const iconPath = isFullscreen
      ? "M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"
      : "M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z";
    $("fullscreenBtn").querySelector("svg path").setAttribute("d", iconPath);
  };

  // ESC로 전체화면 해제
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isFullscreen) {
      isFullscreen = false;
      document.body.classList.remove("fs-mode");
      $("fullscreenBtn").querySelector("span").textContent = "전체화면";
    }
    // Ctrl+F or Cmd+F → 검색 열기
    if ((e.ctrlKey || e.metaKey) && e.key === "f") {
      e.preventDefault();
      toggleSearch(true);
    }
  });

  // ------------------------------------------------------------ 검색
  function toggleSearch(forceOpen) {
    const bar = $("searchBar");
    const open = forceOpen != null ? forceOpen : bar.hidden;
    bar.hidden = !open;
    if (open) {
      $("searchInput").focus();
    } else {
      $("searchInput").value = "";
      searchQuery = "";
      clearSearchHighlights();
      $("searchCount").textContent = "";
    }
  }

  $("searchToggle").onclick = () => toggleSearch();
  $("searchClear").onclick = () => toggleSearch(false);

  $("searchInput").addEventListener("input", (e) => {
    searchQuery = e.target.value.trim().toLowerCase();
    applySearch();
  });

  function clearSearchHighlights() {
    for (const it of items) it.el.classList.remove("search-hit");
  }

  function applySearch() {
    clearSearchHighlights();
    if (!searchQuery) { $("searchCount").textContent = ""; return; }
    let hits = 0;
    for (const it of items) {
      if (it.rec.text.toLowerCase().includes(searchQuery)) {
        it.el.classList.add("search-hit");
        hits++;
      }
    }
    $("searchCount").textContent = `${hits}건`;
  }

  // ------------------------------------------------------------ 다운로드
  $("downloadBtn").onclick = () => {
    if (!items.length) return;
    let text = "";
    let lastSession = null;
    for (const it of items) {
      if (it.rec.session !== lastSession) {
        if (lastSession != null) text += "\n---\n\n";
        lastSession = it.rec.session;
      }
      const stamp = fmtTime(it.rec.broadcast_time);
      text += `[${stamp}] ${it.rec.text}\n`;
    }
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const today = new Date().toISOString().slice(0, 10);
    a.download = `회의록_${today}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ------------------------------------------------------------ 패널
  function resetList() {
    list.innerHTML = "";
    items.length = 0;
    seen.clear();
    lastPara = null;
    current = null;
  }

  function addSentence(rec, fresh) {
    if (seen.has(rec.id)) return;
    seen.add(rec.id);

    const gapNewPara =
      !lastPara ||
      lastPara.session !== rec.session ||
      lastPara.count >= PARA_MAX_SENTENCES ||
      (rec.start != null && lastPara.lastEnd != null && rec.start - lastPara.lastEnd > PARA_GAP_S) ||
      (rec.start == null && rec.broadcast_time - lastPara.lastTime > 15);

    if (lastPara && lastPara.session !== rec.session) {
      const br = document.createElement("div");
      br.className = "session-break";
      br.textContent = `— ${fmtTime(rec.broadcast_time)} 방송 재개 —`;
      list.appendChild(br);
    }
    if (gapNewPara) {
      const el = document.createElement("div");
      el.className = "para empty";
      const t = document.createElement("div");
      t.className = "para-time";
      t.textContent = fmtTime(rec.broadcast_time);
      el.appendChild(t);
      list.appendChild(el);
      lastPara = { el, session: rec.session, count: 0 };
    }
    lastPara.lastEnd = rec.start == null ? null : rec.start + rec.text.length / CHARS_PER_S;
    lastPara.lastTime = rec.broadcast_time;
    lastPara.count += 1;

    const el = document.createElement("span");
    el.className = "s pending" + (fresh ? " fresh" : "");
    el.innerHTML = rec.html + " ";
    el.title = `${fmtTime(rec.broadcast_time)} · 클릭하면 이 발언으로 이동`;
    el.onclick = () => seekTo(rec);
    lastPara.el.appendChild(el);
    items.push({ rec, el });
    $("count").textContent = `${items.length}문장`;
    const note = list.querySelector(".empty-note");
    if (note) note.remove();

    // 검색어가 있으면 즉시 매치 확인
    if (searchQuery && rec.text.toLowerCase().includes(searchQuery)) {
      el.classList.add("search-hit");
    }
  }

  function seekTo(rec) {
    const t = streamPts();
    if (!isSynced(rec) || t == null) return;
    video.currentTime = Math.max(0, video.currentTime + ptsDiff(rec.pts, t) - 0.5);
    video.play().catch(() => {});
  }

  function reveal(item) {
    item.el.classList.remove("pending");
    item.el.parentElement.classList.remove("empty");
  }

  function nearBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  }

  function scrollToLatest() {
    autoScroll = true;
    list.scrollTop = list.scrollHeight;
    requestAnimationFrame(() => { autoScroll = false; });
  }

  list.addEventListener("scroll", () => {
    if (autoScroll) return;
    followLatest = nearBottom();
    $("jumpBtn").hidden = followLatest;
  });
  $("jumpBtn").onclick = () => {
    followLatest = true;
    $("jumpBtn").hidden = true;
    scrollToLatest();
  };

  // 0.25초마다: 재생 위치에 도달한 문장 공개 + 현재 발언 하이라이트
  function tick() {
    const t = streamPts();
    const waitForVideo = (playedOnce && t != null) || Date.now() - sessionLoadedAt < 6000;
    let revealed = false;
    let now = null;
    let nowAge = 0;

    for (const it of items) {
      const pending = it.el.classList.contains("pending");
      if (!isSynced(it.rec) || !waitForVideo) {
        if (pending) { reveal(it); revealed = true; }
        continue;
      }
      if (t == null) continue;
      const age = -ptsDiff(it.rec.pts, t);
      if (age >= -0.2) {
        if (pending) { reveal(it); revealed = true; }
        if (!now || age <= nowAge) { now = it; nowAge = age; }
      }
    }

    if (current !== (now && now.el)) {
      if (current) current.classList.remove("current");
      current = now ? now.el : null;
      if (current) current.classList.add("current");
    }

    // 하단 자막(뉴스 로어서드)
    const lt = $("lowerThird");
    if (ccOn && now && nowAge < LOWER_THIRD_HOLD_S) {
      $("ltText").innerHTML = now.rec.html;
      $("ltTime").textContent = fmtTime(now.rec.broadcast_time);
      lt.hidden = false;
    } else {
      lt.hidden = true;
    }

    if (followLatest && (revealed || !nearBottom())) scrollToLatest();
  }
  setInterval(tick, 250);

  setInterval(() => {
    $("clock").textContent = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  }, 1000);

  // ------------------------------------------------------------ 수신 처리 (공통)
  // ------------------------------------------------------------ STT 인디케이터
  const sttIndicator = document.getElementById("sttIndicator");
  let sttHideTimer = null;
  function showSttIndicator() {
    if (sttIndicator) sttIndicator.hidden = false;
    clearTimeout(sttHideTimer);
    sttHideTimer = setTimeout(() => { if (sttIndicator) sttIndicator.hidden = true; }, 5000);
  }
  function hideSttIndicator() {
    clearTimeout(sttHideTimer);
    sttHideTimer = setTimeout(() => { if (sttIndicator) sttIndicator.hidden = true; }, 2000);
  }

  // ------------------------------------------------------------ partial (타이핑) 표시
  let partialEl = null;
  let partialPara = null;

  function showPartial(text) {
    if (!text) {
      // 빈 텍스트 = partial 초기화 (문장 확정됨)
      removePartial();
      return;
    }
    if (!partialEl) {
      // 새 문단 또는 기존 문단에 partial 요소 추가
      if (!partialPara) {
        partialPara = document.createElement("div");
        partialPara.className = "para partial-para";
        const t = document.createElement("div");
        t.className = "para-time";
        t.textContent = new Date().toLocaleTimeString("ko-KR", { hour12: false });
        partialPara.appendChild(t);
        list.appendChild(partialPara);
      }
      partialEl = document.createElement("span");
      partialEl.className = "s partial-typing";
      partialPara.appendChild(partialEl);
    }
    partialEl.textContent = text;
    if (followLatest) scrollToLatest();
  }

  function removePartial() {
    if (partialEl) {
      partialEl.remove();
      partialEl = null;
    }
    if (partialPara) {
      partialPara.remove();
      partialPara = null;
    }
  }

  const handlers = {
    hello({ status, session: s, sentences }) {
      $("status").textContent = status || "";
      resetList();
      removePartial();
      sentences.forEach((r) => addSentence(r, false));
      if (!items.length) list.innerHTML = '<div class="empty-note">아직 인식된 발언이 없습니다.</div>';
      if ((session && session.id) !== (s && s.id)) loadSession(s);
      followLatest = true;
      $("jumpBtn").hidden = true;
      scrollToLatest();
      tick();
    },
    partial(text) { showPartial(text); showSttIndicator(); },
    sentence(rec) { removePartial(); addSentence(rec, true); tick(); showSttIndicator(); },
    summary(data) {
      if (window._summaryBridge) window._summaryBridge.handleSummary(data);
    },
    session(s) { if ((session && session.id) !== (s && s.id)) loadSession(s); },
    status(text) { $("status").textContent = text; },
  };

  // summary.js에서 items 배열에 접근할 수 있도록 노출
  window._appBridge = { items };

  // ------------------------------------------------------------ 수신: 앱 WebSocket
  function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "hello") {
        handlers.hello(msg);
        if (msg.summary) handlers.summary(msg.summary);
      }
      else if (msg.type === "partial") handlers.partial(msg.text);
      else if (msg.type === "sentence") handlers.sentence(msg.sentence);
      else if (msg.type === "summary") handlers.summary(msg.summary);
      else if (msg.type === "session") handlers.session(msg.session);
      else if (msg.type === "status") handlers.status(msg.status);
    };
    ws.onclose = () => {
      $("status").textContent = "서버 재연결 중...";
      setTimeout(connectWebSocket, 2000);
    };
  }

  // ------------------------------------------------------------ 수신: Supabase Realtime
  function loadScript(src) {
    return new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = ok;
      s.onerror = fail;
      document.head.appendChild(s);
    });
  }

  async function connectSupabase() {
    await loadScript("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js");
    const db = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);
    const committee = cfg.committee;

    const toRec = (row) => ({
      id: row.id,
      session: row.session_id,
      start: row.start_s,
      pts: row.pts,
      broadcast_time: Date.parse(row.broadcast_time) / 1000,
      text: row.text,
      html: row.html,
    });
    const toSession = (row) => (row && !row.ended_at ? { id: row.id, relay: row.relay_path } : null);

    // ---- 세션(날짜) 선택 UI ----
    const sessionSelect = document.getElementById("sessionSelect");
    let currentSessionFilter = null;  // null = 실시간(오늘)

    // KST 날짜 문자열 → KST 기준 하루의 시작/끝 ISO 문자열
    function kstDateRange(dateStr) {
      // dateStr = "YYYY-MM-DD"
      const start = dateStr + "T00:00:00+09:00";
      const end = dateStr + "T23:59:59.999+09:00";
      return { start: new Date(start).toISOString(), end: new Date(end).toISOString() };
    }

    async function loadSessionList() {
      if (!sessionSelect) return;
      const { data: allSessions } = await db.from("broadcast_sessions").select("id,started_at,status")
        .eq("committee", committee).order("started_at", { ascending: false }).limit(60);
      if (!allSessions || !allSessions.length) return;

      // 날짜별 그룹핑 (KST 기준)
      const dateMap = new Map();  // dateStr → { date, status, sessions: [id, ...] }
      for (const s of allSessions) {
        // KST 날짜 추출
        const d = new Date(s.started_at);
        const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
        const dateStr = `${kst.getUTCFullYear()}-${String(kst.getUTCMonth()+1).padStart(2,"0")}-${String(kst.getUTCDate()).padStart(2,"0")}`;
        if (!dateMap.has(dateStr)) {
          dateMap.set(dateStr, { date: dateStr, status: s.status || "", sessions: [] });
        }
        dateMap.get(dateStr).sessions.push(s.id);
      }

      // 기존 옵션 (실시간) 유지, 과거 날짜 추가
      for (const [dateStr, info] of dateMap) {
        const label = `${dateStr} ${info.status}`.trim();
        const opt = document.createElement("option");
        opt.value = dateStr;
        opt.textContent = label;
        sessionSelect.appendChild(opt);
      }
    }
    loadSessionList();

    if (sessionSelect) sessionSelect.onchange = () => {
      const val = sessionSelect.value;
      if (val === "live") {
        currentSessionFilter = null;
        loadAll();
      } else {
        currentSessionFilter = val;
        loadByDate(val);
      }
    };

    async function loadByDate(dateStr) {
      const range = kstDateRange(dateStr);

      // 해당 날짜의 세션 ID 목록 조회
      const { data: dateSessions } = await db.from("broadcast_sessions").select("id")
        .eq("committee", committee)
        .gte("started_at", range.start).lte("started_at", range.end);
      const sessionIds = (dateSessions || []).map(s => s.id);

      // transcripts는 broadcast_time 범위로, ai_ 테이블은 날짜 범위 + session_id in 조합
      const queries = [
        db.from("transcripts").select("id,session_id,start_s,pts,broadcast_time,text,html")
          .eq("committee", committee)
          .gte("broadcast_time", range.start).lte("broadcast_time", range.end)
          .order("broadcast_time", { ascending: false }).limit(500),
      ];

      // ai_ 테이블: created_at 범위로 조회 (session_id 필터보다 날짜 범위가 더 안정적)
      if (sessionIds.length > 0) {
        queries.push(
          db.from("ai_requests").select("*").in("session_id", sessionIds).order("created_at", { ascending: true }),
          db.from("ai_issues").select("*").in("session_id", sessionIds).order("created_at", { ascending: true }),
          db.from("ai_summaries").select("*").in("session_id", sessionIds).order("window_num", { ascending: true }),
        );
      } else {
        // 세션이 없으면 created_at 범위로 fallback
        queries.push(
          db.from("ai_requests").select("*").eq("committee", committee)
            .gte("created_at", range.start).lte("created_at", range.end).order("created_at", { ascending: true }),
          db.from("ai_issues").select("*").eq("committee", committee)
            .gte("created_at", range.start).lte("created_at", range.end).order("created_at", { ascending: true }),
          db.from("ai_summaries").select("*").eq("committee", committee)
            .gte("created_at", range.start).lte("created_at", range.end).order("window_num", { ascending: true }),
        );
      }

      const [{ data: rows }, { data: requests }, { data: issues }, { data: summaries }] = await Promise.all(queries);

      handlers.hello({
        status: "과거 기록 조회",
        session: null,
        sentences: (rows || []).reverse().map(toRec),
      });
      const allSummaries = (summaries || []).map(s => {
        const sj = typeof s.summary_json === "string" ? JSON.parse(s.summary_json) : s.summary_json;
        return { ...sj, window_num: s.window_num, current_topic: s.current_topic, created_at: s.created_at };
      });
      const lastWithFull = [...(summaries || [])].reverse().find(s => s.full_summary);
      const lastSummary = summaries && summaries.length ? summaries[summaries.length - 1] : null;
      const lastJson = lastSummary ? (typeof lastSummary.summary_json === "string" ? JSON.parse(lastSummary.summary_json) : lastSummary.summary_json) : {};
      handlers.summary({
        hourly: allSummaries,
        current_topic: lastSummary ? ((lastSummary.current_topic) || lastJson.current_topic || "") : "",
        recent: lastJson.recent || null,
        topics: lastJson.topics || [],
        past: lastJson.past || [],
        full_summary: lastWithFull ? lastWithFull.full_summary : (lastJson.full_summary || ""),
        updated_at: lastSummary ? Date.parse(lastSummary.created_at) / 1000 : 0,
        requests: (requests || []).map(toRequest),
        issues: (issues || []).map(toIssue),
      });
      $("liveBadge").classList.remove("on");
      $("liveBadge").textContent = "과거 기록";
    }

    async function loadAll() {
      // KST 기준 오늘 날짜 범위
      const now = new Date();
      const kstNow = new Date(now.getTime() + 9 * 60 * 60 * 1000);
      const todayStr = `${kstNow.getUTCFullYear()}-${String(kstNow.getUTCMonth()+1).padStart(2,"0")}-${String(kstNow.getUTCDate()).padStart(2,"0")}`;
      const todayRange = kstDateRange(todayStr);
      const [{ data: sessions }, { data: rows }, { data: requests }, { data: issues }, { data: summaries }] = await Promise.all([
        db.from("broadcast_sessions").select("*").eq("committee", committee)
          .order("started_at", { ascending: false }).limit(1),
        db.from("transcripts").select("id,session_id,start_s,pts,broadcast_time,text,html")
          .eq("committee", committee).gte("broadcast_time", todayRange.start)
          .order("broadcast_time", { ascending: false }).limit(500),
        db.from("ai_requests").select("*")
          .eq("committee", committee).gte("created_at", todayRange.start)
          .order("created_at", { ascending: true }),
        db.from("ai_issues").select("*")
          .eq("committee", committee).gte("created_at", todayRange.start)
          .order("created_at", { ascending: true }),
        db.from("ai_summaries").select("*")
          .eq("committee", committee).gte("created_at", todayRange.start)
          .order("window_num", { ascending: true }),
      ]);
      const latest = sessions && sessions[0];
      handlers.hello({
        status: latest ? latest.status : "방송 대기 중",
        session: toSession(latest),
        sentences: (rows || []).reverse().map(toRec),
      });
      // AI 분석 결과 로드
      const allSummaries = (summaries || []).map(s => {
        const sj = typeof s.summary_json === "string" ? JSON.parse(s.summary_json) : s.summary_json;
        return { ...sj, window_num: s.window_num, current_topic: s.current_topic, created_at: s.created_at };
      });
      const lastWithFull = [...(summaries || [])].reverse().find(s => s.full_summary);
      const lastSummary = summaries && summaries.length ? summaries[summaries.length - 1] : null;
      const lastJson = lastSummary ? (typeof lastSummary.summary_json === "string" ? JSON.parse(lastSummary.summary_json) : lastSummary.summary_json) : {};
      handlers.summary({
        hourly: allSummaries,
        current_topic: lastSummary ? ((lastSummary.current_topic) || lastJson.current_topic || "") : "",
        recent: lastJson.recent || null,
        topics: lastJson.topics || [],
        past: lastJson.past || [],
        full_summary: lastWithFull ? lastWithFull.full_summary : (lastJson.full_summary || ""),
        updated_at: lastSummary ? Date.parse(lastSummary.created_at) / 1000 : 0,
        requests: (requests || []).map(toRequest),
        issues: (issues || []).map(toIssue),
      });
    }

    const toRequest = (row) => ({
      description: row.description,
      original_text: row.original_text,
      raised_by: row.raised_by,
      type: row.type,
      target: row.target,
      period: row.period,
      deadline: row.deadline,
      status: row.status,
      requested_at: row.requested_at ? Date.parse(row.requested_at) / 1000 : null,
      sentence_ids: row.sentence_ids,
    });

    const toIssue = (row) => ({
      issue: row.issue,
      raised_by: row.raised_by,
      issue_ids: row.issue_ids,
      response: row.response,
      responded_by: row.responded_by,
      response_ids: row.response_ids,
      status: row.status,
      commitment: row.commitment,
    });

    db.channel(`obs-whisper-${committee}`)
      .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "transcripts", filter: `committee=eq.${committee}` },
        (p) => handlers.sentence(toRec(p.new)))
      .on("postgres_changes",
        { event: "*", schema: "public", table: "broadcast_sessions", filter: `committee=eq.${committee}` },
        (p) => {
          const row = p.new;
          if (!row || !row.id) return;
          if (row.ended_at && session && row.id !== session.id) return;
          handlers.status(row.status || "");
          handlers.session(toSession(row));
        })
      .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "ai_requests", filter: `committee=eq.${committee}` },
        (p) => {
          if (window._summaryBridge) window._summaryBridge.addRequest(toRequest(p.new));
        })
      .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "ai_issues", filter: `committee=eq.${committee}` },
        (p) => {
          if (window._summaryBridge) window._summaryBridge.addIssue(toIssue(p.new));
        })
      .subscribe((status) => {
        if (status === "SUBSCRIBED") loadAll().catch((e) => console.error(e));
        else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") $("status").textContent = "실시간 연결 재시도 중...";
      });
  }

  // ------------------------------------------------------------ 시작
  if (cfg.title) {
    $("title").textContent = cfg.title;
    document.title = `${cfg.title} · 실시간 발언`;
  }
  // YouTube/외부 영상 → iframe으로 가운데 영상 영역에 임베드
  //   - 영상 ID: https://www.youtube.com/watch?v=XXXX 또는 https://youtu.be/XXXX
  //   - 채널 라이브: youtube://channel/UCXXXX (채널의 현재 라이브 자동 감지)
  const streamUrl = cfg.streamUrl || "";
  const ytMatch = streamUrl.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/);
  const ytChannel = streamUrl.match(/youtube:\/\/channel\/([\w-]+)/);
  if (ytMatch || ytChannel) {
    const embedUrl = ytMatch
      ? `https://www.youtube.com/embed/${ytMatch[1]}?autoplay=1&mute=1&rel=0`
      : `https://www.youtube.com/embed/live_stream?channel=${ytChannel[1]}&autoplay=1&mute=1`;
    const wrap = $("videoWrap");
    wrap.querySelector("video").style.display = "none";
    $("offair").hidden = true;
    const iframe = document.createElement("iframe");
    iframe.src = embedUrl;
    iframe.allow = "autoplay; encrypted-media; picture-in-picture";
    iframe.allowFullscreen = true;
    iframe.referrerPolicy = "strict-origin-when-cross-origin"; // 글로벌 no-referrer를 iframe 단위로 오버라이드
    iframe.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:none";
    wrap.appendChild(iframe);
    $("liveBadge").classList.add("on");
    $("liveBadge").textContent = "LIVE";
    $("soundBtn").hidden = true;
    $("liveBtn").hidden = true;
    $("ccBtn").hidden = true;
    $("sourceHint").textContent = "YouTube 실시간";
  } else if (!streamUrl) {
    document.body.classList.add("no-video");
  }
  $("delayHint").textContent = document.body.classList.contains("no-video")
    ? "실시간 음성 인식"
    : `자막 싱크를 위해 방송보다 약 ${Math.round(viewerDelay)}초 늦게 재생`;
  if (cfg.supabaseUrl && cfg.supabaseAnonKey) {
    connectSupabase().catch((e) => {
      console.error(e);
      $("status").textContent = "Supabase 연결 실패";
    });
  } else {
    connectWebSocket();
  }
})();
