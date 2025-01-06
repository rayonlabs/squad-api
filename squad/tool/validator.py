"""
Minimal tool validation, checking function def, docstring, etc.
"""

import ast
from typing import List, Set, Optional


class CodeValidator(ast.NodeVisitor):
    def __init__(self, allowed_functions: Set[str], allowed_return_types: Set[str]):
        self.allowed_functions = allowed_functions
        self.allowed_return_types = allowed_return_types
        self.errors: List[str] = []
        self.has_docstring = False
        self.has_return_annotation = False
        self.has_param_annotations = True
        self.function_name: Optional[str] = None

    def _get_type_name(self, node: ast.AST) -> str:
        """
        Extract the string representation of a type annotation.
        """
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                return f"{node.value.id}[...]"
        elif isinstance(node, ast.Attribute):
            return f"{self._get_full_name(node)}"
        return str(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        Ensure only one function is defined, that it has a docstring,
        and that each parameter has type hints.
        """
        if self.function_name is not None:
            self.errors.append("Only one function definition allowed")
            return
        self.function_name = node.name

        # Require a docstring, since that is useful in tool selection.
        if (ast.get_docstring(node)) is not None:
            self.has_docstring = True
        else:
            self.errors.append("Function must have a docstring")

        # Return and parameter type annotations.
        if node.returns is not None:
            return_type = self._get_type_name(node.returns)
            if return_type not in self.allowed_return_types:
                self.errors.append(
                    f"Return type '{return_type}' is not allowed. Must be one of: {', '.join(sorted(self.allowed_return_types))}"
                )
            self.has_return_annotation = True
        else:
            self.errors.append("Function must have a return type annotation")

        # First arg is always the agent (as "self")
        if not node.args.args or node.args.args[0].arg != "self":
            self.errors.append("First parameter must be 'self'")
            return

        for arg in node.args.args[1:]:
            if arg.annotation is None:
                self.has_param_annotations = False
                self.errors.append(f"Parameter '{arg.arg}' missing type annotation")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """
        Ensure the code is only calling whitelisted functions.
        """
        if isinstance(node.func, ast.Name):
            if node.func.id not in self.allowed_functions:
                self.errors.append(f"Function '{node.func.id}' is not allowed")
        elif isinstance(node.func, ast.Attribute):
            full_name = self._get_full_name(node.func)
            if full_name not in self.allowed_functions:
                self.errors.append(f"Function '{full_name}' is not allowed")
        self.generic_visit(node)

    def _get_full_name(self, node: ast.Attribute) -> str:
        """
        Get the full name of an attribute (typically, func).
        """
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.value.id)
        return ".".join(reversed(parts))
