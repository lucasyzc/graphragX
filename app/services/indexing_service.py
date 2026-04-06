import os
from pathlib import Path
from dataclasses import dataclass

from app.core.config import get_settings
from app.db.models import Symbol
from app.ir.models import EdgeIR
from app.ir.extractors.csharp_extractor import CSharpExtractor
from app.ir.extractors.java_extractor import JavaExtractor
from app.ir.extractors.python_extractor import PythonExtractor

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    "target",
    "bin",
    "obj",
    "__pycache__",
}


@dataclass
class IndexingResult:
    symbols: list[Symbol]
    edges: list[EdgeIR]
    scanned_files: int
    scanned_paths: set[str]


class IndexingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.extractors = [PythonExtractor(), JavaExtractor(), CSharpExtractor()]

    def scan_symbols(self, project_id: str, repo_dir: Path) -> tuple[list[Symbol], int]:
        result = self.scan_repo(project_id=project_id, repo_dir=repo_dir)
        return result.symbols, result.scanned_files

    def scan_repo(
        self,
        project_id: str,
        repo_dir: Path,
        include_files: set[str] | None = None,
    ) -> IndexingResult:
        symbols: list[Symbol] = []
        edges: list[EdgeIR] = []
        scanned_files = 0
        scanned_paths: set[str] = set()

        if include_files is None:
            iterator = self._iter_repo_files(repo_dir)
        else:
            iterator = self._iter_selected_files(repo_dir, include_files)

        for rel_path, file_path in iterator:
            if scanned_files >= self.settings.index_max_files:
                break

            extractor = self._pick_extractor(rel_path)
            if not extractor:
                continue

            content = self._safe_read(file_path)
            if content is None:
                continue

            file_ir = extractor.extract(project_id=project_id, file_path=rel_path, content=content)
            scanned_files += 1
            scanned_paths.add(rel_path)

            for ir_symbol in file_ir.symbols:
                symbols.append(
                    Symbol(
                        id=ir_symbol.symbol_id,
                        project_id=ir_symbol.project_id,
                        language=ir_symbol.language,
                        symbol_type=ir_symbol.symbol_type,
                        qualified_name=ir_symbol.qualified_name,
                        file_path=ir_symbol.file_path,
                        start_line=ir_symbol.start_line,
                        end_line=ir_symbol.end_line,
                    )
                )
            edges.extend(file_ir.edges)

        return IndexingResult(
            symbols=symbols,
            edges=edges,
            scanned_files=scanned_files,
            scanned_paths=scanned_paths,
        )

    def _pick_extractor(self, file_path: str):
        for extractor in self.extractors:
            if extractor.supports(file_path):
                return extractor
        return None

    @staticmethod
    def _iter_repo_files(repo_dir: Path):
        for root, dirs, files in os.walk(repo_dir, topdown=True):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            root_path = Path(root)
            for file_name in files:
                file_path = root_path / file_name
                rel_path = file_path.relative_to(repo_dir).as_posix()
                yield rel_path, file_path

    @staticmethod
    def _iter_selected_files(repo_dir: Path, include_files: set[str]):
        for rel_path in sorted(include_files):
            normalized = rel_path.replace("\\", "/")
            file_path = repo_dir / normalized
            if file_path.exists() and file_path.is_file():
                yield normalized, file_path

    @staticmethod
    def _safe_read(file_path: Path) -> str | None:
        try:
            # Ignore undecodable bytes so indexing does not stop on a single malformed file.
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
