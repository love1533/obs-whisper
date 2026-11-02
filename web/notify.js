// 요청자료 알림 - 새 요청이 감지되면 열려 있는 뷰어에 알린다
//   - 화면 내 토스트(항상), 소리·진동, 브라우저 알림 배너(권한 허용 시)
//   - 상단 🔔 버튼으로 켜고 끈다. 소리는 브라우저 정책상 버튼을 한 번 눌러야 풀린다.
//   - 아이폰 Safari는 홈 화면에 추가한 경우에만 배너 알림을 지원한다(토스트·진동은 동작).
(() => {
  const $ = (id) => document.getElementById(id);
  const btn = $("notifyBtn");
  const KEY = "notifyOn";
  const supported = "Notification" in window;
  let on = false;
  let audioCtx = null;

  function hasPermission() { return supported && Notification.permission === "granted"; }

  function setOn(v) {
    on = v;
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch (e) {}
    if (btn) {
      btn.setAttribute("aria-pressed", String(v));
      btn.title = v ? "요청자료 알림 켜짐 (클릭하면 끔)" : "요청자료 알림 꺼짐 (클릭하면 켬)";
      btn.querySelector("span").textContent = v ? "알림 켜짐" : "알림";
    }
  }

  // 소리: 짧은 2음 알림음 (외부 파일 없이 생성)
  function unlockAudio() {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) {}
  }
  function beep() {
    if (!audioCtx) return;
    try {
      const t0 = audioCtx.currentTime;
      [[880, 0], [1175, 0.16]].forEach(([freq, dt]) => {
        const o = audioCtx.createOscillator();
        const g = audioCtx.createGain();
        o.type = "sine"; o.frequency.value = freq;
        g.gain.setValueAtTime(0.0001, t0 + dt);
        g.gain.exponentialRampToValueAtTime(0.25, t0 + dt + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.22);
        o.connect(g).connect(audioCtx.destination);
        o.start(t0 + dt); o.stop(t0 + dt + 0.25);
      });
    } catch (e) {}
  }

  // 화면 내 토스트
  //   bodyHtml: 이미 이스케이프된 HTML(상세 알림용). ttl: 표시 시간(ms)
  function toast(title, body, onClick, opts) {
    const box = $("toastBox");
    if (!box) return;
    const o = opts || {};
    const el = document.createElement("div");
    el.className = "toast" + (o.cls ? " " + o.cls : "");
    el.innerHTML = `<div class="toast-title">📋 ${escape(title)}</div><div class="toast-body">${o.bodyHtml || escape(body)}</div>`;
    el.onclick = () => { el.remove(); if (onClick) onClick(); };
    box.appendChild(el);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, o.ttl || 8000);
  }
  function escape(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function openRequestsTab() {
    const tab = document.querySelector('.tab[data-tab="requests"]');
    if (tab) tab.click();
    window.focus();
  }

  // 새 요청자료 알림 (summary.js에서 호출)
  //   화면 토스트는 상세하게(요청 요약 + 발언 원문 + 종류·대상·기간·기한), 약 13초 유지
  function notifyRequest(req) {
    const who = req.raised_by ? `${req.raised_by} 위원` : "위원";
    const title = `새 요청자료 · ${who}` + (req.target ? ` → ${req.target}` : "");
    const desc = req.description || req.original_text || "";
    const quote = req.original_text && req.original_text !== desc ? req.original_text.slice(0, 240) + (req.original_text.length > 240 ? "…" : "") : "";
    const meta = [
      req.type ? `종류 ${req.type}` : "",
      req.period ? `기간 ${req.period}` : "",
      req.deadline ? `기한 ${req.deadline}` : "",
      req.requested_at ? new Date(req.requested_at * 1000).toLocaleTimeString("ko-KR", { hour12: false }) + " 발언" : "",
    ].filter(Boolean);
    const bodyHtml =
      `<div class="toast-desc">${escape(desc)}</div>` +
      (quote ? `<div class="toast-quote">“${escape(quote)}”</div>` : "") +
      (meta.length ? `<div class="toast-meta">${meta.map(escape).join(" · ")}</div>` : "") +
      `<div class="toast-hint">클릭하면 요청자료 탭으로 이동</div>`;
    toast(title, desc, openRequestsTab, { bodyHtml, ttl: 13000, cls: "toast-request" });
    const body = desc + (quote ? `\n“${quote.slice(0, 120)}${quote.length > 120 ? "…" : ""}”` : "");
    if (!on) return;
    beep();
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    if (hasPermission()) {
      try {
        const n = new Notification(title, { body, icon: "mascot-alert.png", tag: "gcgf-request-" + (req.description || "").slice(0, 40), renotify: true });
        n.onclick = () => { openRequestsTab(); n.close(); };
      } catch (e) {}
    }
  }

  // 버튼: 켜기/끄기 (켤 때 권한 요청 + 소리 잠금 해제)
  if (btn) {
    btn.onclick = async () => {
      if (on) { setOn(false); toast("알림 끔", "요청자료 알림을 껐습니다."); return; }
      unlockAudio();
      let granted = hasPermission();
      if (supported && Notification.permission === "default") {
        try { granted = (await Notification.requestPermission()) === "granted"; } catch (e) {}
      }
      setOn(true);
      beep();
      if (granted) {
        toast("알림 켜짐", "새 요청자료가 감지되면 배너·소리로 알려 드립니다.");
      } else if (!supported) {
        toast("알림 켜짐", "이 브라우저는 배너 알림을 지원하지 않습니다. 화면 알림과 진동으로 알려 드립니다. (아이폰: 홈 화면에 추가하면 배너도 가능)");
      } else {
        toast("알림 켜짐 (배너 없음)", "브라우저 알림 권한이 거부되어 화면 알림과 소리만 울립니다.");
      }
    };
  }

  // 이전에 켜 두었으면 복원 (소리는 첫 터치/클릭 후 풀림)
  try { if (localStorage.getItem(KEY) === "1") setOn(true); else setOn(false); } catch (e) { setOn(false); }
  document.addEventListener("pointerdown", unlockAudio, { once: true });

  window._notify = { request: notifyRequest };
})();
