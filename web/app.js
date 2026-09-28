"use strict";

const $ = (s) => document.querySelector(s);
const viewer = $("#viewer");
const select = $("#lecture-select");
const statusEl = $("#update-status");
const currentPageEl = $("#current-page");
const messagesEl = $("#messages");
const emptyEl = $("#deck-empty");

let current = null; // {lecture, page, title, number}

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
  // unordered lists
  s = s.replace(/^[ \t]*[-*] (.*)$/gm, "<li>$1</li>");
  s = s.replace(/(<li>.*<\/li>)(\n<li>)/g, "$1$2");
  s = s.replace(/((?:<li>.*<\/li>\n?)+)/g, (m) => `<ul>${m.replace(/\n/g, "")}</ul>`);
  // ordered lists
  s = s.replace(/^[ \t]*\d+\. (.*)$/gm, "<li>$1</li>");
  // paragraphs
  s = s.replace(/\n{2,}/g, "</p><p>");
  s = "<p>" + s + "</p>";
  s = s.replace(/<p><(ul|pre|h[1-4])/g, "<$1").replace(/<\/(ul|pre|h[1-4])><\/p>/g, "</$1>");

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
  }
  openLecture(select.value);
}

function openLecture(id) {
  if (!id) { viewer.src = "about:blank"; current = null; emptyEl.style.display = "flex"; return; }
  emptyEl.style.display = "none";
  viewer.src = "/lectures/" + id + "/index.html";
  current = current && current.lecture === id ? current : null;
}

// 从内嵌的课件阅读器里读出「当前正在看哪一页」
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
  return { page: page.id, title: page.title, number: page.number || n };
}

function pollPage() {
  const p = currentPage();
  if (p) {
    current = { lecture: select.value, ...p };
    currentPageEl.textContent = "正在看：第 " + p.number + " 页《" + p.title + "》";
  } else if (!viewer.src || viewer.src === "about:blank") {
    currentPageEl.textContent = "未在阅读某一页";
  } else {
    currentPageEl.textContent = "在目录页 — 点进某一页即可提问";
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

  const loading = addMessage("assistant", "思考中…");
  try {
    const r = await api("/api/chat", {
      method: "POST",
      body: { lecture: select.value, page: current ? current.page : null, question: q },
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

// ------------------------------------------------------------------ 设置
async function openSettings() {
  const c = await api("/api/config");
  $("#cfg-base").value = c.api_base || "";
  $("#cfg-model").value = c.model || "";
  $("#cfg-key").value = c.api_key || "";
  $("#cfg-autoupdate").checked = !!c.auto_update;
  $("#settings-modal").showModal();
}

async function saveSettings() {
  await api("/api/config", {
    method: "POST",
    body: {
      api_base: $("#cfg-base").value.trim(),
      model: $("#cfg-model").value.trim(),
      api_key: $("#cfg-key").value.trim(),
      auto_update: $("#cfg-autoupdate").checked,
    },
  });
  $("#settings-modal").close();
  loadCourse();
}

// ------------------------------------------------------------------ 绑定
select.addEventListener("change", () => openLecture(select.value));
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("#btn-update").addEventListener("click", triggerUpdate);
$("#btn-settings").addEventListener("click", openSettings);
$("#btn-save").addEventListener("click", saveSettings);
$("#btn-cancel").addEventListener("click", () => $("#settings-modal").close());

setInterval(pollPage, 600);
loadCourse();
