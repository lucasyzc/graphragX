from app.ir.extractors.python_extractor import PythonExtractor


def test_python_extractor_emits_calls_imports_references_edges():
    content = (
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def helper(x):\n"
        "    return str(x)\n"
        "\n"
        "def runner(y):\n"
        "    p = Path(y)\n"
        "    return helper(p)\n"
    )
    extractor = PythonExtractor()
    file_ir = extractor.extract(project_id="p1", file_path="demo.py", content=content)

    edge_types = {edge.edge_type for edge in file_ir.edges}
    assert "IMPORTS" in edge_types
    assert "CALLS" in edge_types
    assert "REFERENCES" in edge_types

    call_targets = {edge.to_qualified_name for edge in file_ir.edges if edge.edge_type == "CALLS"}
    assert "helper" in call_targets or "Path" in call_targets
