import ast
import uuid

from app.ir.extractors.base import BaseExtractor
from app.ir.models import EdgeIR, FileIR, SymbolIR


class PythonExtractor(BaseExtractor):
    def supports(self, file_path: str) -> bool:
        return file_path.endswith(".py")

    def extract(self, project_id: str, file_path: str, content: str) -> FileIR:
        file_ir = FileIR(project_id=project_id, language="python", file_path=file_path)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return file_ir

        collector = _PythonSymbolCollector(project_id=project_id, file_path=file_path)
        collector.visit(tree)
        collector.finalize_edges()
        file_ir.symbols.extend(collector.symbols)
        file_ir.edges.extend(collector.edges)
        return file_ir


class _PythonSymbolCollector(ast.NodeVisitor):
    def __init__(self, project_id: str, file_path: str) -> None:
        self.project_id = project_id
        self.file_path = file_path
        self.scope_stack: list[str] = []
        self.scope_symbol_stack: list[str] = []
        self.symbols: list[SymbolIR] = []
        self.edges: list[EdgeIR] = []
        self._edge_keys: set[tuple[str, str, str]] = set()
        self._simple_name_to_symbol_id: dict[str, str] = {}
        self._qualified_name_to_symbol_id: dict[str, str] = {}
        self._import_aliases: dict[str, str] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = self._add_symbol("class", node.name, node)
        self.scope_stack.append(node.name)
        self.scope_symbol_stack.append(symbol.symbol_id)
        self.generic_visit(node)
        self.scope_symbol_stack.pop()
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        symbol = self._add_symbol("function", node.name, node)
        self.scope_stack.append(node.name)
        self.scope_symbol_stack.append(symbol.symbol_id)
        self.generic_visit(node)
        self.scope_symbol_stack.pop()
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        symbol = self._add_symbol("function", node.name, node)
        self.scope_stack.append(node.name)
        self.scope_symbol_stack.append(symbol.symbol_id)
        self.generic_visit(node)
        self.scope_symbol_stack.pop()
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        from_symbol = self._current_symbol_id()
        callee = self._expr_name(node.func)
        if from_symbol and callee:
            self._add_edge("CALLS", from_symbol, callee)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name:
                alias_name = alias.asname or alias.name.split(".")[0]
                self._import_aliases[alias_name] = alias.name

        from_symbol = self._current_symbol_id()
        if from_symbol:
            for alias in node.names:
                target = alias.name
                if target:
                    self._add_edge("IMPORTS", from_symbol, target)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            alias_name = alias.asname or alias.name
            target = f"{module}.{alias.name}" if module else alias.name
            self._import_aliases[alias_name] = target

        from_symbol = self._current_symbol_id()
        if from_symbol:
            for alias in node.names:
                if alias.name == "*":
                    target = f"{module}.*" if module else "*"
                else:
                    target = f"{module}.{alias.name}" if module else alias.name
                self._add_edge("IMPORTS", from_symbol, target)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        from_symbol = self._current_symbol_id()
        if from_symbol and isinstance(node.ctx, ast.Load):
            self._add_edge("REFERENCES", from_symbol, node.id)
            imported = self._import_aliases.get(node.id)
            if imported:
                self._add_edge("IMPORTS", from_symbol, imported)
        self.generic_visit(node)

    def _add_symbol(self, symbol_type: str, name: str, node: ast.AST) -> SymbolIR:
        qualified_name = ".".join(self.scope_stack + [name]) if self.scope_stack else name
        start_line = int(getattr(node, "lineno", 1))
        end_line = int(getattr(node, "end_lineno", start_line))
        symbol = SymbolIR(
            symbol_id=str(uuid.uuid4()),
            project_id=self.project_id,
            language="python",
            symbol_type=symbol_type,
            qualified_name=qualified_name,
            file_path=self.file_path,
            start_line=start_line,
            end_line=end_line,
        )
        self.symbols.append(symbol)
        self._simple_name_to_symbol_id[name] = symbol.symbol_id
        self._qualified_name_to_symbol_id[qualified_name] = symbol.symbol_id
        return symbol

    def _current_symbol_id(self) -> str | None:
        return self.scope_symbol_stack[-1] if self.scope_symbol_stack else None

    def _add_edge(self, edge_type: str, from_symbol_id: str, target_name: str) -> None:
        key = (edge_type, from_symbol_id, target_name)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(
            EdgeIR(
                edge_type=edge_type,  # type: ignore[arg-type]
                from_symbol_id=from_symbol_id,
                to_qualified_name=target_name,
            )
        )

    def finalize_edges(self) -> None:
        for edge in self.edges:
            if edge.to_symbol_id:
                continue
            target = edge.to_qualified_name or ""
            edge.to_symbol_id = (
                self._qualified_name_to_symbol_id.get(target)
                or self._simple_name_to_symbol_id.get(target)
            )

    @staticmethod
    def _expr_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            left = _PythonSymbolCollector._expr_name(node.value)
            if left:
                return f"{left}.{node.attr}"
            return node.attr
        return None
