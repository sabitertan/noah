import ast
import re
from typing import Any, Callable

_BIN: dict[type, Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    raise ValueError("unsupported expression")


def extract_rule(message: str) -> dict[str, Any]:
    text = message.lower()
    pct = re.search(r"([\d.]+)\s*%\s*of\s*([\d.]+)", text)
    if pct:
        return {"expression": f"{pct.group(1)}/100*{pct.group(2)}"}
    text = text.replace("divided by", "/").replace("times", "*").replace("plus", "+").replace("minus", "-")
    text = re.sub(r"(\d+)\s*to the power of\s*(\d+)", r"\1**\2", text)
    expr = re.findall(r"[\d.+\-*/()%^ ]{3,}", text)
    return {"expression": max(expr, key=len).strip().replace("^", "**")} if expr else {}


def run(args: dict[str, Any]) -> str:
    expr = args["expression"]
    for candidate in (expr, extract_rule(expr).get("expression", "")):
        try:
            return f"{expr} = {_eval(ast.parse(candidate, mode='eval').body):g}"
        except Exception:
            continue
    return f"Could not evaluate '{expr}'."
