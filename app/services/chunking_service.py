from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.db.models import CodeChunk, Symbol


class ChunkingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._file_cache: dict[str, list[str]] = {}

    def build_chunks(self, project_id: str, repo_dir: Path, symbols: list[Symbol]) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []

        for symbol in symbols:
            snippet = self._extract_snippet(repo_dir=repo_dir, symbol=symbol)
            if not snippet:
                continue

            chunks.append(
                CodeChunk(
                    project_id=project_id,
                    symbol_id=symbol.id,
                    language=symbol.language,
                    symbol_type=symbol.symbol_type,
                    qualified_name=symbol.qualified_name,
                    file_path=symbol.file_path,
                    source_type="code",
                    source_uri=symbol.file_path,
                    title=symbol.qualified_name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    content=snippet,
                    embedding_model=self.settings.embedding_model,
                )
            )

        return chunks

    def _extract_snippet(self, repo_dir: Path, symbol: Symbol) -> str:
        lines = self._load_file_lines(repo_dir, symbol.file_path)
        if not lines:
            return ""

        start = max(1, int(symbol.start_line))
        end = max(start, int(symbol.end_line))

        max_lines = self.settings.chunk_max_lines
        if end - start + 1 > max_lines:
            end = start + max_lines - 1

        end = min(end, len(lines))
        start_idx = start - 1
        end_idx = end
        body = "\n".join(lines[start_idx:end_idx]).strip()
        if not body:
            return ""

        header = (
            f"file={symbol.file_path}\n"
            f"symbol={symbol.qualified_name}\n"
            f"kind={symbol.symbol_type}\n"
            f"lines={start}-{end}\n"
        )
        content = f"{header}\n{body}"

        max_chars = self.settings.chunk_max_chars
        if len(content) > max_chars:
            content = content[:max_chars]
        return content

    def _load_file_lines(self, repo_dir: Path, file_path: str) -> list[str] | None:
        if file_path in self._file_cache:
            return self._file_cache[file_path]

        abs_path = repo_dir / file_path
        try:
            lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None

        self._file_cache[file_path] = lines
        return lines
