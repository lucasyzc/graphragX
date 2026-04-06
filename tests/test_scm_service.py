import pytest

from app.db.models import Project
from app.services.scm_service import SCMError, ensure_repo_checkout


def test_ensure_repo_checkout_local_path(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("print('ok')\n", encoding="utf-8")

    project = Project(name="local", scm_provider="local", repo_url=str(repo_dir), default_branch="")
    checkout_dir = ensure_repo_checkout(project)

    assert checkout_dir == repo_dir.resolve()


def test_ensure_repo_checkout_local_path_missing(tmp_path):
    missing = tmp_path / "missing"
    project = Project(name="local", scm_provider="local", repo_url=str(missing), default_branch="")

    with pytest.raises(SCMError) as exc:
        ensure_repo_checkout(project)
    assert "does not exist" in str(exc.value)
