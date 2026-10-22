"""Parse function bodies without importing or executing repository code."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

FUNCTIONS = {
    "function_definition",
    "function_item",
    "function_declaration",
    "method_definition",
    "arrow_function",
    "function_expression",
    "generator_function_declaration",
}
CONTROL = {
    "if_statement",
    "if_expression",
    "for_statement",
    "for_in_statement",
    "do_statement",
    "for_expression",
    "while_statement",
    "while_expression",
    "loop_expression",
    "match_statement",
    "match_expression",
    "switch_statement",
    "try_statement",
    "with_statement",
}
DECISIONS = CONTROL - {
    "try_statement",
    "with_statement",
    "match_statement",
    "match_expression",
    "switch_statement",
}
DECISIONS |= {
    "elif_clause",
    "except_clause",
    "catch_clause",
    "case_clause",
    "match_arm",
    "switch_case",
    "conditional_expression",
    "ternary_expression",
    "for_in_clause",
    "if_clause",
    "try_expression",
}


def walk(node: Node, stop_functions: bool = False) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        if not stop_functions or child.type not in FUNCTIONS:
            yield from walk(child, stop_functions)


def script(path: str, text: str) -> str:
    if not path.endswith(".svelte"):
        return text
    # Keep script offsets so diagnostics still point into the original component.
    output = ["\n" if c == "\n" else " " for c in text]
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script\s*>", text, re.DOTALL):
        output[match.start(1) : match.end(1)] = match.group(1)
    return "".join(output)


def parse(path: str, text: str) -> Node:
    suffix = Path(path).suffix
    if suffix in {".py", ".pyi"}:
        language = tree_sitter_python.language()
    elif suffix == ".rs":
        language = tree_sitter_rust.language()
    elif suffix == ".tsx":
        language = tree_sitter_typescript.language_tsx()
    elif suffix in {".ts", ".svelte"}:
        language = tree_sitter_typescript.language_typescript()
    else:
        language = tree_sitter_javascript.language()
    root = Parser(Language(language)).parse(script(path, text).encode()).root_node
    if root.has_error:
        raise ValueError(
            f"{path}: parser could not analyze this source; check syntax/parser support"
        )
    return root


def value(node: Node, data: bytes) -> str:
    return data[node.start_byte : node.end_byte].decode()


def nesting(node: Node, depth: int = 0) -> int:
    depth += int(node.type in CONTROL)
    return max(
        [
            depth,
            *(
                nesting(child, depth)
                for child in node.named_children
                if child.type not in FUNCTIONS
            ),
        ]
    )


def identity(node: Node, data: bytes) -> str:
    names: list[str] = []
    current: Node | None = node
    while current:
        name = current.child_by_field_name("name")
        if name:
            names.append(value(name, data))
        current = current.parent
    return "::".join(reversed(names)) or f"anonymous:{node.type}"


@dataclass(frozen=True)
class Function:
    name: str
    start: int
    end: int
    complexity: int
    depth: int
    length: int
    test: bool


def is_test(path: str, node: Node, data: bytes) -> bool:
    if any(part in {"tests", "test", "e2e", "e2e-native"} for part in Path(path).parts):
        return True
    if re.search(r"(^test_|[._](test|spec)\.)", Path(path).name):
        return True
    current: Node | None = node
    while current:
        sibling = current.prev_named_sibling
        while sibling and sibling.type in {"attribute_item", "line_comment", "block_comment"}:
            if re.search(r"#\[(?:test\b|.*::test\b|cfg\(test\))", value(sibling, data)):
                return True
            sibling = sibling.prev_named_sibling
        current = current.parent
    return False


def functions(path: str, text: str) -> list[Function]:
    result: list[Function] = []
    data = script(path, text).encode()
    for node in walk(parse(path, text)):
        if node.type not in FUNCTIONS:
            continue
        body = node.child_by_field_name("body")
        if body is None:
            continue
        tokens = list(walk(body, stop_functions=True))
        complexity = 1 + sum(n.type in DECISIONS for n in tokens)
        complexity += sum(
            n.type in {"boolean_operator", "binary_expression"}
            and any(value(c, data) in {"&&", "||", "and", "or", "??"} for c in n.children)
            for n in tokens
        )
        # Count source lines containing code tokens; comments and blank lines do not count.
        code_lines: set[int] = set()
        for token in walk(node, stop_functions=True):
            if token.child_count == 0 and "comment" not in token.type:
                code_lines.update(range(token.start_point.row, token.end_point.row + 1))
        result.append(
            Function(
                identity(node, data),
                node.start_point.row + 1,
                node.end_point.row + 1,
                complexity,
                nesting(body),
                len(code_lines),
                is_test(path, node, data),
            )
        )
    return result
