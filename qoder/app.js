const state = { view: "overview", notes: [], overview: {}, mode: "hybrid", subject: "", currentNote: "", currentCard: "", reviewCards: [] };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function highlight(text, query) {
  const position = text.toLowerCase().indexOf(query.toLowerCase());
  if (position < 0 || !query) return escapeHtml(text);
  return escapeHtml(text.slice(0, position)) + "<mark>" + escapeHtml(text.slice(position, position + query.length)) + "</mark>" + escapeHtml(text.slice(position + query.length));
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

let toastTimer;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "操作失败，请稍后重试");
  return data;
}

const viewMeta = {
  overview: ["WORKSPACE / 01", "今日概览"],
  library: ["WORKSPACE / 02", "我的知识库"],
  search: ["WORKSPACE / 03", "混合检索"],
  review: ["WORKSPACE / 04", "复习空间"],
  technology: ["WORKSPACE / 05", "技术与验证"],
};

function showView(view) {
  if (!viewMeta[view]) view = "overview";
  state.view = view;
  $$(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${view}`));
  $$(".nav-item[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  $("#view-eyebrow").textContent = viewMeta[view][0];
  $("#view-title").textContent = viewMeta[view][1];
  history.replaceState(null, "", `#${view}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (view === "review") loadReview().catch((error) => toast(error.message, true));
}

async function refreshAll() {
  const [overview, notes] = await Promise.all([api("/api/overview"), api("/api/notes")]);
  state.overview = overview;
  state.notes = notes.notes;
  renderOverview();
  renderSubjects();
  renderNotes();
}

function renderOverview() {
  const data = state.overview;
  ["notes", "chunks", "due"].forEach((key) => { $(`#stat-${key}`).textContent = data[key] || 0; });
  $("#stat-mastery").textContent = data.mastery || 0;
  $("#mastery-bar").style.width = `${data.mastery || 0}%`;
  $("#nav-due").textContent = data.due || 0;
  $("#nav-due").classList.toggle("hidden", !data.due);
  const recent = data.recent || [];
  $("#recent-activity").innerHTML = recent.length ? recent.map((item) => `
    <div class="activity-item"><i></i><div><strong>${escapeHtml(item.summary)}</strong><small>${escapeHtml(item.detail)}</small></div><time>${formatDate(item.created_at)}</time></div>
  `).join("") : '<div class="empty-compact">载入示例后，这里会记录真实读写。</div>';
  $("#tech-events").innerHTML = recent.length ? recent.map((item) => `
    <div class="tech-event"><strong>${escapeHtml(item.summary)}</strong><code>${escapeHtml(item.detail || item.event_type)}</code></div>
  `).join("") : '<div class="empty-compact">暂无技术记录。</div>';
}

function renderSubjects() {
  const subjects = Object.keys(state.overview.subjects || {});
  $("#subject-filters").innerHTML = '<button class="chip active" data-subject="">全部</button>' + subjects.map((subject) => `<button class="chip" data-subject="${escapeHtml(subject)}">${escapeHtml(subject)}</button>`).join("");
  const current = $("#search-subject").value;
  $("#search-subject").innerHTML = '<option value="">全部分类</option>' + subjects.map((subject) => `<option value="${escapeHtml(subject)}">${escapeHtml(subject)}</option>`).join("");
  $("#search-subject").value = current;
}

function renderNotes() {
  const notes = state.subject ? state.notes.filter((note) => note.subject === state.subject) : state.notes;
  $("#library-empty").classList.toggle("hidden", state.notes.length > 0);
  $("#notes-grid").innerHTML = notes.map((note, index) => `
    <article class="note-card">
      <div class="note-card-top"><span class="tag">${escapeHtml(note.subject)}</span><span class="note-card-number">${String(index + 1).padStart(2, "0")}</span></div>
      <h3>${escapeHtml(note.title)}</h3><p>${escapeHtml(note.preview)}</p>
      <div class="note-card-footer"><span>${note.chunks} 个片段 · ${escapeHtml(note.source)}</span><button data-note="${note.note_id}">打开 ↗</button></div>
    </article>
  `).join("");
}

async function loadDemo() {
  try {
    toast("正在写入示例并建立索引…");
    await api("/api/demo", { method: "POST", body: "{}" });
    await refreshAll();
    toast("示例知识库已就绪，12 张复习卡同步生成");
  } catch (error) { toast(error.message, true); }
}

async function openNote(noteId) {
  try {
    const data = await api(`/api/notes/${noteId}`);
    const note = data.note;
    state.currentNote = noteId;
    $("#detail-subject").textContent = note.subject;
    $("#detail-title").textContent = note.title;
    $("#detail-meta").textContent = `${note.source} · ${note.chunks} 个检索片段 · ${formatDate(note.created_at)}`;
    $("#detail-content").textContent = note.content;
    $("#detail-card-status").textContent = data.cards.length ? `已有 ${data.cards.length} 张复习卡，生成时会保留原有复习进度。` : "尚未生成复习卡";
    $("#generate-cards").textContent = data.cards.length ? "查看已有复习卡 ↗" : "生成复习卡 ↗";
    $("#note-dialog").showModal();
  } catch (error) { toast(error.message, true); }
}

async function generateCards() {
  try {
    const data = await api(`/api/notes/${state.currentNote}/cards`, { method: "POST", body: "{}" });
    $("#detail-card-status").textContent = data.created ? `已生成 ${data.created} 张复习卡并写入 seekdb。` : `已有 ${data.cards.length} 张复习卡，未重复生成。`;
    $("#generate-cards").textContent = "查看已有复习卡 ↗";
    await refreshAll();
    toast(data.created ? `已生成 ${data.created} 张复习卡` : "复习卡已存在，学习进度已保留");
    if (!data.created) {
      $("#note-dialog").close();
      showView("review");
    }
  } catch (error) { toast(error.message, true); }
}

async function deleteNote() {
  if (!confirm("确定删除这篇笔记及其关联复习卡吗？此操作不可撤销。")) return;
  try {
    await api(`/api/notes/${state.currentNote}`, { method: "DELETE" });
    $("#note-dialog").close();
    await refreshAll();
    toast("笔记及关联复习卡已删除");
  } catch (error) { toast(error.message, true); }
}

async function executeSearch(query) {
  const input = $("#search-input");
  input.value = query || input.value.trim();
  if (!input.value.trim()) return;
  const button = $('#search-form button[type="submit"]');
  button.disabled = true;
  button.textContent = "检索中…";
  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query: input.value.trim(), mode: state.mode, subject: $("#search-subject").value }),
    });
    renderSearch(data, input.value.trim());
    await refreshAll();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = "检索<span>↗</span>"; }
}

function renderSearch(data, query) {
  $("#suggestion-area").classList.add("hidden");
  $("#search-summary").classList.remove("hidden");
  $("#search-summary").innerHTML = `找到 <strong>${data.results.length}</strong> 条原文依据 · ${data.trace.elapsed_ms} ms · ${escapeHtml(data.trace.engine)}`;
  $("#search-results").innerHTML = data.results.length ? data.results.map((result, index) => `
    <article class="result-card">
      <div class="result-card-top"><span class="result-number">${index + 1}</span><h3>${escapeHtml(result.title)}</h3><span class="tag">${escapeHtml(result.subject)}</span></div>
      <p class="result-snippet">${highlight(result.snippet, query)}</p>
      <div class="result-footer"><span>来源：${escapeHtml(result.source)} · 排序分 ${result.score}</span><button data-note="${result.note_id}">核对原文 ↗</button></div>
    </article>
  `).join("") : '<div class="empty-state"><h3>没有找到匹配内容</h3><p>试试更短的关键词、切换混合检索，或取消分类筛选。</p></div>';
  const trace = data.trace;
  $("#search-trace").innerHTML = `
    <dl class="trace-list">
      <div><dt>执行引擎</dt><dd>${escapeHtml(trace.engine)}</dd></div><div><dt>检索模式</dt><dd>${escapeHtml(trace.mode)}</dd></div>
      <div><dt>向量索引</dt><dd>${escapeHtml(trace.vector_index)}</dd></div><div><dt>全文索引</dt><dd>${escapeHtml(trace.fulltext_index)}</dd></div>
      <div><dt>融合排序</dt><dd>${escapeHtml(trace.fusion)}</dd></div><div><dt>分类过滤</dt><dd>${escapeHtml(trace.subject)}</dd></div>
      <div><dt>执行耗时</dt><dd>${trace.elapsed_ms} ms</dd></div><div><dt>审计事件</dt><dd>${escapeHtml(trace.event_id)}</dd></div>
    </dl><div class="trace-ids">命中 ID<br>${trace.result_ids.map(escapeHtml).join("<br>") || "无"}</div>`;
}

async function loadReview() {
  const data = await api("/api/review");
  state.reviewCards = data.cards;
  $("#review-due").textContent = data.due;
  $("#review-mastery").textContent = `${data.mastery}%`;
  if (!state.reviewCards.length) {
    state.currentCard = "";
    $("#review-subject").textContent = "等待开始";
    $("#review-question").textContent = state.notes.length ? "打开我的知识库，为一篇笔记生成复习卡。" : "先导入笔记或载入示例，再开始主动回忆。";
    $("#review-answer").classList.add("hidden");
    $("#review-actions").classList.remove("hidden");
    $("#reveal-answer").disabled = true;
    $("#rating-actions").classList.add("hidden");
    $("#review-queue").innerHTML = '<div class="empty-compact">暂无复习卡。</div>';
    return;
  }
  const current = state.reviewCards.find((card) => card.card_id === state.currentCard) || state.reviewCards[0];
  showReviewCard(current);
  $("#review-queue").innerHTML = state.reviewCards.slice(0, 12).map((card) => `
    <button class="queue-item ${card.card_id === current.card_id ? "active" : ""}" data-card="${card.card_id}"><strong>${escapeHtml(card.question)}</strong><small>${escapeHtml(card.title)} · 掌握 ${card.mastery}% · ${formatDate(card.due_at)}</small></button>
  `).join("");
}

function showReviewCard(card) {
  state.currentCard = card.card_id;
  $("#review-subject").textContent = card.subject;
  $("#review-progress").textContent = `${card.last_result} · 掌握 ${card.mastery}%`;
  $("#review-question").textContent = card.question;
  $("#review-answer-text").textContent = card.answer;
  $("#review-answer").classList.add("hidden");
  $("#review-actions").classList.remove("hidden");
  $("#reveal-answer").disabled = false;
  $("#rating-actions").classList.add("hidden");
}

function revealAnswer() {
  if (!state.currentCard) return;
  $("#review-answer").classList.remove("hidden");
  $("#review-actions").classList.add("hidden");
  $("#rating-actions").classList.remove("hidden");
}

async function rateCard(rating) {
  try {
    await api(`/api/review/${state.currentCard}`, { method: "POST", body: JSON.stringify({ rating }) });
    state.currentCard = "";
    await Promise.all([loadReview(), refreshAll()]);
    toast("复习反馈已保存，下次复习时间已更新");
  } catch (error) { toast(error.message, true); }
}

function bindEvents() {
  document.addEventListener("click", (event) => {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) showView(viewButton.dataset.view);
    const noteButton = event.target.closest("[data-note]");
    if (noteButton) openNote(noteButton.dataset.note);
    const cardButton = event.target.closest("[data-card]");
    if (cardButton) {
      const card = state.reviewCards.find((item) => item.card_id === cardButton.dataset.card);
      if (card) { showReviewCard(card); loadReview().catch(() => {}); }
    }
    const queryButton = event.target.closest("[data-query]");
    if (queryButton) executeSearch(queryButton.dataset.query);
    const modeButton = event.target.closest("[data-mode]");
    if (modeButton) {
      state.mode = modeButton.dataset.mode;
      $$("[data-mode]").forEach((node) => node.classList.toggle("active", node === modeButton));
    }
    const subjectButton = event.target.closest("[data-subject]");
    if (subjectButton) {
      state.subject = subjectButton.dataset.subject;
      $$("[data-subject]").forEach((node) => node.classList.toggle("active", node === subjectButton));
      renderNotes();
    }
    const ratingButton = event.target.closest("[data-rating]");
    if (ratingButton) rateCard(ratingButton.dataset.rating);
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "open-import") $("#import-dialog").showModal();
    if (action === "close-import") $("#import-dialog").close();
    if (action === "close-note") $("#note-dialog").close();
    if (action === "load-demo") loadDemo();
    if (action === "refresh") refreshAll().then(() => toast("数据已刷新")).catch((error) => toast(error.message, true));
  });

  $("#search-form").addEventListener("submit", (event) => { event.preventDefault(); executeSearch(); });
  $("#generate-cards").addEventListener("click", generateCards);
  $("#delete-note").addEventListener("click", deleteNote);
  $("#reveal-answer").addEventListener("click", revealAnswer);
  $("#note-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 100 * 1024) { toast("文件不能超过 100 KB", true); event.target.value = ""; return; }
    if (!/\.(md|txt)$/i.test(file.name)) { toast("仅支持 .md 和 .txt 文件", true); event.target.value = ""; return; }
    $("#note-content").value = await file.text();
    if (!$("#note-title").value) $("#note-title").value = file.name.replace(/\.(md|txt)$/i, "");
    if (!$("#note-source").value) $("#note-source").value = file.name;
    $("#file-label").textContent = `已读取 ${file.name}，可继续编辑正文`;
  });
  $("#import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = { title: $("#note-title").value.trim(), subject: $("#note-subject").value.trim() || "未分类", source: $("#note-source").value.trim() || "手动录入", content: $("#note-content").value.trim() };
    try {
      await api("/api/notes", { method: "POST", body: JSON.stringify(payload) });
      $("#import-dialog").close();
      event.target.reset();
      $("#file-label").textContent = "也可以直接在下方粘贴内容，单篇不超过 100 KB";
      await refreshAll();
      showView("library");
      toast("笔记已写入，向量与全文索引已更新");
    } catch (error) { toast(error.message, true); }
  });
  document.addEventListener("keydown", (event) => {
    if (state.view === "review" && event.code === "Space" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault(); revealAnswer();
    }
  });
}

bindEvents();
refreshAll().then(() => showView(location.hash.slice(1) || "overview")).catch((error) => toast(error.message, true));
