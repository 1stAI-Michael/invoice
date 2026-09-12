from contextlib import asynccontextmanager
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory


async def fetch_one(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None) -> Optional[Mapping[str, Any]]:
    result: Result = await session.execute(text(query), params or {})
    row = result.mappings().first()
    return dict(row) if row else None


async def fetch_all(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None) -> list[Mapping[str, Any]]:
    result: Result = await session.execute(text(query), params or {})
    return [dict(r) for r in result.mappings().all()]


async def fetch_val(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None) -> Any:
    result: Result = await session.execute(text(query), params or {})
    return result.scalar()


async def execute(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None) -> None:
    await session.execute(text(query), params or {})


async def execute_returning_id(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None, key: str = "id") -> int:
    result: Result = await session.execute(text(query), params or {})
    value = result.scalar_one()
    return int(value)


async def execute_returning_row(session: AsyncSession, query: str, params: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    result: Result = await session.execute(text(query), params or {})
    row = result.mappings().one()
    return dict(row)


@asynccontextmanager
async def transaction_session():
    async with async_session_factory() as session:
        try:
            async with session.begin():
                yield session
        except Exception:
            await session.rollback()
            raise
