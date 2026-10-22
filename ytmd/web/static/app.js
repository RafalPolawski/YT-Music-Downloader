"use strict";

/* ===================================================================== helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const ICONS = {
  logo: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  playlists: '<path d="M3 6h12M3 12h12M3 18h7"/><circle cx="17" cy="17" r="3"/><path d="M20 17V5l2-1"/>',
  tracks: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
  logs: '<path d="M4 17l6-5-6-5M12 19h8"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  sync: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M3 21v-5h5"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  cloud: '<path d="M18 10h-1.3A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
};
const icon = (name, attrs = "") =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${attrs}>${ICONS[name] || ""}</svg>`;

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-YTMD": "1" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || `${res.status} ${res.statusText}`);
  return data;
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
  return `${(n / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${u[i]}`;
}
function fmtNum(n) { return (n ?? 0).toLocaleString(); }
function fmtSpan(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 86400) { const h = Math.floor(sec / 3600); const m = Math.round((sec % 3600) / 60); return m ? `${h}h ${m}m` : `${h}h`; }
  const d = Math.round(sec / 86400); return `${d} day${d === 1 ? "" : "s"}`;
}
function ago(ts) { if (!ts) return "never"; const s = Date.now() / 1000 - ts; return s < 45 ? "just now" : `${fmtSpan(s)} ago`; }
function inTime(ts) { if (!ts) return "—"; const s = ts - Date.now() / 1000; return s <= 1 ? "now" : `in ${fmtSpan(s)}`; }
function clock(ts) { return ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—"; }
function hm(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function initials(str) { return String(str || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

function confirmDialog({ title, body, confirm = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true">
      <h2>${esc(title)}</h2><div class="muted">${body}</div>
      <div class="actions"><button class="btn ghost" data-r="0">Cancel</button>
      <button class="btn ${danger ? "danger" : "primary"}" data-r="1">${esc(confirm)}</button></div></div></div>`;
    const done = (v) => { root.innerHTML = ""; resolve(v); };
    root.querySelector(".modal-bg").addEventListener("click", (e) => {
      if (e.target.classList.contains("modal-bg")) done(false);
      const b = e.target.closest("[data-r]");
      if (b) done(b.dataset.r === "1");
    });
    root.querySelector('[data-r="1"]').focus();
  });
}

async function act(fn, okMessage) {
  try {
    const r = await fn();
    if (okMessage) toast(okMessage, "success");
    refreshStatus();
    return r;
  } catch (e) {
    toast(e.message, "error");
    return undefined;
  }
}

function coverHtml(url, title, cls = "") {
  const h = hue(title);
  const fallback = `<div class="fallback" style="background:linear-gradient(135deg,hsl(${h} 70% 45%),hsl(${(h + 60) % 360} 70% 35%))">${esc(initials(title))}</div>`;
  if (!url) return fallback;
  return `${fallback}<img class="${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`;
}

function thumbHtml(url, label) {
  const h = hue(label);
  const img = url ? `<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
  return `<div class="thumb" style="background:linear-gradient(135deg,hsl(${h} 65% 42%),hsl(${(h + 60) % 360} 65% 32%))">${esc(initials(label))}${img}</div>`;
}

/* ===================================================================== global state */
const store = { status: null, page: null, route: null };
const NAV = [
  ["dashboard", "Dashboard", "dashboard"],
  ["playlists", "Playlists", "playlists"],
  ["tracks", "Tracks", "tracks"],
  ["trash", "Trash", "trash"],
  ["logs", "Logs", "logs"],
  ["settings", "Settings", "settings"],
];
const PHASES = {
  connecting: "Connecting", playlists: "Reading playlists", cleanup: "Updating files",
  downloading: "Downloading", finishing: "Finishing up", idle: "Idle",
};

function renderNav() {
  const failed = store.status?.stats?.tracks?.failed || 0;
  $("#nav").innerHTML = NAV.map(([id, label, ic]) => {
    const count = id === "tracks" && failed ? `<span class="count">${fmtNum(failed)}</span>` : "";
    return `<a href="#/${id}" class="${store.route === id ? "active" : ""}" title="${label}">${icon(ic)}<span class="label-text">${label}</span>${count}</a>`;
  }).join("");
}

function renderChrome() {
  const s = store.status;
  if (!s) return;
  renderNav();
  const dot = $("#foot-dot");
  const text = $("#foot-status");
  dot.className = "dot";
  if (!s.auth.connected) { dot.classList.add("warning"); text.textContent = "Not connected"; }
  else if (s.auth.error) { dot.classList.add("danger"); text.textContent = "Sign-in expired"; }
  else if (s.state.running) { dot.classList.add("running"); text.textContent = PHASES[s.state.phase] || "Syncing"; }
  else if (s.paused) { dot.classList.add("warning"); text.textContent = "Paused"; }
  else if (s.cooldown_until > s.now) { dot.classList.add("warning"); text.textContent = "Cooling down"; }
  else { dot.classList.add("success"); text.textContent = "Watching for changes"; }
  $("#foot-version").textContent = `v${s.version} · yt-dlp ${s.ytdlp_version || "?"}`;
}

let statusTimer = null;
async function refreshStatus() {
  try {
    store.status = await api("/api/status");
    renderChrome();
    store.page?.onStatus?.(store.status);
  } catch (e) {
    $("#foot-status").textContent = "Server unreachable";
    $("#foot-dot").className = "dot danger";
  }
}
function schedulePoll() {
  clearTimeout(statusTimer);
  const fast = store.status?.state?.running || store.route === "dashboard";
  statusTimer = setTimeout(async () => { await refreshStatus(); schedulePoll(); }, fast ? 2000 : 5000);
}

/* ===================================================================== dashboard */
const Dashboard = {
  mount(el) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Dashboard</h1><p class="muted">Your YouTube Music playlists, mirrored to disk.</p></div></div>
      <div id="dash"></div>
      <div class="grid grid-2" style="margin-top:18px">
        <div class="card"><div class="card-head"><h2>Last sync</h2></div><div id="dash-last"></div></div>
        <div class="card"><div class="card-head"><h2>Recent activity</h2><span class="spacer"></span>
          <a href="#/logs" class="small">Full log</a></div><div id="dash-events" class="events"></div></div>
      </div>`;
    this.lastHtml = "";
    this.eventsAt = 0;
    el.addEventListener("click", (e) => this.onClick(e));
    if (store.status) this.onStatus(store.status);
  },
  async onClick(e) {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const a = b.dataset.action;
    if (a === "sync") await act(() => api("/api/sync", { method: "POST" }), "Sync started");
    if (a === "cancel") await act(() => api("/api/sync/cancel", { method: "POST" }), "Stopping after the current step…");
    if (a === "pause") await act(() => api("/api/pause", { method: "POST" }), "Automatic sync paused");
    if (a === "resume") await act(() => api("/api/resume", { method: "POST" }), "Automatic sync resumed");
    if (a === "cooldown") await act(() => api("/api/cooldown/clear", { method: "POST" }), "Downloads will resume with the next sync");
    if (a === "approve-library") {
      const n = store.status.held_library_removal.length;
      if (await confirmDialog({ title: "Remove these playlists locally?", body: `${n} playlist file(s) will be deleted and tracks that are in no other synced playlist move to the trash.`, confirm: "Approve removal", danger: true })) {
        await act(() => api("/api/library/approve-removal", { method: "POST" }), "Approved — syncing now");
      }
    }
  },
  onStatus(s) {
    const html = this.heroHtml(s) + this.statsHtml(s);
    if (html !== this.lastHtml) { $("#dash").innerHTML = html; this.lastHtml = html; }
    $("#dash-last").innerHTML = this.lastHtmlBlock(s);
    const now = Date.now();
    if (now - this.eventsAt > (s.state.running ? 4000 : 15000)) { this.eventsAt = now; this.loadEvents(); }
  },
  heroHtml(s) {
    const warn = s.music_writable === false
      ? `<div class="callout danger" style="margin-bottom:18px">${icon("alert")}<div><b>The music folder is not writable.</b> The app cannot save files to <code>${esc(s.paths.music)}</code>. With Docker, set <code>PUID</code>/<code>PGID</code> to the owner of that folder (run <code>id</code> on the host).</div></div>`
      : "";
    const held = s.held_library_removal;
    const heldBox = held && held.length
      ? `<div class="callout warning" style="margin-bottom:18px">${icon("alert")}<div style="flex:1"><b>${held.length} playlist(s) disappeared from your YouTube Music library at once.</b>
         Nothing was removed locally yet: ${esc(held.slice(0, 6).join(", "))}${held.length > 6 ? "…" : ""}. If you deleted them on purpose, approve the removal.</div>
         <button class="btn sm" data-action="approve-library">Approve removal</button></div>`
      : "";
    return warn + heldBox + this.heroCard(s);
  },
  heroCard(s) {
    if (!s.auth.connected) {
      return `<div class="card hero"><div class="hero-row">
        <div class="orb warn">${icon("link")}</div>
        <div class="hero-status"><div class="hero-title">Connect your YouTube Music account</div>
          <p class="muted" style="margin:6px 0 0">It takes about a minute in Settings: paste your browser session or sign in with Google. Nothing is downloaded until you do.</p></div>
        <div class="hero-actions"><a class="btn primary" href="#/settings">${icon("link")} Connect account</a></div>
      </div></div>`;
    }
    const st = s.state;
    let orb = "ok", ic = "check", title = "Up to date", sub = "";
    const actions = [];
    if (st.running) {
      orb = "running"; ic = "sync"; title = PHASES[st.phase] || "Syncing"; sub = esc(st.message || "");
      actions.push(`<button class="btn" data-action="cancel">${icon("stop")} Stop</button>`);
    } else {
      if (s.auth.error) { orb = "err"; ic = "alert"; title = "YouTube Music sign-in stopped working"; sub = `${esc(s.auth.error)} — <a href="#/settings">reconnect in Settings</a>.`; }
      else if (s.paused) { orb = "warn"; ic = "pause"; title = "Automatic sync is paused"; sub = "Nothing happens until you resume (manual syncs still work)."; }
      else if (s.cooldown_until > s.now) { orb = "warn"; ic = "alert"; title = "Downloads are cooling down"; sub = `YouTube rate-limited downloads. They resume automatically ${inTime(s.cooldown_until)} (${hm(s.cooldown_until)}). Playlists keep syncing meanwhile.`; }
      else if (s.last_sync?.error) { orb = "err"; ic = "alert"; title = "Last sync failed"; sub = esc(s.last_sync.error); }
      else if (s.stats.tracks.pending) { orb = "warn"; ic = "cloud"; title = `${fmtNum(s.stats.tracks.pending)} tracks waiting`; sub = `They will be downloaded during the next sync (${inTime(s.next_run_at)}).`; }
      else { sub = `Next check ${inTime(s.next_run_at)}. Change something on YouTube Music and it will show up here.`; }
      actions.push(`<button class="btn primary" data-action="sync">${icon("sync")} Sync now</button>`);
    }
    if (s.cooldown_until > s.now) actions.push(`<button class="btn" data-action="cooldown">Resume downloads</button>`);
    actions.push(s.paused
      ? `<button class="btn" data-action="resume">${icon("play")} Resume</button>`
      : `<button class="btn ghost" data-action="pause">${icon("pause")} Pause</button>`);

    let progress = "";
    if (st.running) {
      const pTot = st.playlists_total || 0, pDone = Math.min(st.playlists_done || 0, pTot);
      progress += `<div class="progress-label"><span>Playlists</span><span>${pDone} / ${pTot}</span></div>
        <div class="progress"><span style="width:${pTot ? (100 * pDone) / pTot : 0}%"></span></div>`;
      if (st.downloads_total) {
        const d = st.downloads_done + st.downloads_failed;
        progress += `<div class="progress-label"><span>Downloads${st.downloads_failed ? ` · <span style="color:var(--danger)">${st.downloads_failed} failed</span>` : ""}</span><span>${fmtNum(d)} / ${fmtNum(st.downloads_total)}</span></div>
          <div class="progress"><span style="width:${(100 * d) / st.downloads_total}%"></span></div>`;
      }
      if (st.active?.length) {
        progress += `<div class="active-list">${st.active.map((a) => `
          <div class="active-item"><div class="t"><b>${esc(a.title)}</b> <span class="muted">${esc((a.artists || []).join(", "))}</span></div>
          <div class="progress thin"><span style="width:${a.percent || 0}%"></span></div>
          <div class="small muted nowrap" style="text-align:right">${a.stage === "downloading" ? `${Math.round(a.percent || 0)}%` : esc(a.stage || "")}</div></div>`).join("")}</div>`;
      }
    }
    return `<div class="card hero"><div class="hero-row">
        <div class="orb ${orb}">${icon(ic)}</div>
        <div class="hero-status"><div class="hero-title">${title}</div><div class="muted" style="margin-top:4px">${sub}</div></div>
        <div class="hero-actions">${actions.join("")}</div></div>
        <div style="position:relative;margin-top:${progress ? 10 : 0}px">${progress}</div></div>`;
  },
  statsHtml(s) {
    const t = s.stats.tracks;
    const tile = (label, value, sub, cls = "", href = "") =>
      `<${href ? `a href="${href}" style="color:inherit;text-decoration:none"` : "div"} class="stat ${cls}"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></${href ? "a" : "div"}>`;
    return `<div class="stats">
      ${tile("Playlists", `${fmtNum(s.stats.playlists_active)}<span class="faint" style="font-size:15px"> / ${fmtNum(s.stats.playlists_total)}</span>`, "synced / in library", "", "#/playlists")}
      ${tile("On disk", fmtNum(t.downloaded), "tracks", "", "#/tracks")}
      ${tile("Library size", fmtBytes(s.stats.library_bytes), `${fmtBytes(s.disk.free)} free`)}
      ${tile("Waiting", fmtNum(t.pending), "to download", "", "#/tracks?status=pending")}
      ${tile("Failed", fmtNum(t.failed), "retried automatically", t.failed ? "danger" : "", "#/tracks?status=failed")}
      ${tile("Trash", fmtNum(t.trashed), fmtBytes(s.stats.trash_bytes), "", "#/trash")}
    </div>`;
  },
  lastHtmlBlock(s) {
    const l = s.last_sync;
    if (!l) return `<p class="muted">No sync has run yet.</p>`;
    const took = l.finished_at ? fmtSpan(l.finished_at - l.started_at) : "—";
    const row = (k, v) => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--border)"><span class="muted">${k}</span><span>${v}</span></div>`;
    return `<p><b>${esc(l.text || "")}</b></p>
      ${row("Finished", `${clock(l.finished_at)} <span class="faint">(${ago(l.finished_at)})</span>`)}
      ${row("Duration", took)}
      ${row("Playlists checked", fmtNum(l.playlists))}
      ${row("Downloaded / removed", `${fmtNum(l.downloaded)} / ${fmtNum(l.removed)}`)}
      ${row("Next sync", s.paused ? "paused" : `${inTime(s.next_run_at)}`)}
      ${l.error ? `<div class="callout danger" style="margin-top:12px">${icon("alert")}<div>${esc(l.error)}</div></div>` : ""}`;
  },
  async loadEvents() {
    try {
      const events = await api("/api/events?limit=12");
      const cls = { success: "success", warning: "warning", error: "danger", info: "" };
      $("#dash-events").innerHTML = events.length
        ? events.map((e) => `<div class="event"><span class="dot ${cls[e.level] || ""}"></span><div>${esc(e.message)}</div><time title="${clock(e.ts)}">${ago(e.ts)}</time></div>`).join("")
        : `<p class="muted">Nothing yet.</p>`;
    } catch { /* ignore */ }
  },
};

/* ===================================================================== playlists */
const Playlists = {
  mount(el) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Playlists</h1><p class="muted">Pick which playlists are mirrored. Order and contents always follow YouTube Music.</p></div>
        <span class="spacer"></span>
        <button class="btn" data-action="refresh">${icon("refresh")} Refresh list</button>
        <button class="btn primary" data-action="sync">${icon("sync")} Sync now</button></div>
      <div class="toolbar">
        <input type="search" id="pl-q" placeholder="Filter playlists…">
        <div class="segmented" id="pl-filter">
          <button data-f="all" class="active">All</button><button data-f="on">Synced</button><button data-f="off">Not synced</button><button data-f="issues">Needs attention</button>
        </div>
        <span style="flex:1"></span>
        <button class="btn sm ghost" data-action="all-on">Enable all</button>
        <button class="btn sm ghost" data-action="all-off">Disable all</button>
      </div>
      <div id="pl-grid" class="pl-grid"></div>`;
    this.filter = "all";
    this.items = [];
    this.loadedAt = 0;
    $("#pl-q").addEventListener("input", debounce(() => this.render(), 150));
    $("#pl-filter").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      this.filter = b.dataset.f;
      $$("#pl-filter button").forEach((x) => x.classList.toggle("active", x === b));
      this.render();
    });
    el.addEventListener("click", (e) => this.onClick(e));
    el.addEventListener("change", (e) => this.onToggle(e));
    this.load();
  },
  async load() {
    try { this.items = await api("/api/playlists"); this.loadedAt = Date.now(); this.render(); }
    catch (e) { toast(e.message, "error"); }
  },
  onStatus(s) {
    if (s.state.running && Date.now() - this.loadedAt > 5000) this.load();
    if (!s.state.running && this.wasRunning) this.load();
    this.wasRunning = s.state.running;
  },
  async onClick(e) {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const a = b.dataset.action;
    if (a === "sync") await act(() => api("/api/sync", { method: "POST" }), "Sync started");
    if (a === "refresh") {
      b.disabled = true;
      const r = await act(() => api("/api/playlists/refresh", { method: "POST" }), "Playlist list refreshed");
      b.disabled = false;
      if (r) { this.items = r; this.render(); }
    }
    if (a === "all-on" || a === "all-off") {
      const on = a === "all-on";
      if (!on && !(await confirmDialog({ title: "Disable all playlists?", body: "At the next sync every downloaded track will be moved to the trash.", confirm: "Disable all", danger: true }))) return;
      await act(() => api("/api/playlists/bulk", { method: "POST", body: { enabled: on } }));
      this.load();
    }
    if (a === "approve") {
      const p = this.items.find((x) => x.id === b.dataset.id);
      if (await confirmDialog({ title: "Apply removals?", body: `${p.held_removals} tracks disappeared from <b>${esc(p.title)}</b> on YouTube Music at once. Approve to remove them locally too (they go to the trash first).`, confirm: "Approve removal" })) {
        await act(() => api(`/api/playlists/${encodeURIComponent(p.id)}/approve`, { method: "POST" }), "Approved — syncing now");
        this.load();
      }
    }
  },
  async onToggle(e) {
    const input = e.target.closest("input[data-toggle]");
    if (!input) return;
    const p = this.items.find((x) => x.id === input.dataset.toggle);
    if (!input.checked && p.downloaded_count > 0) {
      const ok = await confirmDialog({ title: `Stop syncing “${p.title}”?`, body: "Its .m3u8 file is removed and tracks that are not in any other synced playlist move to the trash at the next sync.", confirm: "Stop syncing", danger: true });
      if (!ok) { input.checked = true; return; }
    }
    const r = await act(() => api(`/api/playlists/${encodeURIComponent(p.id)}`, { method: "PATCH", body: { enabled: input.checked } }),
      input.checked ? `“${p.title}” will be synced` : `“${p.title}” will no longer be synced`);
    if (r) { Object.assign(p, r); this.render(); }
  },
  statusBadge(p) {
    if (!p.in_library) return `<span class="badge danger">Removed from library</span>`;
    if (!p.enabled) return `<span class="badge">Not synced</span>`;
    if (p.held_removals) return `<span class="badge warning">${p.held_removals} removals on hold</span> <button class="btn sm" data-action="approve" data-id="${esc(p.id)}">Approve</button>`;
    if (p.last_error) return `<span class="badge danger" title="${esc(p.last_error)}">Error</span> <span class="small faint" title="${esc(p.last_error)}">${esc(p.last_error.slice(0, 60))}</span>`;
    if (!p.fetched) return `<span class="badge info">Waiting for first sync</span>`;
    return `<span class="badge success">${icon("check", 'width="12" height="12"')} Synced ${ago(p.last_synced_at)}</span>`;
  },
  render() {
    const q = ($("#pl-q")?.value || "").toLowerCase();
    const list = this.items.filter((p) => {
      if (q && !p.title.toLowerCase().includes(q)) return false;
      if (this.filter === "on") return p.enabled;
      if (this.filter === "off") return !p.enabled;
      if (this.filter === "issues") return p.held_removals || p.last_error || !p.in_library;
      return true;
    });
    const grid = $("#pl-grid");
    if (!this.items.length) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1">${icon("playlists")}<p>No playlists yet.</p>
        <p class="small">${store.status?.auth?.connected ? "Click <b>Refresh list</b> to read them from YouTube Music." : 'Connect your account in <a href="#/settings">Settings</a> first.'}</p></div>`;
      return;
    }
    if (!list.length) { grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>No playlists match.</p></div>`; return; }
    grid.innerHTML = list.map((p) => {
      const total = p.local_count || p.remote_count || 0;
      const pct = total ? Math.min(100, (100 * p.downloaded_count) / total) : 0;
      const tag = p.id === "LM" ? `<span class="badge violet tag">Liked Music</span>` : (!p.owned ? `<span class="badge tag">Saved</span>` : "");
      return `<div class="pl-card ${p.enabled ? "" : "disabled"}">
        <div class="pl-cover">${coverHtml(p.thumbnail, p.title)}${tag}</div>
        <div class="pl-body">
          <div class="pl-title"><h3 title="${esc(p.title)}">${esc(p.title)}</h3>
            <label class="switch" title="Sync this playlist"><input type="checkbox" data-toggle="${esc(p.id)}" ${p.enabled ? "checked" : ""}><span></span></label></div>
          <div class="pl-meta"><span>${p.enabled && p.fetched ? `${fmtNum(p.downloaded_count)} / ${fmtNum(total)} on disk` : `${fmtNum(p.remote_count ?? total)} tracks`}</span>
            <span>${esc(p.author || (p.owned ? "Your playlist" : ""))}</span></div>
          ${p.enabled && p.fetched ? `<div class="progress thin"><span style="width:${pct}%"></span></div>` : ""}
          <div class="pl-foot">${this.statusBadge(p)}</div>
        </div></div>`;
    }).join("");
  },
};

/* ===================================================================== tracks */
const STATUS_BADGE = {
  downloaded: ["success", "On disk"], pending: ["info", "Waiting"], failed: ["danger", "Failed"],
  unavailable: ["", "Unavailable"], trashed: ["warning", "In trash"],
};
const Tracks = {
  mount(el, params) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Tracks</h1><p class="muted" id="tr-sub">Every track referenced by your synced playlists.</p></div>
        <span class="spacer"></span><button class="btn" data-action="retry-all">${icon("refresh")} Retry failed</button></div>
      <div class="toolbar">
        <input type="search" id="tr-q" placeholder="Search title, artist, album…">
        <div class="segmented" id="tr-status">
          ${[["", "All"], ["downloaded", "On disk"], ["pending", "Waiting"], ["failed", "Failed"], ["unavailable", "Unavailable"], ["trashed", "In trash"]]
            .map(([v, l]) => `<button data-s="${v}">${l}</button>`).join("")}
        </div>
        <select id="tr-pl" style="max-width:240px"><option value="">All playlists</option></select>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Track</th><th>Album</th><th>Playlists</th><th>Status</th><th></th></tr></thead>
        <tbody id="tr-body"><tr><td colspan="5" class="muted">Loading…</td></tr></tbody></table></div>
      <div class="pager"><span id="tr-count"></span><span class="inline"><button class="btn sm" data-action="prev">Previous</button><button class="btn sm" data-action="next">Next</button></span></div>`;
    this.status = params.get("status") || "";
    this.offset = 0;
    this.limit = 100;
    $$("#tr-status button").forEach((b) => b.classList.toggle("active", b.dataset.s === this.status));
    $("#tr-q").addEventListener("input", debounce(() => { this.offset = 0; this.load(); }, 250));
    $("#tr-pl").addEventListener("change", () => { this.offset = 0; this.load(); });
    $("#tr-status").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      this.status = b.dataset.s; this.offset = 0;
      $$("#tr-status button").forEach((x) => x.classList.toggle("active", x === b));
      this.load();
    });
    el.addEventListener("click", (e) => this.onClick(e));
    api("/api/playlists").then((pls) => {
      $("#tr-pl").innerHTML += pls.filter((p) => p.enabled).map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
    }).catch(() => {});
    this.load();
  },
  onStatus(s) {
    if (s.state.running && Date.now() - (this.loadedAt || 0) > 6000) this.load();
  },
  async onClick(e) {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const a = b.dataset.action;
    if (a === "prev" && this.offset > 0) { this.offset = Math.max(0, this.offset - this.limit); this.load(); }
    if (a === "next" && this.offset + this.limit < this.total) { this.offset += this.limit; this.load(); }
    if (a === "retry") { await act(() => api(`/api/tracks/${encodeURIComponent(b.dataset.id)}/retry`, { method: "POST" }), "Queued for the next sync"); this.load(); }
    if (a === "retry-all") { await act(() => api("/api/tracks/retry-failed", { method: "POST" }), "Failed tracks queued for the next sync"); this.load(); }
  },
  async load() {
    const params = new URLSearchParams({ q: $("#tr-q").value, status: this.status, playlist: $("#tr-pl").value, offset: this.offset, limit: this.limit });
    try {
      const r = await api(`/api/tracks?${params}`);
      this.total = r.total;
      this.loadedAt = Date.now();
      this.render(r.items);
    } catch (e) { toast(e.message, "error"); }
  },
  render(items) {
    const active = new Map((store.status?.state?.active || []).map((a) => [a.video_id, a]));
    $("#tr-count").textContent = this.total ? `${fmtNum(this.offset + 1)}–${fmtNum(Math.min(this.offset + this.limit, this.total))} of ${fmtNum(this.total)}` : "";
    if (!items.length) { $("#tr-body").innerHTML = `<tr><td colspan="5"><div class="empty">${icon("tracks")}<p>No tracks here.</p></div></td></tr>`; return; }
    $("#tr-body").innerHTML = items.map((t) => {
      let [cls, label] = STATUS_BADGE[t.status] || ["", t.status];
      const a = active.get(t.video_id);
      if (a) { cls = "violet"; label = a.stage === "downloading" ? `Downloading ${Math.round(a.percent || 0)}%` : "Processing"; }
      const retryInfo = t.status === "failed" && t.next_attempt_at ? ` title="Next automatic retry ${inTime(t.next_attempt_at)}"` : "";
      const err = t.last_error && t.status !== "downloaded" ? `<div class="err-line">${esc(t.last_error)}</div>` : "";
      const where = t.path || t.trash_path;
      return `<tr>
        <td><div class="cell-track">${thumbHtml(t.thumbnail, t.album || t.title)}<div><div class="t" title="${esc(where || "")}">${esc(t.title)}</div>
          <div class="a">${esc((t.artists || []).join(", "))}</div>${err}</div></div></td>
        <td class="small">${esc(t.album || "")}${t.year ? ` <span class="faint">· ${esc(t.year)}</span>` : ""}</td>
        <td class="small muted" style="max-width:260px">${esc(t.playlists || "")}</td>
        <td><span class="badge ${cls}"${retryInfo}>${esc(label)}</span>${t.attempts && t.status === "failed" ? ` <span class="small faint">×${t.attempts}</span>` : ""}</td>
        <td style="text-align:right">${t.status === "failed" ? `<button class="btn sm" data-action="retry" data-id="${esc(t.video_id)}">Retry</button>` : ""}
          <a class="btn sm ghost" href="https://music.youtube.com/watch?v=${esc(t.video_id)}" target="_blank" rel="noopener" title="Open on YouTube Music">${icon("link")}</a></td>
      </tr>`;
    }).join("");
  },
};

/* ===================================================================== trash */
const Trash = {
  mount(el) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Trash</h1><p class="muted" id="trash-sub"></p></div>
        <span class="spacer"></span><button class="btn danger" data-action="empty">${icon("trash")} Empty trash</button></div>
      <div class="callout info" style="margin-bottom:16px">${icon("restore")}<div>Tracks you remove on YouTube Music land here first. Add a track back on YouTube Music and the next sync restores it automatically — no download needed.</div></div>
      <div class="table-wrap"><table><thead><tr><th>File</th><th>Size</th><th>Removed</th><th>Deleted for good</th><th></th></tr></thead>
      <tbody id="trash-body"></tbody></table></div>`;
    el.addEventListener("click", (e) => this.onClick(e));
    this.load();
  },
  async load() {
    try {
      const r = await api("/api/trash");
      $("#trash-sub").textContent = r.retention_days
        ? `Removed tracks are kept for ${r.retention_days} days (inside the hidden .ytmd-trash folder of your music directory).`
        : "Trash is disabled (retention 0 days): removed tracks are deleted right away.";
      const body = $("#trash-body");
      if (!r.items.length) { body.innerHTML = `<tr><td colspan="5"><div class="empty">${icon("trash")}<p>The trash is empty.</p></div></td></tr>`; return; }
      body.innerHTML = r.items.map((i) => `<tr>
        <td class="mono small">${esc(i.path)}</td><td class="nowrap">${fmtBytes(i.size)}</td>
        <td class="nowrap" title="${clock(i.trashed_at)}">${ago(i.trashed_at)}</td>
        <td class="nowrap">${inTime(i.expires_at)}</td>
        <td style="text-align:right"><button class="btn sm danger" data-action="delete" data-path="${esc(i.path)}">Delete now</button></td></tr>`).join("");
    } catch (e) { toast(e.message, "error"); }
  },
  async onClick(e) {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    if (b.dataset.action === "empty") {
      if (await confirmDialog({ title: "Empty the trash?", body: "All files in the trash are deleted permanently.", confirm: "Empty trash", danger: true })) {
        await act(() => api("/api/trash", { method: "DELETE" }), "Trash emptied");
        this.load();
      }
    }
    if (b.dataset.action === "delete") {
      await act(() => api("/api/trash/delete", { method: "POST", body: { path: b.dataset.path } }), "Deleted");
      this.load();
    }
  },
};

/* ===================================================================== logs */
const Logs = {
  mount(el) {
    el.innerHTML = `
      <div class="page-head"><div><h1>Logs</h1><p class="muted">Live output of the sync engine (also written to <code>config/logs/ytmd.log</code>).</p></div>
        <span class="spacer"></span>
        <div class="segmented" id="log-level"><button data-l="INFO" class="active">All</button><button data-l="WARNING">Warnings</button><button data-l="ERROR">Errors</button></div>
        <label class="inline small muted"><span class="switch"><input type="checkbox" id="log-follow" checked><span></span></span> Follow</label></div>
      <div class="logbox" id="logbox"></div>`;
    this.after = 0;
    this.level = "INFO";
    this.lines = [];
    $("#log-level").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      this.level = b.dataset.l;
      $$("#log-level button").forEach((x) => x.classList.toggle("active", x === b));
      this.render(true);
    });
    this.poll();
  },
  unmount() { clearTimeout(this.timer); },
  async poll() {
    try {
      const items = await api(`/api/logs?after=${this.after}`);
      if (items.length) {
        this.after = items[items.length - 1].id;
        this.lines.push(...items);
        if (this.lines.length > 3000) this.lines = this.lines.slice(-2000);
        this.render(false, items);
      }
    } catch { /* ignore */ }
    this.timer = setTimeout(() => this.poll(), 2000);
  },
  render(full, fresh = []) {
    const box = $("#logbox");
    if (!box) return;
    const rank = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
    const min = rank[this.level];
    const line = (l) => `<div class="l ${l.level}"><span class="ts">${new Date(l.ts * 1000).toLocaleTimeString()}</span> <span class="lg">${esc(l.logger.replace(/^ytmd\./, ""))}</span> ${esc(l.message)}</div>`;
    const pick = (arr) => arr.filter((l) => rank[l.level] >= min).map(line).join("");
    if (full) box.innerHTML = pick(this.lines) || `<div class="faint">No log lines yet.</div>`;
    else { if (box.querySelector(".faint")) box.innerHTML = ""; box.insertAdjacentHTML("beforeend", pick(fresh)); }
    if (!box.innerHTML) box.innerHTML = `<div class="faint">No log lines yet.</div>`;
    if ($("#log-follow").checked) box.scrollTop = box.scrollHeight;
  },
};

/* ===================================================================== settings */
const Settings = {
  async mount(el) {
    el.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p class="muted">Changes apply from the next sync.</p></div></div><div id="set-body" class="muted">Loading…</div>`;
    try {
      this.data = await api("/api/settings");
    } catch (e) { toast(e.message, "error"); return; }
    this.original = JSON.stringify(this.data);
    this.render();
    el.addEventListener("input", () => this.markDirty());
    el.addEventListener("change", () => this.markDirty());
    el.addEventListener("click", (e) => this.onClick(e));
    if (location.hash.includes("account")) $("#account")?.scrollIntoView();
  },
  onStatus(s) {
    const sig = JSON.stringify([s.auth, s.cookies_uploaded, s.ytdlp_version]);
    if (this.authSig !== sig && $("#account")) { this.authSig = sig; $("#account").innerHTML = this.accountHtml(s); $("#cookies-state").innerHTML = this.cookiesState(s); $("#ytdlp-ver").textContent = s.ytdlp_version || "unknown"; }
  },
  field(name, help, control) {
    return `<div class="field"><div><label class="name">${name}</label>${help ? `<div class="help">${help}</div>` : ""}</div><div class="control">${control}</div></div>`;
  },
  toggle(key, name, help) {
    return this.field(name, help, `<label class="switch"><input type="checkbox" data-key="${key}" ${this.data[key] ? "checked" : ""}><span></span></label>`);
  },
  number(key, name, help, { min = 0, max = 100000, step = 1, suffix = "" } = {}) {
    return this.field(name, help, `<div class="inline"><input type="number" data-key="${key}" value="${esc(this.data[key])}" min="${min}" max="${max}" step="${step}" style="max-width:130px">${suffix ? `<span class="muted">${suffix}</span>` : ""}</div>`);
  },
  text(key, name, help, { placeholder = "", type = "text" } = {}) {
    return this.field(name, help, `<input type="${type}" data-key="${key}" value="${esc(this.data[key])}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">`);
  },
  radios(key, name, help, options) {
    return this.field(name, help, `<div class="radio-cards">${options.map((o) => `
      <label class="radio-card"><input type="radio" name="${key}" data-key="${key}" value="${o.value}" ${this.data[key] === o.value ? "checked" : ""}>
      <div><b>${o.title}</b>${o.desc ? `<div class="small muted">${o.desc}</div>` : ""}${o.example ? `<div class="ex">${esc(o.example)}</div>` : ""}</div></label>`).join("")}</div>`);
  },
  intervalSelect() {
    const opts = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440];
    if (!opts.includes(this.data.sync_interval_minutes)) opts.push(this.data.sync_interval_minutes);
    const label = (m) => (m < 60 ? `Every ${m} minutes` : m === 60 ? "Every hour" : m < 1440 ? `Every ${m / 60} hours` : "Once a day");
    return this.field("Check YouTube Music", "How often playlists are compared with YouTube Music. Checking is cheap; only changes cause downloads.",
      `<select data-key="sync_interval_minutes" data-type="number" style="max-width:240px">${opts.sort((a, b) => a - b).map((m) => `<option value="${m}" ${m === this.data.sync_interval_minutes ? "selected" : ""}>${label(m)}</option>`).join("")}</select>`);
  },
  accountHtml(s) {
    const a = s.auth;
    if (a.connected) {
      const acc = a.account || {};
      const avatar = acc.photo ? `<img src="${esc(acc.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : esc(initials(acc.name || "YT"));
      return `<div class="card-head"><h2>YouTube Music account</h2></div>
        <div class="account"><div class="avatar">${avatar}</div>
          <div style="flex:1"><div style="font-weight:700">${esc(acc.name || "Connected")}</div><div class="small muted">${esc(acc.handle || "")}${acc.handle ? " · " : ""}${a.mode === "oauth" ? "Google sign-in" : "browser session"}</div></div>
          ${a.error ? `<span class="badge danger">Sign-in expired</span>` : `<span class="badge success">Connected</span>`}
          <button class="btn sm ghost" data-action="show-connect">Replace</button>
          <button class="btn sm danger" data-action="disconnect">Disconnect</button></div>
        ${a.error ? `<div class="callout danger" style="margin-top:14px">${icon("alert")}<div><p><b>YouTube Music rejected the saved session.</b> ${esc(a.error)}</p><p>Paste fresh headers below to reconnect.</p></div></div>` : ""}
        <div id="connect-form" class="${a.error ? "" : "hidden"}" style="margin-top:16px">${this.connectForm()}</div>`;
    }
    return `<div class="card-head"><h2>Connect YouTube Music</h2></div>${this.connectForm()}`;
  },
  connectForm() {
    const tab = this.connectTab || "browser";
    const browser = `<ol class="steps">
        <li><div>Open <a href="https://music.youtube.com" target="_blank" rel="noopener">music.youtube.com</a> in a desktop browser and sign in to the account you want to mirror. <span class="muted">Tip: use a private window and just close it afterwards — logging out would invalidate the copied session.</span></div></li>
        <li><div>Open developer tools (<code>F12</code> or <code>⌥⌘I</code>), go to the <b>Network</b> tab and type <code>browse</code> in the filter.</div></li>
        <li><div>Click <b>Library</b> in YouTube Music (or scroll) so a <code>browse</code> request (POST) shows up.</div></li>
        <li><div>Right-click it → <b>Copy</b> → <b>Copy request headers</b> (Firefox: “Copy Request Headers”, Safari: “Copy as cURL” works too).</div></li>
        <li><div>Paste everything below and click <b>Connect</b>.</div></li>
      </ol>
      <textarea id="headers-input" placeholder="cookie: ...&#10;x-goog-authuser: 0&#10;..."></textarea>
      <div class="inline" style="margin-top:10px"><button class="btn primary" data-action="connect">${icon("link")} Connect</button>
        <span class="small muted">Stored only in <code>config/browser.json</code> on this machine.</span></div>`;
    const oauth = `<p class="muted small">Sign in like on a TV: you get a code and confirm it at google.com/device on any device. Google requires your own (free) OAuth client for this — a one-time setup:</p>
      <ol class="steps">
        <li><div>In <a href="https://console.cloud.google.com/" target="_blank" rel="noopener">Google Cloud Console</a> create a project and enable <b>YouTube Data API v3</b>.</div></li>
        <li><div><b>APIs &amp; Services → OAuth consent screen</b>: user type <i>External</i>, add your Google account as a <b>test user</b>.</div></li>
        <li><div><b>Credentials → Create credentials → OAuth client ID</b>, application type <b>TVs and Limited Input devices</b>. Copy the client ID and secret below.</div></li>
      </ol>
      <div class="grid grid-2" style="gap:10px"><input type="text" id="oauth-id" placeholder="Client ID" autocomplete="off" spellcheck="false">
        <input type="password" id="oauth-secret" placeholder="Client secret" autocomplete="off"></div>
      <div class="inline" style="margin-top:10px"><button class="btn primary" data-action="oauth-start">${icon("link")} Get sign-in code</button>
        <span class="small muted">Downloading as signed-in user (age-restricted tracks) needs the browser-session method.</span></div>
      <div id="oauth-code" class="hidden" style="margin-top:16px"></div>`;
    return `<div class="segmented" style="margin-bottom:16px" id="connect-tabs">
        <button data-action="connect-tab" data-tab="browser" class="${tab === "browser" ? "active" : ""}">Browser session <span class="badge violet">recommended</span></button>
        <button data-action="connect-tab" data-tab="oauth" class="${tab === "oauth" ? "active" : ""}">Sign in with Google</button></div>
      ${tab === "browser" ? browser : oauth}`;
  },
  async oauthStart(btn) {
    const body = { client_id: $("#oauth-id").value, client_secret: $("#oauth-secret").value };
    btn.disabled = true;
    const r = await act(() => api("/api/auth/oauth/start", { method: "POST", body }));
    btn.disabled = false;
    if (!r) return;
    const box = $("#oauth-code");
    const link = `${r.verification_url}?user_code=${encodeURIComponent(r.user_code)}`;
    box.classList.remove("hidden");
    box.innerHTML = `<div class="callout info">${icon("link")}<div><p>Open <a href="${esc(link)}" target="_blank" rel="noopener"><b>${esc(r.verification_url)}</b></a> on any device, sign in to the YouTube Music account and enter this code:</p>
      <div style="font:700 28px var(--mono);letter-spacing:.12em;margin:6px 0 10px" class="grad-text">${esc(r.user_code)}</div>
      <p class="small muted" id="oauth-wait"><span class="dot running" style="display:inline-block;margin-right:6px"></span>Waiting for you to approve… (this page checks automatically)</p></div></div>`;
    clearTimeout(this.oauthTimer);
    const poll = async () => {
      if (!$("#oauth-code")) return;  // left the page
      try {
        const p = await api("/api/auth/oauth/poll", { method: "POST" });
        if (p.status === "pending") { this.oauthTimer = setTimeout(poll, r.interval * 1000); return; }
        this.connected(p);
      } catch (e) {
        $("#oauth-wait").innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      }
    };
    this.oauthTimer = setTimeout(poll, r.interval * 1000);
  },
  async connected(r) {
    toast(`Connected as ${r.account.name}`, "success");
    await refreshStatus();
    $("#account").innerHTML = this.accountHtml(store.status);
    if (r.first_sync_in > 60) {
      toast(`Choose your playlists now — the first sync starts in ${fmtSpan(r.first_sync_in)}.`, "success");
      location.hash = "#/playlists";
    }
  },
  unmount() { clearTimeout(this.oauthTimer); },
  cookiesState(s) {
    return s.cookies_uploaded
      ? `<div class="inline"><span class="badge success">cookies.txt uploaded</span><button class="btn sm danger" data-action="del-cookies">Remove</button></div><div class="help">The uploaded file is used for all downloads (it takes precedence over the account switch above).</div>`
      : `<textarea id="cookies-input" style="min-height:90px" placeholder="# Netscape HTTP Cookie File&#10;.youtube.com	TRUE	/	TRUE	..."></textarea>
         <div class="inline"><input type="file" id="cookies-file" accept=".txt,text/plain" style="max-width:260px"><button class="btn sm" data-action="upload-cookies">Upload</button></div>`;
  },
  render() {
    const s = store.status || { auth: {}, paths: {} };
    this.authSig = JSON.stringify([s.auth, s.cookies_uploaded, s.ytdlp_version]);
    $("#set-body").className = "stack";
    $("#set-body").innerHTML = `
      <div class="card" id="account">${this.accountHtml(s)}</div>

      <div class="card"><div class="card-head"><h2>Sync</h2></div>
        ${this.intervalSelect()}
        ${this.toggle("auto_enable_new_playlists", "Sync new playlists automatically", "Playlists you create or save on YouTube Music start syncing on their own. Liked Music is never enabled automatically.")}
      </div>

      <div class="card"><div class="card-head"><h2>Audio</h2></div>
        ${this.radios("audio_format", "Format", "Applies to new downloads; existing files are kept as they are.", [
          { value: "opus", title: "Opus <span class='badge violet'>recommended</span>", desc: "The original YouTube stream, no re-encoding. Best quality per MB. Supported by Jellyfin, Symfonium, Navidrome, Plex." },
          { value: "m4a", title: "M4A (AAC)", desc: "Also the original stream, no re-encoding. Best for Apple apps." },
          { value: "mp3", title: "MP3 (V0)", desc: "Re-encoded for maximum compatibility with old devices. Slight quality loss." },
        ])}
        ${this.toggle("prefer_album_version", "Prefer album versions", "When a playlist contains a music video, download the matching album track instead: clean audio without intros/skits and proper album tags.")}
        ${this.toggle("write_lyrics", "Lyrics", "Embed lyrics and save synced <code>.lrc</code> files next to the tracks when YouTube Music has them.")}
        ${this.toggle("write_album_covers", "Album folder covers", "Write <code>cover.jpg</code> into each album folder (cover art is always embedded in the files).")}
        ${this.toggle("sponsorblock_remove_offtopic", "Cut non-music parts", "Uses SponsorBlock to remove talking/skits from music videos (only affects videos that have segments).")}
      </div>

      <div class="card"><div class="card-head"><h2>Library</h2></div>
        ${this.field("Music folder", "Set by the Docker volume / <code>YTMD_MUSIC_DIR</code>.", `<input type="text" value="${esc(s.paths?.music || "")}" disabled>`)}
        ${this.radios("layout", "Folder layout", "Changing it moves existing files at the next sync.", [
          { value: "artist_album", title: "Artist / Album / Track <span class='badge violet'>recommended</span>", desc: "Best for Jellyfin, Navidrome and Plex. Tracks without an album go to Artist/Singles.", example: "Daft Punk/Discovery/03 Digital Love.opus" },
          { value: "artist", title: "Artist / Track", example: "Daft Punk/Digital Love.opus" },
          { value: "flat", title: "One folder", example: "Daft Punk - Digital Love.opus" },
        ])}
        ${this.text("playlists_dir", "Playlist files folder", "Inside the music folder. One <code>.m3u8</code> per playlist, in YouTube Music order, with relative paths — Jellyfin imports them as playlists.", { placeholder: "Playlists" })}
      </div>

      <div class="card"><div class="card-head"><h2>Removals &amp; trash</h2></div>
        <div class="callout info" style="margin-bottom:8px">${icon("info")}<div>YouTube Music is the source of truth: when a track leaves every synced playlist, its file is removed locally too.</div></div>
        ${this.number("trash_retention_days", "Keep removed tracks for", "Removed files wait in <code>.ytmd-trash</code> inside the music folder (hidden from media servers). 0 deletes immediately.", { min: 0, max: 3650, suffix: "days" })}
        ${this.number("removal_guard_percent", "Safety net", "If a playlist suddenly loses at least this share of its tracks, removals are put on hold until you approve them (protects against glitches on YouTube's side).", { min: 1, max: 100, suffix: "% of a playlist" })}
        ${this.number("removal_guard_min_tracks", "…but only above", "Smaller removals are always applied.", { min: 0, max: 100000, suffix: "tracks" })}
      </div>

      <div class="card"><div class="card-head"><h2>Downloads</h2></div>
        ${this.number("concurrent_downloads", "Parallel downloads", "2 is a good balance; higher values make rate limiting by YouTube more likely.", { min: 1, max: 8 })}
        ${this.number("download_delay_seconds", "Pause between downloads", "Randomized ±50% per worker to look less like a bot.", { min: 0, max: 120, step: 0.5, suffix: "seconds" })}
        ${this.number("max_attempts", "Retries before giving up", "Failed tracks are retried after 1h, 3h, 12h, 24h… After this many attempts they are retried weekly.", { min: 1, max: 50 })}
        ${this.toggle("use_account_cookies", "Download as signed-in user", "Needed for age-restricted tracks and Premium-only 256 kbps streams (works with the browser-session connection). <b>Use with care:</b> heavy downloading with an account can get it flagged by YouTube.")}
        ${this.field("Custom cookies.txt", "Optional: a Netscape cookies export (e.g. from a secondary account). Used instead of the switch above.", `<div id="cookies-state">${this.cookiesState(s)}</div>`)}
      </div>

      <div class="card"><div class="card-head"><h2>Jellyfin <span class="badge">optional</span></h2></div>
        <p class="muted small">Not required: Jellyfin picks up new files and playlists with its scheduled library scan. With an API key the scan is triggered right after every sync that changed something.</p>
        ${this.text("jellyfin_url", "Server URL", "", { placeholder: "http://192.168.1.10:8096", type: "url" })}
        ${this.text("jellyfin_api_key", "API key", "Jellyfin → Dashboard → API Keys.", { type: "password" })}
        ${this.field("", "", `<div class="inline"><button class="btn sm" data-action="test-jellyfin">Test connection</button><span id="jf-result" class="small muted"></span></div>`)}
      </div>

      <div class="card"><div class="card-head"><h2>Advanced</h2></div>
        ${this.field("yt-dlp", "The download engine. YouTube changes often, so it is kept up to date automatically.", `<div class="inline"><code id="ytdlp-ver">${esc(s.ytdlp_version || "unknown")}</code><button class="btn sm" data-action="update-ytdlp">Update now</button></div>`)}
        ${this.toggle("ytdlp_auto_update", "Update yt-dlp daily", "")}
        ${this.text("ytdlp_extra_args", "Extra yt-dlp arguments", "Appended to every download, e.g. <code>--extractor-args youtube:player_client=tv</code>. Only network/format options are accepted.", { placeholder: "--limit-rate 5M" })}
        ${this.number("cover_size", "Cover size", "Embedded cover art resolution.", { min: 300, max: 3000, step: 100, suffix: "px" })}
        ${this.text("brand_account_id", "Brand account ID", "Only if your music lives on a brand account (myaccount.google.com/brandaccounts → the number in the URL).", { placeholder: "" })}
      </div>

      <div class="savebar hidden" id="savebar"><span class="muted">You have unsaved changes</span>
        <button class="btn ghost" data-action="discard">Discard</button><button class="btn primary" data-action="save">Save settings</button></div>`;
  },
  collect() {
    const out = { ...this.data };
    $$("[data-key]").forEach((el) => {
      const key = el.dataset.key;
      if (el.type === "checkbox") out[key] = el.checked;
      else if (el.type === "radio") { if (el.checked) out[key] = el.value; }
      else if (el.type === "number" || el.dataset.type === "number") out[key] = el.value === "" ? null : Number(el.value);
      else out[key] = el.value;
    });
    return out;
  },
  markDirty() {
    const dirty = JSON.stringify(this.collect()) !== this.original;
    $("#savebar")?.classList.toggle("hidden", !dirty);
  },
  async onClick(e) {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const a = b.dataset.action;
    if (a === "save") {
      const r = await act(() => api("/api/settings", { method: "PUT", body: this.collect() }), "Settings saved");
      if (r) { this.data = r; this.original = JSON.stringify(r); this.markDirty(); }
    }
    if (a === "discard") { this.render(); }
    if (a === "show-connect") $("#connect-form")?.classList.toggle("hidden");
    if (a === "connect") {
      const headers = $("#headers-input").value;
      if (!headers.trim()) { toast("Paste the request headers first", "error"); return; }
      b.disabled = true; b.textContent = "Connecting…";
      const r = await act(() => api("/api/auth", { method: "POST", body: { headers } }));
      b.disabled = false; b.innerHTML = `${icon("link")} Connect`;
      if (r) this.connected(r);
    }
    if (a === "connect-tab") {
      this.connectTab = b.dataset.tab;
      const form = $("#connect-form");
      if (form) form.innerHTML = this.connectForm();
      else $("#account").innerHTML = this.accountHtml(store.status);
    }
    if (a === "oauth-start") await this.oauthStart(b);
    if (a === "disconnect") {
      if (await confirmDialog({ title: "Disconnect account?", body: "Syncing stops until you connect again. Downloaded files stay where they are.", confirm: "Disconnect", danger: true })) {
        await act(() => api("/api/auth", { method: "DELETE" }), "Disconnected");
        await refreshStatus();
        $("#account").innerHTML = this.accountHtml(store.status);
      }
    }
    if (a === "upload-cookies") {
      let text = $("#cookies-input").value;
      const file = $("#cookies-file").files[0];
      if (file) text = await file.text();
      if (!text.trim()) { toast("Choose a file or paste the cookies first", "error"); return; }
      await act(() => api("/api/cookies", { method: "POST", body: { text } }), "cookies.txt saved");
      await refreshStatus();
      $("#cookies-state").innerHTML = this.cookiesState(store.status);
    }
    if (a === "del-cookies") {
      await act(() => api("/api/cookies", { method: "DELETE" }), "cookies.txt removed");
      await refreshStatus();
      $("#cookies-state").innerHTML = this.cookiesState(store.status);
    }
    if (a === "test-jellyfin") {
      const v = this.collect();
      $("#jf-result").textContent = "Testing…";
      try {
        const r = await api("/api/jellyfin/test", { method: "POST", body: { url: v.jellyfin_url, api_key: v.jellyfin_api_key } });
        $("#jf-result").innerHTML = `<span style="color:var(--success)">Connected to ${esc(r.server_name)} (${esc(r.version)})</span>`;
      } catch (err) { $("#jf-result").innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>`; }
    }
    if (a === "update-ytdlp") {
      b.disabled = true; b.textContent = "Updating…";
      const r = await act(() => api("/api/ytdlp/update", { method: "POST" }));
      b.disabled = false; b.textContent = "Update now";
      if (r) toast(r.updated ? `yt-dlp updated to ${r.after}` : `yt-dlp ${r.after} is already the latest`, "success");
    }
  },
};

/* ===================================================================== router */
const PAGES = { dashboard: Dashboard, playlists: Playlists, tracks: Tracks, trash: Trash, logs: Logs, settings: Settings };

function route() {
  const raw = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [name, query] = raw.split("?");
  const id = PAGES[name] ? name : "dashboard";
  if (store.page?.unmount) store.page.unmount();
  store.route = id;
  store.page = PAGES[id];
  const el = $("#page");
  const fresh = el.cloneNode(false);  // drop old listeners
  el.replaceWith(fresh);
  store.page.mount(fresh, new URLSearchParams(query || ""));
  renderNav();
  document.title = `${NAV.find((n) => n[0] === id)[1]} · YT Music Downloader`;
  window.scrollTo(0, 0);
  schedulePoll();
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const dark = theme ? theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  $("#theme-btn").innerHTML = icon(dark ? "sun" : "moon");
}

(function init() {
  $("#brand-logo").innerHTML = icon("logo");
  let theme = null;
  try { theme = localStorage.getItem("ytmd-theme"); } catch { /* storage unavailable */ }
  applyTheme(theme);
  $("#theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : !matchMedia("(prefers-color-scheme: light)").matches;
    const next = dark ? "light" : "dark";
    try { localStorage.setItem("ytmd-theme", next); } catch { /* ignore */ }
    applyTheme(next);
  });
  window.addEventListener("hashchange", route);
  refreshStatus().then(() => {
    if (!location.hash && store.status && !store.status.auth.connected) location.hash = "#/dashboard";
    route();
  });
})();
