"""fA-2245: Pruefung der Agenten-Schnittstelle.

Laeuft IM Container gegen die echte Datenbank, aber in EINER Transaktion, die am Ende
zurueckgerollt wird: ein Wegwerf-Kunde, nichts bleibt stehen (nur Sequenz-Luecken).

    docker exec -i -w /app invoice-api python3 - < tests/test_device_entries.py
"""
import asyncio
import json
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi import HTTPException

from app import device_auth
from app.db import async_session_factory
from app.db_utils import fetch_one
from app.routes_device import Aenderung, Stapel, aendern, anlegen

FEHLER = []


def pruefe(bedingung, text):
    print(("OK   " if bedingung else "FEHL ") + text)
    if not bedingung:
        FEHLER.append(text)


def stapel(positionen, **kw):
    return Stapel(customer_id=kw.pop("customer_id"), provider_code="MS", positionen=positionen, **kw)


P1 = {"service_nummer": "AUSL", "leistungsdatum": "2026-01-05", "menge": "12.50", "kommentar": "Test A"}
P2 = {"service_nummer": "AUSL", "leistungsdatum": "2026-01-06", "menge": "7.30", "kommentar": "Test B"}
P3 = {"service_nummer": "AUSL", "leistungsdatum": "2026-01-07", "menge": "1.00", "kommentar": None}


async def db_teil():
    async with async_session_factory() as s:
        await s.begin()
        try:
            row = await fetch_one(s, "INSERT INTO invoice.customers (nachname) VALUES ('fA-2245 Wegwerf') RETURNING id")
            cid = int(row["id"])

            st, a = await anlegen(s, stapel([P1, P2, P2], customer_id=cid, erwartet_anzahl=0, erwartet_summe="0", probe=True), "test")
            pruefe(st == 200 and a["probe"] and a["wuerde_angelegt"] == 3 and a["nachher_erwartet"] == {"anzahl": 3, "summe": "27.10"},
                   f"Probelauf: wuerde 3 anlegen, erwartet 3 / 27.10 -> {a.get('wuerde_angelegt')} {a.get('nachher_erwartet')}")
            n = await fetch_one(s, "SELECT count(*) AS n FROM invoice.service_entries WHERE customer_id=:c", {"c": cid})
            pruefe(int(n["n"]) == 0, "Probelauf schreibt nichts")

            st, a = await anlegen(s, stapel([P1, P2, P2], customer_id=cid, erwartet_anzahl=0, erwartet_summe="0"), "test")
            pruefe(st == 200 and a["angelegt"] == 3, f"erster Stapel: 3 angelegt (Doppel im Stapel zaehlt) -> {st} {a.get('angelegt')}")
            pruefe(a["nachher"] == {"anzahl": 3, "summe": "27.10"}, f"Gegenprobe nachher 3 / 27.10 -> {a.get('nachher')}")
            pruefe(a["waechter"] == "geprueft", f"Waechter geprueft -> {a.get('waechter')}")

            st, a = await anlegen(s, stapel([P1, P2, P2], customer_id=cid, erwartet_anzahl=0, erwartet_summe="0"), "test")
            pruefe(st == 200 and a["angelegt"] == 0 and a["uebersprungen"] == 3,
                   f"Wiederholung: 0 angelegt, 3 uebersprungen (Waechter veraltet, aber nichts anzulegen) -> {st} {a.get('angelegt')}/{a.get('uebersprungen')}")
            pruefe(a["nachher"]["anzahl"] == 3, "Wiederholung: Stand unveraendert")

            st, a = await anlegen(s, stapel([P1, P2, P2, P2], customer_id=cid), "test")
            pruefe(st == 200 and a["angelegt"] == 1, f"drittes P2: genau 1 angelegt (Multimenge) -> {a.get('angelegt')}")

            st, a = await anlegen(s, stapel([P3], customer_id=cid, erwartet_anzahl=99), "test")
            pruefe(st == 409 and a["angelegt"] == 0 and a["ist"]["anzahl"] == 4,
                   f"falsche erwartet_anzahl: 409, 0 angelegt, Ist 4 -> {st} {a.get('ist')}")
            st, a = await anlegen(s, stapel([P3], customer_id=cid, erwartet_anzahl=4, erwartet_summe="1.00"), "test")
            pruefe(st == 409 and a["abweichung"] == ["summe"], f"falsche Summe: 409 nur summe -> {st} {a.get('abweichung')}")
            n = await fetch_one(s, "SELECT count(*) AS n FROM invoice.service_entries WHERE customer_id=:c", {"c": cid})
            pruefe(int(n["n"]) == 4, "nach den 409 steht nichts Neues")

            st, a = await anlegen(s, stapel([P3, {**P3, "service_nummer": "GIBTSNICHT"}], customer_id=cid), "test")
            pruefe(st == 422 and a["positionen_index"] == [1], f"unbekannte Leistung: 422, Index 1 -> {st} {a.get('positionen_index')}")

            st, a = await anlegen(s, stapel([P3], customer_id=cid, erwartet_anzahl=4, erwartet_summe="34.40"), "test")
            pruefe(st == 200 and a["angelegt"] == 1 and a["nachher"] == {"anzahl": 5, "summe": "35.40"},
                   f"richtiger Waechter, Kommentar NULL: 1 angelegt, 5 / 35.40 -> {a.get('nachher')}")
            st, a = await anlegen(s, stapel([P3], customer_id=cid), "test")
            pruefe(a["angelegt"] == 0, "Kommentar NULL wird als gleich erkannt (IS NOT DISTINCT FROM)")

            try:
                await anlegen(s, Stapel(customer_id=-1, provider_code="MS", positionen=[P1]), "test")
                pruefe(False, "unbekannter Kunde: haette 404 sein muessen")
            except HTTPException as e:
                pruefe(e.status_code == 404, f"unbekannter Kunde: 404, kein Anlegen -> {e.status_code}")
            # ---- v2: Aendern ----
            r = await fetch_one(s, "SELECT id FROM invoice.service_entries WHERE customer_id=:c AND kommentar='Test A'", {"c": cid})
            eid = int(r["id"])
            def A(**kw): return Aenderung(customer_id=kw.pop("customer_id", cid), provider_code="MS", **kw)
            st, a = await aendern(s, eid, A(vorher={"leistungsdatum": "2026-01-05"}, leistungsdatum="2026-08-05"), "test")
            pruefe(st == 200 and a["geaendert"] and a["nachher"]["leistungsdatum"] == "2026-08-05", f"v2 Datum geaendert -> {st} {a.get('nachher')}")
            st, a = await aendern(s, eid, A(vorher={"leistungsdatum": "2026-08-05"}, leistungsdatum="2026-08-05"), "test")
            pruefe(st == 200 and a["geaendert"] is False, f"v2 Wiederholung: geaendert=False -> {st} {a.get('geaendert')}")
            st, a = await aendern(s, eid, A(vorher={"leistungsdatum": "2026-01-05"}, leistungsdatum="2026-09-01"), "test")
            pruefe(st == 409 and a["error"] == "vorher_weicht_ab" and a["ist"]["leistungsdatum"] == "2026-08-05", f"v2 veraltetes vorher: 409 mit Ist -> {st} {a.get('ist')}")
            st, a = await aendern(s, eid, A(vorher={}, leistungsdatum="2026-09-01"), "test")
            pruefe(st == 422 and a["error"] == "vorher_fehlt", f"v2 ohne vorher: 422 -> {st}")
            st, a = await aendern(s, eid, A(customer_id=-1, vorher={"leistungsdatum": "2026-08-05"}, leistungsdatum="2026-09-01"), "test")
            pruefe(st == 404, f"v2 falscher Kunde: 404 -> {st}")
            st, a = await aendern(s, eid, A(vorher={"kommentar": "Test A"}, kommentar_leeren=True), "test")
            pruefe(st == 200 and a["nachher"]["kommentar"] is None, f"v2 Kommentar leeren -> {a.get('nachher')}")
            await fetch_one(s, "UPDATE invoice.service_entries SET status='invoiced' WHERE id=:i RETURNING id", {"i": eid})
            st, a = await aendern(s, eid, A(vorher={"leistungsdatum": "2026-08-05"}, leistungsdatum="2026-09-01"), "test")
            pruefe(st == 409 and a["error"] == "nicht_offen", f"v2 abgerechneter Posten: 409 nicht_offen -> {st}")
            r = await fetch_one(s, "SELECT leistungsdatum FROM invoice.service_entries WHERE id=:i", {"i": eid})
            pruefe(str(r["leistungsdatum"]) == "2026-08-05", "v2 nach 409/422/404 steht der Wert unveraendert")
            # ---- fA-2302 Punkt 3: angelegt_von + Spur ----
            r = await fetch_one(s, "SELECT count(*) AS n, count(*) FILTER (WHERE angelegt_von = 'test') AS t "
                                   "FROM invoice.service_entries WHERE customer_id=:c", {"c": cid})
            pruefe(r["n"] > 0 and r["n"] == r["t"], f"angelegt_von: alle {r['n']} Zeilen tragen das Geraet -> {r['t']}")
            r = await fetch_one(s, "SELECT string_agg(feld || ':' || coalesce(vorher,'-') || '>' || coalesce(nachher,'-'), ' ' ORDER BY id) AS z, "
                                   "count(*) AS n FROM invoice.service_entry_aenderung WHERE service_entry_id=:i AND von='test'", {"i": eid})
            pruefe(r["n"] == 2 and r["z"] == "leistungsdatum:2026-01-05>2026-08-05 kommentar:Test A>-",
                   f"Spur: genau 2 Zeilen (Datum, Kommentar geleert), keine fuer Wiederholung/409/422/404 -> {r['z']}")
        finally:
            await s.rollback()
    async with async_session_factory() as s:
        n = await fetch_one(s, "SELECT count(*) AS n FROM invoice.customers WHERE nachname='fA-2245 Wegwerf'")
        pruefe(int(n["n"]) == 0, "Rollback: Wegwerf-Kunde ist weg")


class Fake(BaseHTTPRequestHandler):
    def do_GET(self):
        tok = self.headers.get("Authorization", "")[7:]
        antworten = {
            "gut": (200, {"device_name": "x__y__invoice", "instanz": "codex", "scopes": ["invoice:write", "invoice:read"]}),
            "lesen": (200, {"device_name": "x__y__lesen", "instanz": None, "scopes": ["invoice:read"]}),
            "boese": (403, {"detail": "Invalid device token"}),
            "kaputt": (500, {}),
        }
        code, body = antworten.get(tok, (403, {}))
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


class Req:
    def __init__(self, h):
        self.headers = h


async def auth_teil():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    device_auth.TOKEN_CHECK_URL = f"http://127.0.0.1:{srv.server_port}/ich"
    schreiben = device_auth.braucht_scope("invoice:write")

    async def code(h):
        device_auth.cache_leeren()
        try:
            g = await schreiben(Req(h))
            return 200, g
        except HTTPException as e:
            return e.status_code, e.detail

    c, g = await code({})
    pruefe(c == 401, f"ohne Header: 401 -> {c}")
    c, g = await code({"Authorization": "Bearer gut"})
    pruefe(c == 200 and g.device_name == "x__y__invoice", f"gutes Token: 200 -> {c}")
    c, d = await code({"Authorization": "Bearer lesen"})
    pruefe(c == 403 and d["error"] == "scope_required", f"nur invoice:read beim Schreiben: 403 scope_required -> {c}")
    c, d = await code({"Authorization": "Bearer boese"})
    pruefe(c == 403 and d["error"] == "token_ungueltig", f"ungueltig: 403 -> {c}")
    c, d = await code({"Authorization": "Bearer kaputt"})
    pruefe(c == 503, f"Flows 500: 503 geschlossen -> {c}")
    srv.shutdown()
    srv.server_close()
    c, d = await code({"Authorization": "Bearer gut"})
    pruefe(c == 503, f"Flows nicht erreichbar: 503 geschlossen -> {c}")


asyncio.run(auth_teil())
asyncio.run(db_teil())
print("ERGEBNIS:", "alles gruen" if not FEHLER else f"{len(FEHLER)} rot")
raise SystemExit(1 if FEHLER else 0)
