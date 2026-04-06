from pathlib import Path

from app.services.indexing_service import IndexingService


def test_indexing_service_extracts_python_symbols(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n"
        "\n"
        "def helper():\n"
        "    return 2\n",
        encoding="utf-8",
    )

    service = IndexingService()
    symbols, scanned_files = service.scan_symbols(project_id="p1", repo_dir=repo)

    names = {s.qualified_name for s in symbols}
    assert scanned_files == 1
    assert "Service" in names
    assert "Service.run" in names
    assert "helper" in names
