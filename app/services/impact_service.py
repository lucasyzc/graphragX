from app.db.models import Symbol
from app.graph.repository import GraphRepository
from app.core.config import get_settings


def estimate_impact(
    project_id: str,
    changed_files: list[str],
    symbols: list[Symbol],
) -> tuple[list[str], list[str], str]:
    settings = get_settings()
    seed_symbols = [s for s in symbols if s.file_path in changed_files]
    impacted = {s.qualified_name for s in seed_symbols}

    graph_repo = GraphRepository()
    graph_enabled = bool(getattr(graph_repo, "driver", None))
    try:
        related = graph_repo.related_symbols(
            project_id=project_id,
            seed_symbol_ids=[s.id for s in seed_symbols],
            max_hops=settings.retrieval_graph_hops,
            limit=500,
        )
        impacted.update(
            item["qualified_name"]
            for item in related
            if item.get("qualified_name")
        )
        note = (
            f"Impact analysis includes Neo4j graph expansion ({settings.retrieval_graph_hops} hops)."
            if graph_enabled
            else "Impact analysis uses file-linked symbols (graph disabled)."
        )
    except Exception:
        note = "Impact analysis fallback to file-linked symbols (graph unavailable)."
    finally:
        graph_repo.close()

    return changed_files, sorted(impacted), note
