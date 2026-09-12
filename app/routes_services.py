from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import fetch_all
from app.models import ServiceListItem

router = APIRouter(tags=["services"])


@router.get("/services", response_model=list[ServiceListItem])
async def list_services(
    provider_code: str = Query(..., pattern="^(TI|PP|MS)$"),
    session: AsyncSession = Depends(get_session),
):
    rows = await fetch_all(
        session,
        """
        SELECT id, nummer, beschreibung, standard_einzelpreis, mwst_satz, standard_faktor, kommentar_template
        FROM invoice.services_master
        WHERE rechnungskuertzel = :provider_code
        ORDER BY nummer
        """,
        {"provider_code": provider_code},
    )
    return [ServiceListItem.model_validate(row) for row in rows]
