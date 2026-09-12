from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import fetch_all, fetch_one

router = APIRouter(prefix="/openemr", tags=["openemr"])


@router.get("/patients/search")
async def search_patients(q: str = Query(..., min_length=1), session: AsyncSession = Depends(get_session)):
    term = f"%{q}%"
    rows = await fetch_all(
        session,
        """
        SELECT pid, fname, lname, dob, city
        FROM openemr.patients
        WHERE fname ILIKE :term OR lname ILIKE :term
        ORDER BY lname, fname, pid
        LIMIT 50
        """,
        {"term": term},
    )
    return rows


@router.get("/patients/{pid}/timeline")
async def patient_timeline(pid: int, session: AsyncSession = Depends(get_session)):
    patient = await fetch_one(session, "SELECT pid FROM openemr.patients WHERE pid = :pid", {"pid": pid})
    if not patient:
        raise HTTPException(status_code=404, detail="patient not found")

    encounters = await fetch_all(
        session,
        """
        SELECT id, date, reason
        FROM openemr.encounters
        WHERE pid = :pid
        ORDER BY date DESC NULLS LAST, id DESC
        """,
        {"pid": pid},
    )

    encounter_ids = [e["id"] for e in encounters]
    notes_by_encounter = {}
    if encounter_ids:
        notes = await fetch_all(
            session,
            """
            SELECT encounter_id, date, codetext, description
            FROM openemr.clinical_notes
            WHERE encounter_id = ANY(:ids)
            ORDER BY date DESC NULLS LAST, encounter_id DESC, id DESC
            """,
            {"ids": encounter_ids},
        )
        for n in notes:
            notes_by_encounter.setdefault(n["encounter_id"], []).append(
                {
                    "date": n["date"],
                    "codetext": n["codetext"],
                    "description": n["description"],
                }
            )

    for e in encounters:
        e["clinical_notes"] = notes_by_encounter.get(e["id"], [])

    return {"pid": pid, "encounters": encounters}


@router.get("/patients/{pid}/diagnoses")
async def patient_diagnoses(pid: int, active_only: bool = True, session: AsyncSession = Depends(get_session)):
    patient = await fetch_one(session, "SELECT pid FROM openemr.patients WHERE pid = :pid", {"pid": pid})
    if not patient:
        raise HTTPException(status_code=404, detail="patient not found")

    where = "WHERE pid = :pid"
    if active_only:
        where += " AND enddate IS NULL"

    rows = await fetch_all(
        session,
        f"""
        SELECT id, title, diagnosis, begdate, enddate
        FROM openemr.problems
        {where}
        ORDER BY begdate DESC NULLS LAST, id DESC
        """,
        {"pid": pid},
    )
    return rows
