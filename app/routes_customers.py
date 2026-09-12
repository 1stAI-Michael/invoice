from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import fetch_all
from app.models import CustomerListItem

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerListItem])
async def list_customers(
    q: Optional[str] = Query(None, min_length=1),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    where = ""
    params: dict[str, object] = {"limit": limit}
    if q:
        term = f"%{q}%"
        where = """
        WHERE (firma ILIKE :term OR vorname ILIKE :term OR nachname ILIKE :term OR ort ILIKE :term
               OR CAST(external_id AS TEXT) ILIKE :term OR CAST(id AS TEXT) ILIKE :term)
        """
        params["term"] = term

    rows = await fetch_all(
        session,
        f"""
        SELECT id, firma, vorname, nachname, ort, external_system, external_id
        FROM invoice.customers
        {where}
        ORDER BY COALESCE(firma, nachname), vorname, id
        LIMIT :limit
        """,
        params,
    )
    return [CustomerListItem.model_validate(r) for r in rows]
