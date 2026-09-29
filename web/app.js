"use strict";

const $ = (s) => document.querySelector(s);
const viewer = $("#viewer");
const select = $("#lecture-select");
const statusEl = $("#update-status");
const messagesEl = $("#messages");
const resultsEl = $("#results");
const scopeEl = $("#search-scope");
const emptyEl = $("#deck-empty");
const ctxMenu = $("#ctx-menu");
const resizer = $("#chat-resizer");

let current = null;              // {lecture, page, title, number}
let mode = "ask";                // ask | search
let lectureVid = {};             // 目录名 -> 外壳自己的讲 id（跳页用的 sessionStorage 键）
let pendingQuote = null;         // 划词菜单选中待用的文本

// ------------------------------------------------------------------ 基础
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return res.json();
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// 极简 markdown（离线，不引 CDN）
function md(src) {
  // 先把 LaTeX 公式换成占位符，避免被转义/markdown 规则破坏，最后用 KaTeX 还原。
  const math = [];
  let s = src;
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    math.push({ display: true, tex });
    return `\u0000K${math.length - 1}\u0000`;
  });
  s = s.replace(/\$([^$\n]+)\$/g, (_, tex) => {
    math.push({ display: false, tex });
    return `\u0000K${math.length - 1}\u0000`;
  });

  s = escapeHtml(s);
  // fenced code blocks
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.replace(/^\n/, "")}</code></pre>`);
  // inline code
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // headers
  s = s.replace(/^#### (.*)$/gm, "<h4>$1</h4>");
  s = s.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  s = s.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  s = s.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  // bold / italic
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  // 列表：整段抓连续的列表行再逐行转 <li>。分两步做，否则有序列表会被并进无序列表里。
  s = s.replace(/(?:^[ \t]*[-*] .*(?:\n|$))+/gm, (m) =>
    "<ul>" + m.replace(/^[ \t]*[-*] (.*)$/gm, "<li>$1</li>").replace(/\n/g, "") + "</ul>");
  s = s.replace(/(?:^[ \t]*\d+\. .*(?:\n|$))+/gm, (m) =>
    "<ol>" + m.replace(/^[ \t]*\d+\. (.*)$/gm, "<li>$1</li>").replace(/\n/g, "") + "</ol>");
  // paragraphs
  s = s.replace(/\n{2,}/g, "</p><p>");
  s = "<p>" + s + "</p>";
  s = s.replace(/<p><(ul|ol|pre|h[1-4])/g, "<$1").replace(/<\/(ul|ol|pre|h[1-4])><\/p>/g, "</$1>");

  // 还原公式
  s = s.replace(/\u0000K(\d+)\u0000/g, (_, i) => {
    const m = math[+i];
    try {
      return katex.renderToString(m.tex, { displayMode: m.display, throwOnError: false });
    } catch (e) {
      return escapeHtml(m.display ? `$$${m.tex}$$` : `$${m.tex}$`);
    }
  });
  return s;
}

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  if (role === "assistant") {
    const inner = document.createElement("div");
    inner.className = "markdown";
    inner.innerHTML = md(text);
    el.appendChild(inner);
  } else {
    el.textContent = text;
  }
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

// ------------------------------------------------------------------ 课件
async function loadCourse() {
  const data = await api("/api/course");
  lectureVid = {};
  select.innerHTML = "";
  if (!data.lectures || data.lectures.length === 0) {
    select.innerHTML = '<option value="">（暂无课件）</option>';
    // 首次启动自动更新可能还在跑：盯一下，跑完自动重载
    const st = await api("/api/update/status");
    if (st.running) { emptyEl.textContent = "正在渲染课件，请稍候…"; watchUpdate(); }
    else { emptyEl.textContent = "还没有课件，点右上角「更新」渲染"; }
    return;
  }
  for (const lec of data.lectures) {
    const o = document.createElement("option");
    o.value = lec.id;
    o.textContent = lec.title + "（" + lec.id + "）";
    select.appendChild(o);
    lectureVid[lec.id] = lec.vid || lec.id;
  }
  openLecture(select.value);
}

function openLecture(id) {
  if (!id) { viewer.src = "about:blank"; current = null; emptyEl.style.display = "flex"; return; }
  emptyEl.style.display = "none";
  viewer.src = "/lectures/" + id + "/index.html";
  current = current && current.lecture === id ? current : null;
}

// 读进两层 iframe：外壳(index.html) → .slide-frame(slides.html)
function innerFrame() {
  let doc;
  try { doc = viewer.contentDocument; } catch (e) { return null; }
  if (!doc) return null;
  try { return doc.querySelector(".slide-frame"); } catch (e) { return null; }
}

function innerDoc() {
  const f = innerFrame();
  if (!f) return null;
  try { return f.contentDocument; } catch (e) { return null; }
}

// 从内嵌的课件阅读器里读出「当前正在看哪一页」
// 外壳用 #N 定位第 N 张幻灯片，与 lecture.json 的 pages[N-1] 一一对应。
function currentPage() {
  let doc;
  try { doc = viewer.contentDocument; } catch (e) { return null; }
  if (!doc) return null;
  const slide = doc.querySelector(".slide-frame");
  if (!slide) return null; // 还在目录页
  let n;
  try { n = parseInt(slide.contentWindow.location.hash.replace(/^#/, ""), 10); } catch (e) { return null; }
  if (isNaN(n)) return null;
  let data;
  try { data = JSON.parse(doc.getElementById("lecture-data").textContent); } catch (e) { return null; }
  const page = data.pages && data.pages[n - 1];
  if (!page) return null;
  return { page: page.id, title: page.title, number: page.number || n, index: n - 1 };
}

// 提问 / 引用要立刻用到当前页，所以用之前先现读一遍（不再靠轮询维护）
function livePage() {
  const p = currentPage();
  if (p) current = { lecture: select.value, ...p };
  return current;
}

// ------------------------------------------------------------------ 跳页
// 外壳把阅读位置存在 sessionStorage（键 lecturekit:viewer:<外壳讲id>）并在载入时恢复，
// 所以跳页 = 改写这个键 + 让外壳重载；已经在看幻灯片时直接改内层 hash 更快。
async function jumpTo(lid, index, pageId) {
  const vid = lectureVid[lid] || lid;
  const key = "lecturekit:viewer:" + vid;
  let st = {};
  try { st = JSON.parse(sessionStorage.getItem(key) || "{}") || {}; } catch (e) { /* 无存储 */ }
  st.mode = "slide";
  st.currentPageId = pageId;
  try { sessionStorage.setItem(key, JSON.stringify(st)); } catch (e) { /* 无存储 */ }

  if (select.value !== lid) {
    select.value = lid;
    openLecture(lid);           // 外壳 restoreState() 会直接落到这一页
    return;
  }
  const frame = innerFrame();
  if (frame) frame.src = "slides.html#" + (index + 1);
  else openLecture(lid);
}

// ------------------------------------------------------------------ 模式切换
function setMode(m) {
  mode = m;
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("active", t.dataset.mode === m);
  }
  const isSearch = m === "search";
  messagesEl.hidden = isSearch;
  resultsEl.hidden = !isSearch;
  scopeEl.hidden = !isSearch;
  $("#question").placeholder = isSearch ? "搜索关键词" : "问问AI助手";
}

// ------------------------------------------------------------------ 检索
async function doSearch() {
  const q = $("#question").value.trim();
  if (!q) return;
  resultsEl.innerHTML = '<div class="result-empty">搜索中…</div>';
  let r;
  try {
    r = await api("/api/search", {
      method: "POST",
      body: { query: q, scope: scopeEl.value, lecture: select.value },
    });
  } catch (e) {
    resultsEl.innerHTML = '<div class="result-empty">搜索失败：' + escapeHtml(e.message) + "</div>";
    return;
  }
  renderResults(r.results || [], q);
}

function renderResults(list, q) {
  resultsEl.innerHTML = "";
  if (!list.length) {
    resultsEl.innerHTML = '<div class="result-empty">没有找到「' + escapeHtml(q) + "」</div>";
    return;
  }
  const head = document.createElement("div");
  head.className = "result-head";
  head.textContent = "找到 " + list.length + " 处（点击跳转）";
  resultsEl.appendChild(head);

  for (const r of list) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "result";
    const meta = document.createElement("div");
    meta.className = "result-meta";
    const tag = document.createElement("span");
    tag.className = "result-tag";
    tag.textContent = "第 " + (r.page_number || "?") + " 页";
    meta.append(tag, document.createTextNode(r.lecture_title || r.lecture));
    const title = document.createElement("div");
    title.className = "result-title";
    title.textContent = r.page_title || r.page;
    row.append(meta, title);
    if (r.snippet) {
      const sn = document.createElement("div");
      sn.className = "result-snippet";
      sn.textContent = r.snippet;
      row.appendChild(sn);
    }
    row.addEventListener("click", () => jumpTo(r.lecture, r.index, r.page));
    resultsEl.appendChild(row);
  }
}

// ------------------------------------------------------------------ 聊天
async function send() {
  const q = $("#question").value.trim();
  if (!q) return;
  if (!select.value) { addMessage("error", "还没有可用的课件，先点右上角「更新」渲染。"); return; }

  addMessage("user", q);
  $("#question").value = "";
  $("#btn-send").disabled = true;

  const p = livePage();
  const loading = addMessage("assistant", "思考中…");
  try {
    const r = await api("/api/chat", {
      method: "POST",
      body: { lecture: select.value, page: p ? p.page : null, question: q },
    });
    loading.remove();
    if (r.error) addMessage("error", "出错了：" + r.error);
    else addMessage("assistant", r.answer || "（空回答）");
  } catch (e) {
    loading.remove();
    addMessage("error", "请求失败：" + e.message);
  } finally {
    $("#btn-send").disabled = false;
    $("#question").focus();
  }
}

// ------------------------------------------------------------------ 划词引用
function quoteLabel() {
  const title = select.options[select.selectedIndex]
    ? select.options[select.selectedIndex].textContent.replace(/（.*?）$/, "")
    : select.value;
  const p = livePage();
  return "[" + title + " 第" + (p ? p.number : "?") + "页]";
}

// 公式是 MathJax 预渲染的 SVG，选不中；但 data-c 属性里存了字符码点，
// 解出来是可读的（上下标会压平，如 10¹ 变 101 —— 配合页面原文的 $...$ 足够用）。
function texFromTarget(node) {
  let el = node;
  while (el && el.nodeType === 1) {
    if (el.tagName && el.tagName.toLowerCase() === "mjx-container") {
      let text = "";
      for (const p of el.querySelectorAll("[data-c]")) {
        try { text += String.fromCodePoint(parseInt(p.getAttribute("data-c"), 16)); }
        catch (e) { /* 跳过坏码点 */ }
      }
      return text.trim();
    }
    el = el.parentNode;
  }
  return "";
}

function openCtxMenu(x, y, text) {
  pendingQuote = text;
  ctxMenu.hidden = false;
  const r = ctxMenu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - r.width - 8);
  const top = Math.min(y, window.innerHeight - r.height - 8);
  ctxMenu.style.left = Math.max(8, left) + "px";
  ctxMenu.style.top = Math.max(8, top) + "px";
}

function closeCtxMenu() {
  ctxMenu.hidden = true;
  pendingQuote = null;
}

// 换讲或重载都会把内层文档整个重建，所以要反复确认监听还在；两个标志位防重复挂。
function ensureQuoteInjection() {
  const doc = innerDoc();
  if (!doc || !doc.body) return;
  if (!doc.__quoteHooked) {
    doc.__quoteHooked = true;
    doc.addEventListener("contextmenu", onInnerContextMenu);
    doc.addEventListener("mouseup", onInnerMouseUp);
  }
  const frame = innerFrame();
  if (frame && !frame.__quoteHooked) {
    frame.__quoteHooked = true;
    // 内层自己导航时立刻补挂，不用等下一次轮询（它已经把 flag 置上了，不会无限递归）
    frame.addEventListener("load", () => { try { ensureQuoteInjection(); } catch (e) { /* 忽略 */ } });
  }
}

// 内层视口坐标 → 外层窗口坐标（两层 iframe 的 rect 相加）
function toWindowXY(e) {
  try {
    const sr = viewer.getBoundingClientRect();
    const frame = innerFrame();
    const dr = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 };
    return { x: sr.left + dr.left + e.clientX, y: sr.top + dr.top + e.clientY };
  } catch (err) {
    return { x: e.clientX, y: e.clientY };
  }
}

function innerSelection(doc) {
  try {
    const s = doc.getSelection();
    if (!s) return "";
    let text = String(s).trim();
    // WebView2 里，由脚本塞进去的 range 会让 Selection.toString() 返回空串（range 本身
    // 是有文字的）。真人拖选两条路结果一样，这里只是留个退路。
    if (!text && s.rangeCount) text = String(s.getRangeAt(0)).trim();
    return text;
  } catch (err) {
    return "";
  }
}

// 划词：松开左键就弹菜单，选区空了（在别处点一下）就收起来。
// iframe 是独立的浏览上下文，事件不会冒泡到外层 document —— 「点一下消失」只能在这儿做。
function onInnerMouseUp(e) {
  if (e.button !== 0) return;          // 右键交给 contextmenu
  const text = innerSelection(e.currentTarget);
  if (!text) { closeCtxMenu(); return; }
  const { x, y } = toWindowXY(e);
  // 稍微错开一点，别让指针正好压在第一个按钮上
  openCtxMenu(x + 2, y + 8, text);
}

function onInnerContextMenu(e) {
  const text = innerSelection(e.currentTarget) || texFromTarget(e.target);
  if (!text) return;          // 没有可引用的内容，放行系统右键菜单

  e.preventDefault();
  const { x, y } = toWindowXY(e);
  openCtxMenu(x, y, text);
}

ctxMenu.addEventListener("click", (e) => {
  const act = e.target.dataset ? e.target.dataset.act : null;
  if (!act) return;
  const text = pendingQuote;
  closeCtxMenu();
  if (!text) return;

  if (act === "copy") {
    navigator.clipboard.writeText(text).catch(() => {});
    return;
  }
  const quoted = "> " + quoteLabel() + " " + text.replace(/\n/g, "\n> ") + "\n\n";
  const box = $("#question");
  if (act === "quote") {
    box.value = quoted + box.value;
    if (mode !== "ask") setMode("ask");
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    return;
  }
  if (act === "explain") {
    if (mode !== "ask") setMode("ask");
    box.value = quoted + "请解释这段话。";
    send();
  }
});

document.addEventListener("mousedown", (e) => {
  if (!ctxMenu.hidden && !ctxMenu.contains(e.target)) closeCtxMenu();
});
window.addEventListener("blur", closeCtxMenu);
// 内层翻页/滚动后关掉菜单
document.addEventListener("scroll", closeCtxMenu, true);

// ------------------------------------------------------------------ 导出 PDF
let exportTimer = null;

async function exportPdf() {
  const lid = select.value;
  if (!lid) { statusEl.textContent = "先选一讲"; return; }
  const opt = select.options[select.selectedIndex];
  const title = opt ? opt.textContent.replace(/（.*?）$/, "") : lid;

  let dest = "";
  const bridge = window.pywebview && window.pywebview.api;
  if (bridge && bridge.save_pdf_dialog) {
    dest = await bridge.save_pdf_dialog(title + ".pdf");
    if (!dest) return;                        // 用户取消
  }                                          // 否则留空：后端落到 exe 旁的 exports/

  statusEl.textContent = "正在导出…";
  const r = await api("/api/export_pdf", { method: "POST", body: { lecture: lid, dest } });
  if (r.error) { statusEl.textContent = "导出失败：" + r.error; return; }
  if (!r.started) { statusEl.textContent = "已有导出在进行"; return; }
  watchExport();
}

function watchExport() {
  if (exportTimer) clearInterval(exportTimer);
  $("#btn-export").disabled = true;
  exportTimer = setInterval(async () => {
    const st = await api("/api/export/status");
    const last = st.lines && st.lines.length ? st.lines[st.lines.length - 1] : "";
    statusEl.textContent = st.running ? "导出中：" + last : (st.error ? "导出失败" : "已导出");
    if (st.running) return;
    clearInterval(exportTimer);
    exportTimer = null;
    $("#btn-export").disabled = false;
    if (st.error) {
      if (mode !== "ask") setMode("ask");
      addMessage("error", "导出失败：" + st.error);
    } else if (st.result && st.result.path) {
      if (mode !== "ask") setMode("ask");
      showExportDone(st.result.path);
    }
  }, 1200);
}

function showExportDone(path) {
  const el = document.createElement("div");
  el.className = "msg assistant";
  const inner = document.createElement("div");
  inner.className = "markdown";
  const p = document.createElement("p");
  p.textContent = "PDF 已导出：";
  const code = document.createElement("code");
  code.textContent = path;
  const p2 = document.createElement("p");
  p2.appendChild(code);
  inner.append(p, p2);
  el.appendChild(inner);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "打开文件夹";
  btn.addEventListener("click", () => api("/api/reveal", { method: "POST", body: { path } }));
  el.appendChild(btn);

  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ------------------------------------------------------------------ 更新
let updateTimer = null;
function watchUpdate() {
  if (updateTimer) clearInterval(updateTimer);
  $("#btn-update").disabled = true;
  updateTimer = setInterval(async () => {
    const st = await api("/api/update/status");
    const last = st.lines && st.lines.length ? st.lines[st.lines.length - 1] : "";
    statusEl.textContent = (st.running ? "更新中：" : "完成：") + (last || "");
    if (!st.running) {
      clearInterval(updateTimer);
      updateTimer = null;
      $("#btn-update").disabled = false;
      loadCourse(); // 重新读课程列表
    }
  }, 1200);
}

async function triggerUpdate() {
  await api("/api/update", { method: "POST" });
  statusEl.textContent = "更新已启动…";
  watchUpdate();
}

// ------------------------------------------------------------------ 向导
let installTimer = null;

async function openWizard() {
  const w = await api("/api/wizard");
  renderWizard(w.items || []);
  $("#wizard-modal").showModal();
}

function renderWizard(items) {
  const list = $("#wizard-list");
  list.innerHTML = "";
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "wizard-row " + (it.ok ? "ok" : (it.skip ? "skip" : "bad"));

    const mark = document.createElement("span");
    mark.className = "wizard-mark";
    mark.textContent = it.ok ? "✓" : (it.skip ? "–" : "✗");

    const body = document.createElement("div");
    body.className = "wizard-body";
    const name = document.createElement("div");
    name.className = "wizard-name";
    name.textContent = it.label;
    const detail = document.createElement("div");
    detail.className = "wizard-detail";
    detail.textContent = it.detail || "";
    body.append(name, detail);
    row.append(mark, body);

    if (!it.ok && !it.skip && it.action) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = it.action.kind === "clone" ? "选目录并克隆" : "安装";
      btn.addEventListener("click", () => runInstall(it.key, btn));
      row.appendChild(btn);
    }
    list.appendChild(row);
  }
}

async function runInstall(key, btn) {
  let dir = null;
  if (key === "repo") {
    const bridge = window.pywebview && window.pywebview.api;
    if (bridge && bridge.pick_directory) dir = await bridge.pick_directory();
    if (!dir) dir = prompt("把课程仓库克隆到哪里？请输入完整路径：");
    if (!dir) return;
  }
  btn.disabled = true;
  const r = await api("/api/wizard/install", { method: "POST", body: { key, dir } });
  if (r.error) { btn.disabled = false; statusEl.textContent = "安装失败：" + r.error; return; }
  if (!r.started) { btn.disabled = false; return; }
  watchInstall();
}

function watchInstall() {
  if (installTimer) clearInterval(installTimer);
  const logEl = $("#wizard-log");
  logEl.hidden = false;
  logEl.textContent = "正在安装…";
  installTimer = setInterval(async () => {
    const st = await api("/api/wizard/status");
    logEl.textContent = (st.lines || []).join("\n") || "…";
    logEl.scrollTop = logEl.scrollHeight;
    if (st.running) return;
    clearInterval(installTimer);
    installTimer = null;
    if (st.error) logEl.textContent += "\n" + st.error;
    const w = await api("/api/wizard");
    renderWizard(w.items || []);
  }, 1200);
}

// ------------------------------------------------------------------ 界面偏好
// 主题和侧栏宽度都存在 config.json 里 —— pywebview 默认 private_mode，cookies 和
// localStorage 都不保留，存那边等于没存。首绘用的主题见 index.html 的内联脚本。
const media = window.matchMedia("(prefers-color-scheme: dark)");
const MIN_CHAT_W = 280;
let prefs = { theme: "system", chat_width: 440 };
let chatW = 440;

function applyTheme() {
  const t = prefs.theme === "light" || prefs.theme === "dark"
    ? prefs.theme
    : (media.matches ? "dark" : "light");
  document.documentElement.dataset.theme = t;
}

// 选了「跟随系统」才理系统，强制了就别跟
function onSystemThemeChange() {
  if (prefs.theme !== "light" && prefs.theme !== "dark") applyTheme();
}
if (media.addEventListener) media.addEventListener("change", onSystemThemeChange);
else if (media.addListener) media.addListener(onSystemThemeChange);

function maxChatW() { return Math.max(MIN_CHAT_W, Math.round(window.innerWidth * 0.55)); }

function applyChatWidth(w) {
  const n = Math.round(Number(w));
  // 只挡「根本没有值」。负数是合法的：指针拖到窗口右边以外时宽度算出来就是负的，
  // 那种情况要夹到下限，不能让侧栏卡在原地不动。
  if (!isFinite(n)) return 0;
  chatW = Math.min(Math.max(n, MIN_CHAT_W), maxChatW());
  document.documentElement.style.setProperty("--chat-w", chatW + "px");
  return chatW;
}

async function loadPrefs() {
  try {
    const c = await api("/api/config");
    prefs.theme = c.theme || "system";
    prefs.chat_width = c.chat_width || 440;
  } catch (e) { /* 读不到就按默认值来 */ }
  applyTheme();
  applyChatWidth(prefs.chat_width);
}

// ------------------------------------------------------------------ 设置
async function openSettings() {
  const c = await api("/api/config");
  $("#cfg-base").value = c.api_base || "";
  $("#cfg-model").value = c.model || "";
  $("#cfg-key").value = c.api_key || "";
  $("#cfg-autoupdate").checked = !!c.auto_update;
  $("#cfg-theme").value = c.theme || "system";
  // 上一轮探测的残留别留到这一轮，免得看起来像是刚测出来的
  $("#test-result").hidden = true;
  $("#model-picker").hidden = true;
  $("#settings-modal").showModal();
}

// 测试连接 / 列出模型都用输入框里的「当前值」，不必先保存
function probeBody() {
  return {
    api_base: $("#cfg-base").value.trim(),
    model: $("#cfg-model").value.trim(),
    api_key: $("#cfg-key").value.trim(),
  };
}

async function saveSettings() {
  prefs.theme = $("#cfg-theme").value;
  await api("/api/config", {
    method: "POST",
    body: {
      api_base: $("#cfg-base").value.trim(),
      model: $("#cfg-model").value.trim(),
      api_key: $("#cfg-key").value.trim(),
      auto_update: $("#cfg-autoupdate").checked,
      theme: prefs.theme,
    },
  });
  applyTheme();
  $("#settings-modal").close();
  loadCourse();
}

// ------------------------------------------------------------------ 绑定
select.addEventListener("change", () => openLecture(select.value));
$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (mode === "search") doSearch();
  else send();
});
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (mode === "search") doSearch(); else send(); }
});
for (const t of document.querySelectorAll(".tab")) {
  t.addEventListener("click", () => setMode(t.dataset.mode));
}
$("#btn-update").addEventListener("click", triggerUpdate);
$("#btn-export").addEventListener("click", exportPdf);
$("#btn-settings").addEventListener("click", openSettings);
$("#btn-save").addEventListener("click", saveSettings);
$("#btn-cancel").addEventListener("click", () => $("#settings-modal").close());
$("#btn-wizard-open").addEventListener("click", () => { $("#settings-modal").close(); openWizard(); });
// 后端根本没能拼出候选地址时（没填 base / 没填 key），urls 是缺的 —— 那时候还报
// 「尝试的地址：(没填 Base URL)」只会把人带偏，比如明明只是 key 没填
function triedLine(urls) {
  return urls && urls.length ? "\n尝试的地址：" + urls.join("\n            ") : "";
}
$("#btn-test-api").addEventListener("click", async () => {
  const el = $("#test-result");
  const btn = $("#btn-test-api");
  el.hidden = false;
  el.className = "test-result";
  el.textContent = "正在测试…";
  btn.disabled = true;
  try {
    const r = await api("/api/test_api", { method: "POST", body: probeBody() });
    el.className = "test-result " + (r.ok ? "ok" : "bad");
    el.textContent = (r.ok ? "✓ " + r.detail : "✗ " + r.error) + triedLine(r.urls);
  } catch (e) {
    el.className = "test-result bad";
    el.textContent = "✗ 测试请求本身失败了：" + e.message;
  } finally {
    btn.disabled = false;
  }
});
// 中转站的模型名和官方不一样，还常带自己的后缀。列出来让人点，比让他猜一个强。
$("#btn-list-models").addEventListener("click", async () => {
  const el = $("#test-result");
  const picker = $("#model-picker");
  const btn = $("#btn-list-models");
  el.hidden = false;
  picker.hidden = true;
  el.className = "test-result";
  el.textContent = "正在取模型列表…";
  btn.disabled = true;
  try {
    const r = await api("/api/models", { method: "POST", body: probeBody() });
    if (!r.ok) {
      el.className = "test-result bad";
      el.textContent = "✗ " + r.error + triedLine(r.urls);
      return;
    }
    el.className = "test-result ok";
    el.textContent = "共 " + r.models.length + " 个模型，点一个填进「模型」框：";
    picker.innerHTML = "";
    const cur = $("#cfg-model").value.trim();
    for (const id of r.models) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "model-chip" + (id === cur ? " current" : "");
      b.textContent = id;
      b.addEventListener("click", () => {
        $("#cfg-model").value = id;
        for (const c of picker.children) c.classList.toggle("current", c === b);
      });
      picker.appendChild(b);
    }
    picker.hidden = false;
  } catch (e) {
    el.className = "test-result bad";
    el.textContent = "✗ 取模型列表的请求本身失败了：" + e.message;
  } finally {
    btn.disabled = false;
  }
});
$("#btn-wizard-close").addEventListener("click", () => $("#wizard-modal").close());
$("#btn-wizard-redetect").addEventListener("click", () => {
  $("#wizard-log").hidden = true;
  openWizard();
});
$("#btn-wizard-dismiss").addEventListener("click", async () => {
  const w = await api("/api/wizard");
  const cur = await api("/api/config");
  const merged = Array.from(new Set([].concat(cur.wizard_dismissed || [], w.missing || [])));
  await api("/api/config", { method: "POST", body: { wizard_dismissed: merged } });
  $("#wizard-modal").close();
});

// ------------------------------------------------------------------ 侧栏拖拽
// 侧栏贴着窗口右边，所以宽度 = 窗口右边到指针的距离。拖动期间只改 CSS 变量，
// 松手才写 config.json（免得一次拖动发几十个请求）。
function dragWidth(e) { return window.innerWidth - e.clientX; }

resizer.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  resizer.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
});
resizer.addEventListener("pointermove", (e) => {
  if (!resizer.hasPointerCapture(e.pointerId)) return;
  applyChatWidth(dragWidth(e));
});
function endResize(e) {
  if (!resizer.hasPointerCapture(e.pointerId)) return;
  resizer.releasePointerCapture(e.pointerId);
  document.body.classList.remove("resizing");
  const w = applyChatWidth(dragWidth(e));
  if (w) api("/api/config", { method: "POST", body: { chat_width: w } }).catch(() => {});
}
resizer.addEventListener("pointerup", endResize);
// 指针被系统收走（Alt+Tab、切窗口）时不会再有 pointerup，而且捕获已经自动释放 ——
// 走 endResize 会被 hasPointerCapture 挡回来，body 上的锁就再也解不掉了
resizer.addEventListener("pointercancel", () => document.body.classList.remove("resizing"));
// 窗口被拉窄时把记住的宽度夹回来，否则侧栏会把课件挤没
window.addEventListener("resize", () => applyChatWidth(chatW));

setMode("ask");
loadPrefs();
setInterval(ensureQuoteInjection, 600);
// 外壳重载后 .slide-frame 要等 viewer.js 跑完才建出来，光靠轮询会漏掉刚载入的头几百毫秒
viewer.addEventListener("load", () => {
  for (const t of [0, 200, 600]) setTimeout(ensureQuoteInjection, t);
});
loadCourse().then(async () => {
  // 启动时环境不齐就直接把向导摆出来（用户点过「不再提示」的缺失项不算）
  try {
    const w = await api("/api/wizard");
    if (w.should_prompt) {
      renderWizard(w.items || []);
      $("#wizard-modal").showModal();
    }
  } catch (e) { /* 检测失败不打扰 */ }
});
