"""PostToolUse check after Edit/Write.

* ``*.py``           -> py_compile (syntax only).
* ``programs/*.lua`` -> block balance (function/if/for/while/repeat vs end/until)
                        and a ban on ``pcall``, which the controller's
                        post-upload check rejects outright.

Problems exit 2 with the message on stderr so Claude sees and fixes them.
Anything unexpected exits 0 (fail open).
"""
from __future__ import annotations

import json
import py_compile
import re
import sys
from pathlib import Path

LUA_TOKEN = re.compile(r"\b(function|if|for|while|repeat|do|end|until|then|elseif)\b")


def strip_lua(src: str) -> str:
    """Remove comments and string literals so keywords inside them don't count."""
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", " ", src, flags=re.DOTALL)   # long comments
    src = re.sub(r"\[(=*)\[.*?\]\1\]", '""', src, flags=re.DOTALL)    # long strings
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("--", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_lua(path: Path) -> list[str]:
    src = strip_lua(path.read_text(encoding="utf-8", errors="replace"))
    problems = []
    for lineno, line in enumerate(src.splitlines(), 1):
        if re.search(r"\bpcall\s*\(", line):
            problems.append(f"line {lineno}: pcall is banned by the controller's upload check")
    # `while/for ... do` share one `end` with their `do`; a bare `do` opens its own.
    stack: list[tuple[str, int]] = []
    pending_loop = False
    for lineno, line in enumerate(src.splitlines(), 1):
        for tok in LUA_TOKEN.findall(line):
            if tok in ("function", "if", "repeat"):
                stack.append((tok, lineno))
            elif tok in ("for", "while"):
                stack.append((tok, lineno))
                pending_loop = True
            elif tok == "do":
                if pending_loop:
                    pending_loop = False
                else:
                    stack.append(("do", lineno))
            elif tok == "end":
                if not stack or stack[-1][0] == "repeat":
                    problems.append(f"line {lineno}: unmatched 'end'")
                else:
                    stack.pop()
            elif tok == "until":
                if not stack or stack[-1][0] != "repeat":
                    problems.append(f"line {lineno}: 'until' without 'repeat'")
                else:
                    stack.pop()
    for tok, lineno in stack:
        problems.append(f"line {lineno}: '{tok}' never closed")
    return problems


def main() -> None:
    try:
        data = json.load(sys.stdin)
        raw = (data.get("tool_input") or {}).get("file_path") or ""
        if not raw:
            sys.exit(0)
        path = Path(raw)
        if not path.is_file():
            sys.exit(0)
        problems: list[str] = []
        if path.suffix == ".py":
            try:
                py_compile.compile(str(path), doraise=True, cfile=None)
            except py_compile.PyCompileError as exc:
                problems.append(str(exc.msg).strip())
        elif path.suffix == ".lua" and path.parent.name == "programs":
            problems = check_lua(path)
        if problems:
            print(f"{path.name}:\n  " + "\n  ".join(problems), file=sys.stderr)
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
