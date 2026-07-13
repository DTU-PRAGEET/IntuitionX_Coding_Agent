"""Add missing docstrings to Python files without breaking syntax.

M1.3 COMMENTED OUT — see docs/cleanup-audit.md
Uncomment the block below to re-enable.
"""

# M1.3 COMMENTED OUT — see docs/cleanup-audit.md
# Uncomment the block below to re-enable.
#
# """Add missing docstrings to Python files without breaking syntax."""
#
# from __future__ import annotations
#
# import ast
# import re
# import sys
# from pathlib import Path
#
#
# def _name_to_phrase(name: str) -> str:
#     if name.startswith("_") and not name.startswith("__"):
#         name = name[1:]
#     name = name.replace("__", " ")
#     parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
#     return " ".join(parts)
#
#
# def _guess_doc(name: str, kind: str, parent: str | None = None) -> str | None:
#     phrase = _name_to_phrase(name)
#     if kind == "ClassDef":
#         # Class docstrings need producer/consumer context — do not auto-generate placeholders.
#         return None
#     if name == "__init__":
#         return f"Initialize {parent or 'the object'} with the provided arguments."
#     if name.startswith("__") and name.endswith("__"):
#         return f"Python special method: {phrase}."
#     if parent:
#         return f"{phrase.title()} for {parent}."
#     verbs = {
#         "get": "Return",
#         "set": "Set",
#         "load": "Load",
#         "build": "Build",
#         "run": "Run",
#         "parse": "Parse",
#         "validate": "Validate",
#         "create": "Create",
#         "fetch": "Fetch",
#         "apply": "Apply",
#         "invoke": "Invoke",
#         "record": "Record",
#         "extract": "Extract",
#         "report": "Report",
#         "reset": "Reset",
#         "start": "Start",
#         "stop": "Stop",
#         "handle": "Handle",
#         "emit": "Emit",
#         "check": "Check",
#         "resolve": "Resolve",
#         "select": "Select",
#         "execute": "Execute",
#         "format": "Format",
#         "main": "CLI entry point",
#     }
#     lower = name.lower()
#     for prefix, verb in verbs.items():
#         if lower.startswith(prefix):
#             rest = _name_to_phrase(name[len(prefix) :]).lower()
#             if verb == "CLI entry point":
#                 return f"{verb} for this module."
#             return f"{verb} {rest or phrase.lower()}."
#     return f"{phrase.title()} for this module."
#
#
# def _is_stub_body(body: list[ast.stmt]) -> bool:
#     if not body:
#         return True
#     if len(body) == 1 and isinstance(body[0], ast.Pass):
#         return True
#     if len(body) == 1 and isinstance(body[0], ast.Expr):
#         val = body[0].value
#         if isinstance(val, ast.Constant) and val.value is Ellipsis:
#             return True
#     return False
#
#
# def _insert_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
#     if ast.get_docstring(node) is not None:
#         return None
#     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_stub_body(node.body):
#         return None
#     if not node.body:
#         return node.lineno
#     return node.body[0].lineno - 1
#
#
# class DocstringAdder(ast.NodeVisitor):
#     def __init__(self) -> None:
#         self.insertions: list[tuple[int, str, str]] = []
#         self.class_stack: list[str] = []
#
#     def visit_ClassDef(self, node: ast.ClassDef) -> None:
#         line = _insert_line(node)
#         if line is not None:
#             parent = self.class_stack[-1] if self.class_stack else None
#             doc = _guess_doc(node.name, "ClassDef", parent)
#             if doc is not None:
#                 self.insertions.append((line, node.lineno, doc))
#         self.class_stack.append(node.name)
#         self.generic_visit(node)
#         self.class_stack.pop()
#
#     def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
#         self._maybe_add(node)
#         self.generic_visit(node)
#
#     def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
#         self._maybe_add(node)
#         self.generic_visit(node)
#
#     def _maybe_add(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
#         line = _insert_line(node)
#         if line is None:
#             return
#         parent = self.class_stack[-1] if self.class_stack else None
#         doc = _guess_doc(node.name, type(node).__name__, parent)
#         if doc is None:
#             return
#         self.insertions.append((line, node.lineno, doc))
#
#
# def process_file(path: Path) -> bool:
#     text = path.read_text(encoding="utf-8")
#     try:
#         tree = ast.parse(text)
#     except SyntaxError:
#         return False
#     adder = DocstringAdder()
#     adder.visit(tree)
#     if not adder.insertions:
#         return False
#     lines = text.splitlines(keepends=True)
#
#     def indent_for(line_no: int, node_line: int) -> str:
#         def_line = lines[node_line - 1]
#         base = len(def_line) - len(def_line.lstrip(" "))
#         return " " * (base + 4)
#
#     for line_no, node_line, doc in sorted(adder.insertions, key=lambda x: x[0], reverse=True):
#         indent = indent_for(line_no, node_line)
#         block = f'{indent}"""{doc}"""\n'
#         lines[line_no:line_no] = [block]
#     path.write_text("".join(lines), encoding="utf-8")
#     return True
#
#
# def main() -> int:
#     roots = [Path(p) for p in sys.argv[1:]] or [Path("src"), Path("tests"), Path("evals")]
#     changed = 0
#     for root in roots:
#         if not root.exists():
#             continue
#         for path in sorted(root.rglob("*.py")):
#             if process_file(path):
#                 changed += 1
#                 print("updated", path)
#     print("files updated:", changed)
#     return 0
#
#
# if __name__ == "__main__":
#     raise SystemExit(main())
