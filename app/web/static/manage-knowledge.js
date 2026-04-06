const requestApi = window.ConsoleCommon.api;
const fmtTime = window.ConsoleCommon.formatTime;
const statusTag = window.ConsoleCommon.statusTag;

const sourceForm = document.getElementById("sourceForm");
const sourceMsg = document.getElementById("sourceMsg");
const projectIdSelect = document.getElementById("projectId");
const sourceTableBody = document.getElementById("sourceTableBody");
const jobTableBody = document.getElementById("jobTableBody");
const systemLog = document.getElementById("systemLog");

let projects = [];
let sources = [];

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
}

function projectName(projectId) {
  const row = projects.find((item) => item.id === projectId);
  return row ? row.name : projectId;
}

async function loadProjects() {
  projects = await requestApi("/projects", { method: "GET" });
  projectIdSelect.innerHTML = projects.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
}

function renderSources() {
  if (!sources.length) {
    sourceTableBody.innerHTML = '<tr><td colspan="7">暂无知识源</td></tr>';
    return;
  }
  sourceTableBody.innerHTML = sources
    .map(
      (source) => `
        <tr>
          <td>${source.id.slice(0, 8)}</td>
          <td>${projectName(source.project_id)}</td>
          <td>${source.name}</td>
          <td>${source.source_type}</td>
          <td>${source.source_uri}</td>
          <td>${(source.tags || []).join(", ") || "-"}</td>
          <td><button class="small-btn" type="button" data-source-id="${source.id}">同步</button></td>
        </tr>
      `,
    )
    .join("");

  sourceTableBody.querySelectorAll("button[data-source-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sourceId = btn.getAttribute("data-source-id");
      try {
        await requestApi(`/knowledge/sources/${sourceId}/sync`, {
          method: "POST",
          body: JSON.stringify({ mode: "incremental" }),
        });
        log(`已触发知识同步: ${sourceId}`);
        await refreshJobs();
      } catch (error) {
        log(`触发知识同步失败: ${error.message}`);
      }
    });
  });
}

async function refreshSources() {
  sources = await requestApi("/knowledge/sources", { method: "GET" });
  renderSources();
}

async function refreshJobs() {
  const payload = await requestApi("/knowledge/jobs?limit=50", { method: "GET" });
  const items = payload.items || [];
  if (!items.length) {
    jobTableBody.innerHTML = '<tr><td colspan="6">暂无任务</td></tr>';
    return;
  }
  jobTableBody.innerHTML = items
    .map(
      (job) => `
        <tr>
          <td>${job.id.slice(0, 8)}</td>
          <td>${job.source_id.slice(0, 8)}</td>
          <td>${statusTag(job.status)}</td>
          <td>scanned=${job.scanned_count}, indexed=${job.indexed_count}, skipped=${job.skipped_count}</td>
          <td>${job.message || "-"}</td>
          <td>${fmtTime(job.created_at)}</td>
        </tr>
      `,
    )
    .join("");
}

sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  sourceMsg.textContent = "创建中...";
  const form = new FormData(sourceForm);
  const tags = String(form.get("tags") || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  try {
    const created = await requestApi("/knowledge/sources", {
      method: "POST",
      body: JSON.stringify({
        project_id: form.get("project_id"),
        name: form.get("name"),
        source_type: form.get("source_type"),
        source_uri: form.get("source_uri"),
        tags,
        enabled: true,
      }),
    });
    sourceMsg.textContent = `创建成功: ${created.id}`;
    await refreshSources();
  } catch (error) {
    sourceMsg.textContent = error.message;
    log(`创建知识源失败: ${error.message}`);
  }
});

(async function bootstrap() {
  try {
    await loadProjects();
    await refreshSources();
    await refreshJobs();
  } catch (error) {
    log(`初始化失败: ${error.message}`);
  }
})();
