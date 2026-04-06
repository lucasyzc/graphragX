const requestApi = window.ConsoleCommon.api;

const impactForm = document.getElementById("impactForm");
const impactProjectId = document.getElementById("impactProjectId");
const impactBox = document.getElementById("impactBox");
const prefillInfo = document.getElementById("prefillInfo");
const systemLog = document.getElementById("systemLog");

function log(message) {
  const ts = new Date().toLocaleTimeString();
  systemLog.textContent = `[${ts}] ${message}\n${systemLog.textContent}`;
}

function parsePrefill() {
  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("project_id") || "";
  const filePaths = params.getAll("file_paths");
  return { projectId, filePaths };
}

function fillPrefillInfo(projectId, filePaths) {
  const rows = [
    `<div class="meta-item"><strong>来源项目：</strong>${projectId || "未指定"}</div>`,
    `<div class="meta-item"><strong>来源文件数：</strong>${filePaths.length}</div>`,
  ];
  if (filePaths.length) {
    rows.push(
      `<div class="meta-item"><strong>文件清单：</strong><br/>${filePaths
        .map((path) => `<code>${path}</code>`)
        .join("<br/>")}</div>`,
    );
  }
  prefillInfo.innerHTML = rows.join("");
}

async function loadProjects(defaultProjectId) {
  const projects = await requestApi("/projects", { method: "GET" });
  impactProjectId.innerHTML = projects
    .map((p) => `<option value="${p.id}">${p.name} (${p.id.slice(0, 8)})</option>`)
    .join("");
  if (defaultProjectId && projects.some((p) => p.id === defaultProjectId)) {
    impactProjectId.value = defaultProjectId;
  }
  log(`项目加载完成，共 ${projects.length} 个`);
}

impactForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  impactBox.textContent = "分析中...";

  const form = new FormData(impactForm);
  const filePaths = String(form.get("file_paths") || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const payload = {
    project_id: impactProjectId.value,
    file_paths: filePaths,
  };

  try {
    const result = await requestApi("/analysis/impact", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    impactBox.textContent = JSON.stringify(result, null, 2);
    log(`影响分析完成，命中 ${result.impacted_symbols.length} 个符号`);
  } catch (error) {
    impactBox.textContent = error.message;
    log(`影响分析失败: ${error.message}`);
  }
});

(async function bootstrap() {
  const { projectId, filePaths } = parsePrefill();
  fillPrefillInfo(projectId, filePaths);
  try {
    await loadProjects(projectId);
  } catch (error) {
    log(`初始化失败: ${error.message}`);
  }
  if (filePaths.length) {
    impactForm.elements.file_paths.value = filePaths.join("\n");
  }
})();
