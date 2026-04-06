from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db.session import engine


def run_startup_checks() -> None:
    settings = get_settings()

    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
        _ensure_schema_compatibility(conn)

    if settings.require_neo4j:
        try:
            _check_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        except Exception as exc:
            raise RuntimeError(
                "Neo4j check failed. Verify NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD, "
                "or temporarily set REQUIRE_NEO4J=false to continue without graph checks."
            ) from exc

    if settings.require_qdrant:
        try:
            _check_qdrant(settings.qdrant_url)
        except Exception as exc:
            raise RuntimeError(
                "Qdrant check failed. Verify QDRANT_URL, or set REQUIRE_QDRANT=false temporarily."
            ) from exc


def _check_neo4j(uri: str, user: str, password: str) -> None:
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            session.run("RETURN 1").single()
    finally:
        driver.close()


def _check_qdrant(url: str) -> None:
    client = QdrantClient(url=url)
    client.get_collections()


def _ensure_schema_compatibility(conn: Connection) -> None:
    if conn.dialect.name != "postgresql":
        return

    inspector = inspect(conn)
    table_names = set(inspector.get_table_names())

    if "projects" in table_names:
        project_columns = {col["name"] for col in inspector.get_columns("projects")}
        if "instructions" not in project_columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN instructions TEXT"))

    if "code_chunks" in table_names:
        columns = {col["name"] for col in inspector.get_columns("code_chunks")}
        if "source_type" not in columns:
            conn.execute(
                text("ALTER TABLE code_chunks ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'code'")
            )
        if "source_uri" not in columns:
            conn.execute(text("ALTER TABLE code_chunks ADD COLUMN source_uri VARCHAR(1024)"))
        if "title" not in columns:
            conn.execute(text("ALTER TABLE code_chunks ADD COLUMN title VARCHAR(512)"))
        if "tags" not in columns:
            conn.execute(text("ALTER TABLE code_chunks ADD COLUMN tags TEXT"))
        if "updated_at" not in columns:
            conn.execute(text("ALTER TABLE code_chunks ADD COLUMN updated_at TIMESTAMP"))
            conn.execute(text("UPDATE code_chunks SET updated_at = COALESCE(created_at, NOW()) WHERE updated_at IS NULL"))
            conn.execute(text("ALTER TABLE code_chunks ALTER COLUMN updated_at SET NOT NULL"))


def collect_dependency_status() -> dict:
    settings = get_settings()
    status = {
        "postgres": {"ok": True, "error": None, "required": True},
        "neo4j": {"ok": True, "error": None, "required": settings.require_neo4j},
        "qdrant": {"ok": True, "error": None, "required": settings.require_qdrant},
    }

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        status["postgres"] = {"ok": False, "error": str(exc), "required": True}

    if settings.require_neo4j:
        try:
            _check_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        except Exception as exc:  # pragma: no cover
            status["neo4j"] = {"ok": False, "error": str(exc), "required": True}
    else:
        status["neo4j"]["error"] = "skip (require_neo4j=false)"

    if settings.require_qdrant:
        try:
            _check_qdrant(settings.qdrant_url)
        except Exception as exc:  # pragma: no cover
            status["qdrant"] = {"ok": False, "error": str(exc), "required": True}
    else:
        status["qdrant"]["error"] = "skip (require_qdrant=false)"

    return status
