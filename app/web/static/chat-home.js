const requestApi = window.ConsoleCommon.api;
const fmtTime = window.ConsoleCommon.formatTime;
const extractFilePath = window.ConsoleCommon.parseFilePathFromSourceRef;

const newSessionBtn = document.getElementById("newSessionBtn");
const projectList = document.getElementById("projectList");
const sessionDefaultProject = document.getElementById("sessionDefaultProject");
const chatKnowledgeScope = document.getElementById("chatKnowledgeScope");
const chatTopK = document.getElementById("chatTopK");
const saveSessionSettingsBtn = document.getElementById("saveSessionSettingsBtn");
const renameSessionBtn = document.getElementById("renameSessionBtn");
const messageList = document.getElementById("messageList");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const sendMessageBtn = document.getElementById("sendMessageBtn");
const appShell = document.querySelector(".cgpt-app");
const evidencePanel = document.getElementById("evidencePanel");
const toggleEvidenceBtn = document.getElementById("toggleEvidenceBtn");
const toggleEvidenceBtnInner = document.getElementById("toggleEvidenceBtnInner");
const evidenceTabs = Array.from(document.querySelectorAll(".evidence-tab"));
const evidenceSummary = document.getElementById("evidenceSummary");
const evidenceOverview = document.getElementById("evidenceOverview");
const evidenceRecall = document.getElementById("evidenceRecall");
const evidenceAdopted = document.getElementById("evidenceAdopted");
const evidenceCitations = document.getElementById("evidenceCitations");
const evidenceMeta = document.getElementById("evidenceMeta");
const evidenceModeButtons = Array.from(document.querySelectorAll(".evidence-mode-btn"));
const systemLog = document.getElementById("systemLog");

const state = {
  projects: [],
  sessions: [],
  sessionsByProject: new Map(),
  expandedProjects: new Set(),
  messages: [],
  currentProjectId: null,
  currentSessionId: null,
  selectedAssistantMessageId: null,
  evidenceByAssistantId: new Map(),
  activeTab: "overview",
  evidenceMode: "simple",
};

const PROCESS_SLOW_HINT_MS = 8000;
const PROCESS_PHASES = {
  received: {
    main: "Message sent. Starting processing...",
    detail: () => "Preparing context for this request",
  },
  understanding: {
    main: "Understanding your request...",
    detail: () => "Extracting key intent and entities",
  },
  planning: {
    main: "Planning retrieval strategy...",
    detail: ({ projectName, scope, topK }) => `Project ${projectName} | Scope ${scope} | Top K ${topK}`,
  },
  retrieving: {
    main: "Retrieving related information...",
    detail: ({ stats }) =>
      Number.isFinite(stats?.hitCount) ? `Found ${stats.hitCount} candidate results` : "Expanding search scope...",
  },
  filtering: {
    main: "Filtering high-relevance evidence...",
    detail: ({ stats }) =>
      Number.isFinite(stats?.keptCount) ? `Kept ${stats.keptCount} high-confidence evidence items` : "Reranking and deduplicating...",
  },
  composing: {
    main: "Composing the answer...",
    detail: ({ stats }) =>
      Number.isFinite(stats?.citationCount)
        ? `Building response with ${stats.citationCount} citation(s)`
        : "Integrating final context and consistency checks",
  },
  completed: {
    main: "Response is ready.",
    detail: ({ stats }) => {
      const summary = [];
      if (Number.isFinite(stats?.hitCount)) {
        summary.push(`${stats.hitCount} candidates`);
      }
      if (Number.isFinite(stats?.keptCount)) {
        summary.push(`${stats.keptCount} evidence`);
      }
      if (Number.isFinite(stats?.citationCount)) {
        summary.push(`${stats.citationCount} citations`);
      }
      return summary.length ? `Evidence panel updated (${summary.join(", ")})` : "Evidence panel updated";
    },
  },
};

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
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

function markdownToSafeHtml(markdownText) {
  const source = String(markdownText || "");
  try {
    if (window.marked && typeof window.marked.parse === "function") {
      const rendered = window.marked.parse(source, {
        gfm: true,
        breaks: true,
        mangle: false,
        headerIds: false,
      });
      if (window.DOMPurify && typeof window.DOMPurify.sanitize === "function") {
        return window.DOMPurify.sanitize(rendered, {
          USE_PROFILES: { html: true },
          FORBID_TAGS: ["style", "script", "iframe", "object", "embed", "form"],
        });
      }
    }
  } catch {
    // Fall back to plain text rendering below.
  }
  return escapeHtml(source).replaceAll("\n", "<br />");
}

function currentProject() {
  return state.projects.find((item) => item.id === state.currentProjectId) || null;
}

function currentSession() {
  return state.sessions.find((item) => item.id === state.currentSessionId) || null;
}

function getProjectSessions(projectId) {
  return state.sessionsByProject.get(projectId) || [];
}

function isProjectExpanded(projectId) {
  return state.expandedProjects.has(projectId);
}

function ensureProjectExpanded(projectId) {
  if (projectId) {
    state.expandedProjects.add(projectId);
  }
}

function toCount(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return Math.round(parsed);
}

function collectProcessStats(result) {
  const meta = result?.retrieval_meta || {};
  const sources = Array.isArray(result?.sources) ? result.sources : [];
  const contexts = Array.isArray(result?.contexts) ? result.contexts : [];
  const citations = Array.isArray(result?.citations) ? result.citations : [];

  const hitFromMeta = toCount(meta.reranked);
  const hitFallback = sources.length || contexts.length || 0;
  const hitCount = hitFromMeta ?? (hitFallback > 0 ? hitFallback : null);

  const keptFromMeta = toCount(meta.selected_contexts);
  const keptFallback = contexts.length || sources.length || 0;
  const keptCount = keptFromMeta ?? (keptFallback > 0 ? keptFallback : null);

  const citationCount = citations.length > 0 ? citations.length : null;
  return { hitCount, keptCount, citationCount };
}

function resolveProcessPhase(elapsedMs) {
  if (elapsedMs >= 7000) {
    return "composing";
  }
  if (elapsedMs >= 5400) {
    return "filtering";
  }
  if (elapsedMs >= 3800) {
    return "retrieving";
  }
  if (elapsedMs >= 2400) {
    return "planning";
  }
  if (elapsedMs >= 1200) {
    return "understanding";
  }
  return "received";
}

function buildProcessMessage({
  phaseKey,
  elapsedMs,
  projectName,
  scope,
  topK,
  stats = null,
}) {
  const phase = PROCESS_PHASES[phaseKey] || PROCESS_PHASES.received;
  const elapsedSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const lines = [`Processing your request (${elapsedSeconds}s elapsed)`, phase.main];
  const detail = phase.detail({ projectName, scope, topK, stats });
  if (detail) {
    lines.push(detail);
  }
  if (elapsedMs >= PROCESS_SLOW_HINT_MS && phaseKey !== "completed") {
    lines.push("This request is taking longer than usual. Still working...");
  }
  return lines.join("\n");
}

function createProcessRenderer({
  baseMessages,
  pendingUserMessage,
  pendingAssistantMessage,
  projectName,
  scope,
  topK,
}) {
  const startAt = Date.now();
  let stopped = false;
  let lastStats = null;

  const render = (forcePhase = "") => {
    const elapsedMs = Date.now() - startAt;
    const phaseKey = forcePhase || resolveProcessPhase(elapsedMs);
    const pendingAssistant = {
      ...pendingAssistantMessage,
      content: buildProcessMessage({
        phaseKey,
        elapsedMs,
        projectName,
        scope,
        topK,
        stats: lastStats,
      }),
    };
    renderMessages([...baseMessages, pendingUserMessage, pendingAssistant]);
  };

  render();
  const timer = window.setInterval(() => {
    if (!stopped) {
      render();
    }
  }, 1000);

  return {
    complete(stats = null) {
      lastStats = stats;
      if (!stopped) {
        render("completed");
      }
    },
    stop() {
      if (stopped) {
        return;
      }
      stopped = true;
      window.clearInterval(timer);
    },
  };
}

function formatMessageRole(role) {
  if (role === "user") {
    return "You";
  }
  if (role === "assistant") {
    return "Assistant";
  }
  return "System";
}

function formatMessageMeta(message) {
  if (!message?.created_at) {
    return "";
  }
  const date = new Date(message.created_at);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

function formatSessionTime(raw) {
  if (!raw) {
    return "";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function summarizeSessionTitle(content) {
  const source = String(content || "").replace(/\s+/g, " ").trim();
  if (!source) {
    return "New Chat";
  }
  const chunk = source
    .split(/[。！？.!?;；:\n]/)
    .map((item) => item.trim())
    .find(Boolean);
  const base = chunk || source;
  const hasCJK = /[\u3400-\u9fff]/.test(base);
  if (hasCJK) {
    const compact = Array.from(base.replace(/\s+/g, ""));
    if (!compact.length) {
      return "New Chat";
    }
    if (compact.length < 5) {
      return Array.from(source.replace(/\s+/g, "")).slice(0, 10).join("");
    }
    return compact.slice(0, 10).join("");
  }
  const cleaned = base.replace(/[`*_#[\]()]/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) {
    return "New Chat";
  }
  const words = cleaned.split(" ").filter(Boolean);
  const headline = words.slice(0, 6).join(" ");
  if (headline.length <= 42) {
    return headline;
  }
  return `${headline.slice(0, 42).trim()}...`;
}
function renderProjectOptions(selectedProjectId = "") {
  const options = state.projects.map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`);
  sessionDefaultProject.innerHTML = options.join("");
  if (selectedProjectId && state.projects.some((item) => item.id === selectedProjectId)) {
    sessionDefaultProject.value = selectedProjectId;
    return;
  }
  if (state.currentProjectId && state.projects.some((item) => item.id === state.currentProjectId)) {
    sessionDefaultProject.value = state.currentProjectId;
    return;
  }
  if (state.projects.length > 0) {
    sessionDefaultProject.value = state.projects[0].id;
  }
}

function renderProjectList() {
  if (!state.projects.length) {
    projectList.innerHTML = '<div class="meta-item">No projects yet. Create one in Manage.</div>';
    return;
  }

  projectList.innerHTML = state.projects
    .map((project) => {
      const expanded = isProjectExpanded(project.id);
      const activeProject = project.id === state.currentProjectId ? "is-active" : "";
      const sessions = getProjectSessions(project.id);
      const sessionsHtml = !expanded
        ? ""
        : sessions.length
          ? sessions
              .map((item) => {
                const activeSession = item.id === state.currentSessionId ? "is-active" : "";
                const title = String(item.title || "Untitled").trim();
                const archived = item.archived ? '<span class="tag tag-failed">archived</span>' : "";
                const time = formatSessionTime(item.last_message_at || item.updated_at || item.created_at);
                return (
                  `<button class="chat-session-item ${activeSession}" type="button" data-project-id="${escapeHtml(project.id)}" data-session-id="${escapeHtml(item.id)}">` +
                  `<div class="chat-session-title" title="${escapeHtml(title)}">${escapeHtml(title)} ${archived}</div>` +
                  `<div class="chat-session-meta">${escapeHtml(time || "No messages")}</div>` +
                  `</button>`
                );
              })
              .join("")
          : '<div class="project-session-empty">No sessions yet</div>';

      return (
        `<section class="project-tree-item ${activeProject}" data-project-id="${escapeHtml(project.id)}">` +
        `<div class="project-tree-head">` +
        `<button class="project-tree-toggle" type="button" data-project-id="${escapeHtml(project.id)}">${expanded ? "▾" : "▸"}</button>` +
        `<button class="chat-project-item project-tree-header ${activeProject}" type="button" data-project-id="${escapeHtml(project.id)}">${escapeHtml(project.name)}</button>` +
        `</div>` +
        `<div class="project-tree-sessions ${expanded ? "is-open" : ""}" data-project-id="${escapeHtml(project.id)}">${sessionsHtml}</div>` +
        `</section>`
      );
    })
    .join("");
}

function renderMessages(messages) {
  if (!messages.length) {
    messageList.innerHTML = '<div class="meta-item">No messages yet. Ask anything to get started.</div>';
    return;
  }

  messageList.innerHTML = messages
    .map((message) => {
      const roleClass = message.role === "assistant" ? "assistant" : message.role === "user" ? "user" : "system";
      const roleLabel = formatMessageRole(message.role);
      const metaLabel = formatMessageMeta(message);
      const active = message.id === state.selectedAssistantMessageId ? "is-selected" : "";
      const pending = message.is_pending ? "is-pending" : "";
      const processing = message.is_pending && message.role === "assistant" ? "is-processing" : "";
      const pendingBadge = message.is_pending ? '<span class="chat-message-pending">Processing...</span>' : "";
      const evidenceBtn =
        message.role === "assistant" && !message.is_pending
          ? `<button class="small-btn evidence-select-btn" type="button" data-message-id="${escapeHtml(message.id)}">View Evidence</button>`
          : "";
      const memoryBtn =
        message.role === "assistant" && !message.is_pending
          ? `<button class="small-btn memory-add-btn" type="button" data-message-id="${escapeHtml(message.id)}">Add to Memory</button>`
          : "";

      let messageContent = "";
      if (message.is_pending && message.role === "assistant") {
        messageContent = `<div class="chat-message-content chat-processing-content">${escapeHtml(message.content).replaceAll(
          "\n",
          "<br />",
        )}</div>`;
      } else if (message.role === "assistant") {
        messageContent = `<div class="chat-message-content chat-markdown">${markdownToSafeHtml(message.content)}</div>`;
      } else {
        messageContent = `<pre class="chat-message-content">${escapeHtml(message.content)}</pre>`;
      }

      return (
        `<article class="chat-message ${roleClass} ${active} ${pending} ${processing}" data-message-id="${escapeHtml(message.id)}">` +
        `<div class="chat-message-head">` +
        `<strong class="chat-message-role">${escapeHtml(roleLabel)}</strong>` +
        `${pendingBadge}` +
        `${metaLabel ? `<span class="chat-message-meta">${escapeHtml(metaLabel)}</span>` : ""}` +
        `${evidenceBtn}${memoryBtn}` +
        `</div>` +
        `${messageContent}` +
        `</article>`
      );
    })
    .join("");

  messageList.scrollTop = messageList.scrollHeight;
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
    return "Semantic Match";
  }
  if (normalized === "keyword") {
    return "Keyword Match";
  }
  if (normalized === "graph") {
    return "Graph Expansion";
  }
  return normalized || "Unknown";
}

function sourceKindReason(kind) {
  const normalized = String(kind || "").toLowerCase();
  if (normalized === "vector") {
    return "Recalled by semantic similarity against your question.";
  }
  if (normalized === "keyword") {
    return "Recalled because question terms overlap with this content.";
  }
  if (normalized === "graph") {
    return "Recalled via related symbols from graph expansion.";
  }
  return "Recalled by the retrieval pipeline.";
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

function clipSnippet(snippet, maxLines = 10, maxChars = 900) {
  const normalized = normalizeSnippet(snippet);
  if (!normalized) {
    return { text: "", truncated: false };
  }
  const lines = normalized.split("\n");
  let preview = lines.slice(0, maxLines).join("\n");
  let truncated = lines.length > maxLines;
  if (preview.length > maxChars) {
    preview = preview.slice(0, maxChars);
    truncated = true;
  }
  return { text: preview, truncated };
}

function normalizeTags(rawTags) {
  if (Array.isArray(rawTags)) {
    return rawTags
      .map((item) => String(item || "").trim())
      .filter((item) => Boolean(item));
  }
  const text = String(rawTags || "").trim();
  if (!text) {
    return [];
  }
  return text
    .split(",")
    .map((item) => item.trim())
    .filter((item) => Boolean(item));
}

function parseChunkIndexFromRef(ref) {
  const match = String(ref || "").match(/#chunk-(\d+)/i);
  if (!match) {
    return null;
  }
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

function encodeDataValue(value) {
  return encodeURIComponent(String(value || ""));
}

function decodeDataValue(value) {
  try {
    return decodeURIComponent(String(value || ""));
  } catch {
    return String(value || "");
  }
}

function buildHitRange(entry) {
  if (!entry || typeof entry !== "object") {
    return "-";
  }
  if (entry.sourceType === "doc" || entry.sourceType === "faq") {
    const rawChunkIndex = Number(entry.chunkIndex);
    if (Number.isFinite(rawChunkIndex) && rawChunkIndex >= 0) {
      const oneBased = Math.round(rawChunkIndex) + 1;
      return `${oneBased}-${oneBased}`;
    }
    const fromRef = parseChunkIndexFromRef(entry.ref);
    if (fromRef !== null) {
      const oneBased = Math.round(fromRef) + 1;
      return `${oneBased}-${oneBased}`;
    }
  }
  const start = Number(entry.startLine);
  const end = Number(entry.endLine);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return `${Math.round(start)}-${Math.round(end)}`;
  }
  return "-";
}

function buildReferenceCopyText(entry) {
  const title = String(entry.title || entry.symbol || "-").trim() || "-";
  const uri = String(entry.sourceUri || "").trim();
  const ref = String(entry.ref || "-").trim() || "-";
  if (uri) {
    return `${title}\n${uri}\nref=${ref}`;
  }
  return `${title}\nref=${ref}`;
}

function parseSnippetMetadata(snippet) {
  const normalized = normalizeSnippet(snippet);
  if (!normalized) {
    return { meta: {}, body: "" };
  }
  const lines = normalized.split("\n");
  const meta = {};
  let bodyStart = 0;
  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = String(lines[idx] || "").trim();
    if (!line) {
      bodyStart = idx + 1;
      continue;
    }
    const match = line.match(/^([a-zA-Z_][a-zA-Z0-9_\-]*)\s*=\s*(.+)$/);
    if (!match) {
      break;
    }
    meta[String(match[1] || "").toLowerCase()] = String(match[2] || "").trim();
    bodyStart = idx + 1;
  }
  const body = lines.slice(bodyStart).join("\n").trim();
  return { meta, body: body || normalized };
}

function cleanEvidenceSnippet(snippet) {
  const normalized = normalizeSnippet(snippet);
  if (!normalized) {
    return "";
  }
  const filtered = normalized
    .split("\n")
    .map((line) => String(line || "").trimEnd())
    .filter((line) => {
      const raw = String(line || "").trim();
      if (!raw) {
        return false;
      }
      if (/^(title|url|id|docid|doc_id|chunk|chunk_range|source_uri|source|ref)\s*=/i.test(raw)) {
        return false;
      }
      return true;
    });
  return filtered.join("\n").trim();
}

function clipText(text, maxChars = 140) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) {
    return "";
  }
  if (value.length <= maxChars) {
    return value;
  }
  return `${value.slice(0, maxChars - 1).trim()}…`;
}

function splitSentences(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return [];
  }
  const matches = normalized.match(/[^。！？.!?]+[。！？.!?]?/g) || [];
  return matches.map((item) => item.trim()).filter((item) => Boolean(item));
}

function buildOneLineSummary(text, fallback = "") {
  const sentences = splitSentences(text);
  if (sentences.length) {
    return clipText(sentences[0], 140);
  }
  return clipText(fallback, 140) || "No summary available.";
}

function buildKeyPoints(text, summary = "") {
  const lines = String(text || "")
    .split("\n")
    .map((item) => String(item || "").trim())
    .filter((item) => Boolean(item));
  const bulletLines = lines
    .filter((line) => /^([-*•]\s+|\d+[.)]\s+|[A-Za-z][\w\s]{0,24}:)/.test(line))
    .map((line) => line.replace(/^([-*•]\s+|\d+[.)]\s+)/, "").trim());
  const points = [];
  const seen = new Set();
  const pushPoint = (raw) => {
    const textLine = clipText(raw, 140);
    if (!textLine) {
      return;
    }
    const key = textLine.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    if (summary && key === String(summary).toLowerCase()) {
      return;
    }
    seen.add(key);
    points.push(textLine);
  };

  bulletLines.forEach((line) => pushPoint(line));
  if (points.length < 3) {
    splitSentences(text).forEach((line) => pushPoint(line));
  }
  if (points.length < 3) {
    lines.forEach((line) => pushPoint(line));
  }
  if (!points.length && summary) {
    points.push(summary);
  }
  return points.slice(0, 3);
}

async function copyText(text) {
  const content = String(text || "");
  if (!content.trim()) {
    throw new Error("Nothing to copy");
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
    throw new Error("Copy is not supported in this browser");
  }
}

function toPercent(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return "0%";
  }
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function buildContextRef(context) {
  if (!context || typeof context !== "object") {
    return "-";
  }
  const source = context.file_path || context.source_uri || "unknown";
  const start = Number(context.start_line || 1);
  const end = Number(context.end_line || start);
  const symbol = context.qualified_name || context.title || "unknown";
  return `${source}:${start}-${end} (${symbol})`;
}

function looksLikeCodeByPath(filePath) {
  const normalized = String(filePath || "").toLowerCase();
  if (!normalized) {
    return false;
  }
  if (
    normalized.endsWith("dockerfile") ||
    normalized.endsWith("makefile") ||
    normalized.endsWith("jenkinsfile")
  ) {
    return true;
  }
  return /\.(py|js|jsx|ts|tsx|java|kt|go|rs|c|cc|cpp|h|hpp|cs|rb|php|swift|scala|sql|sh|bash|ps1|yaml|yml|toml|ini|cfg|json|lock|gradle)$/.test(
    normalized,
  );
}

function looksLikeCodeSnippet(snippet) {
  const text = normalizeSnippet(snippet);
  if (!text) {
    return false;
  }
  if (/[{};<>]/.test(text) && /\b(function|class|return|import|def|const|let|var)\b/.test(text)) {
    return true;
  }
  if (/^\s*(if|for|while|try|except|catch)\b/m.test(text)) {
    return true;
  }
  return false;
}

function isCodeEntry(entry) {
  if (looksLikeCodeByPath(entry.filePath || entry.sourceUri)) {
    return true;
  }
  if (Number(entry.startLine || 1) !== Number(entry.endLine || 1)) {
    return true;
  }
  return looksLikeCodeSnippet(entry.snippet);
}

function citationKey(kind, ref) {
  return `${String(kind || "unknown").toLowerCase()}|${String(ref || "-")}`;
}

function buildEvidenceModel(evidence) {
  const sources = Array.isArray(evidence?.sources) ? evidence.sources : [];
  const contexts = Array.isArray(evidence?.contexts) ? evidence.contexts : [];
  const citations = Array.isArray(evidence?.citations) ? evidence.citations : [];
  const meta = evidence?.retrieval_meta && typeof evidence.retrieval_meta === "object" ? evidence.retrieval_meta : {};

  const citationIndexByKey = new Map();
  citations.forEach((citation, idx) => {
    const key = citationKey(citation?.source_kind, citation?.ref);
    const current = citationIndexByKey.get(key) || [];
    current.push(idx + 1);
    citationIndexByKey.set(key, current);
  });

  const rowCount = Math.max(sources.length, contexts.length);
  const entries = [];
  for (let idx = 0; idx < rowCount; idx += 1) {
    const source = sources[idx] || {};
    const context = contexts[idx] || {};
    const kind = String(context.source_kind || source.kind || "unknown").toLowerCase();
    const sourceType = String(
      context.source_type || (String(source.ref || "").includes("#chunk-") ? "doc" : "code"),
    ).toLowerCase();
    const ref = String(source.ref || buildContextRef(context) || "-");
    const snippet = String(context.snippet || "");
    const parsedSnippet = parseSnippetMetadata(snippet);
    const snippetMeta = parsedSnippet.meta || {};
    const snippetBody = cleanEvidenceSnippet(parsedSnippet.body || snippet);
    const parsed = parseSourceRef(ref);
    const sourceUri = String(context.source_uri || snippetMeta.url || "").trim();
    const filePath = String(
      context.file_path ||
        sourceUri ||
        extractFilePath(ref) ||
        parsed.filePath ||
        "unknown",
    );
    const startLine = Number(context.start_line || parsed.startLine || 1);
    const endLine = Number(context.end_line || parsed.endLine || startLine);
    const symbol = String(context.qualified_name || context.title || snippetMeta.title || parsed.symbol || "unknown");
    const title = String(context.title || context.qualified_name || snippetMeta.title || parsed.symbol || "unknown");
    const documentId = String(context.document_id || snippetMeta.id || snippetMeta.docid || "").trim();
    const chunkIndex = Number(context.chunk_index);
    const tags = normalizeTags(context.tags || snippetMeta.tags || "");
    const score = toScore(source.score ?? context.score);
    const key = citationKey(kind, ref);
    const citationIndexes = citationIndexByKey.get(key) || [];
    const oneLineSummary = buildOneLineSummary(snippetBody, title);
    const keyPoints = buildKeyPoints(snippetBody, oneLineSummary);
    const resolvedSourceUri = sourceUri || filePath;
    const entry = {
      id: `E-${String(idx + 1).padStart(2, "0")}`,
      kind,
      kindLabel: sourceKindLabel(kind),
      reason: sourceKindReason(kind),
      ref,
      filePath,
      sourceUri: resolvedSourceUri,
      sourceType,
      title,
      documentId,
      chunkIndex: Number.isFinite(chunkIndex) ? chunkIndex : null,
      tags,
      startLine,
      endLine,
      symbol,
      score,
      snippet,
      snippetBody,
      oneLineSummary,
      keyPoints,
      citationIndexes,
      preview: clipSnippet(snippetBody || snippet, 5, 360),
    };
    entry.hitRange = buildHitRange(entry);
    entry.referenceCopy = buildReferenceCopyText(entry);
    entry.isCode = isCodeEntry(entry);
    entry.adopted = citationIndexes.length > 0 || citations.length === 0;
    entries.push(entry);
  }

  const entryIdsByCitationKey = new Map();
  entries.forEach((entry) => {
    const key = citationKey(entry.kind, entry.ref);
    const list = entryIdsByCitationKey.get(key) || [];
    list.push(entry.id);
    entryIdsByCitationKey.set(key, list);
  });

  const citationRows = citations.map((item, idx) => {
    const key = citationKey(item?.source_kind, item?.ref);
    return {
      id: `C${idx + 1}`,
      source_kind: item?.source_kind || "unknown",
      title: item?.title || "-",
      source_uri: item?.source_uri || "-",
      ref: item?.ref || "-",
      score: toScore(item?.score),
      evidenceIds: entryIdsByCitationKey.get(key) || [],
    };
  });

  const adoptedEntries = entries.filter((entry) => entry.adopted);
  return {
    meta,
    entries,
    adoptedEntries,
    citations: citationRows,
  };
}

function buildEvidenceSummary(model) {
  const vectorHits = toCount(model.meta?.vector_hits) ?? 0;
  const keywordHits = toCount(model.meta?.keyword_hits) ?? 0;
  const graphHits = toCount(model.meta?.graph_expanded) ?? 0;
  const reranked = toCount(model.meta?.reranked) ?? model.entries.length;
  const selected = toCount(model.meta?.selected_contexts) ?? model.entries.length;
  const citationCount = model.citations.length;

  const parts = [
    `${vectorHits} semantic`,
    `${keywordHits} keyword`,
    `${graphHits} graph`,
  ];
  return (
    `Retrieved ${parts.join(" / ")} items, reranked ${reranked}, selected ${selected}, ` +
    `and produced ${citationCount} citation${citationCount === 1 ? "" : "s"}.`
  );
}

function buildCodeSnippetHtml(snippet, startLine = 1, maxLines = 80) {
  const normalized = normalizeSnippet(snippet);
  if (!normalized) {
    return '<div class="meta-item">No snippet available.</div>';
  }
  const lines = normalized.split("\n");
  const clipped = lines.slice(0, maxLines);
  const safeStart = Number.isFinite(startLine) && startLine > 0 ? Math.round(startLine) : 1;
  const list = clipped
    .map((line) => `<li><code>${escapeHtml(line || " ")}</code></li>`)
    .join("");
  const truncatedNote =
    lines.length > clipped.length
      ? `<div class="source-lines">Showing ${clipped.length}/${lines.length} lines.</div>`
      : "";
  return `<ol class="code-lines" start="${safeStart}">${list}</ol>${truncatedNote}`;
}

function buildDocumentSnippetHtml(snippet) {
  const normalized = normalizeSnippet(snippet);
  if (!normalized) {
    return '<div class="meta-item">No snippet available.</div>';
  }
  const maxLines = state.evidenceMode === "simple" ? 18 : 60;
  const maxChars = state.evidenceMode === "simple" ? 1000 : 2800;
  const clipped = clipSnippet(normalized, maxLines, maxChars);
  const note = clipped.truncated
    ? '<div class="source-lines">Preview is truncated. Switch to Expert mode for more detail.</div>'
    : "";
  return `<div class="doc-snippet">${markdownToSafeHtml(clipped.text)}</div>${note}`;
}

function renderEvidenceOverview(model) {
  const meta = model.meta || {};
  const kpis = [
    { label: "Semantic Recalls", value: toCount(meta.vector_hits) ?? 0 },
    { label: "Keyword Recalls", value: toCount(meta.keyword_hits) ?? 0 },
    { label: "Graph Expansions", value: toCount(meta.graph_expanded) ?? 0 },
    { label: "Reranked", value: toCount(meta.reranked) ?? model.entries.length },
    { label: "Selected", value: toCount(meta.selected_contexts) ?? model.entries.length },
    { label: "Citations", value: model.citations.length },
    { label: "Coverage", value: toPercent(meta.evidence_coverage) },
  ];
  const visibleKpis = state.evidenceMode === "simple" ? kpis.slice(0, 6) : kpis;
  const funnel = [
    `Recall ${
      (toCount(meta.vector_hits) ?? 0) +
      (toCount(meta.keyword_hits) ?? 0) +
      (toCount(meta.graph_expanded) ?? 0)
    }`,
    `Reranked ${toCount(meta.reranked) ?? model.entries.length}`,
    `Selected ${toCount(meta.selected_contexts) ?? model.entries.length}`,
    `Citations ${model.citations.length}`,
  ];

  evidenceOverview.innerHTML =
    `<div class="source-item">` +
    `<div class="source-symbol">${escapeHtml(buildEvidenceSummary(model))}</div>` +
    `</div>` +
    `<div class="evidence-overview-grid">` +
    visibleKpis
      .map(
        (item) =>
          `<article class="overview-kpi">` +
          `<div class="overview-kpi-label">${escapeHtml(item.label)}</div>` +
          `<div class="overview-kpi-value">${escapeHtml(String(item.value))}</div>` +
          `</article>`,
      )
      .join("") +
    `</div>` +
    `<div class="source-item overview-funnel">` +
    funnel
      .map((item, idx) => {
        const arrow = idx < funnel.length - 1 ? '<span class="funnel-arrow">→</span>' : "";
        return `<span class="funnel-step">${escapeHtml(item)}</span>${arrow}`;
      })
      .join("") +
    `</div>`;
}

function renderRecallBrowser(model) {
  if (!model.entries.length) {
    evidenceRecall.innerHTML = '<div class="meta-item">No recalled evidence to preview.</div>';
    return;
  }
  const maxLines = state.evidenceMode === "simple" ? 24 : 70;
  evidenceRecall.innerHTML = model.entries
    .map((entry, idx) => {
      const displaySnippet = entry.isCode ? entry.snippet : entry.snippetBody || entry.snippet;
      const snippetHtml = entry.isCode
        ? buildCodeSnippetHtml(displaySnippet, entry.startLine, maxLines)
        : buildDocumentSnippetHtml(displaySnippet);
      const citationLinks = entry.citationIndexes.length
        ? entry.citationIndexes.map((item) => `<span class="citation-link">C${item}</span>`).join("")
        : '<span class="citation-link is-muted">Not cited directly</span>';
      const sourceUrl = isHttpUrl(entry.sourceUri) ? entry.sourceUri : "";
      const titleText = entry.title || entry.symbol || "unknown";
      const titleLink = sourceUrl
        ? `<a class="recall-title-link" href="${escapeHtml(
            sourceUrl,
          )}" target="_blank" rel="noopener noreferrer">${escapeHtml(titleText)}</a>`
        : `<span class="recall-title-link is-muted">${escapeHtml(titleText)}</span>`;
      const copyTitleText = sourceUrl ? `${titleText}\n${sourceUrl}` : titleText;
      const tagsText = entry.tags.length ? entry.tags.join(", ") : "-";
      const openAction = sourceUrl
        ? `<a class="small-btn recall-action-btn" href="${escapeHtml(
            sourceUrl,
          )}" target="_blank" rel="noopener noreferrer">打开原文</a>`
        : '<button class="small-btn recall-action-btn" type="button" disabled>打开原文</button>';
      const keyPointsHtml = entry.keyPoints.length
        ? `<ol class="recall-key-points">${entry.keyPoints
            .map((item) => `<li>${escapeHtml(item)}</li>`)
            .join("")}</ol>`
        : "";
      const open = idx === 0 ? " open" : "";
      return (
        `<details class="source-item recall-card" data-entry-id="${escapeHtml(entry.id)}"${open}>` +
        `<summary class="recall-summary">` +
        `<span class="evidence-id-badge">${escapeHtml(entry.id)}</span>` +
        `<span class="source-kind">${escapeHtml(entry.kindLabel)}</span>` +
        `<span class="score-pill ${scoreClass(entry.score)}">score ${entry.score.toFixed(4)}</span>` +
        `</summary>` +
        `<div class="recall-title-row">` +
        `<span class="recall-title-wrap">${titleLink}</span>` +
        `<button class="small-btn recall-copy-btn" type="button" data-copy="${escapeHtml(
          encodeDataValue(copyTitleText),
        )}">复制</button>` +
        `</div>` +
        `<div class="recall-one-line">${escapeHtml(entry.oneLineSummary || "-")}</div>` +
        keyPointsHtml +
        `<div class="evidence-citation-links">${citationLinks}</div>` +
        `<details class="recall-snippet-details">` +
        `<summary>证据片段（可折叠）</summary>` +
        `<div class="recall-snippet-body">${snippetHtml}</div>` +
        `</details>` +
        `<div class="recall-tags">标签：${escapeHtml(tagsText)}</div>` +
        (state.evidenceMode === "expert"
          ? `<div class="recall-expert-meta">` +
            `<div class="source-lines">title=${escapeHtml(entry.title || "-")}</div>` +
            `<div class="source-lines">source=${escapeHtml(entry.filePath)}:${entry.startLine}-${entry.endLine}</div>` +
            `<div class="source-lines">${escapeHtml(entry.reason)}</div>` +
            `<div class="source-lines">DocID: ${escapeHtml(entry.documentId || "-")}</div>` +
            `<div class="source-lines">命中范围: ${escapeHtml(entry.hitRange || "-")}</div>` +
            `<div class="source-lines">ref=${escapeHtml(entry.ref)}</div>` +
            `</div>`
          : "") +
        `<div class="recall-actions">` +
        openAction +
        `<button class="small-btn recall-action-btn recall-copy-citation-btn" type="button" data-copy="${escapeHtml(
          encodeDataValue(entry.referenceCopy),
        )}">复制引用</button>` +
        `<button class="small-btn recall-action-btn recall-feedback-btn" type="button" data-feedback="helpful">标记有用</button>` +
        `<button class="small-btn recall-action-btn recall-feedback-btn" type="button" data-feedback="irrelevant">标记无关</button>` +
        `</div>` +
        `</details>`
      );
    })
    .join("");
}

function renderAdoptedEvidence(model) {
  const rows = model.adoptedEntries.length ? model.adoptedEntries : model.entries;
  if (!rows.length) {
    evidenceAdopted.innerHTML = '<div class="meta-item">No adopted evidence available.</div>';
    return;
  }
  evidenceAdopted.innerHTML = rows
    .map((entry) => {
      const preview = entry.preview.text || "(empty snippet)";
      const citationLinks = entry.citationIndexes.length
        ? entry.citationIndexes.map((item) => `<span class="citation-link">C${item}</span>`).join("")
        : '<span class="citation-link is-muted">Implicit support</span>';
      return (
        `<article class="source-item context-card">` +
        `<div class="context-meta">` +
        `<span class="evidence-id-badge">${escapeHtml(entry.id)}</span>` +
        `<span class="source-kind">${escapeHtml(entry.kindLabel)}</span>` +
        `<span class="score-pill ${scoreClass(entry.score)}">score ${entry.score.toFixed(4)}</span>` +
        `</div>` +
        `<div class="source-symbol">${escapeHtml(entry.symbol)}</div>` +
        `<div class="source-path">${escapeHtml(entry.filePath)}:${entry.startLine}-${entry.endLine}</div>` +
        `<div class="source-lines">${escapeHtml(entry.reason)}</div>` +
        `<pre class="code-snippet"><code>${escapeHtml(preview)}</code></pre>` +
        (entry.preview.truncated
          ? '<div class="source-lines">Preview is truncated. Open Recall Browser for full preview.</div>'
          : "") +
        `<div class="evidence-citation-links">${citationLinks}</div>` +
        `</article>`
      );
    })
    .join("");
}

function renderEvidenceCitations(model) {
  if (!model.citations.length) {
    evidenceCitations.innerHTML = '<div class="meta-item">No citations generated for this answer.</div>';
    return;
  }
  evidenceCitations.innerHTML = model.citations
    .map((item) => {
      const evidenceRefs = item.evidenceIds.length
        ? item.evidenceIds.map((entryId) => `<span class="citation-link">${escapeHtml(entryId)}</span>`).join("")
        : '<span class="citation-link is-muted">No linked evidence card</span>';
      return (
        `<article class="source-item">` +
        `<div class="context-meta">` +
        `<span class="evidence-id-badge">${escapeHtml(item.id)}</span>` +
        `<span class="source-kind">${escapeHtml(sourceKindLabel(item.source_kind))}</span>` +
        `<span class="score-pill ${scoreClass(item.score)}">score ${item.score.toFixed(4)}</span>` +
        `</div>` +
        `<div class="source-symbol">${escapeHtml(item.title || "-")}</div>` +
        `<div class="source-path">${escapeHtml(item.source_uri || "-")}</div>` +
        `<div class="source-lines">${escapeHtml(item.ref || "-")}</div>` +
        `<div class="evidence-citation-links">${evidenceRefs}</div>` +
        `</article>`
      );
    })
    .join("");
}

function applyEvidenceModeButtons() {
  evidenceModeButtons.forEach((button) => {
    const isActive = button.getAttribute("data-mode") === state.evidenceMode;
    button.classList.toggle("is-active", isActive);
  });
}

function setPanelVisible(panel, visible) {
  if (!panel) {
    return;
  }
  panel.hidden = !visible;
  panel.style.display = visible ? "" : "none";
}

function applyEvidenceTab() {
  evidenceTabs.forEach((button) => {
    const isActive = button.getAttribute("data-tab") === state.activeTab;
    button.classList.toggle("is-active", isActive);
  });
  setPanelVisible(evidenceOverview, state.activeTab === "overview");
  setPanelVisible(evidenceRecall, state.activeTab === "recall");
  setPanelVisible(evidenceAdopted, state.activeTab === "adopted");
  setPanelVisible(evidenceCitations, state.activeTab === "citations");
  setPanelVisible(evidenceMeta, state.activeTab === "debug");
}

function renderEvidence(evidence) {
  const model = buildEvidenceModel(evidence);
  const summaryText = model.entries.length
    ? buildEvidenceSummary(model)
    : "No retrieval evidence is available for this assistant message.";
  if (evidenceSummary) {
    evidenceSummary.textContent = summaryText;
  }
  renderEvidenceOverview(model);
  renderRecallBrowser(model);
  renderAdoptedEvidence(model);
  renderEvidenceCitations(model);
  evidenceMeta.textContent =
    state.evidenceMode === "expert" && Object.keys(model.meta).length
      ? JSON.stringify(model.meta, null, 2)
      : "Switch to Expert mode to inspect raw retrieval metadata.";
  applyEvidenceModeButtons();
  applyEvidenceTab();
}

function selectEvidenceByAssistantMessage(messageId) {
  if (!messageId) {
    state.selectedAssistantMessageId = null;
    renderEvidence(null);
    return;
  }
  openEvidencePanel();
  state.selectedAssistantMessageId = messageId;
  const evidence = state.evidenceByAssistantId.get(messageId) || null;
  renderEvidence(evidence);

  Array.from(messageList.querySelectorAll(".chat-message.assistant")).forEach((node) => {
    node.classList.toggle("is-selected", node.getAttribute("data-message-id") === messageId);
  });
}

async function loadProjects() {
  const raw = await requestApi("/projects", { method: "GET" });
  const rows = Array.isArray(raw) ? raw : Array.isArray(raw?.items) ? raw.items : [];
  state.projects = rows
    .map((item, idx) => {
      const id = String(item?.id ?? item?.project_id ?? item?.uuid ?? "").trim();
      const name = String(item?.name ?? item?.project_name ?? `project-${idx + 1}`).trim();
      return { id, name };
    })
    .filter((item) => item.id && item.name);

  if (!state.currentProjectId || !state.projects.some((item) => item.id === state.currentProjectId)) {
    state.currentProjectId = state.projects.length ? state.projects[0].id : null;
  }
  ensureProjectExpanded(state.currentProjectId);
  renderProjectList();
  renderProjectOptions("");
}

async function loadSessionsForProject(projectId, { ensureSelection = false } = {}) {
  if (!projectId) {
    return [];
  }
  const result = await requestApi(
    `/chat/sessions?limit=100&offset=0&project_id=${encodeURIComponent(projectId)}`,
    { method: "GET" },
  );
  const sessions = Array.isArray(result?.items) ? result.items : [];
  state.sessionsByProject.set(projectId, sessions);

  if (projectId === state.currentProjectId) {
    state.sessions = sessions;
    if (ensureSelection) {
      if (state.currentSessionId && !sessions.some((item) => item.id === state.currentSessionId)) {
        state.currentSessionId = sessions.length ? sessions[0].id : null;
      }
      if (!state.currentSessionId && sessions.length) {
        state.currentSessionId = sessions[0].id;
      }
    }
  }

  renderProjectList();
  return sessions;
}

async function loadMessages(sessionId) {
  if (!sessionId) {
    state.messages = [];
    renderMessages([]);
    selectEvidenceByAssistantMessage(null);
    return;
  }

  const result = await requestApi(`/chat/sessions/${sessionId}/messages?limit=100`, { method: "GET" });
  const items = Array.isArray(result?.items) ? result.items : [];
  state.messages = items;

  state.evidenceByAssistantId.clear();
  items.forEach((item) => {
    if (item.role === "assistant" && item.query_response) {
      state.evidenceByAssistantId.set(item.id, item.query_response);
    }
  });

  renderMessages(items);
  const selectedExists =
    state.selectedAssistantMessageId && items.some((item) => item.id === state.selectedAssistantMessageId);
  if (selectedExists) {
    selectEvidenceByAssistantMessage(state.selectedAssistantMessageId);
    return;
  }
  const latestAssistant = [...items].reverse().find((item) => item.role === "assistant");
  selectEvidenceByAssistantMessage(latestAssistant?.id || null);
}
function syncCurrentSessionSettings() {
  const session = currentSession();
  if (!session) {
    renderProjectOptions(state.currentProjectId || "");
    return;
  }
  renderProjectOptions(session.default_project_id || state.currentProjectId || "");
}

async function openSession(sessionId, projectId = state.currentProjectId) {
  if (!projectId) {
    return;
  }
  if (projectId !== state.currentProjectId) {
    state.currentProjectId = projectId;
    ensureProjectExpanded(projectId);
  }
  await loadSessionsForProject(projectId, { ensureSelection: false });
  state.sessions = getProjectSessions(projectId);
  state.currentSessionId = sessionId;
  renderProjectList();
  syncCurrentSessionSettings();
  await loadMessages(sessionId);
}

async function createSession() {
  if (!state.currentProjectId) {
    throw new Error("Please select a project first.");
  }

  const payload = { default_project_id: state.currentProjectId };
  const created = await requestApi("/chat/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  state.currentProjectId = created.default_project_id || state.currentProjectId;
  ensureProjectExpanded(state.currentProjectId);
  await loadSessionsForProject(state.currentProjectId, { ensureSelection: false });
  state.sessions = getProjectSessions(state.currentProjectId);
  state.currentSessionId = created.id;
  renderProjectList();
  syncCurrentSessionSettings();
  await loadMessages(created.id);
  log(`Session created (${String(created.id).slice(0, 8)})`);
}

async function saveSessionSettings() {
  const session = currentSession();
  if (!session) {
    throw new Error("Please create or select a session first.");
  }

  const oldProjectId = session.default_project_id || state.currentProjectId;
  const payload = { default_project_id: sessionDefaultProject.value };
  const updated = await requestApi(`/chat/sessions/${session.id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

  state.currentProjectId = updated.default_project_id || state.currentProjectId;
  ensureProjectExpanded(state.currentProjectId);
  if (oldProjectId) {
    await loadSessionsForProject(oldProjectId, { ensureSelection: false });
  }
  await loadSessionsForProject(state.currentProjectId, { ensureSelection: false });
  state.sessions = getProjectSessions(state.currentProjectId);

  if (!state.sessions.some((item) => item.id === session.id)) {
    state.currentSessionId = state.sessions.length ? state.sessions[0].id : null;
  } else {
    state.currentSessionId = session.id;
  }

  renderProjectList();
  syncCurrentSessionSettings();
  await loadMessages(state.currentSessionId);
  log("Session default project updated.");
}

async function renameSession() {
  const session = currentSession();
  if (!session) {
    throw new Error("Please create or select a session first.");
  }
  const title = window.prompt("Enter a new session title", session.title || "");
  if (title === null) {
    return;
  }
  await requestApi(`/chat/sessions/${session.id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  await loadSessionsForProject(state.currentProjectId, { ensureSelection: false });
  renderProjectList();
  log("Session title updated.");
}

async function maybeAutoRenameSessionFromFirstMessage(session, content) {
  const hadUserMessage = state.messages.some((item) => item.role === "user");
  if (hadUserMessage || !session?.id) {
    return;
  }
  const generatedTitle = summarizeSessionTitle(content);
  if (!generatedTitle) {
    return;
  }
  const currentTitle = String(session.title || "").trim();
  if (currentTitle === generatedTitle) {
    return;
  }
  await requestApi(`/chat/sessions/${session.id}`, {
    method: "PATCH",
    body: JSON.stringify({ title: generatedTitle }),
  });
}

async function addAssistantMessageToMemory(messageId) {
  const session = currentSession();
  if (!session || !session.default_project_id) {
    throw new Error("Current session has no default project.");
  }
  const message = state.messages.find((item) => item.id === messageId);
  if (!message || message.role !== "assistant") {
    throw new Error("Only assistant messages can be added to memory.");
  }

  const draft = String(message.content || "").slice(0, 1200);
  const content = window.prompt("Edit memory content before saving", draft);
  if (content === null) {
    return;
  }
  const normalized = content.trim();
  if (!normalized) {
    throw new Error("Memory content cannot be empty.");
  }
  await requestApi(`/projects/${session.default_project_id}/memories`, {
    method: "POST",
    body: JSON.stringify({ content: normalized }),
  });
  log("Added to project memory.");
}

async function sendMessage(event) {
  event.preventDefault();
  let session = currentSession();
  if (!session) {
    await createSession();
    session = currentSession();
  }
  if (!session) {
    throw new Error("Session creation failed. Please retry.");
  }

  const content = String(chatInput.value || "").trim();
  if (!content) {
    chatInput.focus();
    return;
  }

  sendMessageBtn.disabled = true;
  sendMessageBtn.textContent = "Sending...";
  chatInput.disabled = true;

  const scope = chatKnowledgeScope.value || "auto";
  const topKInput = Number(chatTopK.value || 8);
  const topK = Number.isFinite(topKInput) && topKInput > 0 ? Math.round(topKInput) : 8;
  const projectName = currentProject()?.name || "Untitled Project";
  const optimisticNow = new Date().toISOString();
  const pendingUserMessage = {
    id: `pending-user-${Date.now()}`,
    role: "user",
    content,
    created_at: optimisticNow,
    effective_project_id: session.default_project_id || state.currentProjectId,
    is_pending: true,
  };
  const pendingAssistantMessage = {
    id: `pending-assistant-${Date.now()}`,
    role: "assistant",
    content: "",
    created_at: optimisticNow,
    effective_project_id: session.default_project_id || state.currentProjectId,
    is_pending: true,
  };

  const requestStartedAt = Date.now();
  const processRenderer = createProcessRenderer({
    baseMessages: [...state.messages],
    pendingUserMessage,
    pendingAssistantMessage,
    projectName,
    scope,
    topK,
  });

  try {
    const payload = {
      content,
      top_k: topK,
      knowledge_scope: scope,
      need_citations: true,
    };

    const result = await requestApi(`/chat/sessions/${session.id}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    processRenderer.complete(collectProcessStats(result));
    processRenderer.stop();

    try {
      await maybeAutoRenameSessionFromFirstMessage(session, content);
    } catch (renameError) {
      log(`Auto-title skipped: ${renameError.message}`);
    }

    chatInput.value = "";
    await loadSessionsForProject(state.currentProjectId, { ensureSelection: false });
    state.sessions = getProjectSessions(state.currentProjectId);
    state.currentSessionId = session.id;
    renderProjectList();
    await loadMessages(session.id);

    if (Array.isArray(result?.deprecation_warnings) && result.deprecation_warnings.length) {
      result.deprecation_warnings.forEach((item) => log(`Deprecation warning: ${item}`));
    }

    if (result?.assistant_message?.id && result?.assistant_message?.query_response) {
      state.evidenceByAssistantId.set(result.assistant_message.id, result.assistant_message.query_response);
      selectEvidenceByAssistantMessage(result.assistant_message.id);
    } else if (result?.assistant_message?.id) {
      const fallbackEvidence = {
        sources: result.sources || [],
        contexts: result.contexts || [],
        citations: result.citations || [],
        retrieval_meta: result.retrieval_meta || {},
      };
      state.evidenceByAssistantId.set(result.assistant_message.id, fallbackEvidence);
      selectEvidenceByAssistantMessage(result.assistant_message.id);
    }

    log("Message sent successfully.");
  } catch (error) {
    processRenderer.stop();
    const elapsedMs = Date.now() - requestStartedAt;
    const rawMessage = String(error?.message || "");
    const isTimeout = elapsedMs >= 30000 || /timeout|timed out/i.test(rawMessage);
    const failedUserMessage = {
      ...pendingUserMessage,
      id: `failed-user-${Date.now()}`,
      is_pending: false,
    };
    const failedSystemMessage = {
      id: `failed-system-${Date.now()}`,
      role: "system",
      created_at: new Date().toISOString(),
      effective_project_id: session.default_project_id || state.currentProjectId,
      content: isTimeout
        ? "Request timed out.\nTry again or narrow your scope."
        : "Request failed.\nPlease retry with a more specific prompt.",
    };
    renderMessages([...state.messages, failedUserMessage, failedSystemMessage]);
    throw error;
  } finally {
    processRenderer.stop();
    sendMessageBtn.disabled = false;
    sendMessageBtn.textContent = "Send";
    chatInput.disabled = false;
    chatInput.focus();
  }
}

async function switchProject(projectId) {
  if (!projectId || projectId === state.currentProjectId) {
    return;
  }
  state.currentProjectId = projectId;
  state.currentSessionId = null;
  ensureProjectExpanded(projectId);
  await loadSessionsForProject(projectId, { ensureSelection: true });
  state.sessions = getProjectSessions(projectId);
  renderProjectList();
  syncCurrentSessionSettings();
  await loadMessages(state.currentSessionId);
}

async function toggleProject(projectId) {
  if (!projectId) {
    return;
  }
  if (isProjectExpanded(projectId)) {
    state.expandedProjects.delete(projectId);
    renderProjectList();
    return;
  }
  ensureProjectExpanded(projectId);
  if (!state.sessionsByProject.has(projectId)) {
    await loadSessionsForProject(projectId, { ensureSelection: false });
    return;
  }
  renderProjectList();
}

projectList.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const toggle = target.closest(".project-tree-toggle");
  if (toggle instanceof HTMLButtonElement) {
    const projectId = toggle.getAttribute("data-project-id") || "";
    try {
      await toggleProject(projectId);
    } catch (error) {
      log(`Failed to toggle project: ${error.message}`);
    }
    return;
  }

  const sessionBtn = target.closest(".chat-session-item");
  if (sessionBtn instanceof HTMLButtonElement) {
    const sessionId = sessionBtn.getAttribute("data-session-id") || "";
    const projectId = sessionBtn.getAttribute("data-project-id") || state.currentProjectId || "";
    if (!sessionId || (sessionId === state.currentSessionId && projectId === state.currentProjectId)) {
      return;
    }
    try {
      await openSession(sessionId, projectId);
    } catch (error) {
      log(`Failed to open session: ${error.message}`);
    }
    return;
  }

  const projectBtn = target.closest(".project-tree-header");
  if (projectBtn instanceof HTMLButtonElement) {
    const projectId = projectBtn.getAttribute("data-project-id") || "";
    try {
      await switchProject(projectId);
    } catch (error) {
      log(`Failed to switch project: ${error.message}`);
    }
  }
});

messageList.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const evidenceTrigger = target.closest(".evidence-select-btn");
  if (evidenceTrigger instanceof HTMLButtonElement) {
    const messageId = evidenceTrigger.getAttribute("data-message-id") || "";
    selectEvidenceByAssistantMessage(messageId);
    return;
  }
  const memoryTrigger = target.closest(".memory-add-btn");
  if (memoryTrigger instanceof HTMLButtonElement) {
    const messageId = memoryTrigger.getAttribute("data-message-id") || "";
    try {
      await addAssistantMessageToMemory(messageId);
    } catch (error) {
      log(`Failed to add memory: ${error.message}`);
    }
  }
});

evidenceRecall.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const copyTrigger = target.closest(".recall-copy-btn, .recall-copy-citation-btn");
  if (copyTrigger instanceof HTMLButtonElement) {
    event.preventDefault();
    const payload = decodeDataValue(copyTrigger.getAttribute("data-copy") || "");
    try {
      await copyText(payload);
      log("Copied evidence content.");
    } catch (error) {
      log(`Copy failed: ${error.message}`);
    }
    return;
  }

  const feedbackTrigger = target.closest(".recall-feedback-btn");
  if (feedbackTrigger instanceof HTMLButtonElement) {
    event.preventDefault();
    const card = feedbackTrigger.closest(".recall-card");
    if (card instanceof HTMLElement) {
      card.querySelectorAll(".recall-feedback-btn").forEach((node) => {
        node.classList.remove("is-active");
      });
    }
    feedbackTrigger.classList.add("is-active");
    const feedback = feedbackTrigger.getAttribute("data-feedback") === "helpful" ? "有用" : "无关";
    const entryId = feedbackTrigger.closest(".recall-card")?.getAttribute("data-entry-id") || "unknown";
    log(`Evidence ${entryId} marked as ${feedback}.`);
  }
});

evidenceTabs.forEach((tabButton) => {
  tabButton.addEventListener("click", () => {
    state.activeTab = tabButton.getAttribute("data-tab") || "overview";
    applyEvidenceTab();
  });
});

evidenceModeButtons.forEach((modeButton) => {
  modeButton.addEventListener("click", () => {
    const mode = modeButton.getAttribute("data-mode");
    if (mode !== "simple" && mode !== "expert") {
      return;
    }
    if (mode === state.evidenceMode) {
      return;
    }
    state.evidenceMode = mode;
    const evidence = state.selectedAssistantMessageId
      ? state.evidenceByAssistantId.get(state.selectedAssistantMessageId) || null
      : null;
    renderEvidence(evidence);
  });
});

function openEvidencePanel() {
  evidencePanel.classList.add("is-open");
  appShell?.classList.add("has-evidence");
  if (toggleEvidenceBtn) {
    toggleEvidenceBtn.textContent = "Hide Evidence";
  }
}

function closeEvidencePanel() {
  evidencePanel.classList.remove("is-open");
  appShell?.classList.remove("has-evidence");
  if (toggleEvidenceBtn) {
    toggleEvidenceBtn.textContent = "Evidence";
  }
}

function toggleEvidencePanel() {
  if (evidencePanel.classList.contains("is-open")) {
    closeEvidencePanel();
    return;
  }
  openEvidencePanel();
}

if (toggleEvidenceBtn) {
  toggleEvidenceBtn.addEventListener("click", toggleEvidencePanel);
}
if (toggleEvidenceBtnInner) {
  toggleEvidenceBtnInner.addEventListener("click", closeEvidencePanel);
}

newSessionBtn.addEventListener("click", async () => {
  try {
    await createSession();
  } catch (error) {
    log(`Failed to create session: ${error.message}`);
  }
});

saveSessionSettingsBtn.addEventListener("click", async () => {
  try {
    await saveSessionSettings();
  } catch (error) {
    log(`Failed to save session settings: ${error.message}`);
  }
});

renameSessionBtn.addEventListener("click", async () => {
  try {
    await renameSession();
  } catch (error) {
    log(`Rename failed: ${error.message}`);
  }
});

chatForm.addEventListener("submit", async (event) => {
  try {
    await sendMessage(event);
  } catch (error) {
    log(`Message send failed: ${error.message}`);
  }
});

(async function bootstrap() {
  try {
    closeEvidencePanel();
    renderEvidence(null);
    applyEvidenceTab();
    await loadProjects();
    if (state.currentProjectId) {
      await loadSessionsForProject(state.currentProjectId, { ensureSelection: true });
      state.sessions = getProjectSessions(state.currentProjectId);
    }
    syncCurrentSessionSettings();
    if (state.currentSessionId) {
      await loadMessages(state.currentSessionId);
    } else {
      renderMessages([]);
    }
    renderProjectList();
    log("Smart chat is ready.");
  } catch (error) {
    log(`Initialization failed: ${error.message}`);
  }
})();
