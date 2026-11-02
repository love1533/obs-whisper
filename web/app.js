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

  // ------------------------------------------------------------ 영상 창 (기본: 왼쪽 고정 / 플로팅: 떠 있는 PIP)
  (() => {
    const stage = $("pipStage");
    const bar = $("pipBar");
    const scaleBox = $("pipScale");
    const minBtn = $("pipMinBtn");
    const floatBtn = $("pipFloatBtn");
    if (!stage || !bar) return;

    // 플로팅 창 크기: 기본 너비 × 배율(1~4). 화면보다 커지지 않게 제한
    const BASE_W = 300;
    const SCALES = [1, 2, 3, 4];
    let scale = 1;
    const pipWidth = () => Math.round(Math.min(BASE_W * scale, window.innerWidth - 32));
    let minimized = false;
    let floating = false;
    let saved = {};

    // localStorage 복원 (위치/크기는 플로팅일 때만 적용)
    try {
      saved = JSON.parse(localStorage.getItem("pip") || "{}");
      if (SCALES.includes(saved.scale)) scale = saved.scale;
      minimized = !!saved.min;
      // 데스크톱: 왼쪽 고정으로 시작. 모바일: 기본 플로팅(고정은 버튼으로 전환, 영상만 압축 표시)
      floating = window.matchMedia("(max-width: 760px)").matches;
    } catch (e) {}

    function savePip() {
      try {
        const r = stage.getBoundingClientRect();
        const pos = floating ? { x: Math.round(r.left), y: Math.round(r.top) } : { x: saved.x, y: saved.y };
        saved = { ...pos, scale, min: minimized, float: floating };
        localStorage.setItem("pip", JSON.stringify(saved));
      } catch (e) {}
    }

    // 고정(dock) ↔ 플로팅(PIP) 전환
    let applyMode = function () {
      document.body.classList.toggle("pip-float", floating);
      if (floatBtn) {
        floatBtn.textContent = floating ? "고정" : "플로팅";
        floatBtn.title = floating ? "영상을 왼쪽에 고정" : "영상을 떠 있는 창으로";
      }
      scaleBox.hidden = !floating || window.matchMedia("(max-width: 760px)").matches;
      scaleBox.querySelectorAll("[data-scale]").forEach((b) => b.classList.toggle("active", Number(b.dataset.scale) === scale));
      minBtn.hidden = !floating;
      const st = $("splitToggle");
      if (st) {
        st.textContent = floating ? "▶" : "◀";
        st.title = floating ? "영상 영역 펼치기 (왼쪽에 고정)" : "영상 영역 접기 (자막 넓게, 영상은 떠 있는 창으로)";
      }
      if (floating) {
        // 모바일은 CSS가 정한 작은 창 크기를 그대로 쓴다(인라인 너비 미적용)
        const mobile = window.matchMedia("(max-width: 760px)").matches;
        stage.style.width = mobile ? "" : pipWidth() + "px";
        if (saved.x != null) {
          stage.style.left = saved.x + "px"; stage.style.top = saved.y + "px";
          stage.style.right = "auto"; stage.style.bottom = "auto";
        }
        stage.classList.toggle("pip-min", minimized);
        minBtn.textContent = minimized ? "펼치기" : "접기";
        clampPos();
      } else {
        stage.classList.remove("pip-min");
        stage.style.left = stage.style.top = stage.style.right = stage.style.bottom = stage.style.width = "";
      }
    };

    const toggleFloat = () => { floating = !floating; applyMode(); savePip(); };
    if (floatBtn) floatBtn.onclick = toggleFloat;

    // 분할선: 접기/펼치기 버튼(= 플로팅 전환) + 드래그로 왼쪽 열 너비 조절
    const leftCol = $("leftCol");
    const splitter = $("splitter");
    const splitToggle = $("splitToggle");
    const LEFT_MIN = 280, LEFT_MAX_RATIO = 0.6;
    const clampLeft = (w) => {
      const layoutW = (leftCol && leftCol.parentElement ? leftCol.parentElement.clientWidth : window.innerWidth) || window.innerWidth;
      return Math.round(Math.max(LEFT_MIN, Math.min(w, layoutW * LEFT_MAX_RATIO)));
    };
    try {
      const w = parseInt(localStorage.getItem("leftW") || "", 10);
      if (leftCol && w > 0) leftCol.style.flexBasis = clampLeft(w) + "px";
    } catch (e) {}
    if (splitToggle) splitToggle.onclick = toggleFloat;
    if (splitter && leftCol) {
      let startX = 0, startW = 0;
      splitter.addEventListener("pointerdown", (e) => {
        if (floating || e.target.closest(".split-btn")) return;
        e.preventDefault();
        startX = e.clientX;
        startW = leftCol.getBoundingClientRect().width;
        document.body.classList.add("resizing");
        splitter.setPointerCapture(e.pointerId);
      });
      splitter.addEventListener("pointermove", (e) => {
        if (!splitter.hasPointerCapture(e.pointerId)) return;
        leftCol.style.flexBasis = clampLeft(startW + (e.clientX - startX)) + "px";
      });
      const endDrag = (e) => {
        if (!splitter.hasPointerCapture(e.pointerId)) return;
        splitter.releasePointerCapture(e.pointerId);
        document.body.classList.remove("resizing");
        try { localStorage.setItem("leftW", String(parseInt(leftCol.style.flexBasis, 10) || "")); } catch (err) {}
      };
      splitter.addEventListener("pointerup", endDrag);
      splitter.addEventListener("pointercancel", endDrag);
      // 더블클릭: 기본 너비로
      splitter.addEventListener("dblclick", () => {
        leftCol.style.flexBasis = "";
        try { localStorage.removeItem("leftW"); } catch (err) {}
      });
    }

    function clampPos() {
      if (!floating) return;
      const r = stage.getBoundingClientRect();
      const x = Math.max(0, Math.min(r.left, window.innerWidth - r.width));
      const y = Math.max(0, Math.min(r.top, window.innerHeight - r.height));
      stage.style.left = x + "px";
      stage.style.top = y + "px";
      stage.style.right = "auto";
      stage.style.bottom = "auto";
    }

    // 배율 버튼 (1×~4×)
    scaleBox.querySelectorAll("[data-scale]").forEach((b) => {
      b.onclick = () => {
        scale = Number(b.dataset.scale);
        scaleBox.querySelectorAll("[data-scale]").forEach((x) => x.classList.toggle("active", x === b));
        stage.style.width = pipWidth() + "px";
        clampPos();
        savePip();
      };
    });

    // 접기/펼치기
    minBtn.onclick = () => {
      minimized = !minimized;
      stage.classList.toggle("pip-min", minimized);
      minBtn.textContent = minimized ? "펼치기" : "접기";
      if (!minimized) stage.style.width = pipWidth() + "px";
      clampPos();
      savePip();
    };

    // 드래그 이동
    let dragX = 0, dragY = 0;
    bar.addEventListener("pointerdown", (e) => {
      if (!floating || e.target.closest(".pip-btn")) return;
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

    // 창 리사이즈 시 화면 밖 방지 (배율 창이 화면보다 커졌으면 줄임)
    window.addEventListener("resize", () => {
      if (floating && !minimized && !window.matchMedia("(max-width: 760px)").matches) stage.style.width = pipWidth() + "px";
      clampPos();
    });

    applyMode();

    // 플로팅 영상 높이를 CSS 변수로 전달 (모바일에서 자막 하단 여백·버튼 위치 계산용)
    const setPipH = () => {
      const h = floating ? Math.round(stage.getBoundingClientRect().height) : 0;
      document.documentElement.style.setProperty("--pip-h", h + "px");
    };
    if (window.ResizeObserver) new ResizeObserver(setPipH).observe(stage);
    window.addEventListener("resize", setPipH);
    const _applyMode = applyMode;
    applyMode = () => { _applyMode(); setPipH(); };
    setPipH();
  })();

  // ------------------------------------------------------------ 모바일 스크롤 위치 표시
  (() => {
    const scrollers = document.querySelectorAll(".transcript-body, .tab-panel-scroll");
    scrollers.forEach((sc) => {
      const rail = document.createElement("div");
      rail.className = "scroll-rail";
      const thumb = document.createElement("div");
      thumb.className = "scroll-thumb";
      rail.appendChild(thumb);
      sc.parentElement.appendChild(rail);
      const update = () => {
        if (!rail.offsetParent) return;
        const railH = rail.clientHeight;
        const ratio = sc.clientHeight / Math.max(sc.scrollHeight, 1);
        const th = Math.max(24, Math.round(railH * ratio));
        const maxTop = railH - th;
        const prog = sc.scrollHeight > sc.clientHeight ? sc.scrollTop / (sc.scrollHeight - sc.clientHeight) : 1;
        thumb.style.height = th + "px";
        thumb.style.top = Math.round(maxTop * Math.min(1, Math.max(0, prog))) + "px";
        rail.style.opacity = ratio >= 1 ? "0" : "1";
      };
      sc.addEventListener("scroll", update, { passive: true });
      window.addEventListener("resize", update);
      if (window.ResizeObserver) new ResizeObserver(update).observe(sc);
      if (window.MutationObserver) new MutationObserver(update).observe(sc, { childList: true, subtree: true });
      update();
    });
  })();

  const viewerDelay = cfg.viewerDelay || 15;
  let videoMode = cfg.videoMode || "auto";
  // YouTube 라이브 임베드는 https 공개 도메인에서만 재생된다(Referer 검사). 로컬(http/IP)에서는
  // 앱이 중계한 HLS로 대신 재생한다(서버 VIDEO_MODE=auto|relay 필요).
  const ytUrl = /(?:youtube\.com\/|youtu\.be\/|youtube:\/\/)/.test(cfg.streamUrl || "");
  const ytEmbedOk = location.protocol === "https:";
  if (ytUrl && !ytEmbedOk) videoMode = "relay";
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
    $("sourceHint").textContent = kind === "relay" ? (ytUrl ? "YouTube (앱 중계)" : "앱 중계 영상") : "도의회 원본 영상";

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
    list.classList.remove("filtering");
    list.querySelectorAll(".para.para-filtered").forEach((p) => p.classList.remove("para-filtered"));
  }

  // 문단에 일치 문장이 하나도 없으면 숨김 (검색 중에만)
  function updateParaFilter(para) {
    if (!para || !searchQuery) return;
    para.classList.toggle("para-filtered", !para.querySelector(".s.search-hit"));
  }

  // 검색어가 있으면 일치하는 문장(과 그 문단)만 보여 준다
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
    list.classList.add("filtering");
    list.querySelectorAll(".para").forEach(updateParaFilter);
    $("searchCount").textContent = hits ? `${hits}건 · 일치 문장만 표시` : "일치하는 발언 없음";
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

    // 검색어가 있으면 즉시 매치 확인 (새 문장도 필터에 반영)
    if (searchQuery) {
      if (rec.text.toLowerCase().includes(searchQuery)) el.classList.add("search-hit");
      updateParaFilter(lastPara.el);
      const n = list.querySelectorAll(".s.search-hit").length;
      $("searchCount").textContent = n ? `${n}건 · 일치 문장만 표시` : "일치하는 발언 없음";
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

  // 따라가기 제어
  //  - 코드가 움직인 스크롤(부드러운 스크롤 애니메이션 포함)로는 따라가기가 꺼지지 않는다.
  //  - 사용자가 휠/터치/키보드로 위로 올렸을 때만 멈추고 "최신 발언으로" 버튼을 보여 준다.
  //  - 다시 아래 끝까지 내리거나, 버튼을 누르거나, 잠시(AUTO_RESUME_S) 손을 떼면 자동으로 재개한다.
  const AUTO_RESUME_S = 20;
  let resumeTimer = null;
  function setFollow(on) {
    followLatest = on;
    $("jumpBtn").hidden = on;
    clearTimeout(resumeTimer);
    if (!on) resumeTimer = setTimeout(() => { setFollow(true); scrollToLatest(); }, AUTO_RESUME_S * 1000);
  }
  function userScrolledUp() {
    // 사용자가 직접 위로 올리는 중: 일단 멈춘다. 끝까지 다시 내리면 scroll 핸들러가 즉시 재개한다.
    if (followLatest) setFollow(false);
  }
  list.addEventListener("wheel", (e) => { if (e.deltaY < 0) userScrolledUp(); }, { passive: true });
  let touchY = null;
  list.addEventListener("touchstart", (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  list.addEventListener("touchmove", (e) => {
    const y = e.touches[0].clientY;
    if (touchY != null && y - touchY > 8) userScrolledUp();   // 손가락을 아래로 = 내용이 위로
    touchY = y;
  }, { passive: true });
  list.addEventListener("keydown", (e) => {
    if (["ArrowUp", "PageUp", "Home"].includes(e.key)) userScrolledUp();
  });
  list.addEventListener("scroll", () => {
    if (autoScroll) return;
    // 사용자가 끝까지 다시 내리면 즉시 재개. 위로 올린 뒤에는 타이머가 재개.
    if (!followLatest && nearBottom()) setFollow(true);
    else if (!followLatest) { clearTimeout(resumeTimer); resumeTimer = setTimeout(() => { setFollow(true); scrollToLatest(); }, AUTO_RESUME_S * 1000); }
  });
  $("jumpBtn").onclick = () => {
    setFollow(true);
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
      handlers.summary(buildSummaryPayload(summaries, requests || [], issues || []));
      $("liveBadge").classList.remove("on");
      $("liveBadge").textContent = "과거 기록";
    }

    // ai_summaries 행 → 뷰어용 요약 데이터. 같은 시간대(hour)가 여러 행이면 가장 최근 행만 쓴다.
    let lastLists = { requests: [], issues: [] };
    function buildSummaryPayload(summaries, requests, issues) {
      const parsed = (summaries || []).map(s => {
        const sj = typeof s.summary_json === "string" ? JSON.parse(s.summary_json) : (s.summary_json || {});
        return { ...sj, window_num: s.window_num, current_topic: s.current_topic || sj.current_topic || "", created_at: s.created_at, full_summary: s.full_summary || sj.full_summary || "" };
      });
      const byHour = new Map();
      for (const h of parsed) {
        const key = h.hour || `#${h.window_num}`;
        const prev = byHour.get(key);
        if (!prev || Date.parse(h.created_at) >= Date.parse(prev.created_at)) byHour.set(key, h);
      }
      const hourly = [...byHour.values()].sort((a, b) => (a.hour || "").localeCompare(b.hour || "") || a.window_num - b.window_num);
      const last = hourly.length ? hourly[hourly.length - 1] : null;
      const lastWithFull = [...hourly].reverse().find(h => h.full_summary);
      if (requests) lastLists.requests = requests.map(toRequest);
      if (issues) lastLists.issues = issues.map(toIssue);
      return {
        hourly,
        current_topic: last ? last.current_topic : "",
        recent: last ? (last.recent || null) : null,
        topics: last ? (last.topics || []) : [],
        past: last ? (last.past || []) : [],
        full_summary: lastWithFull ? lastWithFull.full_summary : "",
        updated_at: last ? (last.updated_at || Date.parse(last.created_at) / 1000) : 0,
        next_update_at: last ? last.next_update_at : undefined,
        requests: lastLists.requests,
        issues: lastLists.issues,
      };
    }

    // Realtime으로 ai_summaries가 바뀌면(5분마다 갱신) 오늘 요약을 다시 읽어 그린다
    let refreshTimer = null;
    function scheduleSummaryRefresh() {
      if ($("sessionSelect") && $("sessionSelect").value !== "live") return;  // 과거 날짜 조회 중이면 무시
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(async () => {
        try {
          const todayRange = kstDateRange(kstToday());
          const { data: summaries } = await db.from("ai_summaries").select("*")
            .eq("committee", committee).gte("created_at", todayRange.start)
            .order("window_num", { ascending: true });
          handlers.summary(buildSummaryPayload(summaries, null, null));
        } catch (e) { console.error("요약 갱신 실패", e); }
      }, 800);
    }

    function kstToday() {
      const kstNow = new Date(Date.now() + 9 * 60 * 60 * 1000);
      return `${kstNow.getUTCFullYear()}-${String(kstNow.getUTCMonth()+1).padStart(2,"0")}-${String(kstNow.getUTCDate()).padStart(2,"0")}`;
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
      handlers.summary(buildSummaryPayload(summaries, requests || [], issues || []));
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
      .on("postgres_changes",
        { event: "*", schema: "public", table: "ai_summaries", filter: `committee=eq.${committee}` },
        () => scheduleSummaryRefresh())
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
  if ((ytMatch || ytChannel) && ytEmbedOk) {
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
  // 접속 경로 선택
  //   - config.js에 Supabase가 있으면(정적 호스팅: Vercel 등) Supabase Realtime으로 받는다
  //   - 없으면 앱이 직접 띄운 페이지로 보고 같은 호스트의 WebSocket(/ws)에 붙는다
  //   - 정적 호스팅인데 config.js가 빠진 경우는 재연결을 반복하지 말고 원인을 보여 준다
  const localHost = /^(localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$/.test(location.hostname);
  if (cfg.supabaseUrl && cfg.supabaseAnonKey) {
    connectSupabase().catch((e) => {
      console.error(e);
      $("status").textContent = "Supabase 연결 실패";
    });
  } else if (localHost || location.protocol === "file:") {
    connectWebSocket();
  } else {
    console.error("config.js가 없거나 Supabase 설정이 비어 있습니다. web/gen_config.sh로 생성 후 다시 배포하세요.");
    $("status").textContent = "뷰어 설정 누락 (config.js)";
  }
})();
