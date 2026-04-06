const requestApi = window.ConsoleCommon.api;
const fmtTime = window.ConsoleCommon.formatTime;
const readActorRole = window.ConsoleCommon.getActorRole;
const saveActorContext = window.ConsoleCommon.persistActorContext;

const nameFilter = document.getElementById("nameFilter");
const scmFilter = document.getElementById("scmFilter");
const timeFilter = document.getElementById("timeFilter");
const refreshProjectsBtn = document.getElementById("refreshProjectsBtn");
const projectsBody = document.getElementById("projectsBody");
const createProjectForm = document.getElementById("createProjectForm");
const createProjectBtn = document.getElementById("createProjectBtn");
const createProjectMsg = document.getElementById("createProjectMsg");
const projectDetailForm = document.getElementById("projectDetailForm");
const projectDetailMsg = document.getElementById("projectDetailMsg");
const saveProjectBtn = document.getElementById("saveProjectBtn");
const syncStatusBox = document.getElementById("syncStatusBox");
const memberRoleBox = document.getElementById("memberRoleBox");
const systemLog = document.getElementById("systemLog");
const actorRole = document.getElementById("actorRole");

const scmProviderSelect = createProjectForm.querySelector("select[name='scm_provider']");
const repoUrlInput = createProjectForm.querySelector("input[name='repo_url']");
const branchInput = createProjectForm.querySelector("input[name='default_branch']");

let allProjects = [];
let selectedProject = null;
const syncStatusMap = new Map();

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
}

function isEditableRole() {
  return readActorRole() === "admin" || readActorRole() === "editor";
}

function applyRolePermissions() {
  const editable = isEditableRole();
  createProjectBtn.hidden = !editable;
  saveProjectBtn.hidden = !editable;
  createProjectForm.querySelectorAll("input,select").forEach((node) => {
    node.disabled = !editable;
  });
  projectDetailForm.querySelectorAll("input").forEach((node) => {
    if (node.name === "id" || node.name === "scm_provider") {
      return;
    }
    node.disabled = !editable;
  });
  if (!editable) {
    createProjectMsg.textContent = "当前角色为 viewer，仅可查看。";
    projectDetailMsg.textContent = "当前角色为 viewer，仅可查看。";
  } else {
    createProjectMsg.textContent = "";
    projectDetailMsg.textContent = "";
  }
  memberRoleBox.innerHTML = `<div class="meta-item"><strong>当前 Actor 角色：</strong>${readActorRole()}</div>`;
}

function filterProjects(rows) {
  const keyword = (nameFilter.value || "").trim().toLowerCase();
  const scm = scmFilter.value || "";
  const days = Number(timeFilter.value || "0");
  const now = Date.now();

  return rows.filter((project) => {
    if (keyword && !project.name.toLowerCase().includes(keyword)) {
      return false;
    }
    if (scm && project.scm_provider !== scm) {
      return false;
    }
    if (days > 0) {
      const created = new Date(project.created_at).getTime();
      if (Number.isNaN(created)) {
        return false;
      }
      if (now - created > days * 24 * 60 * 60 * 1000) {
        return false;
      }
    }
    return true;
  });
}

function summaryStatus(projectId) {
  const status = syncStatusMap.get(projectId);
  if (!status) {
    return "-";
  }
  if (status.active_job) {
    return status.active_job.status;
  }
  if (status.last_success_job) {
    return `done @ ${fmtTime(status.last_success_job.finished_at || status.last_success_job.created_at)}`;
  }
  if (status.last_failed_job) {
    return "failed";
  }
  return "无记录";
}

function renderProjectTable() {
  const rows = filterProjects(allProjects);
  projectsBody.innerHTML = rows
    .map(
      (project) => `
        <tr>
          <td>${project.name}</td>
          <td>${project.scm_provider}</td>
          <td>${project.default_branch || "-"}</td>
          <td>${fmtTime(project.created_at)}</td>
          <td>${summaryStatus(project.id)}</td>
          <td><button class="small-btn" type="button" data-project-id="${project.id}">详情</button></td>
        </tr>
      `,
    )
    .join("");

  projectsBody.querySelectorAll("button[data-project-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = allProjects.find((item) => item.id === btn.getAttribute("data-project-id"));
      if (target) {
        selectedProject = target;
        renderProjectDetail(target);
      }
    });
  });
}

function renderProjectDetail(project) {
  projectDetailForm.elements.id.value = project.id;
  projectDetailForm.elements.name.value = project.name;
  projectDetailForm.elements.scm_provider.value = project.scm_provider;
  projectDetailForm.elements.repo_url.value = project.repo_url;
  projectDetailForm.elements.default_branch.value = project.default_branch || "";
  projectDetailForm.elements.instructions.value = project.instructions || "";
  projectDetailMsg.textContent = "";
  renderSyncStatus(project.id);
}

function renderSyncStatus(projectId) {
  const status = syncStatusMap.get(projectId);
  if (!status) {
    syncStatusBox.innerHTML = '<div class="meta-item">正在加载同步状态...</div>';
    return;
  }
  syncStatusBox.innerHTML = [
    `<div class="meta-item"><strong>活跃任务：</strong>${status.active_job ? status.active_job.status : "无"}</div>`,
    `<div class="meta-item"><strong>最近成功：</strong>${
      status.last_success_job ? fmtTime(status.last_success_job.finished_at || status.last_success_job.created_at) : "-"
    }</div>`,
    `<div class="meta-item"><strong>最近失败：</strong>${
      status.last_failed_job ? fmtTime(status.last_failed_job.finished_at || status.last_failed_job.created_at) : "-"
    }</div>`,
    `<div class="meta-item"><strong>待处理任务：</strong>${status.pending_count}</div>`,
  ].join("");
}

async function loadSyncStatuses(projects) {
  await Promise.all(
    projects.map(async (project) => {
      try {
        const status = await requestApi(`/projects/${project.id}/sync-status`, { method: "GET" });
        syncStatusMap.set(project.id, status);
      } catch (error) {
        syncStatusMap.set(project.id, {
          active_job: null,
          last_success_job: null,
          last_failed_job: null,
          pending_count: 0,
          _error: error.message,
        });
      }
    }),
  );
}

async function refreshProjects() {
  const projects = await requestApi("/projects", { method: "GET" });
  allProjects = projects;
  await loadSyncStatuses(projects);
  renderProjectTable();
  if (selectedProject) {
    const fresh = allProjects.find((item) => item.id === selectedProject.id);
    if (fresh) {
      selectedProject = fresh;
      renderProjectDetail(fresh);
    }
  } else if (allProjects.length > 0) {
    selectedProject = allProjects[0];
    renderProjectDetail(allProjects[0]);
  }
  log(`项目刷新完成，共 ${projects.length} 个`);
}

function updateCreateProjectHints() {
  const provider = scmProviderSelect.value;
  if (provider === "local") {
    repoUrlInput.placeholder = "例如 D:\\code\\my-repo 或 /home/dev/my-repo";
    branchInput.value = "";
    branchInput.placeholder = "本地项目可留空";
  } else {
    repoUrlInput.placeholder = "https://github.com/org/repo";
    if (!branchInput.value) {
      branchInput.value = "main";
    }
    branchInput.placeholder = "main";
  }
}

createProjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!isEditableRole()) {
    createProjectMsg.textContent = "viewer 无权限创建项目";
    return;
  }
  createProjectMsg.textContent = "创建中...";
  const form = new FormData(createProjectForm);
  const payload = {
    name: form.get("name"),
    scm_provider: form.get("scm_provider"),
    repo_url: form.get("repo_url"),
    default_branch: form.get("default_branch"),
  };
  try {
    const created = await requestApi("/projects", { method: "POST", body: JSON.stringify(payload) });
    createProjectMsg.textContent = `创建成功: ${created.id}`;
    log(`项目创建成功: ${created.name}`);
    await refreshProjects();
  } catch (error) {
    createProjectMsg.textContent = error.message;
    log(`项目创建失败: ${error.message}`);
  }
});

projectDetailForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedProject) {
    projectDetailMsg.textContent = "请先选择项目";
    return;
  }
  if (!isEditableRole()) {
    projectDetailMsg.textContent = "viewer 无权限修改项目";
    return;
  }

  const payload = {
    name: projectDetailForm.elements.name.value,
    repo_url: projectDetailForm.elements.repo_url.value,
    default_branch: projectDetailForm.elements.default_branch.value,
    instructions: projectDetailForm.elements.instructions.value,
  };
  projectDetailMsg.textContent = "保存中...";
  try {
    await requestApi(`/projects/${selectedProject.id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    projectDetailMsg.textContent = "保存成功";
    log(`项目更新成功: ${selectedProject.id}`);
    await refreshProjects();
  } catch (error) {
    projectDetailMsg.textContent = error.message;
    log(`项目更新失败: ${error.message}`);
  }
});

[nameFilter, scmFilter, timeFilter].forEach((node) => {
  node.addEventListener("input", renderProjectTable);
  node.addEventListener("change", renderProjectTable);
});

refreshProjectsBtn.addEventListener("click", async () => {
  await refreshProjects();
});

scmProviderSelect.addEventListener("change", updateCreateProjectHints);
actorRole.addEventListener("change", () => {
  saveActorContext();
  applyRolePermissions();
});

(async function bootstrap() {
  updateCreateProjectHints();
  applyRolePermissions();
  try {
    await refreshProjects();
  } catch (error) {
    log(`初始化失败: ${error.message}`);
  }
})();
