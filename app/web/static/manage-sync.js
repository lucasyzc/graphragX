const requestApi = window.ConsoleCommon.api;
const fmtTime = window.ConsoleCommon.formatTime;
const readActorRole = window.ConsoleCommon.getActorRole;
const saveActorContext = window.ConsoleCommon.persistActorContext;
const renderStatusTag = window.ConsoleCommon.statusTag;

const filterProjectId = document.getElementById("filterProjectId");
const filterStatus = document.getElementById("filterStatus");
const autoRefresh = document.getElementById("autoRefresh");
const refreshJobsBtn = document.getElementById("refreshJobsBtn");
const syncForm = document.getElementById("syncForm");
const syncProjectId = document.getElementById("syncProjectId");
const triggerSyncBtn = document.getElementById("triggerSyncBtn");
const syncMsg = document.getElementById("syncMsg");
const runningJobsBoard = document.getElementById("runningJobsBoard");
const jobsBody = document.getElementById("jobsBody");
const systemLog = document.getElementById("systemLog");
const actorRole = document.getElementById("actorRole");

let projects = [];
let refreshTimer = null;

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
}

function editable() {
  return readActorRole() === "admin" || readActorRole() === "editor";
}

function applyRolePermissions() {
  const canEdit = editable();
  syncForm.querySelectorAll("input,select").forEach((node) => {
    node.disabled = !canEdit;
  });
  triggerSyncBtn.hidden = !canEdit;
  if (!canEdit) {
    syncMsg.textContent = "当前角色为 viewer，仅可查看任务状态。";
  } else {
    syncMsg.textContent = "";
  }
}

function projectName(projectId) {
  const row = projects.find((item) => item.id === projectId);
  return row ? row.name : projectId;
}

function renderHistory(items) {
  if (!items.length) {
    jobsBody.innerHTML = '<tr><td colspan="6">暂无任务记录</td></tr>';
    return;
  }
  jobsBody.innerHTML = items
    .map(
      (job) => `
        <tr>
          <td>${job.id}</td>
          <td>${projectName(job.project_id)}</td>
          <td>${job.mode}</td>
          <td>${renderStatusTag(job.status)}</td>
          <td>${job.message || "-"}</td>
          <td>${fmtTime(job.created_at)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderRunningBoard(items) {
  if (!items.length) {
    runningJobsBoard.innerHTML = '<div class="meta-item">当前无运行中的同步任务</div>';
    return;
  }
  runningJobsBoard.innerHTML = items
    .map(
      (job) => `
        <article class="job-card">
          <strong>${projectName(job.project_id)}</strong><br/>
          ${renderStatusTag(job.status)} mode=${job.mode}<br/>
          job=${job.id.slice(0, 8)}<br/>
          ${job.message || "处理中"}
        </article>
      `,
    )
    .join("");
}

function syncFiltersToForm() {
  if (filterProjectId.value) {
    syncProjectId.value = filterProjectId.value;
  }
}

async function loadProjects() {
  projects = await requestApi("/projects", { method: "GET" });
  const options = projects.map((project) => `<option value="${project.id}">${project.name}</option>`).join("");
  filterProjectId.innerHTML = `<option value="">全部</option>${options}`;
  syncProjectId.innerHTML = options;
  log(`项目加载完成，共 ${projects.length} 个`);
}

async function refreshJobs() {
  const query = new URLSearchParams();
  if (filterProjectId.value) {
    query.set("project_id", filterProjectId.value);
  }
  if (filterStatus.value) {
    query.set("status", filterStatus.value);
  }
  query.set("limit", "100");
  const history = await requestApi(`/jobs?${query.toString()}`, { method: "GET" });
  renderHistory(history.items || []);

  const runningQuery = new URLSearchParams();
  if (filterProjectId.value) {
    runningQuery.set("project_id", filterProjectId.value);
  }
  runningQuery.set("status", "running");
  runningQuery.set("limit", "20");
  const running = await requestApi(`/jobs?${runningQuery.toString()}`, { method: "GET" });
  renderRunningBoard(running.items || []);
}

function setupAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (autoRefresh.checked) {
    refreshTimer = setInterval(async () => {
      try {
        await refreshJobs();
      } catch (error) {
        log(`自动刷新失败: ${error.message}`);
      }
    }, 10000);
  }
}

syncForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editable()) {
    syncMsg.textContent = "viewer 无权限触发同步";
    return;
  }
  syncMsg.textContent = "提交同步任务...";

  const form = new FormData(syncForm);
  const payload = {
    mode: form.get("mode"),
    head_sha: form.get("head_sha") || null,
    base_sha: form.get("base_sha") || null,
    since_sha: form.get("since_sha") || null,
  };
  try {
    const job = await requestApi(`/projects/${syncProjectId.value}/sync`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    syncMsg.textContent = `已提交任务: ${job.id}`;
    log(`同步任务提交成功: ${job.id}`);
    await refreshJobs();
  } catch (error) {
    syncMsg.textContent = error.message;
    log(`同步提交失败: ${error.message}`);
  }
});

[filterProjectId, filterStatus].forEach((node) => {
  node.addEventListener("change", async () => {
    syncFiltersToForm();
    await refreshJobs();
  });
});

refreshJobsBtn.addEventListener("click", async () => {
  await refreshJobs();
  log("任务刷新完成");
});

autoRefresh.addEventListener("change", setupAutoRefresh);
actorRole.addEventListener("change", () => {
  saveActorContext();
  applyRolePermissions();
});

(async function bootstrap() {
  applyRolePermissions();
  try {
    await loadProjects();
    syncFiltersToForm();
    await refreshJobs();
    setupAutoRefresh();
  } catch (error) {
    log(`初始化失败: ${error.message}`);
  }
})();
