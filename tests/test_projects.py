from datetime import datetime, timedelta

from app.db.models import SyncJob
from app.db.session import SessionLocal


def test_create_project_and_sync_flow(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "demo-repo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/demo-repo",
            "default_branch": "main",
        },
    )
    assert project_resp.status_code == 200
    project = project_resp.json()

    list_resp = client.get("/projects", headers={"X-User": "alice", "X-Role": "viewer"})
    assert list_resp.status_code == 200
    assert any(item["id"] == project["id"] for item in list_resp.json())

    sync_resp = client.post(
        f"/projects/{project['id']}/sync",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={"mode": "incremental"},
    )
    assert sync_resp.status_code == 200
    job = sync_resp.json()

    job_resp = client.get(f"/jobs/{job['id']}", headers={"X-User": "alice", "X-Role": "viewer"})
    assert job_resp.status_code == 200
    assert job_resp.json()["project_id"] == project["id"]


def test_sync_rejects_when_active_job_exists(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "demo-repo-2",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/demo-repo-2",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    db = SessionLocal()
    try:
        db.add(SyncJob(project_id=project["id"], mode="incremental", status="running"))
        db.commit()
    finally:
        db.close()

    resp = client.post(
        f"/projects/{project['id']}/sync",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={"mode": "incremental"},
    )
    assert resp.status_code == 409


def test_sync_allows_when_active_job_is_stale(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "demo-repo-3",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/demo-repo-3",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    db = SessionLocal()
    try:
        old_time = datetime.utcnow() - timedelta(minutes=500)
        row = SyncJob(
            project_id=project["id"],
            mode="incremental",
            status="queued",
            created_at=old_time,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    resp = client.post(
        f"/projects/{project['id']}/sync",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={"mode": "incremental"},
    )
    assert resp.status_code == 200


def test_create_local_project(client, tmp_path):
    local_repo = tmp_path / "local-repo"
    local_repo.mkdir()
    (local_repo / "README.md").write_text("# local\n", encoding="utf-8")

    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "local-repo",
            "scm_provider": "local",
            "repo_url": str(local_repo),
            "default_branch": "",
        },
    )

    assert project_resp.status_code == 200
    payload = project_resp.json()
    assert payload["scm_provider"] == "local"
    assert payload["repo_url"] == str(local_repo)


def test_list_jobs_endpoint_filters_and_pagination(client):
    alice_project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "alice-repo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/alice-repo",
            "default_branch": "main",
        },
    )
    alice_project = alice_project_resp.json()

    bob_project_resp = client.post(
        "/projects",
        headers={"X-User": "bob", "X-Role": "admin"},
        json={
            "name": "bob-repo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/bob-repo",
            "default_branch": "main",
        },
    )
    bob_project = bob_project_resp.json()

    db = SessionLocal()
    try:
        db.add(SyncJob(project_id=alice_project["id"], mode="incremental", status="queued"))
        db.add(SyncJob(project_id=alice_project["id"], mode="full", status="failed"))
        db.add(SyncJob(project_id=alice_project["id"], mode="incremental", status="done"))
        db.add(SyncJob(project_id=bob_project["id"], mode="incremental", status="running"))
        db.commit()
    finally:
        db.close()

    list_resp = client.get("/jobs?limit=2&offset=0", headers={"X-User": "alice", "X-Role": "viewer"})
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["total"] == 3
    assert len(payload["items"]) == 2
    assert all(item["project_id"] == alice_project["id"] for item in payload["items"])

    filter_resp = client.get(
        f"/jobs?project_id={alice_project['id']}&status=failed",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert filter_resp.status_code == 200
    filtered = filter_resp.json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["status"] == "failed"


def test_project_sync_status_endpoint(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "status-repo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/status-repo",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    db = SessionLocal()
    try:
        db.add(SyncJob(project_id=project["id"], mode="incremental", status="running"))
        db.add(SyncJob(project_id=project["id"], mode="incremental", status="failed"))
        db.add(SyncJob(project_id=project["id"], mode="full", status="done"))
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/projects/{project['id']}/sync-status", headers={"X-User": "alice", "X-Role": "viewer"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["pending_count"] == 1
    assert payload["active_job"]["status"] == "running"
    assert payload["last_success_job"]["status"] == "done"
    assert payload["last_failed_job"]["status"] == "failed"


def test_patch_project_updates_fields(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "old-name",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/old-name",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    patch_resp = client.patch(
        f"/projects/{project['id']}",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "new-name",
            "repo_url": "https://github.com/acme/new-name",
            "default_branch": "develop",
            "instructions": "回答时优先给出runbook步骤。",
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "new-name"
    assert updated["repo_url"] == "https://github.com/acme/new-name"
    assert updated["default_branch"] == "develop"
    assert updated["instructions"] == "回答时优先给出runbook步骤。"


def test_viewer_cannot_patch_or_trigger_sync(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "viewer-guard",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/viewer-guard",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    patch_resp = client.patch(
        f"/projects/{project['id']}",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"name": "blocked"},
    )
    assert patch_resp.status_code == 403

    sync_resp = client.post(
        f"/projects/{project['id']}/sync",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"mode": "incremental"},
    )
    assert sync_resp.status_code == 403


def test_project_memories_crud(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "memory-demo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/memory-demo",
            "default_branch": "main",
        },
    )
    assert project_resp.status_code == 200
    project = project_resp.json()

    create_resp = client.post(
        f"/projects/{project['id']}/memories",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={"content": "部署前先跑冒烟测试"},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["project_id"] == project["id"]
    assert created["content"] == "部署前先跑冒烟测试"
    assert created["archived"] is False

    list_resp = client.get(
        f"/projects/{project['id']}/memories",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == created["id"]

    patch_resp = client.patch(
        f"/projects/{project['id']}/memories/{created['id']}",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={"archived": True, "content": "部署前必须先跑冒烟测试"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["archived"] is True
    assert patched["content"] == "部署前必须先跑冒烟测试"

    active_list_resp = client.get(
        f"/projects/{project['id']}/memories",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert active_list_resp.status_code == 200
    assert active_list_resp.json()["total"] == 0

    all_list_resp = client.get(
        f"/projects/{project['id']}/memories?include_archived=true",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert all_list_resp.status_code == 200
    assert all_list_resp.json()["total"] == 1
