import logging
import os
import re
from pathlib import Path
from typing import List

from sqlalchemy.exc import SQLAlchemyError

from app.db import engine

logger = logging.getLogger(__name__)


def _should_run() -> bool:
    flag = os.getenv("RUN_SQL_MIGRATIONS", "false").lower()
    return flag in {"1", "true", "yes", "on"}


def _split_sql(sql_text: str) -> List[str]:
    """
    Split a SQL script into individual statements, respecting dollar-quoted blocks and strings.
    """
    statements = []
    buf: list[str] = []
    in_single = False
    in_double = False
    dollar_tag: str | None = None
    i = 0
    length = len(sql_text)

    while i < length:
        ch = sql_text[i]

        # line comments
        if not in_single and not in_double and dollar_tag is None and sql_text.startswith("--", i):
            end = sql_text.find("\n", i)
            if end == -1:
                break
            buf.append(sql_text[i:end])
            i = end
            continue

        # dollar quote start
        if not in_single and not in_double and dollar_tag is None and ch == "$":
            m = re.match(r"\$([A-Za-z0-9_]*)\$", sql_text[i:])
            if m:
                dollar_tag = m.group(1)
                token = m.group(0)
                buf.append(token)
                i += len(token)
                continue

        # dollar quote end
        if dollar_tag is not None and sql_text.startswith(f"${dollar_tag}$", i):
            token = f"${dollar_tag}$"
            buf.append(token)
            i += len(token)
            dollar_tag = None
            continue

        if dollar_tag is None:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double

        if ch == ";" and not in_single and not in_double and dollar_tag is None:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    return statements


async def run_sql_migrations() -> None:
    if not _should_run():
        return

    sql_dir = Path(__file__).resolve().parent.parent / "sql"
    if not sql_dir.is_dir():
        logger.info("SQL directory %s not found, skipping migrations", sql_dir)
        return

    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        logger.info("No SQL files found in %s, skipping migrations", sql_dir)
        return

    for file in files:
        sql_text = file.read_text(encoding="utf-8")
        if not sql_text.strip():
            continue
        statements = _split_sql(sql_text)
        logger.info("Running SQL migration %s (%d statements)", file.name, len(statements))
        try:
            async with engine.begin() as conn:
                for stmt in statements:
                    sql_stmt = str(stmt).strip()
                    if not sql_stmt:
                        continue
                    await conn.exec_driver_sql(sql_stmt)
        except SQLAlchemyError:
            logger.exception("SQL migration %s failed", file.name)
            raise
