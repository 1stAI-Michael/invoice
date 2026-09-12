import logging
import ssl
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import quoted_name

from app.settings import settings

logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext | None:
    mode = (settings.db_sslmode or 'disable').lower()
    if mode == 'disable':
        return None
    if mode in {'require', 'prefer'}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if mode in {'verify-ca', 'verify-full'}:
        return ssl.create_default_context()
    logger.warning("Unknown DB sslmode '%s', falling back to disable", mode)
    return None


_schema_quoted = quoted_name(settings.db_schema, quote=True)

_connect_args: dict = {
    'server_settings': {
        'search_path': f"{settings.db_schema}, public",
    }
}
_ssl_ctx = _build_ssl_context()
if _ssl_ctx is not None:
    _connect_args['ssl'] = _ssl_ctx

engine: AsyncEngine = create_async_engine(
    settings.db_url,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Prueft das Schema, legt es nur an, wenn es fehlt.

    PostgreSQL prueft bei ``CREATE SCHEMA IF NOT EXISTS`` das CREATE-Recht auf
    der Datenbank *vor* der Existenzpruefung. Eine eng berechtigte Dienstrolle
    (kein CREATE auf der Datenbank) scheiterte damit am Start, obwohl das Schema
    laengst existiert. Deshalb: erst nachsehen, dann anlegen. Fehlt das Schema
    und darf die Rolle nicht anlegen, scheitert der Start weiterhin laut.
    """
    try:
        async with engine.begin() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname = :s"),
                {"s": settings.db_schema},
            )
            if exists:
                logger.info("Schema %s vorhanden", settings.db_schema)
            else:
                logger.warning("Schema %s fehlt, wird angelegt", settings.db_schema)
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_schema_quoted}"))
            await conn.execute(text(f"SET search_path TO {_schema_quoted}, public"))
    except Exception:
        logger.exception('Database initialization failed')
        raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
