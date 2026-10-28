"""Every cadgen source parses on the OLDEST Python the wheel says it supports.

This shipped. ``cadgen/cli/store.py`` printed a clause with an f-string nesting
the same quote character inside its replacement field -- syntax PEP 701 made
legal in 3.12 and nothing earlier. CI runs 3.12, so every test passed; the
wheel declares ``requires-python >= 3.11``, so ``pip install cadgen`` succeeded
on 3.11 and then ``cadgen store why`` died with a SyntaxError at import.

Nothing behavioural can catch this, because the interpreter running the suite
is the interpreter that accepts the syntax. So it is policy-checked here:
``ast.parse(..., feature_version=...)`` asks the CURRENT interpreter to parse
with an older grammar, which is exactly the question "would the floor accept
this file?".

The floor is read from the package's own ``requires-python``, so raising the
declared minimum automatically relaxes this check -- the two can never
disagree.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import tomllib
import unittest
from pathlib import Path

from tests.python.support.paths import repo_path

CADGEN_ROOT = repo_path("packages/cadgen")
CADGEN_SRC = CADGEN_ROOT / "src"

# ast.parse only honours feature_version back to 3.7, and only forward to the
# running interpreter. A floor outside that window is not a failure of the
# sources, so the test says so rather than pretending to have checked.
_OLDEST_SUPPORTED_FEATURE_VERSION = (3, 7)


def _declared_floor() -> tuple[int, int]:
    """The (major, minor) of the package's declared ``requires-python``."""
    metadata = tomllib.loads((CADGEN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = metadata["project"]["requires-python"]
    match = re.search(r">=\s*(\d+)\.(\d+)", requires)
    if not match:
        raise AssertionError(
            f"cadgen's requires-python ({requires!r}) has no '>=<major>.<minor>' floor; "
            "this check needs one to know which grammar to parse against",
        )
    return int(match.group(1)), int(match.group(2))


def _quote(token_text: str) -> str:
    """The delimiter of a string or f-string token, without its prefix letters."""
    body = token_text.lstrip("rRbBuUfFtT")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def _same_quote_nesting(source: str) -> list[int]:
    """Lines where a string inside an f-string's replacement field reuses that f-string's quote.

    PEP 701 made this legal in 3.12; 3.11 ends the f-string at the inner quote. ``ast.parse`` with
    ``feature_version=(3, 11)`` on 3.12+ does not reject it (the f-string grammar is not
    versioned), so it is checked on the tokens instead. Only 3.12+ tokenizes f-strings into parts.
    """
    if not hasattr(tokenize, "FSTRING_START"):
        return []
    lines, open_quotes = [], []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.FSTRING_START) and open_quotes:
            inner = _quote(tok.string)
            if any(inner == outer if len(outer) == 3 else inner.startswith(outer) for outer in open_quotes):
                lines.append(tok.start[0])
        if tok.type == tokenize.FSTRING_START:
            open_quotes.append(_quote(tok.string))
        elif tok.type == tokenize.FSTRING_END:
            open_quotes.pop()
    return lines


def _sources() -> list[Path]:
    return sorted(
        path for path in CADGEN_SRC.rglob("*.py")
        if "__pycache__" not in path.parts
    )


class CadgenParsesOnItsDeclaredFloor(unittest.TestCase):
    def test_every_module_parses_with_the_declared_minimum_grammar(self) -> None:
        floor = _declared_floor()
        if floor < _OLDEST_SUPPORTED_FEATURE_VERSION or floor > sys.version_info[:2]:
            self.skipTest(
                f"cannot parse against {floor[0]}.{floor[1]} from "
                f"{sys.version_info.major}.{sys.version_info.minor}",
            )

        sources = _sources()
        self.assertTrue(sources, f"no cadgen sources found under {CADGEN_SRC}")

        offenders = []
        for path in sources:
            source = path.read_text(encoding="utf-8")
            try:
                ast.parse(source, filename=str(path), feature_version=floor)
            except SyntaxError as error:
                offenders.append(
                    f"{path.relative_to(CADGEN_SRC)}:{error.lineno}: {error.msg}",
                )
                continue
            if floor < (3, 12):
                offenders.extend(
                    f"{path.relative_to(CADGEN_SRC)}:{line}: f-string nests its own quote "
                    "(PEP 701, 3.12+)"
                    for line in _same_quote_nesting(source)
                )

        self.assertEqual(
            [],
            offenders,
            f"cadgen declares requires-python >= {floor[0]}.{floor[1]}, so every module "
            f"must parse under that grammar; these do not:\n  " + "\n  ".join(offenders),
        )

    def test_same_quote_nesting_is_caught(self) -> None:
        # The line that shipped in cadgen/cli/store.py: 3.11 cannot parse it, and
        # ast.parse(feature_version=(3, 11)) on 3.12+ accepts it anyway.
        shipped = "x = f\"{c.get('why') or f'{c.get('files', 0)} files unchanged'}\"\n"
        fixed = "n = c.get('files', 0)\nx = f\"{c.get('why') or f'{n} files unchanged'}\"\n"
        if not hasattr(tokenize, "FSTRING_START"):
            self.skipTest("this interpreter (< 3.12) rejects the syntax in ast.parse already")
        self.assertEqual([1], _same_quote_nesting(shipped))
        self.assertEqual([], _same_quote_nesting(fixed))
        self.assertEqual([], _same_quote_nesting('x = f"{a}" + f\'{b["k"]}\'\n'))
        self.assertEqual([], _same_quote_nesting('x = f"""{d["k"]}"""\n'))


if __name__ == "__main__":
    unittest.main()
