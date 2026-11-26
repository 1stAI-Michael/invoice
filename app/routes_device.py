"""Agenten-Schnittstelle fuer Leistungen (fA-2245, Michael 25.11.2026).

Eine Schiene mit Geraete-Token legt OFFENE Leistungen fuer EINEN Kunden und EINEN
Leistungserbringer an und liest sie zur Gegenprobe. Bewusst eng:

  * nur `service_entries` mit status 'open' -- keine Rechnung, kein Festschreiben,
    keine Rechnungsnummer ("bitte nur zusammenstellen, noch keine Rechnung erstellen")
  * kein Loeschen, kein Aendern, kein Kunden- und kein Provider-Anlegen
  * Einzelpreis und Status setzt der Server; der Aufrufer nennt die Leistungsnummer

Drei Sicherungen, keine davon optional:

  1. Idempotenz ueber den fachlichen Schluessel (Kunde, Provider, Leistung,
     Leistungsdatum, Menge, Faktor, Kommentar), gezaehlt als MULTIMENGE: stehen zwei
     gleiche Positionen im Stapel und eine davon schon in der Tabelle, wird genau eine
     angelegt. Ein zweiter identischer Aufruf legt deshalb 0 an. Verglichen wird
     gegen 'open' UND 'invoiced', damit ein Wiederholen nach der Abrechnung nichts
     doppelt.
  2. Waechter auf den Ausgangsstand (`erwartet_anzahl`, `erwartet_summe` der offenen
     Posten dieses Kunden bei diesem Provider). Weicht er ab, wird NICHTS angelegt;
     die Antwort traegt den Ist-Stand (409). Ist nichts anzulegen (alles schon da),
     wird der Waechter nicht angewandt -- es aendert sich ja nichts.
  3. Alles oder nichts: eine Transaktion, eine Sperre je (Kunde, Provider), und die
     Gegenprobe (Anzahl und Summe danach) steht in derselben Antwort.

Summe = sum(menge * faktor * services_master.standard_einzelpreis), auf Cent gerundet.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, condecimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import execute, execute_returning_row, fetch_all, fetch_one
from app.device_auth import Geraet, braucht_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invoice/service-entries/device", tags=["agenten"])

MAX_POSITIONEN = 500
_CENT = Decimal("0.01")


def _cent(v: Decimal | None) -> Decimal:
    return (v or Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_nummer: str = Field(..., min_length=1, max_length=40)
    leistungsdatum: date
    menge: condecimal(max_digits=10, decimal_places=2, gt=0) = Decimal("1.00")
    faktor: condecimal(max_digits=6, decimal_places=2, gt=0) = Decimal("1.00")
    kommentar: Optional[str] = Field(None, max_length=2000)


class Stapel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: int
    provider_code: str = Field(..., pattern="^(TI|PP|MS)$")
    erwartet_anzahl: Optional[int] = Field(None, ge=0)
    erwartet_summe: Optional[condecimal(max_digits=14, decimal_places=2)] = None
    # Probelauf: alles pruefen und planen, NICHTS schreiben (die Route rollt zurueck).
    probe: bool = False
    positionen: List[Position] = Field(..., min_length=1, max_length=MAX_POSITIONEN)


async def _provider_id(session: AsyncSession, code: str) -> int:
    row = await fetch_one(session, "SELECT id FROM invoice.providers WHERE code = :c", {"c": code})
    if not row:  # anders als die alte Route: hier wird KEIN Provider angelegt
        raise HTTPException(status_code=404, detail={"error": "provider_unbekannt", "provider_code": code})
    return int(row["id"])


async def _kunde_da(session: AsyncSession, customer_id: int) -> None:
    row = await fetch_one(session, "SELECT 1 AS da FROM invoice.customers WHERE id = :c", {"c": customer_id})
    if not row:
        raise HTTPException(status_code=404, detail={"error": "kunde_unbekannt", "customer_id": customer_id})


async def offener_stand(session: AsyncSession, customer_id: int, provider_id: int) -> dict[str, Any]:
    row = await fetch_one(
        session,
        """
        SELECT count(*) AS anzahl,
               coalesce(sum(se.menge * se.faktor * sm.standard_einzelpreis), 0) AS summe
          FROM invoice.service_entries se
          JOIN invoice.services_master sm ON sm.id = se.service_id
         WHERE se.customer_id = :c AND se.provider_id = :p AND se.status = 'open'
        """,
        {"c": customer_id, "p": provider_id},
    )
    return {"anzahl": int(row["anzahl"]), "summe": str(_cent(Decimal(row["summe"])))}


async def anlegen(session: AsyncSession, stapel: Stapel, geraet_name: str) -> tuple[int, dict[str, Any]]:
    """Kern ohne Commit: der Aufrufer haelt die Transaktion (Route: commit, Test: rollback).

    Liefert (HTTP-Status, Antwort). Bei 409/422 wurde nichts geschrieben.
    """
    provider_id = await _provider_id(session, stapel.provider_code)
    await _kunde_da(session, stapel.customer_id)

    # Eine Sperre je (Kunde, Provider): zwei gleichzeitige Stapel sehen sonst beide
    # "noch nicht da" und legen beide an. Faellt mit dem Ende der Transaktion.
    await execute(
        session,
        "SELECT pg_advisory_xact_lock(hashtext('invoice-device-entries'), CAST(:k AS integer))",
        {"k": (stapel.customer_id * 31 + provider_id) % 2147483647},
    )

    # Leistungsnummern aufloesen -- unbekannte melden, nichts anlegen.
    nummern = sorted({p.service_nummer for p in stapel.positionen})
    rows = await fetch_all(
        session,
        "SELECT id, nummer, standard_einzelpreis FROM invoice.services_master WHERE nummer = ANY(:n)",
        {"n": nummern},
    )
    service_id = {r["nummer"]: int(r["id"]) for r in rows}
    preis = {r["nummer"]: Decimal(r["standard_einzelpreis"]) for r in rows}
    unbekannt = [i for i, p in enumerate(stapel.positionen) if p.service_nummer not in service_id]
    if unbekannt:
        return 422, {
            "error": "leistung_unbekannt",
            "angelegt": 0,
            "positionen_index": unbekannt,
            "service_nummer": sorted({stapel.positionen[i].service_nummer for i in unbekannt}),
        }

    vorher = await offener_stand(session, stapel.customer_id, provider_id)

    # Multimenge: je fachlichem Schluessel, wie viele stehen schon da (mit ids)?
    def schluessel(p: Position) -> tuple:
        return (service_id[p.service_nummer], p.leistungsdatum, _cent(p.menge), _cent(p.faktor), p.kommentar)

    vorhanden: dict[tuple, list[dict]] = {}
    for k in {schluessel(p) for p in stapel.positionen}:
        treffer = await fetch_all(
            session,
            """
            SELECT id, status FROM invoice.service_entries
             WHERE customer_id = :c AND provider_id = :p AND service_id = :s
               AND leistungsdatum = :d AND menge = :m AND faktor = :f
               AND kommentar IS NOT DISTINCT FROM :k
             ORDER BY id
            """,
            {"c": stapel.customer_id, "p": provider_id, "s": k[0], "d": k[1],
             "m": k[2], "f": k[3], "k": k[4]},
        )
        vorhanden[k] = list(treffer)

    plan: list[tuple[int, Position, Optional[dict]]] = []
    verbraucht: dict[tuple, int] = defaultdict(int)
    for i, p in enumerate(stapel.positionen):
        k = schluessel(p)
        n = verbraucht[k]
        if n < len(vorhanden[k]):
            plan.append((i, p, vorhanden[k][n]))
        else:
            plan.append((i, p, None))
        verbraucht[k] += 1

    anzulegen = [x for x in plan if x[2] is None]

    if anzulegen and (stapel.erwartet_anzahl is not None or stapel.erwartet_summe is not None):
        abw = []
        if stapel.erwartet_anzahl is not None and stapel.erwartet_anzahl != vorher["anzahl"]:
            abw.append("anzahl")
        if stapel.erwartet_summe is not None and _cent(Decimal(stapel.erwartet_summe)) != Decimal(vorher["summe"]):
            abw.append("summe")
        if abw:
            return 409, {
                "error": "ausgangsstand_weicht_ab",
                "abweichung": abw,
                "angelegt": 0,
                "erwartet": {"anzahl": stapel.erwartet_anzahl,
                             "summe": None if stapel.erwartet_summe is None else str(_cent(Decimal(stapel.erwartet_summe)))},
                "ist": vorher,
            }

    ergebnisse: list[dict[str, Any]] = []
    neue_ids: list[int] = []
    for i, p, alt in plan:
        if alt is not None:
            ergebnisse.append({"index": i, "ergebnis": "uebersprungen", "id": int(alt["id"]), "status": alt["status"]})
            continue
        if stapel.probe:
            ergebnisse.append({"index": i, "ergebnis": "wuerde_angelegt"})
            continue
        row = await execute_returning_row(
            session,
            """
            INSERT INTO invoice.service_entries
                   (customer_id, provider_id, service_id, leistungsdatum, menge, faktor, kommentar, status,
                    angelegt_von)
            VALUES (:c, :p, :s, :d, :m, :f, :k, 'open', :von)
            RETURNING id
            """,
            {"c": stapel.customer_id, "p": provider_id, "s": service_id[p.service_nummer],
             "d": p.leistungsdatum, "m": _cent(p.menge), "f": _cent(p.faktor), "k": p.kommentar,
             "von": geraet_name},  # fA-2302 Punkt 3: welches Geraet hat die Zeile angelegt
        )
        neue_ids.append(int(row["id"]))
        ergebnisse.append({"index": i, "ergebnis": "angelegt", "id": int(row["id"])})

    if stapel.probe:
        plus = sum((_cent(p.menge) * _cent(p.faktor) * preis[p.service_nummer] for _, p, _a in anzulegen), Decimal("0"))
        nachher = {"anzahl": vorher["anzahl"] + len(anzulegen),
                   "summe": str(_cent(Decimal(vorher["summe"]) + plus))}
        return 200, {
            "probe": True,
            "angelegt": 0,
            "wuerde_angelegt": len(anzulegen),
            "uebersprungen": len(plan) - len(anzulegen),
            "waechter": ("geprueft" if anzulegen and (stapel.erwartet_anzahl is not None or stapel.erwartet_summe is not None)
                         else ("nicht_angewandt_nichts_anzulegen" if not anzulegen else "nicht_gesetzt")),
            "vorher": vorher,
            "nachher_erwartet": nachher,
            "positionen": ergebnisse,
            "geraet": geraet_name,
        }

    nachher = await offener_stand(session, stapel.customer_id, provider_id)
    logger.info(
        "fA-2245 Agenten-Leistungen: geraet=%s kunde=%s provider=%s angelegt=%d uebersprungen=%d ids=%s",
        geraet_name, stapel.customer_id, stapel.provider_code, len(neue_ids),
        len(plan) - len(neue_ids), neue_ids,
    )
    return 200, {
        "angelegt": len(neue_ids),
        "uebersprungen": len(plan) - len(neue_ids),
        "waechter": ("geprueft" if anzulegen and (stapel.erwartet_anzahl is not None or stapel.erwartet_summe is not None)
                     else ("nicht_angewandt_nichts_anzulegen" if not anzulegen else "nicht_gesetzt")),
        "vorher": vorher,
        "nachher": nachher,
        "positionen": ergebnisse,
        "geraet": geraet_name,
    }


@router.post("")
async def leistungen_anlegen(
    stapel: Stapel,
    geraet: Geraet = Depends(braucht_scope("invoice:write")),
    session: AsyncSession = Depends(get_session),
):
    try:
        status, antwort = await anlegen(session, stapel, geraet.device_name)
    except Exception:
        await session.rollback()
        raise
    if status != 200 or stapel.probe:
        await session.rollback()
    if status != 200:
        return JSONResponse(status_code=status, content=antwort)
    await session.commit()
    return antwort


@router.get("")
async def offene_leistungen(
    customer_id: int,
    provider_code: str = Query(..., pattern="^(TI|PP|MS)$"),
    geraet: Geraet = Depends(braucht_scope("invoice:read")),
    session: AsyncSession = Depends(get_session),
):
    provider_id = await _provider_id(session, provider_code)
    await _kunde_da(session, customer_id)
    rows = await fetch_all(
        session,
        """
        SELECT se.id, sm.nummer AS service_nummer, se.leistungsdatum, se.menge, se.faktor,
               sm.standard_einzelpreis AS einzelpreis, se.kommentar, se.created_at
          FROM invoice.service_entries se
          JOIN invoice.services_master sm ON sm.id = se.service_id
         WHERE se.customer_id = :c AND se.provider_id = :p AND se.status = 'open'
         ORDER BY se.leistungsdatum, se.id
        """,
        {"c": customer_id, "p": provider_id},
    )
    stand = await offener_stand(session, customer_id, provider_id)
    return {
        **stand,
        "positionen": [
            {**r, "leistungsdatum": r["leistungsdatum"].isoformat(), "menge": str(r["menge"]),
             "faktor": str(r["faktor"]), "einzelpreis": str(r["einzelpreis"]),
             "created_at": r["created_at"].isoformat()}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# v2 (fA-2245, Michael 25.11.2026: "API v2: Aendern erlauben")
# ---------------------------------------------------------------------------
# Anlass: einzelne angelegte Positionen trugen ein falsches Belegdatum aus dem OCR.
# Bewusst eng wie v1:
#   * nur `leistungsdatum` und `kommentar`, nur bei status 'open'
#   * der Aufrufer nennt Kunde und Provider (ein Tippfehler in der id trifft sonst einen
#     fremden Posten) UND den Wert, den er ersetzt (`vorher`): weicht der Stand ab, 409
#     und nichts geaendert -- dieselbe Idee wie der Waechter beim Anlegen
#   * Menge, Faktor, Leistung, Kunde, Status bleiben unveraenderlich; kein Loeschen

class Aenderung(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: int
    provider_code: str = Field(..., pattern="^(TI|PP|MS)$")
    vorher: dict[str, Optional[str]] = Field(..., description="aktuelle Werte der geaenderten Felder")
    leistungsdatum: Optional[date] = None
    kommentar: Optional[str] = Field(None, max_length=2000)
    kommentar_leeren: bool = False


async def aendern(session: AsyncSession, entry_id: int, a: Aenderung, geraet_name: str) -> tuple[int, dict[str, Any]]:
    felder: dict[str, Any] = {}
    if a.leistungsdatum is not None:
        felder["leistungsdatum"] = a.leistungsdatum
    if a.kommentar is not None and a.kommentar_leeren:
        return 422, {"error": "widerspruch", "detail": "kommentar und kommentar_leeren schliessen sich aus"}
    if a.kommentar is not None:
        felder["kommentar"] = a.kommentar
    if a.kommentar_leeren:
        felder["kommentar"] = None
    if not felder:
        return 422, {"error": "nichts_zu_aendern", "detail": "leistungsdatum und/oder kommentar angeben"}
    fehlend = [f for f in felder if f not in a.vorher]
    if fehlend:
        return 422, {"error": "vorher_fehlt", "felder": fehlend,
                     "detail": "fuer jedes geaenderte Feld den aktuellen Wert in `vorher` nennen"}

    provider_id = await _provider_id(session, a.provider_code)
    row = await fetch_one(
        session,
        """
        SELECT id, customer_id, provider_id, status, leistungsdatum, kommentar
          FROM invoice.service_entries WHERE id = :id FOR UPDATE
        """,
        {"id": entry_id},
    )
    if not row or row["customer_id"] != a.customer_id or row["provider_id"] != provider_id:
        # Gleiche Antwort fuer "gibt es nicht" und "gehoert nicht zu diesem Kunden":
        # die Route verraet keine fremden Posten.
        return 404, {"error": "posten_unbekannt", "id": entry_id}
    if row["status"] != "open":
        return 409, {"error": "nicht_offen", "id": entry_id, "status": row["status"]}

    ist = {"leistungsdatum": row["leistungsdatum"].isoformat(), "kommentar": row["kommentar"]}
    abw = [f for f in felder if (a.vorher.get(f) or None) != (ist[f] or None)]
    if abw:
        return 409, {"error": "vorher_weicht_ab", "abweichung": abw, "ist": ist, "geaendert": False}

    neu = {**ist, **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in felder.items()}}
    if neu == ist:
        return 200, {"id": entry_id, "geaendert": False, "vorher": ist, "nachher": ist, "geraet": geraet_name}

    sets = ", ".join(f"{k} = :{k}" for k in felder)
    await execute(session, f"UPDATE invoice.service_entries SET {sets} WHERE id = :id AND status = 'open'",
                  {**felder, "id": entry_id})
    nachher_row = await fetch_one(
        session, "SELECT leistungsdatum, kommentar FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
    nachher = {"leistungsdatum": nachher_row["leistungsdatum"].isoformat(), "kommentar": nachher_row["kommentar"]}
    # fA-2302 Punkt 3: jede Aenderung als Zeile in der Spur (nur anhaengen), in derselben Transaktion
    for feld in sorted(felder):
        if ist[feld] != nachher[feld]:
            await execute(session,
                          "INSERT INTO invoice.service_entry_aenderung (service_entry_id, feld, vorher, nachher, von) "
                          "VALUES (:id, :feld, :vorher, :nachher, :von)",
                          {"id": entry_id, "feld": feld, "vorher": ist[feld], "nachher": nachher[feld],
                           "von": geraet_name})
    logger.info("fA-2245 v2 Aenderung: geraet=%s id=%s kunde=%s felder=%s vorher=%s nachher=%s",
                geraet_name, entry_id, a.customer_id, sorted(felder), ist, nachher)
    return 200, {"id": entry_id, "geaendert": True, "vorher": ist, "nachher": nachher, "geraet": geraet_name}


@router.patch("/{entry_id}")
async def leistung_aendern(
    entry_id: int,
    a: Aenderung,
    geraet: Geraet = Depends(braucht_scope("invoice:write")),
    session: AsyncSession = Depends(get_session),
):
    try:
        status, antwort = await aendern(session, entry_id, a, geraet.device_name)
    except Exception:
        await session.rollback()
        raise
    if status != 200:
        await session.rollback()
        return JSONResponse(status_code=status, content=antwort)
    await session.commit()
    return antwort
