/* 管理端逻辑：上传、选档、选择入口与截止时间、预览、回放记录/缺档清单。 */
"use strict";

const $ = (id) => document.getElementById(id);
let currentArchive = null;
let replayBase = null;
let logTimer = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}

function fmtCutoff(epoch) {
  return new Date(epoch * 1000).toISOString().replace(".000Z", "Z");
}

/* ---------- 初始化 ---------- */
async function init() {
  const cfg = await api("/api/config");
  replayBase = location.protocol + "//" + location.hostname + ":" + cfg.replay_port;
  await refreshArchives();
  bind();
}

function bind() {
  $("uploadBtn").onclick = upload;
  $("replayBtn").onclick = startReplay;
  $("openTab").onclick = (e) => { e.preventDefault(); openPreview(true); };
  $("tabAll").onclick = () => refreshLog(false);
  $("tabMissing").onclick = () => refreshLog(true);
  $("refreshLog").onclick = () => refreshLog($("tabMissing").classList.contains("active"));
  $("clearLog").onclick = clearLog;
  logTimer = setInterval(() => {
    if ($("autoRefresh").checked && currentArchive &&
        $("logSection").style.display !== "none") {
      refreshLog($("tabMissing").classList.contains("active"), true);
    }
  }, 3000);
}

/* ---------- 上传 ---------- */
async function upload() {
  const f = $("file").files[0];
  const msg = $("uploadMsg");
  if (!f) { msg.textContent = "请先选择文件"; msg.className = "err"; return; }
  msg.textContent = "上传并导入中…"; msg.className = "muted";
  $("uploadBtn").disabled = true;
  try {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/archives", { method: "POST", body: fd });
    msg.textContent = `导入成功：档案 #${r.id}，共 ${r.records} 条记录`;
    msg.className = "ok";
    await refreshArchives();
  } catch (e) {
    msg.textContent = "导入失败：" + e.message;
    msg.className = "err";
  } finally {
    $("uploadBtn").disabled = false;
  }
}

/* ---------- 档案列表 ---------- */
async function refreshArchives() {
  const rows = await api("/api/archives");
  const tb = $("archives").querySelector("tbody");
  tb.innerHTML = "";
  for (const a of rows) {
    const tr = document.createElement("tr");
    const status = a.status === "ok"
      ? '<span class="ok">可用</span>'
      : `<span class="err" title="${esc(a.error || "")}">导入失败</span>`;
    tr.innerHTML =
      `<td>${a.id}</td><td>${esc(a.filename)}</td><td>${esc(a.uploaded_at)}</td>` +
      `<td>${status}</td><td>${a.record_count}</td><td></td>`;
    const td = tr.lastChild;
    if (a.status === "ok") {
      const pick = document.createElement("button");
      pick.textContent = "选择";
      pick.onclick = () => selectArchive(a.id);
      td.appendChild(pick);
      td.appendChild(document.createTextNode(" "));
    }
    const del = document.createElement("button");
    del.textContent = "删除";
    del.className = "danger";
    del.onclick = async () => {
      if (!confirm(`删除档案 #${a.id} 及其全部记录？`)) return;
      await api("/api/archives/" + a.id, { method: "DELETE" });
      if (currentArchive === a.id) {
        currentArchive = null;
        $("replaySection").style.display = "none";
      }
      refreshArchives();
    };
    td.appendChild(del);
    tb.appendChild(tr);
  }
}

/* ---------- 选择档案：载入 URL 清单 ---------- */
async function selectArchive(id) {
  currentArchive = id;
  const urls = await api(`/api/archives/${id}/urls`);
  const dl = $("urlList");
  dl.innerHTML = "";
  let maxTs = null;
  for (const u of urls) {
    const opt = document.createElement("option");
    opt.value = u.url;
    opt.label = `${u.captures} 次捕获 · ${u.first_ts} ~ ${u.last_ts}`;
    dl.appendChild(opt);
    if (!maxTs || u.last_ts > maxTs) maxTs = u.last_ts;
  }
  if (urls.length && !$("urlInput").value) $("urlInput").value = urls[0].url;
  if (maxTs && !$("cutoff").value) {
    // 默认截止时间 = 全档案最晚采集时间（UTC）
    $("cutoff").value = maxTs.replace("Z", "").slice(0, 19);
  }
  $("replaySection").style.display = "";
  $("logSection").style.display = "";
  refreshLog(true);
}

/* ---------- 回放 ---------- */
function cutoffEpoch() {
  const v = $("cutoff").value;
  if (!v) return null;
  const iso = v.length === 16 ? v + ":00Z" : v + "Z";
  const t = Date.parse(iso);
  return isNaN(t) ? null : Math.floor(t / 1000);
}

function replayUrl() {
  const url = $("urlInput").value.trim();
  const cut = cutoffEpoch();
  if (!url || cut === null || !currentArchive) return null;
  return `${replayBase}/web/${currentArchive}/${cut}/${encodeURIComponent(url)}`;
}

function startReplay() {
  const u = replayUrl();
  if (!u) { alert("请选择档案、入口 URL 与截止时间（UTC）"); return; }
  const frame = $("preview");
  frame.src = u;
  $("previewSection").style.display = "";
  refreshLog($("tabMissing").classList.contains("active"));
}

function openPreview(newTab) {
  const u = replayUrl();
  if (!u) { alert("请先选择档案、入口 URL 与截止时间"); return; }
  window.open(u, "_blank", "noopener");
}

/* ---------- 回放记录 / 缺档清单 ---------- */
async function refreshLog(missingOnly, silent) {
  if (!currentArchive) return;
  $("tabAll").classList.toggle("active", !missingOnly);
  $("tabMissing").classList.toggle("active", !!missingOnly);
  const rows = await api(
    `/api/archives/${currentArchive}/log?missing_only=${missingOnly ? 1 : 0}`);
  const tb = $("log").querySelector("tbody");
  tb.innerHTML = "";
  if (!rows.length && !silent) {
    tb.innerHTML = '<tr><td colspan="7" class="muted">暂无记录</td></tr>';
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const state = r.hit
      ? `<span class="hit">命中</span>`
      : '<span class="miss">缺档</span>';
    tr.innerHTML =
      `<td>${esc(r.logged_at)}</td><td>${esc(r.requested_url)}</td>` +
      `<td>${state}</td><td>${esc(r.hit_ts || "—")}</td>` +
      `<td>${r.http_status ?? "—"}</td><td>${esc(r.record_type || r.via || "—")}</td>` +
      `<td class="miss">${esc(r.reason || "")}</td>`;
    tb.appendChild(tr);
  }
}

async function clearLog() {
  if (!currentArchive) return;
  await api(`/api/archives/${currentArchive}/log/clear`, { method: "POST" });
  refreshLog($("tabMissing").classList.contains("active"));
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

init().catch((e) => { $("uploadMsg").textContent = "初始化失败：" + e.message; });
