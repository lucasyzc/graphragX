from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])


def render_template(name: str) -> str:
    template_path = Path(__file__).resolve().parents[2] / "web" / "templates" / name
    return template_path.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def chat_home_page() -> str:
    return render_template("chat_home.html")


@router.get("/search", response_class=HTMLResponse, include_in_schema=False)
def search_center_page() -> str:
    return render_template("index.html")


@router.get("/scenarios/impact", response_class=HTMLResponse, include_in_schema=False)
def impact_scenario_page() -> str:
    return render_template("impact.html")


@router.get("/manage/projects", response_class=HTMLResponse, include_in_schema=False)
def manage_projects_page() -> str:
    return render_template("manage_projects.html")


@router.get("/manage/sync", response_class=HTMLResponse, include_in_schema=False)
def manage_sync_page() -> str:
    return render_template("manage_sync.html")


@router.get("/manage/knowledge", response_class=HTMLResponse, include_in_schema=False)
def manage_knowledge_page() -> str:
    return render_template("manage_knowledge.html")
