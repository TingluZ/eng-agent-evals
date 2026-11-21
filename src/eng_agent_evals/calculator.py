"""Plain arithmetic for the agent's `calculate` tool.

Expressions are parsed with `ast` and only numbers, `pi`, parentheses and the
operators + - * / ** are evaluated; anything else is rejected. Python's
`eval` is never used.
"""

from __future__ import annotations

import ast
import math
import operator

MAX_LENGTH = 200
MAX_EXPONENT = 10

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CONSTANTS = {"pi": math.pi}
_ALLOWED = (
    "Only numbers, pi, parentheses and + - * / ** are allowed; "
    "units, names and functions are not. Convert units with `convert_units` first."
)


def evaluate(expression: str) -> float:
    """Evaluate an arithmetic expression such as '0.060 - 2 * 0.005'.

    Raises ValueError with a readable message for anything that is not plain
    arithmetic on numbers, so the agent can see the mistake and retry.
    """
    if len(expression) > MAX_LENGTH:
        raise ValueError(f"Expression longer than {MAX_LENGTH} characters.")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Not a valid arithmetic expression: {expression!r}. {_ALLOWED}") from exc
    try:
        result = float(_eval(tree.body))
    except ZeroDivisionError as exc:
        raise ValueError("Division by zero.") from exc
    except (OverflowError, TypeError) as exc:  # TypeError: complex result of a negative base
        raise ValueError(f"Result is not a finite real number: {expression!r}.") from exc
    if not math.isfinite(result):
        raise ValueError(f"Result is not a finite real number: {expression!r}.")
    return result


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"Exponents are limited to ±{MAX_EXPONENT}.")
        result = _BINARY[type(node.op)](left, right)
        if isinstance(result, complex):
            raise TypeError("complex result")
        return result
    raise ValueError(_ALLOWED)
