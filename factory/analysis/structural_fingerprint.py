#!/usr/bin/env python3
import ast
import json
import sys

FEATURE_ORDER = [
    "total_lines",
    "blank_lines",
    "comment_lines",
    "max_indent_depth",
    "function_count",
    "class_count",
    "if_count",
    "for_count",
    "while_count",
    "try_count",
    "return_count",
    "ast_node_count",
    "ast_max_depth",
    "branch_node_ratio",
    "lambda_count",
    "yield_count",
    "recursion_flag",
    "inheritance_edges",
    "import_count",
]


class AstWalker:
    def __init__(self):
        self.ast_node_count = 0
        self.ast_max_depth = 0
        self.function_count = 0
        self.class_count = 0
        self.if_count = 0
        self.for_count = 0
        self.while_count = 0
        self.try_count = 0
        self.return_count = 0
        self.lambda_count = 0
        self.yield_count = 0
        self.import_count = 0
        self.inheritance_edges = 0
        self.function_stack = []
        self.called_names = set()
        self.recursion_flag = 0

    def walk(self, node, depth=1):
        self.ast_node_count += 1
        if depth > self.ast_max_depth:
            self.ast_max_depth = depth

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.function_count += 1
            self.function_stack.append(node.name)
        elif isinstance(node, ast.ClassDef):
            self.class_count += 1
            self.inheritance_edges += len(node.bases)
        elif isinstance(node, ast.If):
            self.if_count += 1
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.for_count += 1
        elif isinstance(node, ast.While):
            self.while_count += 1
        elif isinstance(node, ast.Try):
            self.try_count += 1
        elif isinstance(node, ast.Return):
            self.return_count += 1
        elif isinstance(node, ast.Lambda):
            self.lambda_count += 1
        elif isinstance(node, (ast.Yield, ast.YieldFrom)):
            self.yield_count += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.import_count += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            self.called_names.add(node.func.id)

        for child in ast.iter_child_nodes(node):
            self.walk(child, depth + 1)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.function_stack.pop()


def _line_features(source_code):
    lines = source_code.splitlines()
    total_lines = len(lines)
    blank_lines = 0
    comment_lines = 0
    max_indent_depth = 0

    for line in lines:
        stripped = line.strip()
        if stripped == "":
            blank_lines += 1
        if stripped.startswith("#"):
            comment_lines += 1
        if stripped:
            expanded = line.replace("\t", "    ")
            leading = len(expanded) - len(expanded.lstrip(" "))
            depth = leading // 4
            if depth > max_indent_depth:
                max_indent_depth = depth

    return total_lines, blank_lines, comment_lines, max_indent_depth


def structural_fingerprint(source_code):
    total_lines, blank_lines, comment_lines, max_indent_depth = _line_features(source_code)

    base = {
        "total_lines": total_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "max_indent_depth": max_indent_depth,
        "function_count": 0,
        "class_count": 0,
        "if_count": 0,
        "for_count": 0,
        "while_count": 0,
        "try_count": 0,
        "return_count": 0,
        "ast_node_count": 0,
        "ast_max_depth": 0,
        "branch_node_ratio": 0.0,
        "lambda_count": 0,
        "yield_count": 0,
        "recursion_flag": 0,
        "inheritance_edges": 0,
        "import_count": 0,
    }

    parse_error = False
    try:
        tree = ast.parse(source_code)
        walker = AstWalker()
        walker.walk(tree)
        branch_count = walker.if_count + walker.for_count + walker.while_count + walker.try_count
        node_count = walker.ast_node_count
        ratio = (branch_count / node_count) if node_count else 0.0

        base.update(
            {
                "function_count": walker.function_count,
                "class_count": walker.class_count,
                "if_count": walker.if_count,
                "for_count": walker.for_count,
                "while_count": walker.while_count,
                "try_count": walker.try_count,
                "return_count": walker.return_count,
                "ast_node_count": node_count,
                "ast_max_depth": walker.ast_max_depth,
                "branch_node_ratio": ratio,
                "lambda_count": walker.lambda_count,
                "yield_count": walker.yield_count,
                "recursion_flag": 1 if any(name in walker.called_names for name in _function_names(tree)) else 0,
                "inheritance_edges": walker.inheritance_edges,
                "import_count": walker.import_count,
            }
        )
    except SyntaxError:
        parse_error = True
        base = {k: 0 if k != "branch_node_ratio" else 0.0 for k in FEATURE_ORDER}

    vector = [base[name] for name in FEATURE_ORDER]
    return {
        "fingerprint_vector": vector,
        "named_features": base,
        "error": parse_error,
    }


def _function_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return names


def main():
    source = sys.stdin.read()
    out = structural_fingerprint(source)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
