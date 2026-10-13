"""Hangar Builder Log — a self-hosted build log for Van's Aircraft kits.

Server-rendered FastAPI + Jinja2, SQLite, and files on local disk. Nothing
leaves the machine it runs on.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from collections import OrderedDict
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import charts, db, exports, files, report
from .security import CSRF_FIELD, SecurityMiddleware, get_form

VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create the database and folders before serving the first request."""
    db.init_db(seed=os.environ.get("HBL_SEED", "1") != "0")
    files.ensure_dirs()
    yield


app = FastAPI(
    title="Hangar Builder Log",
    version=VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _asset_version() -> str:
    digest = hashlib.sha256()
    for name in ("app.css", "annotate.js"):
        path = os.path.join(STATIC_DIR, name)
        try:
            with open(path, "rb") as fh:
                digest.update(fh.read())
        except OSError:
            digest.update(name.encode())
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()

NAV = [
    ("/", "Dashboard", "gauge"),
    ("/log", "Build log", "list"),
    ("/kits", "Kits", "layers"),
    ("/photos", "Photos", "camera"),
    ("/inventory", "Inventory", "box"),
    ("/costs", "Costs", "dollar"),
    ("/documents", "Documents", "file"),
    ("/insights", "Insights", "chart"),
    ("/search", "Search", "search"),
    ("/settings", "Settings", "cog"),
]


# ------------------------------------------------------------------ helpers


def money(cents: int | float | None) -> str:
    return f"${(int(cents or 0)) / 100:,.2f}"


def hours_fmt(value: float | None) -> str:
    return f"{float(value or 0):,.1f}"


def to_float(value, default: float = 0.0) -> float:
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return default
        if ":" in text:  # accept 2:30 as 2.5 hours
            hh, mm = text.split(":", 1)
            return float(hh) + float(mm or 0) / 60
        return float(text)
    except (TypeError, ValueError):
        return default


def to_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_cents(value) -> int:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return 0
    try:
        return int(round(float(text) * 100))
    except ValueError:
        return 0


def to_date(value, default: str | None = None) -> str:
    text = str(value or "").strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        return default if default is not None else date.today().isoformat()


def opt_id(value) -> int | None:
    number = to_int(value, 0)
    return number if number > 0 else None


def render(
    request: Request, template: str, status_code: int = 200, **context
) -> HTMLResponse:
    project = db.project()
    base = {
        "request": request,
        "csrf_field": CSRF_FIELD,
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "asset_version": ASSET_VERSION,
        "nav": NAV,
        "path": request.url.path,
        "project": project,
        "version": VERSION,
        "money": money,
        "hours_fmt": hours_fmt,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "today": date.today().isoformat(),
    }
    base.update(context)
    return templates.TemplateResponse(
        request, template, base, status_code=status_code
    )


def back(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    url = path + (("?" + urlencode(params)) if params else "")
    return RedirectResponse(url, status_code=303)


def kits_list() -> list:
    return db.query("SELECT * FROM kits ORDER BY sort_order, id")


def sections_list() -> list:
    return db.query(
        "SELECT s.*, k.name AS kit_name FROM sections s"
        " JOIN kits k ON k.id = s.kit_id"
        " ORDER BY k.sort_order, s.sort_order, s.id"
    )


def contributors_list() -> list:
    return db.query("SELECT * FROM contributors ORDER BY name")


def active_timer():
    return db.one("SELECT * FROM timers WHERE id = 1")


def timer_elapsed(row) -> float:
    if row is None:
        return 0.0
    try:
        started = datetime.fromisoformat(row["started_at"])
    except ValueError:
        return 0.0
    return max(0.0, (db.utcnow() - started).total_seconds() / 3600)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    db.scalar("SELECT 1")
    return "ok"


# ---------------------------------------------------------------- dashboard


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    kits = db.kits_with_progress()
    overall = db.overall_percent()
    total_hours = float(db.scalar("SELECT SUM(hours) FROM entries", (), 0.0) or 0)
    total_entries = int(db.scalar("SELECT COUNT(*) FROM entries") or 0)
    total_photos = int(
        db.scalar("SELECT COUNT(*) FROM attachments WHERE kind = 'photo'") or 0
    )
    total_cost = int(db.scalar("SELECT SUM(amount_cents) FROM costs", (), 0) or 0)

    since = (date.today() - timedelta(days=30)).isoformat()
    hours_30 = float(
        db.scalar("SELECT SUM(hours) FROM entries WHERE work_date >= ?", (since,), 0.0)
        or 0
    )
    days_30 = int(
        db.scalar(
            "SELECT COUNT(DISTINCT work_date) FROM entries WHERE work_date >= ?",
            (since,),
        )
        or 0
    )

    recent = db.query(
        "SELECT e.*, k.name AS kit_name, s.name AS section_name,"
        " (SELECT COUNT(*) FROM attachments a WHERE a.entry_id = e.id"
        "  AND a.kind = 'photo') AS photo_count"
        " FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " LEFT JOIN sections s ON s.id = e.section_id"
        " ORDER BY e.work_date DESC, e.id DESC LIMIT 6"
    )
    latest_photos = db.query(
        "SELECT * FROM attachments WHERE kind = 'photo' ORDER BY id DESC LIMIT 8"
    )

    months = month_series(6)
    hours_chart = charts.bar_chart(months, unit="hours")
    kit_chart = charts.hbar_chart(
        [(k["name"], k["percent"]) for k in kits], unit="percent"
    )

    inv = inventory_summary()
    target = float(db.project()["target_hours"] or 0)
    remaining = max(0.0, target - total_hours) if target else 0.0
    pace = hours_30 / 30 if hours_30 else 0.0
    finish_estimate = ""
    if target and pace > 0 and remaining > 0:
        finish_estimate = (
            date.today() + timedelta(days=int(remaining / pace))
        ).strftime("%b %Y")

    return render(
        request,
        "dashboard.html",
        title="Dashboard",
        kits=kits,
        overall=overall,
        overall_ring=charts.progress_ring(overall, "complete", 150),
        total_hours=total_hours,
        total_entries=total_entries,
        total_photos=total_photos,
        total_cost=total_cost,
        hours_30=hours_30,
        days_30=days_30,
        recent=recent,
        latest_photos=latest_photos,
        hours_chart=hours_chart,
        kit_chart=kit_chart,
        inv=inv,
        timer=active_timer(),
        timer_hours=timer_elapsed(active_timer()),
        target=target,
        remaining=remaining,
        finish_estimate=finish_estimate,
        all_kits=kits_list(),
    )


def month_series(count: int) -> list[tuple[str, float]]:
    """Hours logged per month for the last `count` months, oldest first."""
    today = date.today().replace(day=1)
    buckets: OrderedDict[str, float] = OrderedDict()
    cursor = today
    months = []
    for _ in range(count):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    for month in reversed(months):
        buckets[month.strftime("%Y-%m")] = 0.0
    rows = db.query(
        "SELECT substr(work_date, 1, 7) AS ym, SUM(hours) AS hours"
        " FROM entries GROUP BY ym"
    )
    for row in rows:
        if row["ym"] in buckets:
            buckets[row["ym"]] = round(float(row["hours"] or 0), 1)
    return [
        (datetime.strptime(key, "%Y-%m").strftime("%b"), value)
        for key, value in buckets.items()
    ]


def inventory_summary() -> dict:
    counts = {status: 0 for status in db.INVENTORY_STATUS}
    for row in db.query("SELECT status, COUNT(*) AS n FROM inventory GROUP BY status"):
        if row["status"] in counts:
            counts[row["status"]] = int(row["n"])
    total = sum(counts.values())
    received = counts["received"]
    return {
        "counts": counts,
        "total": total,
        "received": received,
        "open": total - received,
        "percent": round(received / total * 100, 1) if total else 0.0,
        "meter": charts.stacked_meter(
            [
                ("Received", counts["received"], "seg-ok"),
                ("Partial", counts["partial"], "seg-warn"),
                ("Pending", counts["pending"], "seg-idle"),
                ("Backordered", counts["backordered"], "seg-warn"),
                ("Missing", counts["missing"], "seg-bad"),
                ("Damaged", counts["damaged"], "seg-bad"),
            ]
        ),
    }


# ---------------------------------------------------------------- build log


@app.get("/log", response_class=HTMLResponse)
def log_list(request: Request):
    params = request.query_params
    q = params.get("q", "").strip()
    kit_id = opt_id(params.get("kit"))
    contributor_id = opt_id(params.get("contributor"))
    tag = params.get("tag", "").strip()
    start = params.get("start", "").strip()[:10]
    end = params.get("end", "").strip()[:10]
    page = max(1, to_int(params.get("page"), 1))
    per_page = 25

    where = []
    args: list = []
    if q:
        where.append("(e.title LIKE ? OR e.notes LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if kit_id:
        where.append("e.kit_id = ?")
        args.append(kit_id)
    if contributor_id:
        where.append(
            "EXISTS (SELECT 1 FROM entry_contributors ec WHERE ec.entry_id = e.id"
            " AND ec.contributor_id = ?)"
        )
        args.append(contributor_id)
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
            " WHERE et.entry_id = e.id AND t.name = ?)"
        )
        args.append(tag)
    if start:
        where.append("e.work_date >= ?")
        args.append(start)
    if end:
        where.append("e.work_date <= ?")
        args.append(end)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = int(db.scalar(f"SELECT COUNT(*) FROM entries e{clause}", args) or 0)
    sum_hours = float(
        db.scalar(f"SELECT SUM(e.hours) FROM entries e{clause}", args, 0.0) or 0
    )
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = db.query(
        "SELECT e.*, k.name AS kit_name, s.name AS section_name,"
        " (SELECT COUNT(*) FROM attachments a WHERE a.entry_id = e.id"
        "  AND a.kind = 'photo') AS photo_count,"
        " (SELECT COUNT(*) FROM attachments a WHERE a.entry_id = e.id"
        "  AND a.kind = 'document') AS doc_count"
        " FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " LEFT JOIN sections s ON s.id = e.section_id"
        f"{clause} ORDER BY e.work_date DESC, e.id DESC LIMIT ? OFFSET ?",
        args + [per_page, (page - 1) * per_page],
    )

    filter_args = {
        k: v
        for k, v in {
            "q": q,
            "kit": kit_id or "",
            "contributor": contributor_id or "",
            "tag": tag,
            "start": start,
            "end": end,
        }.items()
        if v
    }

    return render(
        request,
        "log_list.html",
        title="Build log",
        rows=rows,
        total=total,
        sum_hours=sum_hours,
        page=page,
        pages=pages,
        all_kits=kits_list(),
        all_contributors=contributors_list(),
        all_tags=db.query("SELECT name FROM tags ORDER BY name"),
        filters={
            "q": q,
            "kit": kit_id,
            "contributor": contributor_id,
            "tag": tag,
            "start": start,
            "end": end,
        },
        query_string=urlencode(filter_args),
    )


@app.get("/log/new", response_class=HTMLResponse)
def log_new(request: Request):
    timer = active_timer()
    prefill = {
        "work_date": date.today().isoformat(),
        "hours": round(timer_elapsed(timer), 2) if timer else "",
        "kit_id": timer["kit_id"] if timer else None,
        "section_id": timer["section_id"] if timer else None,
        "title": (timer["title"] if timer else "") or "",
    }
    return render(
        request,
        "log_form.html",
        title="New log entry",
        entry=None,
        prefill=prefill,
        all_kits=kits_list(),
        all_sections=sections_list(),
        all_contributors=contributors_list(),
        entry_contributors={},
        entry_tags="",
        from_timer=bool(timer),
    )


@app.get("/log/{entry_id}", response_class=HTMLResponse)
def log_detail(request: Request, entry_id: int):
    entry = db.one(
        "SELECT e.*, k.name AS kit_name, s.name AS section_name"
        " FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " LEFT JOIN sections s ON s.id = e.section_id"
        " WHERE e.id = ?",
        (entry_id,),
    )
    if entry is None:
        return render(
            request, "missing.html", status_code=404, title="Not found", what="log entry"
        )
    photos = db.query(
        "SELECT * FROM attachments WHERE entry_id = ? AND kind = 'photo' ORDER BY id",
        (entry_id,),
    )
    docs = db.query(
        "SELECT * FROM attachments WHERE entry_id = ? AND kind = 'document' ORDER BY id",
        (entry_id,),
    )
    people = db.query(
        "SELECT c.name, ec.hours FROM entry_contributors ec"
        " JOIN contributors c ON c.id = ec.contributor_id"
        " WHERE ec.entry_id = ? ORDER BY c.name",
        (entry_id,),
    )
    tags = db.query(
        "SELECT t.name FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
        " WHERE et.entry_id = ? ORDER BY t.name",
        (entry_id,),
    )
    return render(
        request,
        "log_detail.html",
        title=entry["title"],
        entry=entry,
        photos=photos,
        docs=docs,
        people=people,
        tags=tags,
        human_size=files.human_size,
    )


@app.get("/log/{entry_id}/edit", response_class=HTMLResponse)
def log_edit(request: Request, entry_id: int):
    entry = db.one("SELECT * FROM entries WHERE id = ?", (entry_id,))
    if entry is None:
        return render(
            request, "missing.html", status_code=404, title="Not found", what="log entry"
        )
    hours_by_person = {
        row["contributor_id"]: float(row["hours"] or 0)
        for row in db.query(
            "SELECT contributor_id, hours FROM entry_contributors WHERE entry_id = ?",
            (entry_id,),
        )
    }
    tags = ", ".join(
        row["name"]
        for row in db.query(
            "SELECT t.name FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
            " WHERE et.entry_id = ? ORDER BY t.name",
            (entry_id,),
        )
    )
    return render(
        request,
        "log_form.html",
        title="Edit entry",
        entry=entry,
        prefill=None,
        all_kits=kits_list(),
        all_sections=sections_list(),
        all_contributors=contributors_list(),
        entry_contributors=hours_by_person,
        entry_tags=tags,
        from_timer=False,
    )


async def _save_entry(request: Request, entry_id: int | None) -> int:
    form = await get_form(request)
    work_date = to_date(form.get("work_date"))
    title = str(form.get("title", "")).strip()[:200] or "Build session"
    notes = str(form.get("notes", "")).strip()
    hours = max(0.0, to_float(form.get("hours"), 0.0))
    kit_id = opt_id(form.get("kit_id"))
    section_id = opt_id(form.get("section_id"))
    if section_id:
        owner = db.one("SELECT kit_id FROM sections WHERE id = ?", (section_id,))
        if owner is None:
            section_id = None
        elif kit_id is None:
            kit_id = owner["kit_id"]
        elif owner["kit_id"] != kit_id:
            section_id = None

    if entry_id:
        db.execute(
            "UPDATE entries SET work_date = ?, title = ?, notes = ?, kit_id = ?,"
            " section_id = ?, hours = ?, updated_at = ? WHERE id = ?",
            (work_date, title, notes, kit_id, section_id, hours, db.now(), entry_id),
        )
    else:
        entry_id = db.execute(
            "INSERT INTO entries (work_date, title, notes, kit_id, section_id, hours,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (work_date, title, notes, kit_id, section_id, hours, db.now(), db.now()),
        )

    db.execute("DELETE FROM entry_contributors WHERE entry_id = ?", (entry_id,))
    for contributor in contributors_list():
        field = f"contrib_{contributor['id']}"
        raw = str(form.get(field, "")).strip()
        if raw == "":
            continue
        db.execute(
            "INSERT INTO entry_contributors (entry_id, contributor_id, hours)"
            " VALUES (?, ?, ?)",
            (entry_id, contributor["id"], max(0.0, to_float(raw, 0.0))),
        )

    db.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
    for raw_tag in str(form.get("tags", "")).split(","):
        name = raw_tag.strip()[:40]
        if not name:
            continue
        existing = db.one("SELECT id FROM tags WHERE name = ?", (name,))
        tag_id = existing["id"] if existing else db.execute(
            "INSERT INTO tags (name) VALUES (?)", (name,)
        )
        db.execute(
            "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            (entry_id, tag_id),
        )

    uploads = form.getlist("photos") + form.getlist("documents")
    for upload in uploads:
        filename = getattr(upload, "filename", "")
        if not filename:
            continue
        data = await upload.read()
        if not data:
            continue
        try:
            files.save_upload(
                data, filename, getattr(upload, "content_type", ""), entry_id=entry_id
            )
        except files.UploadError:
            continue
    return entry_id


@app.post("/log/new")
async def log_create(request: Request):
    entry_id = await _save_entry(request, None)
    form = await get_form(request)
    if str(form.get("stop_timer", "")) == "1":
        db.execute("DELETE FROM timers WHERE id = 1")
    return back(f"/log/{entry_id}", "Entry saved.")


@app.post("/log/{entry_id}/edit")
async def log_update(request: Request, entry_id: int):
    if db.one("SELECT id FROM entries WHERE id = ?", (entry_id,)) is None:
        return back("/log", err="That entry no longer exists.")
    await _save_entry(request, entry_id)
    return back(f"/log/{entry_id}", "Entry updated.")


@app.post("/log/{entry_id}/delete")
async def log_delete(request: Request, entry_id: int):
    for row in db.query(
        "SELECT id FROM attachments WHERE entry_id = ?", (entry_id,)
    ):
        files.delete_attachment(row["id"])
    db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    return back("/log", "Entry deleted.")


@app.post("/log/{entry_id}/upload")
async def log_upload(request: Request, entry_id: int):
    if db.one("SELECT id FROM entries WHERE id = ?", (entry_id,)) is None:
        return back("/log", err="That entry no longer exists.")
    form = await get_form(request)
    saved = 0
    problem = ""
    for upload in form.getlist("attachments"):
        filename = getattr(upload, "filename", "")
        if not filename:
            continue
        data = await upload.read()
        try:
            files.save_upload(
                data, filename, getattr(upload, "content_type", ""), entry_id=entry_id
            )
            saved += 1
        except files.UploadError as exc:
            problem = str(exc)
    if saved:
        return back(f"/log/{entry_id}", f"Added {saved} file(s).")
    return back(f"/log/{entry_id}", err=problem or "Nothing was uploaded.")


# -------------------------------------------------------------------- timer


@app.post("/timer/start")
async def timer_start(request: Request):
    form = await get_form(request)
    db.execute("DELETE FROM timers WHERE id = 1")
    db.execute(
        "INSERT INTO timers (id, started_at, kit_id, section_id, title)"
        " VALUES (1, ?, ?, ?, ?)",
        (
            db.now(),
            opt_id(form.get("kit_id")),
            opt_id(form.get("section_id")),
            str(form.get("title", "")).strip()[:200],
        ),
    )
    return back("/", "Timer started.")


@app.post("/timer/stop")
async def timer_stop(request: Request):
    timer = active_timer()
    if timer is None:
        return back("/", err="No timer was running.")
    return back("/log/new", "Timer stopped. Check the hours and save.")


@app.post("/timer/cancel")
async def timer_cancel(request: Request):
    db.execute("DELETE FROM timers WHERE id = 1")
    return back("/", "Timer discarded.")


# --------------------------------------------------------------------- kits


@app.get("/kits", response_class=HTMLResponse)
def kits_page(request: Request):
    kits = db.kits_with_progress()
    by_kit = {}
    for section in sections_list():
        by_kit.setdefault(section["kit_id"], []).append(section)
    return render(
        request,
        "kits.html",
        title="Kits",
        kits=kits,
        sections_by_kit=by_kit,
        overall=db.overall_percent(),
        ring=charts.progress_ring(db.overall_percent(), "overall", 120),
    )


@app.post("/kits/add")
async def kits_add(request: Request):
    form = await get_form(request)
    name = str(form.get("name", "")).strip()[:60]
    if not name:
        return back("/kits", err="Give the kit a name.")
    order = int(db.scalar("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM kits") or 1)
    kit_id = db.execute(
        "INSERT INTO kits (name, sort_order) VALUES (?, ?)", (name, order)
    )
    for raw in str(form.get("sections", "")).split(","):
        section = raw.strip()[:80]
        if section:
            db.execute(
                "INSERT INTO sections (kit_id, name, sort_order) VALUES (?, ?, ?)",
                (kit_id, section, 0),
            )
    return back("/kits", f"Added {name}.")


@app.post("/kits/{kit_id}/update")
async def kits_update(request: Request, kit_id: int):
    form = await get_form(request)
    override_raw = str(form.get("percent_override", "")).strip()
    override = None
    if override_raw != "":
        override = max(0.0, min(100.0, to_float(override_raw, 0.0)))
    db.execute(
        "UPDATE kits SET name = ?, status = ?, notes = ?, percent_override = ?,"
        " sort_order = ? WHERE id = ?",
        (
            str(form.get("name", "")).strip()[:60] or "Kit",
            str(form.get("status", "not_started"))[:20],
            str(form.get("notes", "")).strip(),
            override,
            to_int(form.get("sort_order"), 0),
            kit_id,
        ),
    )
    return back("/kits", "Kit updated.")


@app.post("/kits/{kit_id}/delete")
async def kits_delete(request: Request, kit_id: int):
    db.execute("DELETE FROM kits WHERE id = ?", (kit_id,))
    return back("/kits", "Kit removed. Log entries were kept.")


@app.post("/kits/{kit_id}/sections/add")
async def sections_add(request: Request, kit_id: int):
    form = await get_form(request)
    name = str(form.get("name", "")).strip()[:80]
    if not name:
        return back("/kits", err="Give the section a name.")
    order = int(
        db.scalar(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM sections WHERE kit_id = ?",
            (kit_id,),
        )
        or 1
    )
    db.execute(
        "INSERT INTO sections (kit_id, name, sort_order, weight) VALUES (?, ?, ?, ?)",
        (kit_id, name, order, max(0.1, to_float(form.get("weight"), 1.0))),
    )
    return back("/kits", f"Added {name}.")


@app.post("/sections/update")
async def sections_update(request: Request):
    """Save every section percentage on the page in one submit."""
    form = await get_form(request)
    changed = 0
    for key, value in form.multi_items():
        if not key.startswith("percent_"):
            continue
        section_id = to_int(key[len("percent_") :], 0)
        if section_id <= 0:
            continue
        percent = max(0.0, min(100.0, to_float(value, 0.0)))
        db.execute(
            "UPDATE sections SET percent = ? WHERE id = ?", (percent, section_id)
        )
        changed += 1
    for key, value in form.multi_items():
        if key.startswith("weight_"):
            section_id = to_int(key[len("weight_") :], 0)
            if section_id > 0:
                db.execute(
                    "UPDATE sections SET weight = ? WHERE id = ?",
                    (max(0.1, to_float(value, 1.0)), section_id),
                )
    update_kit_statuses()
    return back("/kits", f"Saved {changed} section(s).")


@app.post("/sections/{section_id}/delete")
async def sections_delete(request: Request, section_id: int):
    db.execute("DELETE FROM sections WHERE id = ?", (section_id,))
    return back("/kits", "Section removed.")


def update_kit_statuses() -> None:
    for kit in db.kits_with_progress():
        status = (
            "complete"
            if kit["percent"] >= 100
            else "in_progress"
            if kit["percent"] > 0
            else "not_started"
        )
        db.execute("UPDATE kits SET status = ? WHERE id = ?", (status, kit["id"]))


# ------------------------------------------------------------------- photos


@app.get("/photos", response_class=HTMLResponse)
def photos_page(request: Request):
    kit_id = opt_id(request.query_params.get("kit"))
    q = request.query_params.get("q", "").strip()
    where = ["a.kind = 'photo'"]
    args: list = []
    if kit_id:
        where.append("e.kit_id = ?")
        args.append(kit_id)
    if q:
        where.append("(a.caption LIKE ? OR e.title LIKE ? OR a.original_name LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    rows = db.query(
        "SELECT a.*, e.work_date, e.title AS entry_title, k.name AS kit_name,"
        " (SELECT COUNT(*) FROM annotations an WHERE an.attachment_id = a.id) AS pins"
        " FROM attachments a"
        " LEFT JOIN entries e ON e.id = a.entry_id"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        f" WHERE {' AND '.join(where)} ORDER BY a.id DESC",
        args,
    )
    return render(
        request,
        "photos.html",
        title="Photos",
        rows=rows,
        all_kits=kits_list(),
        kit_id=kit_id,
        q=q,
        recent_entries=db.query(
            "SELECT id, work_date, title FROM entries"
            " ORDER BY work_date DESC, id DESC LIMIT 30"
        ),
    )


@app.post("/photos/upload")
async def photos_upload(request: Request):
    form = await get_form(request)
    entry_id = opt_id(form.get("entry_id"))
    caption = str(form.get("caption", "")).strip()[:300]
    saved = 0
    problem = ""
    for upload in form.getlist("photos"):
        filename = getattr(upload, "filename", "")
        if not filename:
            continue
        data = await upload.read()
        try:
            files.save_upload(
                data,
                filename,
                getattr(upload, "content_type", ""),
                entry_id=entry_id,
                caption=caption,
                kind="photo",
            )
            saved += 1
        except files.UploadError as exc:
            problem = str(exc)
    if saved:
        return back("/photos", f"Uploaded {saved} photo(s).")
    return back("/photos", err=problem or "No photos were uploaded.")


@app.get("/photos/{photo_id}", response_class=HTMLResponse)
def photo_detail(request: Request, photo_id: int):
    photo = db.one(
        "SELECT a.*, e.work_date, e.title AS entry_title, e.id AS entry_ref,"
        " k.name AS kit_name FROM attachments a"
        " LEFT JOIN entries e ON e.id = a.entry_id"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " WHERE a.id = ?",
        (photo_id,),
    )
    if photo is None:
        return render(
            request, "missing.html", status_code=404, title="Not found", what="photo"
        )
    pins = db.query(
        "SELECT * FROM annotations WHERE attachment_id = ? ORDER BY number, id",
        (photo_id,),
    )
    return render(
        request,
        "photo_detail.html",
        title=photo["caption"] or photo["original_name"],
        photo=photo,
        pins=pins,
        pins_json=json.dumps(
            [
                {
                    "number": p["number"],
                    "x": round(float(p["x"]), 3),
                    "y": round(float(p["y"]), 3),
                    "label": p["label"],
                }
                for p in pins
            ]
        ),
        all_entries=db.query(
            "SELECT id, work_date, title FROM entries ORDER BY work_date DESC, id DESC"
        ),
        human_size=files.human_size,
    )


@app.post("/photos/{photo_id}/save")
async def photo_save(request: Request, photo_id: int):
    photo = db.one("SELECT id FROM attachments WHERE id = ?", (photo_id,))
    if photo is None:
        return back("/photos", err="That photo no longer exists.")
    form = await get_form(request)
    db.execute(
        "UPDATE attachments SET caption = ?, entry_id = ? WHERE id = ?",
        (
            str(form.get("caption", "")).strip()[:300],
            opt_id(form.get("entry_id")),
            photo_id,
        ),
    )

    raw = str(form.get("pins", "")).strip()
    if raw:
        try:
            pins = json.loads(raw)
        except json.JSONDecodeError:
            pins = None
        if isinstance(pins, list):
            db.execute("DELETE FROM annotations WHERE attachment_id = ?", (photo_id,))
            for index, pin in enumerate(pins[:60], start=1):
                if not isinstance(pin, dict):
                    continue
                db.execute(
                    "INSERT INTO annotations (attachment_id, number, x, y, label)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        photo_id,
                        to_int(pin.get("number"), index),
                        max(0.0, min(100.0, to_float(pin.get("x"), 50.0))),
                        max(0.0, min(100.0, to_float(pin.get("y"), 50.0))),
                        str(pin.get("label", ""))[:200],
                    ),
                )
    return back(f"/photos/{photo_id}", "Photo saved.")


@app.post("/photos/{photo_id}/delete")
async def photo_delete(request: Request, photo_id: int):
    files.delete_attachment(photo_id)
    return back("/photos", "Photo deleted.")


# ---------------------------------------------------------------- inventory


@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request):
    params = request.query_params
    kit_id = opt_id(params.get("kit"))
    status = params.get("status", "").strip()
    q = params.get("q", "").strip()
    where = []
    args: list = []
    if kit_id:
        where.append("i.kit_id = ?")
        args.append(kit_id)
    if status in db.INVENTORY_STATUS:
        where.append("i.status = ?")
        args.append(status)
    if q:
        where.append("(i.part_number LIKE ? OR i.description LIKE ? OR i.notes LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.query(
        "SELECT i.*, k.name AS kit_name FROM inventory i"
        " LEFT JOIN kits k ON k.id = i.kit_id"
        f"{clause} ORDER BY i.status = 'received', k.sort_order, i.part_number, i.id",
        args,
    )
    return render(
        request,
        "inventory.html",
        title="Inventory",
        rows=rows,
        all_kits=kits_list(),
        statuses=db.INVENTORY_STATUS,
        filters={"kit": kit_id, "status": status, "q": q},
        inv=inventory_summary(),
    )


@app.post("/inventory/add")
async def inventory_add(request: Request):
    form = await get_form(request)
    part = str(form.get("part_number", "")).strip()[:60]
    desc = str(form.get("description", "")).strip()[:300]
    if not part and not desc:
        return back("/inventory", err="Enter a part number or a description.")
    qty = max(0.0, to_float(form.get("qty_expected"), 1.0))
    got = max(0.0, to_float(form.get("qty_received"), 0.0))
    status = str(form.get("status", "")).strip()
    if status not in db.INVENTORY_STATUS:
        status = "received" if got >= qty and qty > 0 else "partial" if got else "pending"
    db.execute(
        "INSERT INTO inventory (kit_id, part_number, description, qty_expected,"
        " qty_received, status, location, received_on, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            opt_id(form.get("kit_id")),
            part,
            desc,
            qty,
            got,
            status,
            str(form.get("location", "")).strip()[:60],
            to_date(form.get("received_on"), ""),
            str(form.get("notes", "")).strip(),
        ),
    )
    return back("/inventory", "Part added.")


@app.post("/inventory/save")
async def inventory_save(request: Request):
    """Save the received quantities and statuses shown on the page."""
    form = await get_form(request)
    ids = {
        to_int(key.split("_", 1)[1], 0)
        for key in form.keys()
        if key.startswith("received_")
    }
    changed = 0
    for item_id in sorted(i for i in ids if i > 0):
        row = db.one("SELECT * FROM inventory WHERE id = ?", (item_id,))
        if row is None:
            continue
        got = max(0.0, to_float(form.get(f"received_{item_id}"), 0.0))
        status = str(form.get(f"status_{item_id}", row["status"]))
        if status not in db.INVENTORY_STATUS:
            status = row["status"]
        received_on = row["received_on"]
        if got > 0 and not received_on:
            received_on = date.today().isoformat()
        db.execute(
            "UPDATE inventory SET qty_received = ?, status = ?, received_on = ?,"
            " location = ? WHERE id = ?",
            (
                got,
                status,
                received_on,
                str(form.get(f"location_{item_id}", row["location"])).strip()[:60],
                item_id,
            ),
        )
        changed += 1
    return back("/inventory", f"Saved {changed} line(s).")


@app.post("/inventory/{item_id}/delete")
async def inventory_delete(request: Request, item_id: int):
    db.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
    return back("/inventory", "Line removed.")


@app.post("/inventory/import")
async def inventory_import(request: Request):
    form = await get_form(request)
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return back("/inventory", err="Choose a CSV file first.")
    data = await upload.read()
    try:
        message = exports.import_inventory_csv(data)
    except Exception as exc:
        return back("/inventory", err=f"Import failed: {exc}")
    return back("/inventory", message)


# -------------------------------------------------------------------- costs


@app.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request):
    params = request.query_params
    kit_id = opt_id(params.get("kit"))
    category = params.get("category", "").strip()
    year = params.get("year", "").strip()
    where = []
    args: list = []
    if kit_id:
        where.append("c.kit_id = ?")
        args.append(kit_id)
    if category:
        where.append("c.category = ?")
        args.append(category)
    if year:
        where.append("substr(c.spent_on, 1, 4) = ?")
        args.append(year[:4])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.query(
        "SELECT c.*, k.name AS kit_name, a.stored_name AS receipt_name,"
        " a.original_name AS receipt_label FROM costs c"
        " LEFT JOIN kits k ON k.id = c.kit_id"
        " LEFT JOIN attachments a ON a.id = c.attachment_id"
        f"{clause} ORDER BY c.spent_on DESC, c.id DESC",
        args,
    )
    shown = sum(int(r["amount_cents"] or 0) for r in rows)
    total = int(db.scalar("SELECT SUM(amount_cents) FROM costs", (), 0) or 0)
    by_category = db.query(
        "SELECT category, SUM(amount_cents) AS cents FROM costs"
        " GROUP BY category ORDER BY cents DESC"
    )
    by_kit = db.query(
        "SELECT COALESCE(k.name, 'Unassigned') AS name, SUM(c.amount_cents) AS cents"
        " FROM costs c LEFT JOIN kits k ON k.id = c.kit_id"
        " GROUP BY k.id ORDER BY cents DESC"
    )
    budget = int(db.project()["budget_cents"] or 0)
    years = [
        r["y"]
        for r in db.query(
            "SELECT DISTINCT substr(spent_on, 1, 4) AS y FROM costs ORDER BY y DESC"
        )
    ]
    return render(
        request,
        "costs.html",
        title="Costs",
        rows=rows,
        shown=shown,
        total=total,
        budget=budget,
        budget_percent=round(total / budget * 100, 1) if budget else 0.0,
        categories=db.CATEGORIES,
        all_kits=kits_list(),
        years=years,
        filters={"kit": kit_id, "category": category, "year": year},
        category_chart=charts.hbar_chart(
            [(r["category"], float(r["cents"] or 0) / 100) for r in by_category],
            unit="dollars",
        ),
        kit_chart=charts.hbar_chart(
            [(r["name"], float(r["cents"] or 0) / 100) for r in by_kit],
            unit="dollars",
        ),
        month_chart=charts.bar_chart(cost_month_series(8), unit="dollars"),
    )


def cost_month_series(count: int) -> list[tuple[str, float]]:
    today = date.today().replace(day=1)
    months = []
    cursor = today
    for _ in range(count):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    buckets: OrderedDict[str, float] = OrderedDict(
        (m.strftime("%Y-%m"), 0.0) for m in reversed(months)
    )
    for row in db.query(
        "SELECT substr(spent_on, 1, 7) AS ym, SUM(amount_cents) AS cents"
        " FROM costs GROUP BY ym"
    ):
        if row["ym"] in buckets:
            buckets[row["ym"]] = round(float(row["cents"] or 0) / 100, 2)
    return [
        (datetime.strptime(k, "%Y-%m").strftime("%b"), v) for k, v in buckets.items()
    ]


@app.post("/costs/add")
async def costs_add(request: Request):
    form = await get_form(request)
    cents = to_cents(form.get("amount"))
    if cents == 0:
        return back("/costs", err="Enter an amount.")
    category = str(form.get("category", "Other")).strip()
    if category not in db.CATEGORIES:
        category = "Other"
    attachment_id = None
    upload = form.get("receipt")
    if upload is not None and getattr(upload, "filename", ""):
        data = await upload.read()
        try:
            attachment_id = files.save_upload(
                data,
                upload.filename,
                getattr(upload, "content_type", ""),
                doc_type="Receipt",
            )
        except files.UploadError as exc:
            return back("/costs", err=str(exc))
    db.execute(
        "INSERT INTO costs (spent_on, vendor, category, description, amount_cents,"
        " kit_id, attachment_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            to_date(form.get("spent_on")),
            str(form.get("vendor", "")).strip()[:120],
            category,
            str(form.get("description", "")).strip()[:300],
            cents,
            opt_id(form.get("kit_id")),
            attachment_id,
            str(form.get("notes", "")).strip(),
        ),
    )
    return back("/costs", "Cost added.")


@app.post("/costs/{cost_id}/delete")
async def costs_delete(request: Request, cost_id: int):
    db.execute("DELETE FROM costs WHERE id = ?", (cost_id,))
    return back("/costs", "Cost removed.")


# ---------------------------------------------------------------- documents


@app.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request):
    q = request.query_params.get("q", "").strip()
    where = ["a.kind = 'document'"]
    args: list = []
    if q:
        where.append("(a.original_name LIKE ? OR a.caption LIKE ? OR a.doc_type LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    rows = db.query(
        "SELECT a.*, e.title AS entry_title FROM attachments a"
        " LEFT JOIN entries e ON e.id = a.entry_id"
        f" WHERE {' AND '.join(where)} ORDER BY a.id DESC",
        args,
    )
    return render(
        request,
        "documents.html",
        title="Documents",
        rows=rows,
        q=q,
        human_size=files.human_size,
        usage=files.disk_usage(),
        doc_types=[
            "Plans",
            "Instructions",
            "Invoice",
            "Receipt",
            "Packing list",
            "Service bulletin",
            "Weight and balance",
            "Registration",
            "Inspection",
            "Insurance",
            "Other",
        ],
    )


@app.post("/documents/upload")
async def documents_upload(request: Request):
    form = await get_form(request)
    doc_type = str(form.get("doc_type", "")).strip()[:60]
    caption = str(form.get("caption", "")).strip()[:300]
    saved = 0
    problem = ""
    for upload in form.getlist("documents"):
        filename = getattr(upload, "filename", "")
        if not filename:
            continue
        data = await upload.read()
        try:
            files.save_upload(
                data,
                filename,
                getattr(upload, "content_type", ""),
                caption=caption,
                doc_type=doc_type,
                kind="document",
            )
            saved += 1
        except files.UploadError as exc:
            problem = str(exc)
    if saved:
        return back("/documents", f"Uploaded {saved} file(s).")
    return back("/documents", err=problem or "No files were uploaded.")


@app.post("/documents/{doc_id}/save")
async def documents_save(request: Request, doc_id: int):
    form = await get_form(request)
    db.execute(
        "UPDATE attachments SET caption = ?, doc_type = ? WHERE id = ?",
        (
            str(form.get("caption", "")).strip()[:300],
            str(form.get("doc_type", "")).strip()[:60],
            doc_id,
        ),
    )
    return back("/documents", "Document updated.")


@app.post("/documents/{doc_id}/delete")
async def documents_delete(request: Request, doc_id: int):
    files.delete_attachment(doc_id)
    return back("/documents", "Document deleted.")


# -------------------------------------------------------------- file serving


@app.get("/files/{stored_name}")
def serve_file(stored_name: str, download: int = 0):
    row = db.one(
        "SELECT * FROM attachments WHERE stored_name = ?",
        (os.path.basename(stored_name),),
    )
    if row is None:
        return PlainTextResponse("Not found", status_code=404)
    try:
        path = files.stored_path(row["stored_name"])
    except files.UploadError:
        return PlainTextResponse("Not found", status_code=404)
    if not os.path.exists(path):
        return PlainTextResponse("Not found", status_code=404)
    inline = row["mime"] in files.PHOTO_TYPES or row["mime"] == "application/pdf"
    disposition = "inline" if inline and not download else "attachment"
    name = quote(row["original_name"])
    return FileResponse(
        path,
        media_type=row["mime"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename=\"{name}\"",
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/thumbs/{stored_name}")
def serve_thumb(stored_name: str):
    safe = os.path.basename(stored_name)
    row = db.one("SELECT stored_name FROM attachments WHERE stored_name = ?", (safe,))
    if row is None:
        return PlainTextResponse("Not found", status_code=404)
    path = files.thumb_path(safe)
    if not os.path.exists(path):
        try:
            files.make_thumb(files.stored_path(safe), safe)
        except Exception:
            return PlainTextResponse("Not found", status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=604800"},
    )


# ----------------------------------------------------------------- insights


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request):
    total_hours = float(db.scalar("SELECT SUM(hours) FROM entries", (), 0.0) or 0)
    sessions = int(db.scalar("SELECT COUNT(*) FROM entries") or 0)
    work_days = int(db.scalar("SELECT COUNT(DISTINCT work_date) FROM entries") or 0)
    first = db.scalar("SELECT MIN(work_date) FROM entries", (), "")
    last = db.scalar("SELECT MAX(work_date) FROM entries", (), "")

    span_days = 0
    if first and last:
        try:
            span_days = (
                datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")
            ).days + 1
        except ValueError:
            span_days = 0

    by_month = db.query(
        "SELECT substr(work_date, 1, 7) AS ym, SUM(hours) AS hours, COUNT(*) AS n"
        " FROM entries GROUP BY ym ORDER BY ym"
    )
    cumulative = []
    running = 0.0
    for row in by_month:
        running += float(row["hours"] or 0)
        label = datetime.strptime(row["ym"], "%Y-%m").strftime("%b %y")
        cumulative.append((label, round(running, 1)))

    by_kit = [(k["name"], k["hours"]) for k in db.kits_with_progress()]
    by_person = db.query(
        "SELECT c.name, COALESCE(SUM(ec.hours), 0) AS hours,"
        " COUNT(ec.entry_id) AS sessions FROM contributors c"
        " LEFT JOIN entry_contributors ec ON ec.contributor_id = c.id"
        " GROUP BY c.id ORDER BY hours DESC, c.name"
    )
    by_weekday_rows = db.query(
        "SELECT CAST(strftime('%w', work_date) AS INTEGER) AS dow,"
        " SUM(hours) AS hours FROM entries GROUP BY dow"
    )
    weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    weekday_map = {int(r["dow"]): float(r["hours"] or 0) for r in by_weekday_rows}
    by_weekday = [(name, round(weekday_map.get(i, 0.0), 1)) for i, name in enumerate(weekday_names)]

    top_sessions = db.query(
        "SELECT e.work_date, e.title, e.hours, k.name AS kit_name FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " ORDER BY e.hours DESC, e.work_date DESC LIMIT 5"
    )

    project = db.project()
    target = float(project["target_hours"] or 0)
    overall = db.overall_percent()
    pace_30 = float(
        db.scalar(
            "SELECT SUM(hours) FROM entries WHERE work_date >= ?",
            ((date.today() - timedelta(days=30)).isoformat(),),
            0.0,
        )
        or 0
    )
    pace_90 = (
        float(
            db.scalar(
                "SELECT SUM(hours) FROM entries WHERE work_date >= ?",
                ((date.today() - timedelta(days=90)).isoformat(),),
                0.0,
            )
            or 0
        )
        / 90
    )
    projection = {}
    if target > total_hours and pace_90 > 0:
        days = int((target - total_hours) / pace_90)
        projection["by_hours"] = (date.today() + timedelta(days=days)).strftime("%B %Y")
    if 0 < overall < 100 and span_days > 0:
        rate = overall / span_days
        if rate > 0:
            days = int((100 - overall) / rate)
            projection["by_progress"] = (
                date.today() + timedelta(days=days)
            ).strftime("%B %Y")

    total_cost = int(db.scalar("SELECT SUM(amount_cents) FROM costs", (), 0) or 0)

    return render(
        request,
        "insights.html",
        title="Insights",
        total_hours=total_hours,
        sessions=sessions,
        work_days=work_days,
        span_days=span_days,
        first=first,
        last=last,
        avg_session=round(total_hours / sessions, 2) if sessions else 0.0,
        avg_day=round(total_hours / work_days, 2) if work_days else 0.0,
        per_week=round(total_hours / (span_days / 7), 2) if span_days >= 7 else 0.0,
        pace_30=round(pace_30, 1),
        month_chart=charts.bar_chart(
            [
                (datetime.strptime(r["ym"], "%Y-%m").strftime("%b %y"), float(r["hours"] or 0))
                for r in by_month
            ],
            unit="hours",
        ),
        cumulative_chart=charts.area_chart(cumulative, unit="hours"),
        kit_hours_chart=charts.hbar_chart(by_kit, unit="hours"),
        kit_percent_chart=charts.hbar_chart(
            [(k["name"], k["percent"]) for k in db.kits_with_progress()], unit="percent"
        ),
        weekday_chart=charts.bar_chart(by_weekday, unit="hours"),
        person_chart=charts.hbar_chart(
            [(p["name"], float(p["hours"] or 0)) for p in by_person], unit="hours"
        ),
        by_person=by_person,
        top_sessions=top_sessions,
        target=target,
        projection=projection,
        overall=overall,
        total_cost=total_cost,
        cost_per_hour=round(total_cost / 100 / total_hours, 2) if total_hours else 0.0,
        inv=inventory_summary(),
    )


# ------------------------------------------------------------------- search


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    params = request.query_params
    q = params.get("q", "").strip()
    scope = params.get("scope", "all")
    kit_id = opt_id(params.get("kit"))
    start = params.get("start", "").strip()[:10]
    end = params.get("end", "").strip()[:10]
    like = f"%{q}%"

    results: dict[str, list] = {
        "entries": [],
        "photos": [],
        "documents": [],
        "inventory": [],
        "costs": [],
    }
    if q or kit_id or start or end:
        if scope in ("all", "entries"):
            where = []
            args: list = []
            if q:
                where.append("(e.title LIKE ? OR e.notes LIKE ?)")
                args += [like, like]
            if kit_id:
                where.append("e.kit_id = ?")
                args.append(kit_id)
            if start:
                where.append("e.work_date >= ?")
                args.append(start)
            if end:
                where.append("e.work_date <= ?")
                args.append(end)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            results["entries"] = db.query(
                "SELECT e.*, k.name AS kit_name FROM entries e"
                " LEFT JOIN kits k ON k.id = e.kit_id"
                f"{clause} ORDER BY e.work_date DESC LIMIT 60",
                args,
            )
        if scope in ("all", "photos") and q:
            results["photos"] = db.query(
                "SELECT a.*, e.title AS entry_title FROM attachments a"
                " LEFT JOIN entries e ON e.id = a.entry_id"
                " WHERE a.kind = 'photo' AND (a.caption LIKE ? OR a.original_name LIKE ?"
                " OR EXISTS (SELECT 1 FROM annotations an WHERE an.attachment_id = a.id"
                " AND an.label LIKE ?)) ORDER BY a.id DESC LIMIT 40",
                (like, like, like),
            )
        if scope in ("all", "documents") and q:
            results["documents"] = db.query(
                "SELECT * FROM attachments WHERE kind = 'document'"
                " AND (original_name LIKE ? OR caption LIKE ? OR doc_type LIKE ?)"
                " ORDER BY id DESC LIMIT 40",
                (like, like, like),
            )
        if scope in ("all", "inventory") and q:
            results["inventory"] = db.query(
                "SELECT i.*, k.name AS kit_name FROM inventory i"
                " LEFT JOIN kits k ON k.id = i.kit_id"
                " WHERE i.part_number LIKE ? OR i.description LIKE ? OR i.notes LIKE ?"
                " OR i.location LIKE ? ORDER BY i.id DESC LIMIT 60",
                (like, like, like, like),
            )
        if scope in ("all", "costs") and q:
            results["costs"] = db.query(
                "SELECT c.*, k.name AS kit_name FROM costs c"
                " LEFT JOIN kits k ON k.id = c.kit_id"
                " WHERE c.vendor LIKE ? OR c.description LIKE ? OR c.notes LIKE ?"
                " OR c.category LIKE ? ORDER BY c.spent_on DESC LIMIT 60",
                (like, like, like, like),
            )
    return render(
        request,
        "search.html",
        title="Search",
        q=q,
        scope=scope,
        results=results,
        count=sum(len(v) for v in results.values()),
        all_kits=kits_list(),
        filters={"kit": kit_id, "start": start, "end": end},
    )


# ----------------------------------------------------------------- settings


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render(
        request,
        "settings.html",
        title="Settings",
        contributors=contributors_list(),
        usage=files.disk_usage(),
        counts={
            "entries": int(db.scalar("SELECT COUNT(*) FROM entries") or 0),
            "photos": int(
                db.scalar("SELECT COUNT(*) FROM attachments WHERE kind = 'photo'") or 0
            ),
            "documents": int(
                db.scalar("SELECT COUNT(*) FROM attachments WHERE kind = 'document'")
                or 0
            ),
            "inventory": int(db.scalar("SELECT COUNT(*) FROM inventory") or 0),
            "costs": int(db.scalar("SELECT COUNT(*) FROM costs") or 0),
            "kits": int(db.scalar("SELECT COUNT(*) FROM kits") or 0),
        },
    )


@app.post("/settings/project")
async def settings_project(request: Request):
    form = await get_form(request)
    db.execute(
        "UPDATE project SET name = ?, model = ?, kit_number = ?, builder = ?,"
        " n_number = ?, started_on = ?, target_hours = ?, budget_cents = ?, notes = ?"
        " WHERE id = 1",
        (
            str(form.get("name", "")).strip()[:120] or "My Build",
            str(form.get("model", "")).strip()[:60] or "RV-12iS",
            str(form.get("kit_number", "")).strip()[:60],
            str(form.get("builder", "")).strip()[:120],
            str(form.get("n_number", "")).strip()[:16],
            to_date(form.get("started_on"), ""),
            max(0.0, to_float(form.get("target_hours"), 0.0)),
            to_cents(form.get("budget")),
            str(form.get("notes", "")).strip(),
        ),
    )
    return back("/settings", "Project details saved.")


@app.post("/settings/contributors/add")
async def contributors_add(request: Request):
    form = await get_form(request)
    name = str(form.get("name", "")).strip()[:80]
    if not name:
        return back("/settings", err="Enter a name.")
    if db.one("SELECT id FROM contributors WHERE name = ?", (name,)):
        return back("/settings", err=f"{name} is already listed.")
    db.execute(
        "INSERT INTO contributors (name, role, notes) VALUES (?, ?, ?)",
        (
            name,
            str(form.get("role", "")).strip()[:80],
            str(form.get("notes", "")).strip(),
        ),
    )
    return back("/settings", f"Added {name}.")


@app.post("/settings/contributors/{contributor_id}/delete")
async def contributors_delete(request: Request, contributor_id: int):
    db.execute("DELETE FROM contributors WHERE id = ?", (contributor_id,))
    return back("/settings", "Contributor removed.")


@app.post("/settings/restore")
async def settings_restore(request: Request):
    form = await get_form(request)
    upload = form.get("backup")
    if upload is None or not getattr(upload, "filename", ""):
        return back("/settings", err="Choose a backup zip first.")
    data = await upload.read()
    try:
        message = exports.restore_zip(data)
    except Exception as exc:
        return back("/settings", err=f"Restore failed: {exc}")
    return back("/settings", message)


@app.post("/settings/import/entries")
async def import_entries(request: Request):
    form = await get_form(request)
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return back("/settings", err="Choose a CSV file first.")
    data = await upload.read()
    try:
        message = exports.import_entries_csv(data)
    except Exception as exc:
        return back("/settings", err=f"Import failed: {exc}")
    return back("/settings", message)


@app.post("/settings/thumbs")
async def rebuild_thumbs(request: Request):
    made = exports.rebuild_thumbs()
    return back("/settings", f"Rebuilt {made} thumbnail(s).")


@app.post("/settings/reset-kits")
async def reset_kits(request: Request):
    form = await get_form(request)
    if str(form.get("confirm", "")) != "RESET":
        return back("/settings", err="Type RESET to confirm.")
    db.execute("DELETE FROM kits")
    db.seed_kits()
    return back("/settings", "Kit list reset to the default sections.")


# ------------------------------------------------------------------ exports


def _attachment(content: bytes | str, filename: str, media_type: str) -> Response:
    body = content.encode("utf-8") if isinstance(content, str) else content
    return Response(
        body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _stamp() -> str:
    return date.today().isoformat()


@app.get("/export/entries.csv")
def export_entries():
    return _attachment(
        exports.entries_csv(), f"builder-log-entries-{_stamp()}.csv", "text/csv"
    )


@app.get("/export/inventory.csv")
def export_inventory():
    return _attachment(
        exports.inventory_csv(), f"builder-log-inventory-{_stamp()}.csv", "text/csv"
    )


@app.get("/export/costs.csv")
def export_costs():
    return _attachment(
        exports.costs_csv(), f"builder-log-costs-{_stamp()}.csv", "text/csv"
    )


@app.get("/export/builder-log.json")
def export_json():
    return _attachment(
        exports.full_json(), f"builder-log-{_stamp()}.json", "application/json"
    )


@app.get("/export/photos.zip")
def export_photos():
    return _attachment(
        exports.photos_zip(), f"builder-log-photos-{_stamp()}.zip", "application/zip"
    )


@app.get("/export/backup.zip")
def export_backup():
    return _attachment(
        exports.backup_zip(), f"builder-log-backup-{_stamp()}.zip", "application/zip"
    )


@app.get("/export/report.pdf")
def export_report(
    size: str = "letter",
    photos: int = 1,
    summary: int = 1,
    inventory: int = 0,
    costs: int = 0,
):
    pdf = report.builder_log_pdf(
        page_size=size,
        include_photos=bool(photos),
        include_summary=bool(summary),
        include_inventory=bool(inventory),
        include_costs=bool(costs),
    )
    return _attachment(pdf, f"builder-log-{_stamp()}.pdf", "application/pdf")


# ------------------------------------------------------------- quick add


@app.get("/quick", response_class=HTMLResponse)
def quick_page(request: Request):
    return render(
        request,
        "quick.html",
        title="Quick add",
        all_kits=kits_list(),
        all_sections=sections_list(),
        timer=active_timer(),
        timer_hours=timer_elapsed(active_timer()),
    )


@app.post("/quick")
async def quick_save(request: Request):
    entry_id = await _save_entry(request, None)
    form = await get_form(request)
    if str(form.get("stop_timer", "")) == "1":
        db.execute("DELETE FROM timers WHERE id = 1")
    return back("/quick", "Saved. Add another.")


@app.get("/api/summary")
def api_summary():
    """A small JSON summary, handy for a dashboard widget or a script."""
    return JSONResponse(
        {
            "project": dict(db.project()),
            "overall_percent": db.overall_percent(),
            "total_hours": round(
                float(db.scalar("SELECT SUM(hours) FROM entries", (), 0.0) or 0), 1
            ),
            "entries": int(db.scalar("SELECT COUNT(*) FROM entries") or 0),
            "photos": int(
                db.scalar("SELECT COUNT(*) FROM attachments WHERE kind = 'photo'") or 0
            ),
            "cost_cents": int(
                db.scalar("SELECT SUM(amount_cents) FROM costs", (), 0) or 0
            ),
            "kits": db.kits_with_progress(),
        }
    )
