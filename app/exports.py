"""Exports and backups: CSV, JSON, a photo archive, and a full backup zip."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import zipfile

from . import db, files

MANIFEST = "builder-log-manifest.json"


def _rows(table: str, order: str) -> list[dict]:
    return [dict(r) for r in db.query(f"SELECT * FROM {table} ORDER BY {order}")]


def entries_csv() -> str:
    """One row per log entry, with kit, section, hours, contributors and tags."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "date",
            "title",
            "kit",
            "section",
            "hours",
            "contributors",
            "contributor_hours",
            "tags",
            "photos",
            "documents",
            "notes",
        ]
    )
    rows = db.query(
        "SELECT e.*, k.name AS kit_name, s.name AS section_name"
        " FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " LEFT JOIN sections s ON s.id = e.section_id"
        " ORDER BY e.work_date, e.id"
    )
    for row in rows:
        people = db.query(
            "SELECT c.name, ec.hours FROM entry_contributors ec"
            " JOIN contributors c ON c.id = ec.contributor_id"
            " WHERE ec.entry_id = ? ORDER BY c.name",
            (row["id"],),
        )
        tags = db.query(
            "SELECT t.name FROM entry_tags et JOIN tags t ON t.id = et.tag_id"
            " WHERE et.entry_id = ? ORDER BY t.name",
            (row["id"],),
        )
        photos = db.query(
            "SELECT original_name FROM attachments WHERE entry_id = ? AND kind = 'photo'"
            " ORDER BY id",
            (row["id"],),
        )
        docs = db.query(
            "SELECT original_name FROM attachments WHERE entry_id = ?"
            " AND kind = 'document' ORDER BY id",
            (row["id"],),
        )
        writer.writerow(
            [
                row["id"],
                row["work_date"],
                row["title"],
                row["kit_name"] or "",
                row["section_name"] or "",
                f"{float(row['hours'] or 0):g}",
                "; ".join(p["name"] for p in people),
                "; ".join(f"{p['name']}={float(p['hours'] or 0):g}" for p in people),
                "; ".join(t["name"] for t in tags),
                "; ".join(p["original_name"] for p in photos),
                "; ".join(d["original_name"] for d in docs),
                (row["notes"] or "").replace("\r\n", "\n"),
            ]
        )
    return out.getvalue()


def inventory_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "kit",
            "part_number",
            "description",
            "qty_expected",
            "qty_received",
            "status",
            "location",
            "received_on",
            "notes",
        ]
    )
    rows = db.query(
        "SELECT i.*, k.name AS kit_name FROM inventory i"
        " LEFT JOIN kits k ON k.id = i.kit_id"
        " ORDER BY k.sort_order, i.part_number, i.id"
    )
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["kit_name"] or "",
                row["part_number"],
                row["description"],
                f"{float(row['qty_expected'] or 0):g}",
                f"{float(row['qty_received'] or 0):g}",
                row["status"],
                row["location"],
                row["received_on"],
                row["notes"],
            ]
        )
    return out.getvalue()


def costs_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["id", "date", "vendor", "category", "description", "amount", "kit", "notes"]
    )
    rows = db.query(
        "SELECT c.*, k.name AS kit_name FROM costs c"
        " LEFT JOIN kits k ON k.id = c.kit_id"
        " ORDER BY c.spent_on, c.id"
    )
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["spent_on"],
                row["vendor"],
                row["category"],
                row["description"],
                f"{int(row['amount_cents'] or 0) / 100:.2f}",
                row["kit_name"] or "",
                row["notes"],
            ]
        )
    return out.getvalue()


def full_json() -> str:
    """Everything in the database, for archiving or moving to another tool."""
    project = dict(db.project())
    data = {
        "app": "Hangar Builder Log",
        "schema_version": db.SCHEMA_VERSION,
        "exported_at": db.now() + "Z",
        "project": project,
        "kits": _rows("kits", "sort_order, id"),
        "sections": _rows("sections", "kit_id, sort_order, id"),
        "contributors": _rows("contributors", "name"),
        "entries": _rows("entries", "work_date, id"),
        "entry_contributors": _rows("entry_contributors", "entry_id"),
        "tags": _rows("tags", "name"),
        "entry_tags": _rows("entry_tags", "entry_id"),
        "attachments": _rows("attachments", "id"),
        "annotations": _rows("annotations", "attachment_id, number"),
        "inventory": _rows("inventory", "id"),
        "costs": _rows("costs", "spent_on, id"),
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def photos_zip() -> bytes:
    """Photos foldered by kit, with captions and annotations alongside."""
    files.ensure_dirs()
    buffer = io.BytesIO()
    index = io.StringIO()
    writer = csv.writer(index, lineterminator="\n")
    writer.writerow(
        ["file", "date", "entry", "kit", "section", "caption", "annotations"]
    )
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        rows = db.query(
            "SELECT a.*, e.work_date, e.title, k.name AS kit_name,"
            " s.name AS section_name"
            " FROM attachments a"
            " LEFT JOIN entries e ON e.id = a.entry_id"
            " LEFT JOIN kits k ON k.id = e.kit_id"
            " LEFT JOIN sections s ON s.id = e.section_id"
            " WHERE a.kind = 'photo' ORDER BY a.id"
        )
        for row in rows:
            try:
                path = files.stored_path(row["stored_name"])
            except files.UploadError:
                continue
            if not os.path.exists(path):
                continue
            folder = files.clean_name(row["kit_name"] or "Unsorted")
            date_prefix = (row["work_date"] or row["created_at"] or "")[:10]
            name = f"photos/{folder}/{date_prefix}-{row['stored_name']}"
            archive.write(path, name)
            notes = db.query(
                "SELECT number, label FROM annotations WHERE attachment_id = ?"
                " ORDER BY number",
                (row["id"],),
            )
            writer.writerow(
                [
                    name,
                    date_prefix,
                    row["title"] or "",
                    row["kit_name"] or "",
                    row["section_name"] or "",
                    row["caption"] or "",
                    " | ".join(f"{n['number']}. {n['label']}" for n in notes),
                ]
            )
        archive.writestr("photos/index.csv", index.getvalue())
        archive.writestr("entries.csv", entries_csv())
    return buffer.getvalue()


def backup_zip() -> bytes:
    """The database, every uploaded file, and CSV copies of the main tables."""
    files.ensure_dirs()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST,
            json.dumps(
                {
                    "app": "Hangar Builder Log",
                    "schema_version": db.SCHEMA_VERSION,
                    "created_at": db.now() + "Z",
                },
                indent=2,
            ),
        )
        db.connect().commit()
        if os.path.exists(db.DB_PATH):
            snapshot = os.path.join(db.DATA_DIR, "backup-snapshot.db")
            target = db.sqlite3.connect(snapshot)
            try:
                db.connect().backup(target)
            finally:
                target.close()
            archive.write(snapshot, "builder-log.db")
            os.remove(snapshot)
        for name in sorted(os.listdir(files.UPLOAD_DIR)):
            path = os.path.join(files.UPLOAD_DIR, name)
            if os.path.isfile(path):
                archive.write(path, f"uploads/{name}")
        archive.writestr("csv/entries.csv", entries_csv())
        archive.writestr("csv/inventory.csv", inventory_csv())
        archive.writestr("csv/costs.csv", costs_csv())
        archive.writestr("builder-log.json", full_json())
    return buffer.getvalue()


def restore_zip(data: bytes) -> str:
    """Replace the database and uploads from a backup zip made by this app."""
    files.ensure_dirs()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if "builder-log.db" not in names:
            raise ValueError("That zip has no builder-log.db, so it is not a backup.")
        # Write beside the live file, then move it into place, so connections
        # held by other request threads never see a half-written database.
        db.invalidate()
        staging = db.DB_PATH + ".restoring"
        with archive.open("builder-log.db") as src, open(staging, "wb") as dst:
            shutil.copyfileobj(src, dst)
        for suffix in ("-wal", "-shm"):
            stale = db.DB_PATH + suffix
            if os.path.exists(stale):
                os.remove(stale)
        os.replace(staging, db.DB_PATH)
        restored = 0
        for name in names:
            if not name.startswith("uploads/") or name.endswith("/"):
                continue
            safe = os.path.basename(name)
            if not safe:
                continue
            with archive.open(name) as src, open(
                os.path.join(files.UPLOAD_DIR, safe), "wb"
            ) as dst:
                shutil.copyfileobj(src, dst)
            restored += 1
    db.invalidate()
    db.init_db(seed=False)
    rebuild_thumbs()
    return f"Restored the database and {restored} file(s)."


def rebuild_thumbs() -> int:
    """Recreate missing photo thumbnails after a restore."""
    made = 0
    for row in db.query(
        "SELECT stored_name FROM attachments WHERE kind = 'photo' ORDER BY id"
    ):
        try:
            path = files.stored_path(row["stored_name"])
        except files.UploadError:
            continue
        if not os.path.exists(path):
            continue
        if os.path.exists(files.thumb_path(row["stored_name"])):
            continue
        try:
            files.make_thumb(path, row["stored_name"])
            made += 1
        except Exception:
            continue
    return made


def import_entries_csv(data: bytes) -> str:
    """Import log entries from EAA Builders Log, MyKitLog, KitFlight or this app.

    Column names are matched loosely, so most exports work without editing.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("That file has no header row.")

    def pick(row: dict, *options: str) -> str:
        for key, value in row.items():
            if key is None:
                continue
            flat = key.strip().lower().replace(" ", "_")
            if flat in options:
                return (value or "").strip()
        return ""

    kits = {r["name"].lower(): r["id"] for r in db.query("SELECT id, name FROM kits")}
    added = 0
    skipped = 0
    for row in reader:
        work_date = pick(row, "date", "work_date", "entry_date", "log_date")[:10]
        title = pick(row, "title", "task", "summary", "subject", "name")
        notes = pick(row, "notes", "description", "details", "comments", "body", "text")
        hours_raw = pick(row, "hours", "time", "duration", "hours_worked", "work_hours")
        kit_name = pick(row, "kit", "kit_name", "category", "section_group", "assembly")
        if not work_date and not title and not notes:
            skipped += 1
            continue
        try:
            hours = float(hours_raw.replace(":", ".")) if hours_raw else 0.0
        except ValueError:
            hours = 0.0
        kit_id = kits.get(kit_name.lower()) if kit_name else None
        if kit_name and kit_id is None:
            kit_id = db.execute(
                "INSERT INTO kits (name, sort_order) VALUES (?, ?)",
                (kit_name[:60], 99),
            )
            kits[kit_name.lower()] = kit_id
        db.execute(
            "INSERT INTO entries (work_date, title, notes, kit_id, hours,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                work_date or db.utcnow().date().isoformat(),
                title[:200] or "Imported entry",
                notes,
                kit_id,
                hours,
                db.now(),
                db.now(),
            ),
        )
        added += 1
    message = f"Imported {added} log entr{'y' if added == 1 else 'ies'}."
    if skipped:
        message += f" Skipped {skipped} empty row(s)."
    return message


def import_inventory_csv(data: bytes) -> str:
    """Import a packing list. Part number and description are enough."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("That file has no header row.")

    def pick(row: dict, *options: str) -> str:
        for key, value in row.items():
            if key is None:
                continue
            flat = key.strip().lower().replace(" ", "_")
            if flat in options:
                return (value or "").strip()
        return ""

    kits = {r["name"].lower(): r["id"] for r in db.query("SELECT id, name FROM kits")}
    added = 0
    for row in reader:
        part = pick(row, "part_number", "part", "part_no", "pn", "number", "item")
        desc = pick(row, "description", "desc", "name", "item_description")
        if not part and not desc:
            continue
        qty_raw = pick(row, "qty_expected", "qty", "quantity", "count", "qty_ordered")
        got_raw = pick(row, "qty_received", "received", "qty_on_hand")
        try:
            qty = float(qty_raw) if qty_raw else 1.0
        except ValueError:
            qty = 1.0
        try:
            got = float(got_raw) if got_raw else 0.0
        except ValueError:
            got = 0.0
        kit_name = pick(row, "kit", "kit_name", "assembly", "category")
        kit_id = kits.get(kit_name.lower()) if kit_name else None
        status = pick(row, "status") or (
            "received" if got >= qty and qty > 0 else "partial" if got > 0 else "pending"
        )
        if status not in db.INVENTORY_STATUS:
            status = "pending"
        db.execute(
            "INSERT INTO inventory (kit_id, part_number, description, qty_expected,"
            " qty_received, status, location, received_on, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kit_id,
                part[:60],
                desc[:300],
                qty,
                got,
                status,
                pick(row, "location", "bin", "shelf")[:60],
                pick(row, "received_on", "date_received", "received_date")[:10],
                pick(row, "notes", "comments"),
            ),
        )
        added += 1
    return f"Imported {added} inventory line(s)."
