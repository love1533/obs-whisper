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
    partial(text) { showPartial(text); },
    sentence(rec) { removePartial(); addSentence(rec, true); tick(); },
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

    async function loadAll() {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const [{ data: sessions }, { data: rows }] = await Promise.all([
        db.from("broadcast_sessions").select("*").eq("committee", committee)
          .order("started_at", { ascending: false }).limit(1),
        db.from("transcripts").select("id,session_id,start_s,pts,broadcast_time,text,html")
          .eq("committee", committee).gte("broadcast_time", today.toISOString())
          .order("broadcast_time", { ascending: false }).limit(500),
      ]);
      const latest = sessions && sessions[0];
      handlers.hello({
        status: latest ? latest.status : "방송 대기 중",
        session: toSession(latest),
        sentences: (rows || []).reverse().map(toRec),
      });
    }

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
  // YouTube URL → 팝업 창으로 영상, 메인은 자막 전용
  const streamUrl = cfg.streamUrl || "";
  const ytMatch = streamUrl.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/);
  if (ytMatch) {
    const ytUrl = `https://www.youtube.com/watch?v=${ytMatch[1]}`;
    const wrap = $("videoWrap");
    wrap.querySelector("video").style.display = "none";
    const offair = $("offair");
    offair.hidden = false;
    offair.innerHTML = `
      <div style="text-align:center">
        <strong style="font-size:18px">실시간 방송 자막</strong>
        <p style="margin:12px 0 16px;color:#8b95a3">영상은 별도 창에서 재생됩니다</p>
        <button id="openYt" style="background:#e5484d;color:#fff;border:none;border-radius:8px;padding:12px 24px;font-size:15px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:8px">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg>
          YouTube 영상 열기
        </button>
      </div>`;
    $("openYt").onclick = () => window.open(ytUrl, "ytLive", "width=800,height=500,left=0,top=0");
    $("liveBadge").classList.add("on");
    $("liveBadge").textContent = "LIVE";
    $("soundBtn").hidden = true;
    $("liveBtn").hidden = true;
    $("ccBtn").hidden = true;
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
