import ast
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def extract_rule(message: str) -> dict:
    text = message.lower()
    pct = re.search(r"([\d.]+)\s*%\s*of\s*([\d.]+)", text)
    if pct:
        return {"expression": f"{pct.group(1)}/100*{pct.group(2)}"}
    text = text.replace("divided by", "/").replace("times", "*").replace("plus", "+").replace("minus", "-")
    text = re.sub(r"(\d+)\s*to the power of\s*(\d+)", r"\1**\2", text)
    expr = re.findall(r"[\d.+\-*/()%^ ]{3,}", text)
    return {"expression": max(expr, key=len).strip().replace("^", "**")} if expr else {}


def run(args: dict) -> str:
    expr = args["expression"]
    for candidate in (expr, extract_rule(expr).get("expression", "")):
        try:
            return f"{expr} = {_eval(ast.parse(candidate, mode='eval').body):g}"
        except Exception:
            continue
    return f"Could not evaluate '{expr}'."
