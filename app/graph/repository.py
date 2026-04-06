from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from neo4j import GraphDatabase

from app.core.config import get_settings
from app.db.models import Symbol
from app.ir.models import EdgeIR

REL_TYPES = {"CALLS", "IMPORTS", "REFERENCES", "CONTAINS"}


class GraphRepository:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.driver = None
        if self.settings.enable_external_stores:
            self.driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            )

    def upsert_symbols(
        self,
        project_id: str,
        symbols: list[Symbol],
        edges: list[EdgeIR] | None = None,
        replace: bool = False,
        touched_files: set[str] | None = None,
        deleted_files: set[str] | None = None,
    ) -> None:
        if not self.driver:
            return

        edges = edges or []
        touched_files = touched_files or set()
        deleted_files = deleted_files or set()

        try:
            with self.driver.session() as session:
                self._ensure_schema(session)
                self._prepare_scope(
                    session=session,
                    project_id=project_id,
                    replace=replace,
                    touched_files=touched_files,
                    deleted_files=deleted_files,
                )

                rows = [self._symbol_row(item) for item in symbols]
                if rows:
                    session.run(
                        """
                        UNWIND $rows AS row
                        MATCH (p:Project {id: $project_id})
                        MERGE (f:File {project_id: row.project_id, path: row.file_path})
                          ON CREATE SET f.language = row.language
                          ON MATCH SET f.language = row.language
                        MERGE (p)-[:HAS_FILE]->(f)
                        MERGE (s:Symbol {id: row.id})
                        SET s.project_id = row.project_id,
                            s.language = row.language,
                            s.symbol_type = row.symbol_type,
                            s.qualified_name = row.qualified_name,
                            s.file_path = row.file_path,
                            s.start_line = row.start_line,
                            s.end_line = row.end_line
                        MERGE (s)-[:BELONGS_TO]->(f)
                        """,
                        rows=rows,
                        project_id=project_id,
                    )

                contains_rows = self._build_parent_rows(rows)
                if contains_rows:
                    self._create_symbol_edges(session, project_id, "CONTAINS", contains_rows)

                self._write_runtime_edges(session, project_id, edges)
        except Exception:
            if self.settings.require_neo4j:
                raise

    def related_symbols(
        self,
        project_id: str,
        seed_symbol_ids: Iterable[str],
        max_hops: int = 2,
        limit: int = 200,
    ) -> list[dict]:
        if not self.driver:
            return []

        seed_ids = list(seed_symbol_ids)
        if not seed_ids:
            return []

        hops = max(1, min(int(max_hops), 3))
        try:
            with self.driver.session() as session:
                query = f"""
                    MATCH (seed:Symbol {{project_id:$project_id}})
                    WHERE seed.id IN $seed_ids
                    MATCH path=(seed)-[*1..{hops}]-(peer:Symbol {{project_id:$project_id}})
                    WHERE ALL(rel IN relationships(path) WHERE type(rel) IN ['CALLS', 'IMPORTS', 'REFERENCES', 'CONTAINS', 'BELONGS_TO'])
                    RETURN DISTINCT peer.id AS id,
                           peer.qualified_name AS qualified_name,
                           peer.file_path AS file_path,
                           peer.start_line AS start_line,
                           peer.end_line AS end_line,
                           length(path) AS hops
                    ORDER BY hops ASC
                    LIMIT $limit
                """
                result = session.run(
                    query,
                    project_id=project_id,
                    seed_ids=seed_ids,
                    limit=limit,
                )
                out: list[dict] = []
                for record in result:
                    out.append(
                        {
                            "id": record.get("id"),
                            "qualified_name": record.get("qualified_name"),
                            "file_path": record.get("file_path"),
                            "start_line": record.get("start_line"),
                            "end_line": record.get("end_line"),
                            "hops": record.get("hops", 1),
                        }
                    )
                return out
        except Exception:
            if self.settings.require_neo4j:
                raise
            return []

    def close(self) -> None:
        if self.driver:
            self.driver.close()

    @staticmethod
    def _prepare_scope(
        session,
        project_id: str,
        replace: bool,
        touched_files: set[str],
        deleted_files: set[str],
    ) -> None:
        session.run(
            "MERGE (p:Project {id:$project_id}) SET p.updated_at=datetime()",
            project_id=project_id,
        )
        if replace:
            session.run("MATCH (s:Symbol {project_id:$project_id}) DETACH DELETE s", project_id=project_id)
            session.run("MATCH (f:File {project_id:$project_id}) DETACH DELETE f", project_id=project_id)
            session.run("MATCH (e:ExternalSymbol {project_id:$project_id}) DETACH DELETE e", project_id=project_id)
            return

        stale_paths = sorted(set(touched_files) | set(deleted_files))
        if stale_paths:
            session.run(
                "MATCH (f:File {project_id:$project_id}) WHERE f.path IN $paths DETACH DELETE f",
                project_id=project_id,
                paths=stale_paths,
            )
            session.run(
                "MATCH (s:Symbol {project_id:$project_id}) WHERE s.file_path IN $paths DETACH DELETE s",
                project_id=project_id,
                paths=stale_paths,
            )
            session.run(
                "MATCH (e:ExternalSymbol {project_id:$project_id}) WHERE e.file_path IN $paths DETACH DELETE e",
                project_id=project_id,
                paths=stale_paths,
            )

    @staticmethod
    def _symbol_row(symbol: Symbol) -> dict:
        return {
            "id": symbol.id,
            "project_id": symbol.project_id,
            "language": symbol.language,
            "symbol_type": symbol.symbol_type,
            "qualified_name": symbol.qualified_name,
            "file_path": symbol.file_path,
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
        }

    @staticmethod
    def _build_parent_rows(rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        for row in rows:
            name = row["qualified_name"]
            if "." not in name:
                continue
            parent = name.rsplit(".", 1)[0]
            out.append(
                {
                    "from_symbol_id": row["id"],
                    "to_qualified_name": parent,
                }
            )
        return out

    def _write_runtime_edges(self, session, project_id: str, edges: list[EdgeIR]) -> None:
        grouped_symbol_edges: dict[str, list[dict]] = defaultdict(list)
        grouped_external_edges: dict[str, list[dict]] = defaultdict(list)

        for edge in edges:
            edge_type = edge.edge_type
            if edge_type not in REL_TYPES:
                continue
            if edge.to_symbol_id:
                grouped_symbol_edges[edge_type].append(
                    {
                        "from_symbol_id": edge.from_symbol_id,
                        "to_symbol_id": edge.to_symbol_id,
                    }
                )
            elif edge.to_qualified_name:
                grouped_external_edges[edge_type].append(
                    {
                        "from_symbol_id": edge.from_symbol_id,
                        "target_name": edge.to_qualified_name,
                    }
                )

        for edge_type, rows in grouped_symbol_edges.items():
            self._create_symbol_edges(session, project_id, edge_type, rows)

        for edge_type, rows in grouped_external_edges.items():
            self._create_external_edges(session, project_id, edge_type, rows)

    @staticmethod
    def _create_symbol_edges(session, project_id: str, edge_type: str, rows: list[dict]) -> None:
        if not rows:
            return
        query = f"""
            UNWIND $rows AS row
            MATCH (src:Symbol {{id: row.from_symbol_id, project_id: $project_id}})
            OPTIONAL MATCH (dstById:Symbol {{id: row.to_symbol_id, project_id: $project_id}})
            OPTIONAL MATCH (dstByName:Symbol {{project_id: $project_id, qualified_name: row.to_qualified_name}})
            WITH src, coalesce(dstById, dstByName) AS dst
            WHERE dst IS NOT NULL
            MERGE (src)-[:{edge_type}]->(dst)
        """
        session.run(query, rows=rows, project_id=project_id)

    @staticmethod
    def _create_external_edges(session, project_id: str, edge_type: str, rows: list[dict]) -> None:
        if not rows:
            return
        query = f"""
            UNWIND $rows AS row
            MATCH (src:Symbol {{id: row.from_symbol_id, project_id: $project_id}})
            MERGE (ext:ExternalSymbol {{project_id: $project_id, qualified_name: row.target_name}})
            SET ext.file_path = src.file_path
            MERGE (src)-[:{edge_type}]->(ext)
        """
        session.run(query, rows=rows, project_id=project_id)

    @staticmethod
    def _ensure_schema(session) -> None:
        session.run("CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE")
        session.run("CREATE CONSTRAINT symbol_id_unique IF NOT EXISTS FOR (s:Symbol) REQUIRE s.id IS UNIQUE")
        session.run(
            "CREATE CONSTRAINT file_project_path_unique IF NOT EXISTS "
            "FOR (f:File) REQUIRE (f.project_id, f.path) IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT external_symbol_unique IF NOT EXISTS "
            "FOR (e:ExternalSymbol) REQUIRE (e.project_id, e.qualified_name) IS UNIQUE"
        )
