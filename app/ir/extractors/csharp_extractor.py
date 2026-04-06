import re
import uuid

from app.ir.extractors.base import BaseExtractor
from app.ir.models import FileIR, SymbolIR


class CSharpExtractor(BaseExtractor):
    class_pattern = re.compile(r"\b(class|interface|enum|struct)\s+([A-Za-z_][A-Za-z0-9_]*)")
    method_pattern = re.compile(
        r"\b(public|private|protected|internal)?\s*(static\s+)?[A-Za-z0-9_<>,\\[\\]?\\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
    )

    def supports(self, file_path: str) -> bool:
        return file_path.endswith(".cs")

    def extract(self, project_id: str, file_path: str, content: str) -> FileIR:
        file_ir = FileIR(project_id=project_id, language="csharp", file_path=file_path)
        for idx, line in enumerate(content.splitlines(), start=1):
            class_match = self.class_pattern.search(line)
            if class_match:
                file_ir.symbols.append(
                    SymbolIR(
                        symbol_id=str(uuid.uuid4()),
                        project_id=project_id,
                        language="csharp",
                        symbol_type=class_match.group(1),
                        qualified_name=class_match.group(2),
                        file_path=file_path,
                        start_line=idx,
                        end_line=idx,
                    )
                )

            method_match = self.method_pattern.search(line)
            if method_match:
                file_ir.symbols.append(
                    SymbolIR(
                        symbol_id=str(uuid.uuid4()),
                        project_id=project_id,
                        language="csharp",
                        symbol_type="method",
                        qualified_name=method_match.group(3),
                        file_path=file_path,
                        start_line=idx,
                        end_line=idx,
                    )
                )
        return file_ir
