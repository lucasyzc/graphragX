import subprocess
import time
from pathlib import Path

from app.core.config import get_settings
from app.db.models import Symbol
from app.db.session import SessionLocal


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def _wait_job_done(client, job_id: str, headers: dict, timeout_sec: float = 10.0):
    started = time.time()
    while time.time() - started < timeout_sec:
        resp = client.get(f"/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        payload = resp.json()
        if payload["status"] in {"done", "failed"}:
            return payload
        time.sleep(0.15)
    raise AssertionError("job did not finish in time")


def test_incremental_sync_updates_only_changed_files(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_MOCK_MODE", "false")
    monkeypatch.setenv("ENABLE_EXTERNAL_STORES", "false")
    get_settings.cache_clear()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    base_sha = _git(repo, "rev-parse", "HEAD")

    headers = {"X-User": "alice", "X-Role": "admin"}
    project_resp = client.post(
        "/projects",
        headers=headers,
        json={
            "name": "local-git-repo",
            "scm_provider": "local",
            "repo_url": str(repo),
            "default_branch": "main",
        },
    )
    assert project_resp.status_code == 200
    project = project_resp.json()

    full_job_resp = client.post(
        f"/projects/{project['id']}/sync",
        headers=headers,
        json={"mode": "full"},
    )
    assert full_job_resp.status_code == 200
    full_job = _wait_job_done(client, full_job_resp.json()["id"], headers=headers)
    assert full_job["status"] == "done"

    (repo / "a.py").write_text("def a():\n    return 1\n\ndef a2():\n    return a()\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-m", "update a")
    head_sha = _git(repo, "rev-parse", "HEAD")

    inc_job_resp = client.post(
        f"/projects/{project['id']}/sync",
        headers=headers,
        json={
            "mode": "incremental",
            "base_sha": base_sha,
            "head_sha": head_sha,
        },
    )
    assert inc_job_resp.status_code == 200
    inc_job = _wait_job_done(client, inc_job_resp.json()["id"], headers=headers)
    assert inc_job["status"] == "done"
    assert "mode=incremental" in (inc_job.get("message") or "")

    db = SessionLocal()
    try:
        symbols = db.query(Symbol).filter(Symbol.project_id == project["id"]).all()
        qnames = {s.qualified_name for s in symbols}
        paths = {s.file_path for s in symbols}
    finally:
        db.close()

    assert "b" in qnames
    assert "a2" in qnames
    assert "a.py" in paths
    assert "b.py" in paths

    get_settings.cache_clear()
