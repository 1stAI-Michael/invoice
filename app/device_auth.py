"""Geraete-Token fuer die Agenten-Schnittstelle (fA-2245).

Die invoice-api prueft ein Geraete-Token NICHT selbst. Ob es gilt, weiss nur flows-api
(`flow.device_registry`: Hash, Freigabe, Ablauf). Eine zweite Pruefung hier waere eine
zweite Wahrheit, die beim naechsten Umbau in Flows still auseinanderlaeuft. Deshalb
fragt diese Datei die Selbstauskunft von flows-api (fA-2247) und merkt sich ein
POSITIVES Ergebnis kurz.

Geschlossen bei jedem Zweifel:
  * kein Header                    -> 401
  * Token ungueltig / gesperrt     -> 403 (Antwort von Flows)
  * Scope fehlt                    -> 403 scope_required
  * Flows nicht erreichbar/kaputt  -> 503, es wird NICHTS angelegt

Der Token-Wert verlaesst diese Datei nur als Header an flows-api. Gemerkt wird ein
sha256-Abdruck, geloggt nur der Geraetename.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

# Adresse der Selbstauskunft. Den Pfad legt Flows fest (fA-2247); per Umgebung
# umstellbar, damit ein Umbenennen dort keinen Code-Wechsel hier braucht.
TOKEN_CHECK_URL = os.environ.get(
    "INVOICE_TOKEN_CHECK_URL", "http://flows-api:8000/api/devices/device/ich"
)
TOKEN_CHECK_TIMEOUT = float(os.environ.get("INVOICE_TOKEN_CHECK_TIMEOUT", "5"))
CACHE_SECONDS = float(os.environ.get("INVOICE_TOKEN_CACHE_SECONDS", "60"))


@dataclass
class Geraet:
    device_name: str
    instanz: str | None
    scopes: list[str] = field(default_factory=list)


_cache: dict[str, tuple[float, Geraet]] = {}


def _abdruck(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _frage_flows(token: str) -> tuple[int, dict]:
    """Synchroner Aufruf (urllib, kein Zusatzpaket). Laeuft in einem Thread."""
    req = urllib.request.Request(
        TOKEN_CHECK_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_CHECK_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return exc.code, body


async def pruefe_token(token: str) -> Geraet:
    """Fragt flows-api; wirft HTTPException mit dem passenden Code."""
    key = _abdruck(token)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]

    try:
        status, body = await asyncio.to_thread(_frage_flows, token)
    except Exception as exc:  # Netz, DNS, Timeout, kaputtes JSON
        logger.warning("Token-Pruefung bei flows-api gescheitert: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail={
            "error": "token_pruefung_nicht_erreichbar",
            "detail": "flows-api hat die Selbstauskunft nicht beantwortet; es wurde nichts angelegt.",
        })

    if status == 200 and isinstance(body, dict) and body.get("device_name"):
        geraet = Geraet(
            device_name=str(body["device_name"]),
            instanz=body.get("instanz"),
            scopes=[str(s) for s in (body.get("scopes") or [])],
        )
        _cache[key] = (now, geraet)
        return geraet
    if status in (401, 403):
        raise HTTPException(status_code=403, detail={
            "error": "token_ungueltig",
            "detail": "flows-api kennt dieses Geraete-Token nicht als gueltig.",
        })
    # 404 (Selbstauskunft noch nicht da), 5xx, unerwartete Form: geschlossen.
    logger.warning("Token-Pruefung: unerwartete Antwort %s von flows-api", status)
    raise HTTPException(status_code=503, detail={
        "error": "token_pruefung_unklar",
        "detail": f"flows-api antwortete {status}; es wurde nichts angelegt.",
    })


def _hat_scope(scopes: list[str], noetig: str) -> bool:
    return "*" in scopes or noetig in scopes


def braucht_scope(noetig: str):
    """FastAPI-Abhaengigkeit: Bearer-Token holen, bei Flows pruefen, Scope verlangen."""

    async def _dep(request: Request) -> Geraet:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise HTTPException(
                status_code=401,
                detail={"error": "token_fehlt", "detail": "Authorization: Bearer <Geraete-Token> fehlt."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        geraet = await pruefe_token(auth[7:].strip())
        if not _hat_scope(geraet.scopes, noetig):
            raise HTTPException(status_code=403, detail={
                "error": "scope_required", "missing": [noetig], "have": geraet.scopes,
            })
        return geraet

    return _dep


def cache_leeren() -> None:
    _cache.clear()
