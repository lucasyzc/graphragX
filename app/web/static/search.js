const requestApi = window.ConsoleCommon.api;
const fmtTime = window.ConsoleCommon.formatTime;
const extractFilePath = window.ConsoleCommon.parseFilePathFromSourceRef;

const queryForm = document.getElementById("queryForm");
const queryProjectId = document.getElementById("queryProjectId");
const queryKnowledgeScope = document.getElementById("queryKnowledgeScope");
const queryQuestion = document.getElementById("queryQuestion");
const querySubmitBtn = document.getElementById("querySubmitBtn");
const refreshProjectsBtn = document.getElementById("refreshProjectsBtn");
const projectLoadMsg = document.getElementById("projectLoadMsg");
const projectEmptyActions = document.getElementById("projectEmptyActions");
const answerBox = document.getElementById("answerBox");
const llmStatusBox = document.getElementById("llmStatusBox");
const answerModeHint = document.getElementById("answerModeHint");
const retrievalMetaBox = document.getElementById("retrievalMetaBox");
const sourcesBox = document.getElementById("sourcesBox");
const citationsBox = document.getElementById("citationsBox");
const contextsBox = document.getElementById("contextsBox");
const projectStatusBox = document.getElementById("projectStatusBox");
const dependenciesBox = document.getElementById("dependenciesBox");
const historyList = document.getElementById("historyList");
const systemLog = document.getElementById("systemLog");
const refreshStatusBtn = document.getElementById("refreshStatusBtn");
const checkDepsBtn = document.getElementById("checkDepsBtn");
const toImpactBtn = document.getElementById("toImpactBtn");
const impactShortcut = document.getElementById("impactShortcut");

let lastQuerySources = [];
let lastQueryContexts = [];
let lastQueryCitations = [];

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
}

function setQueryEnabled(enabled) {
  if (queryQuestion) {
    queryQuestion.disabled = !enabled;
  }
  if (querySubmitBtn) {
    querySubmitBtn.disabled = !enabled;
  }
}

function setProjectLoadMessage(message, isError = false) {
  if (!projectLoadMsg) {
    return;
  }
  projectLoadMsg.textContent = message;
  projectLoadMsg.style.color = isError ? "#ffc0c0" : "";
}

function setLlmStatus(message, isError = false) {
  if (!llmStatusBox) {
    return;
  }
  llmStatusBox.textContent = message;
  llmStatusBox.style.color = isError ? "#ffc0c0" : "";
}

function setAnswerMode(message, isError = false) {
  if (!answerModeHint) {
    return;
  }
  answerModeHint.textContent = message;
  answerModeHint.style.color = isError ? "#ffc0c0" : "";
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toScore(raw) {
  const score = Number(raw);
  return Number.isFinite(score) ? score : 0;
}

function scoreClass(score) {
  if (score >= 0.6) {
    return "score-high";
  }
  if (score >= 0.3) {
    return "score-mid";
  }
  return "score-low";
}

function sourceKindLabel(kind) {
  const normalized = String(kind || "").toLowerCase();
  if (normalized === "vector") {
    return "向量召回";
  }
  if (normalized === "graph") {
    return "图谱扩展";
  }
  return normalized || "未知来源";
}

function parseSourceRef(ref) {
  const raw = String(ref || "").trim();
  const match = raw.match(/^(.*):(\d+)-(\d+)\s+\((.*)\)$/);
  if (!match) {
    return {
      filePath: raw || "unknown",
      startLine: 1,
      endLine: 1,
      symbol: "unknown",
    };
  }
  return {
    filePath: match[1],
    startLine: Number(match[2]),
    endLine: Number(match[3]),
    symbol: match[4],
  };
}

function normalizeSnippet(snippet) {
  return String(snippet || "").replaceAll("\r\n", "\n").trim();
}

function buildSnippetView(snippet) {
  const full = normalizeSnippet(snippet);
  if (!full) {
    return { preview: "// 空片段", full: "", truncated: false };
  }
  const maxLines = 14;
  const maxChars = 900;
  const lines = full.split("\n");
  let preview = lines.slice(0, maxLines).join("\n");
  let truncated = lines.length > maxLines;
  if (preview.length > maxChars) {
    preview = preview.slice(0, maxChars);
    truncated = true;
  }
  return { preview, full, truncated };
}

function renderProjectOptions(projects) {
  queryProjectId.innerHTML = "";

  if (!projects.length) {
    queryProjectId.appendChild(new Option("暂无项目", ""));
    if (projectEmptyActions) {
      projectEmptyActions.hidden = false;
    }
    return;
  }

  if (projectEmptyActions) {
    projectEmptyActions.hidden = true;
  }

  queryProjectId.appendChild(new Option("请选择项目", ""));
  projects.forEach((project) => {
    const label = `${project.name} (${String(project.id || "").slice(0, 8)})`;
    queryProjectId.appendChild(new Option(label, project.id));
  });
  queryProjectId.value = projects[0].id;
}

function getHistory() {
  try {
    return JSON.parse(localStorage.getItem("cg_query_history") || "[]");
  } catch {
    return [];
  }
}

function saveHistory(entries) {
  localStorage.setItem("cg_query_history", JSON.stringify(entries.slice(0, 8)));
}

function addHistoryEntry(projectId, question) {
  const entries = getHistory();
  entries.unshift({
    projectId,
    question,
    ts: new Date().toISOString(),
  });
  saveHistory(entries);
  renderHistory();
}

function renderHistory() {
  const entries = getHistory();
  if (!entries.length) {
    historyList.innerHTML = '<div class="meta-item">暂无检索历史</div>';
    return;
  }
  historyList.innerHTML = entries
    .map(
      (item) =>
        `<div class="history-item"><strong>${item.question}</strong><br/>project=${item.projectId}<br/>${fmtTime(
          item.ts,
        )}</div>`,
    )
    .join("");
}

async function loadProjects() {
  setQueryEnabled(false);
  refreshProjectsBtn.disabled = true;
  setProjectLoadMessage("项目加载中...");
  try {
    const raw = await requestApi("/projects", { method: "GET" });
    const projects = Array.isArray(raw) ? raw : Array.isArray(raw?.items) ? raw.items : [];
    const normalizedProjects = projects
      .map((item, idx) => {
        const id = String(item?.id ?? item?.project_id ?? item?.uuid ?? "").trim();
        const name = String(item?.name ?? item?.project_name ?? `project-${idx + 1}`).trim();
        return { ...item, id, name };
      })
      .filter((item) => item.id.length > 0 && item.name.length > 0);

    renderProjectOptions(normalizedProjects);

    if (normalizedProjects.length > 0) {
      setQueryEnabled(true);
      setProjectLoadMessage(`已加载 ${normalizedProjects.length} 个项目。`);
      await refreshProjectStatus(normalizedProjects[0].id);
    } else {
      setProjectLoadMessage("未检测到项目，请先到管理中心创建项目。", true);
      projectStatusBox.innerHTML = '<div class="meta-item">暂无项目状态信息。</div>';
      setQueryEnabled(false);
    }
    log(`加载项目 ${normalizedProjects.length} 个`);
  } catch (error) {
    queryProjectId.innerHTML = '<option value="">项目加载失败</option>';
    if (projectEmptyActions) {
      projectEmptyActions.hidden = false;
    }
    projectStatusBox.innerHTML = `<div class="meta-item">${escapeHtml(error.message)}</div>`;
    setProjectLoadMessage(`项目加载失败：${error.message}`, true);
    setQueryEnabled(false);
    log(`项目加载失败: ${error.message}`);
  } finally {
    if (refreshProjectsBtn) {
      refreshProjectsBtn.disabled = false;
    }
  }
}

async function refreshProjectStatus(projectId) {
  if (!projectId) {
    projectStatusBox.innerHTML = '<div class="meta-item">请先选择项目。</div>';
    return;
  }
  try {
    const status = await requestApi(`/projects/${projectId}/sync-status`, { method: "GET" });
    projectStatusBox.innerHTML = [
      `<div class="meta-item"><strong>活跃任务：</strong>${status.active_job ? status.active_job.status : "无"}</div>`,
      `<div class="meta-item"><strong>最近成功：</strong>${
        status.last_success_job ? fmtTime(status.last_success_job.finished_at || status.last_success_job.created_at) : "-"
      }</div>`,
      `<div class="meta-item"><strong>最近失败：</strong>${
        status.last_failed_job ? fmtTime(status.last_failed_job.finished_at || status.last_failed_job.created_at) : "-"
      }</div>`,
      `<div class="meta-item"><strong>待处理任务数：</strong>${status.pending_count}</div>`,
    ].join("");
  } catch (error) {
    projectStatusBox.innerHTML = `<div class="meta-item">${escapeHtml(error.message)}</div>`;
    log(`项目状态刷新失败: ${error.message}`);
  }
}

async function refreshChatConfigStatus() {
  setLlmStatus("LLM 状态：检查中...");
  try {
    const status = await requestApi("/health/chat-config", { method: "GET" });
    if (!status.enabled) {
      setLlmStatus("LLM 状态：未启用（当前使用 fallback 检索摘要）");
      return;
    }
    if (!status.configured) {
      setLlmStatus("LLM 状态：已启用但配置不完整，请检查 CHAT_API_BASE 或 OPENAI_BASE_URL", true);
      return;
    }
    const model = status.model ? ` / model=${status.model}` : "";
    const wire = status.wire_api ? ` / wire=${status.wire_api}` : "";
    setLlmStatus(`LLM 状态：已启用 (${status.provider}${model}${wire})`);
  } catch (error) {
    setLlmStatus(`LLM 状态检查失败：${error.message}`, true);
    log(`LLM 状态检查失败: ${error.message}`);
  }
}

function renderAnswerMode(meta) {
  const answerMode = String(meta?.answer_mode || "unknown");
  const chatModel = String(meta?.chat_model || "").trim();
  const llmProvider = String(meta?.llm_provider || "").trim();
  const llmWire = String(meta?.llm_wire_api || "").trim();
  const llmError = String(meta?.llm_error || "").trim();
  if (answerMode === "model") {
    const modelLabel = chatModel ? ` (${chatModel})` : "";
    setAnswerMode(`回答模式：LLM 生成${modelLabel}`);
    return;
  }
  if (answerMode === "fallback") {
    const details = [llmProvider, llmWire ? `wire=${llmWire}` : "", llmError]
      .filter((item) => item)
      .join(" / ");
    const suffix = details ? `；原因：${details}` : "";
    setAnswerMode(`回答模式：fallback（未使用 LLM，建议从上下文卡片直接查看代码）${suffix}`);
    return;
  }
  setAnswerMode(`回答模式：${answerMode}`);
}

function renderSources(sources) {
  if (!sources.length) {
    sourcesBox.innerHTML = '<div class="meta-item">无可追溯来源</div>';
    toImpactBtn.disabled = true;
    return;
  }
  toImpactBtn.disabled = false;
  sourcesBox.innerHTML = sources
    .map((source, idx) => {
      const meta = parseSourceRef(source.ref);
      const score = toScore(source.score);
      const kindLabel = sourceKindLabel(source.kind);
      const filePath = extractFilePath(source.ref) || meta.filePath || "";
      return (
        `<div class="source-item source-card">` +
        `<label class="source-header">` +
        `<input type="checkbox" data-source-index="${idx}" data-file-path="${escapeHtml(filePath)}" checked />` +
        `<span class="source-kind">${escapeHtml(kindLabel)}</span>` +
        `<span class="score-pill ${scoreClass(score)}">score ${score.toFixed(4)}</span>` +
        `</label>` +
        `<div class="source-path">${escapeHtml(meta.filePath)}</div>` +
        `<div class="source-symbol">${escapeHtml(meta.symbol)}</div>` +
        `<div class="source-lines">行号 ${meta.startLine}-${meta.endLine}</div>` +
        `</div>`
      );
    })
    .join("");
}

function renderCitations(citations) {
  lastQueryCitations = Array.isArray(citations) ? citations : [];
  if (!lastQueryCitations.length) {
    citationsBox.innerHTML = '<div class="meta-item">无引用</div>';
    return;
  }
  citationsBox.innerHTML = lastQueryCitations
    .map(
      (item) =>
        `<div class="source-item">` +
        `<div class="source-kind">${escapeHtml(item.source_kind || "unknown")}</div>` +
        `<div class="source-symbol">${escapeHtml(item.title || "-")}</div>` +
        `<div class="source-path">${escapeHtml(item.source_uri || "-")}</div>` +
        `<div class="source-lines">${escapeHtml(item.ref || "-")} / score=${toScore(item.score).toFixed(4)}</div>` +
        `</div>`,
    )
    .join("");
}

function renderContexts(contexts) {
  lastQueryContexts = Array.isArray(contexts) ? contexts : [];
  if (!contexts || !contexts.length) {
    contextsBox.innerHTML = '<div class="meta-item">无上下文预览</div>';
    return;
  }
  contextsBox.innerHTML = contexts
    .map((ctx, idx) => {
      const score = toScore(ctx.score);
      const filePath = String(ctx.file_path || "unknown");
      const startLine = Number(ctx.start_line || 1);
      const endLine = Number(ctx.end_line || startLine);
      const qualifiedName = String(ctx.qualified_name || "unknown");
      const kindLabel = sourceKindLabel(ctx.source_kind);
      const snippet = buildSnippetView(ctx.snippet);
      const copyButton = `<button class="small-btn copy-snippet-btn" data-context-index="${idx}" type="button">复制代码</button>`;
      const previewCode = `<pre class="code-snippet"><code>${escapeHtml(snippet.preview)}</code></pre>`;
      const fullCode = snippet.truncated
        ? `<details class="snippet-expand"><summary>展开完整片段</summary><pre class="code-snippet"><code>${escapeHtml(
            snippet.full,
          )}</code></pre></details>`
        : "";
      return (
        `<div class="source-item context-card">` +
        `<div class="context-meta">` +
        `<span class="source-kind">${escapeHtml(kindLabel)}</span>` +
        `<span class="score-pill ${scoreClass(score)}">score ${score.toFixed(4)}</span>` +
        copyButton +
        `</div>` +
        `<div class="source-path">${escapeHtml(filePath)}:${startLine}-${endLine}</div>` +
        `<div class="source-symbol">${escapeHtml(qualifiedName)}</div>` +
        previewCode +
        fullCode +
        `</div>`
      );
    })
    .join("");
}

async function copyText(text) {
  const content = String(text || "");
  if (!content.trim()) {
    throw new Error("没有可复制的代码片段");
  }
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(content);
    return;
  }

  const textArea = document.createElement("textarea");
  textArea.value = content;
  textArea.style.position = "fixed";
  textArea.style.opacity = "0";
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textArea);
  if (!ok) {
    throw new Error("浏览器不支持复制，请手动复制");
  }
}

function getSelectedSourceFiles() {
  const checked = Array.from(sourcesBox.querySelectorAll("input[type='checkbox']:checked"));
  const paths = checked
    .map((node) => node.getAttribute("data-file-path"))
    .filter((item) => item && item.trim());
  return Array.from(new Set(paths));
}

function jumpToImpact() {
  const projectId = queryProjectId.value;
  if (!projectId) {
    setProjectLoadMessage("请先选择项目，再进入影响分析。", true);
    return;
  }
  const selected = getSelectedSourceFiles();
  const fallbackPaths = lastQuerySources
    .map((source) => extractFilePath(source.ref))
    .filter((item) => item && item.trim());
  const filePaths = Array.from(new Set(selected.length ? selected : fallbackPaths));

  const params = new URLSearchParams();
  params.set("project_id", projectId);
  filePaths.forEach((pathItem) => params.append("file_paths", pathItem));
  const url = params.toString() ? `/scenarios/impact?${params.toString()}` : "/scenarios/impact";
  window.location.href = url;
}

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!queryProjectId.value) {
    setProjectLoadMessage("请先选择项目，再执行检索。", true);
    return;
  }
  if (!queryQuestion.value.trim()) {
    if (queryQuestion) {
      queryQuestion.focus();
    }
    return;
  }

  answerBox.textContent = "检索中...";
  retrievalMetaBox.textContent = "检索中...";
  setAnswerMode("回答模式：检索中...");
  sourcesBox.innerHTML = "";
  citationsBox.innerHTML = "";
  contextsBox.innerHTML = "";
  lastQueryContexts = [];
  lastQueryCitations = [];
  toImpactBtn.disabled = true;

  const form = new FormData(queryForm);
  const payload = {
    project_id: queryProjectId.value,
    question: form.get("question"),
    top_k: Number(form.get("top_k") || 8),
    knowledge_scope: queryKnowledgeScope?.value || "auto",
    need_citations: true,
  };
  try {
    const result = await requestApi("/query", { method: "POST", body: JSON.stringify(payload) });
    answerBox.textContent = result.answer;
    const retrievalMeta = result.retrieval_meta || {};
    retrievalMetaBox.textContent = JSON.stringify(retrievalMeta, null, 2);
    renderAnswerMode(retrievalMeta);
    lastQuerySources = result.sources || [];
    renderSources(lastQuerySources);
    renderCitations(result.citations || []);
    renderContexts(result.contexts || []);
    addHistoryEntry(payload.project_id, payload.question);
    log(`查询完成，来源 ${lastQuerySources.length} 条`);
  } catch (error) {
    answerBox.textContent = error.message;
    setAnswerMode("回答模式：请求失败", true);
    log(`查询失败: ${error.message}`);
  }
});

queryProjectId.addEventListener("change", async () => {
  const projectId = queryProjectId.value;
  if (!projectId) {
    setQueryEnabled(false);
    projectStatusBox.innerHTML = '<div class="meta-item">请先选择项目。</div>';
    return;
  }
  setQueryEnabled(true);
  setProjectLoadMessage("项目已选择，可以开始输入检索问题。");
  await refreshProjectStatus(projectId);
});

if (refreshProjectsBtn) {
  refreshProjectsBtn.addEventListener("click", async () => {
    await loadProjects();
  });
}

refreshStatusBtn.addEventListener("click", async () => {
  await refreshProjectStatus(queryProjectId.value);
  log("已刷新项目状态");
});

checkDepsBtn.addEventListener("click", async () => {
  dependenciesBox.textContent = "检查中...";
  try {
    const result = await requestApi("/health/dependencies", { method: "GET" });
    dependenciesBox.textContent = JSON.stringify(result, null, 2);
    log("依赖健康检查完成");
  } catch (error) {
    dependenciesBox.textContent = error.message;
    log(`依赖检查失败: ${error.message}`);
  }
});

contextsBox.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const button = target.closest(".copy-snippet-btn");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const index = Number(button.getAttribute("data-context-index") || "-1");
  if (!Number.isFinite(index) || index < 0 || index >= lastQueryContexts.length) {
    log("复制失败：上下文索引无效");
    return;
  }
  const snippet = String(lastQueryContexts[index]?.snippet || "");
  try {
    await copyText(snippet);
    log(`已复制代码片段 #${index + 1}`);
  } catch (error) {
    log(`复制失败: ${error.message}`);
  }
});

if (toImpactBtn) {
  toImpactBtn.addEventListener("click", jumpToImpact);
}
if (impactShortcut) {
  impactShortcut.addEventListener("click", (event) => {
    event.preventDefault();
    jumpToImpact();
  });
}

(async function bootstrap() {
  renderHistory();
  toImpactBtn.disabled = true;
  setQueryEnabled(false);
  setAnswerMode("回答模式：等待检索...");
  await refreshChatConfigStatus();
  await loadProjects();
})();
