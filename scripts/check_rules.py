"""Check the ETL rules that ruff and pyright do not cover.

Run: uv run python scripts/check_rules.py
Exit code 1 if a rule is broken. CI and pre-commit run this script.

Rules:
- sql-fstring / sql-format / sql-concat: no SQL built from strings (values are parameters;
  identifiers come from psycopg.sql or fixed constants), except in sql_*.py builder files.
- public-schema: SQL names `public.<table>` only for the tables clevrr-backend-2.0 owns or
  co-writes (APP_TABLES: orgs, connectors, UTM rules, manual costs).
- public-write: SQL never writes, creates or alters a `public.` table. Every ETL table is
  unqualified, so it resolves in the target schema (search_path) and never in legacy's.
- ch-database: SQL never names `clevrr_db.`; ClickHouse tables are unqualified, so they resolve
  in the client's database (the target, e.g. clevrr_db_v2).
- sys-path, file-size (400 lines), function-size (60 lines).
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_DIRS = ("etl", "scripts", "alembic", "clickhouse_migrations")
MAX_FILE_LINES = 400
MAX_FUNCTION_LINES = 60
SQL_WORDS = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|ALTER\s+TABLE|DROP\s+TABLE|WHERE)\b"
    r"|\bselect\b.+\bfrom\b|\binsert\s+into\b|\bdelete\s+from\b",
    re.DOTALL,
)
# The tables the ETL may read in `public`: the app owns or co-writes them (manual costs and the
# variable cost rules come from the app). The ETL never migrates or writes them.
APP_TABLES = (
    "organizations", "organization_connectors", "connectors", "connector_endpoints",
    "utm_rules", "utm_mappings", "attribution_channel_rules",
    "product_cost_history", "variable_cost_rules_versions",
)  # fmt: skip
PUBLIC_REF = re.compile(r"\bpublic\.(\w+)")
PUBLIC_WRITE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|ALTER\s+TABLE|DROP\s+TABLE"
    r"|CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)(?:\s+IF\s+NOT\s+EXISTS)?)\s+(?:ONLY\s+)?public\.",
    re.IGNORECASE,
)
CH_DB_REF = re.compile(r"\bclevrr_db\.\w")
SQL_BUILDERS = ("etl/**/sql_*.py", "etl/sql_*.py")
SELF = "scripts/check_rules.py"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


def _const_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_const_text(v) for v in node.values)
    return ""


def _is_builder(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in SQL_BUILDERS)


def _sql_findings(tree: ast.AST, rel: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and SQL_WORDS.search(_const_text(node)):
            yield Finding(rel, node.lineno, "sql-fstring", "SQL built with an f-string.")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and SQL_WORDS.search(_const_text(node.func.value))
        ):
            yield Finding(rel, node.lineno, "sql-format", "SQL built with .format().")
        elif (
            not _is_builder(rel)
            and isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod | ast.Add)
            and SQL_WORDS.search(_const_text(node.left) or _const_text(node.right))
        ):
            yield Finding(rel, node.lineno, "sql-concat", "SQL built with % or +.")


def _target_findings(rel: str, text: str, line: int) -> Iterator[Finding]:
    for match in PUBLIC_REF.finditer(text):
        if match.group(1) not in APP_TABLES:
            message = (
                f"`public.{match.group(1)}` is not an app-owned table; use an unqualified name."
            )
            yield Finding(rel, line, "public-schema", message)
    if PUBLIC_WRITE.search(text):
        message = "The ETL never writes, creates or alters a `public` table; it reads them only."
        yield Finding(rel, line, "public-write", message)
    if CH_DB_REF.search(text):
        message = "Do not name `clevrr_db.`; the client's database is the target."
        yield Finding(rel, line, "ch-database", message)


def _string_findings(tree: ast.AST, rel: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        text = _const_text(node)
        if text and isinstance(node, ast.Constant | ast.JoinedStr):
            yield from _target_findings(rel, text, getattr(node, "lineno", 0))


def _other_findings(tree: ast.AST, rel: str, lines: int) -> Iterator[Finding]:
    if lines > MAX_FILE_LINES:
        yield Finding(rel, 1, "file-size", f"{lines} lines. The limit is {MAX_FILE_LINES}.")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno:
            length = node.end_lineno - node.lineno + 1
            if length > MAX_FUNCTION_LINES:
                message = f"{node.name} has {length} lines. The limit is {MAX_FUNCTION_LINES}."
                yield Finding(rel, node.lineno, "function-size", message)
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"append", "insert"}
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
        ):
            yield Finding(rel, node.lineno, "sys-path", "Do not edit sys.path.")


def check_python(path: Path) -> list[Finding]:
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel)
    findings = [*_sql_findings(tree, rel), *_string_findings(tree, rel)]
    return findings + list(_other_findings(tree, rel, source.count("\n") + 1))


def check_sql(path: Path) -> list[Finding]:
    rel = path.relative_to(ROOT).as_posix()
    findings: list[Finding] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        code = line.split("--", 1)[0]
        findings += _target_findings(rel, code, number)
    return findings


def main() -> int:
    findings: list[Finding] = []
    for folder in CHECKED_DIRS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            if path.relative_to(ROOT).as_posix() != SELF:
                findings += check_python(path)
        for path in sorted((ROOT / folder).rglob("*.sql")):
            findings += check_sql(path)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} rule finding(s).")
        return 1
    print("check_rules: no findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
