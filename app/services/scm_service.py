from pathlib import Path
import subprocess

from app.core.config import get_settings
from app.db.models import Project


class SCMError(RuntimeError):
    pass


def ensure_repo_checkout(project: Project) -> Path:
    settings = get_settings()

    if project.scm_provider == "local":
        local_dir = Path(project.repo_url).expanduser().resolve()
        if not local_dir.exists() or not local_dir.is_dir():
            raise SCMError(f"local repository path does not exist or is not a directory: {local_dir}")
        return local_dir

    base_dir = Path(settings.workspace_repos_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    repo_dir = base_dir / project.id
    branch = project.default_branch or "main"

    if not repo_dir.exists():
        cmd = ["git", "clone", "--branch", branch, project.repo_url, str(repo_dir)]
        _run(cmd, cwd=base_dir)
    else:
        _run(["git", "-C", str(repo_dir), "fetch", "origin", branch], cwd=repo_dir)
        _run(["git", "-C", str(repo_dir), "checkout", branch], cwd=repo_dir)
        _run(["git", "-C", str(repo_dir), "pull", "--ff-only", "origin", branch], cwd=repo_dir)

    return repo_dir


def is_git_repo(repo_dir: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def get_head_sha(repo_dir: Path) -> str:
    return _run_capture(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], cwd=repo_dir).strip()


def commit_exists(repo_dir: Path, commit_ref: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--verify", f"{commit_ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def checkout_ref(repo_dir: Path, commit_ref: str) -> None:
    _run(["git", "-C", str(repo_dir), "checkout", commit_ref], cwd=repo_dir)


def changed_files_between(
    repo_dir: Path,
    base_sha: str,
    head_sha: str,
    rename_detection: bool = True,
) -> tuple[set[str], set[str]]:
    cmd = ["git", "-C", str(repo_dir), "diff", "--name-status"]
    cmd.append("-M" if rename_detection else "--no-renames")
    cmd.extend([base_sha, head_sha])
    raw = _run_capture(cmd, cwd=repo_dir)

    changed: set[str] = set()
    deleted: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        code = status[0]
        if code == "D":
            deleted.add(parts[1])
            continue
        if code == "R":
            if len(parts) >= 3:
                deleted.add(parts[1])
                changed.add(parts[2])
            continue
        changed.add(parts[1])
    return changed, deleted


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SCMError(f"command failed: {' '.join(cmd)} | stderr={proc.stderr.strip()}")


def _run_capture(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SCMError(f"command failed: {' '.join(cmd)} | stderr={proc.stderr.strip()}")
    return proc.stdout
