(() => {
  const actorUserInput = document.getElementById("actorUser");
  const actorRoleInput = document.getElementById("actorRole");

  function loadActorContext() {
    const user = localStorage.getItem("cg_actor_user") || "alice";
    const role = localStorage.getItem("cg_actor_role") || "admin";
    if (actorUserInput) {
      actorUserInput.value = user;
    }
    if (actorRoleInput) {
      actorRoleInput.value = role;
    }
  }

  function persistActorContext() {
    if (actorUserInput) {
      localStorage.setItem("cg_actor_user", actorUserInput.value || "alice");
    }
    if (actorRoleInput) {
      localStorage.setItem("cg_actor_role", actorRoleInput.value || "admin");
    }
  }

  function getActorRole() {
    if (!actorRoleInput) {
      return localStorage.getItem("cg_actor_role") || "admin";
    }
    return actorRoleInput.value || "admin";
  }

  function getHeaders() {
    return {
      "Content-Type": "application/json",
      "X-User": actorUserInput?.value || localStorage.getItem("cg_actor_user") || "alice",
      "X-Role": getActorRole(),
    };
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...getHeaders(),
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const detail = payload?.detail ? JSON.stringify(payload.detail) : JSON.stringify(payload);
      throw new Error(`${response.status} ${response.statusText}: ${detail}`);
    }
    return payload;
  }

  function formatTime(value) {
    if (!value) {
      return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString();
  }

  function statusTag(status) {
    const safe = status || "unknown";
    return `<span class="tag tag-${safe}">${safe}</span>`;
  }

  function parseFilePathFromSourceRef(ref) {
    const match = String(ref || "").match(/^(.*):\d+-\d+\s+\(/);
    return match ? match[1] : null;
  }

  loadActorContext();
  if (actorUserInput) {
    actorUserInput.addEventListener("change", persistActorContext);
  }
  if (actorRoleInput) {
    actorRoleInput.addEventListener("change", persistActorContext);
  }

  window.ConsoleCommon = {
    api,
    formatTime,
    getActorRole,
    parseFilePathFromSourceRef,
    persistActorContext,
    statusTag,
  };
})();
