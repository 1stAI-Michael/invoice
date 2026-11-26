import logging

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import async_session_factory, init_db
from app.sql_runner import run_sql_migrations
from app.routes_openemr import router as openemr_router
from app.routes_invoices import router as invoices_router
from app.routes_service_entries import router as service_entries_router
from app.routes_services import router as services_router
from app.routes_workflow import router as workflow_router
from app.routes_ui import router as ui_router
from app.routes_exports import router as exports_router
from app.routes_customers import router as customers_router
from app.routes_device import router as device_router  # fA-2245: Agenten, Geraete-Token
from app.device_auth import schreib_riegel  # fA-2302


logger = logging.getLogger(__name__)

app = FastAPI(title='Invoice API', version='0.1.0')


@app.on_event('startup')
async def on_startup() -> None:
    await init_db()
    await run_sql_migrations()


@app.get('/health')
async def health() -> dict[str, str]:
    try:
        async with async_session_factory() as session:
            result = await session.execute(text('SELECT 1'))
            result.scalar_one()
        return {'status': 'ok'}
    except SQLAlchemyError as exc:
        logger.exception('Database health check failed', exc_info=exc)
        raise HTTPException(status_code=503, detail='database unavailable')
    except Exception as exc:
        logger.exception('Health check failed', exc_info=exc)
        raise HTTPException(status_code=500, detail='health check failed')


# fA-2302: alle ALTEN Router schreiben nur noch ueber die Web-UI (Anmeldung) oder mit invoice:write.
# Der Geraete-Router prueft selbst (braucht_scope) und bekommt den Riegel NICHT doppelt.
_riegel = [Depends(schreib_riegel)]
app.include_router(invoices_router, dependencies=_riegel)
app.include_router(openemr_router, dependencies=_riegel)
app.include_router(service_entries_router, dependencies=_riegel)
app.include_router(services_router, dependencies=_riegel)
app.include_router(workflow_router, dependencies=_riegel)
app.include_router(ui_router, dependencies=_riegel)
app.include_router(exports_router, dependencies=_riegel)
app.include_router(customers_router, dependencies=_riegel)
app.include_router(device_router)
