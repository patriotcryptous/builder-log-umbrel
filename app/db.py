"""SQLite storage for Builder Log.

One database file, one aircraft project per instance. Schema upgrades are
applied in order at startup so an Umbrel update never loses data.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from typing import Any, Iterable

DATA_DIR = os.environ.get("HBL_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "builder-log.db")

_local = threading.local()

SCHEMA_VERSION = 1

DEFAULT_KITS: list[tuple[str, list[str]]] = [
    (
        "Empennage",
        [
            "Vertical stabilizer",
            "Rudder",
            "Horizontal stabilizer",
            "Elevators",
            "Tailcone",
        ],
    ),
    ("Wings", ["Left wing", "Right wing", "Flaperons", "Fuel tanks", "Wiring"]),
    (
        "Fuselage",
        [
            "Center section",
            "Forward fuselage",
            "Aft fuselage",
            "Seats and floors",
            "Canopy",
        ],
    ),
    ("Finish", ["Cowling", "Fairings", "Wheels and brakes", "Paint and trim"]),
    ("Powerplant", ["Engine mount", "Engine", "Propeller", "Exhaust", "Fuel system"]),
    (
        "Avionics",
        ["Panel", "Wiring harness", "Radios and transponder", "Autopilot", "Lighting"],
    ),
    (
        "Final assembly",
        ["Rigging", "Weight and balance", "Inspection prep", "First engine start"],
    ),
]

CATEGORIES = [
    "Kit",
    "Tools",
    "Hardware",
    "Avionics",
    "Engine",
    "Propeller",
    "Paint",
    "Shipping",
    "Shop supplies",
    "Insurance",
    "Fees",
    "Training",
    "Other",
]

INVENTORY_STATUS = ["pending", "received", "partial", "missing", "backordered", "damaged"]


_generation = 0


def connect() -> sqlite3.Connection:
    """One connection per thread, with foreign keys and WAL enabled.

    Requests are served on a pool of threads, so a restore has to invalidate
    every thread's connection, not just its own. Each one remembers which
    generation of the database file it opened and reopens when that moves on.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "generation", -1) != _generation:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        conn = None
        _local.conn = None
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _local.conn = conn
        _local.generation = _generation
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def invalidate() -> None:
    """Called when the database file is replaced, as a restore does."""
    global _generation
    _generation += 1
    close()


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def scalar(sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
    row = one(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.lastrowid or 0


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    conn = connect()
    conn.executemany(sql, [tuple(r) for r in rows])
    conn.commit()


DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    name         TEXT NOT NULL DEFAULT 'My Build',
    model        TEXT NOT NULL DEFAULT 'RV-12iS',
    kit_number   TEXT NOT NULL DEFAULT '',
    builder      TEXT NOT NULL DEFAULT '',
    n_number     TEXT NOT NULL DEFAULT '',
    started_on   TEXT NOT NULL DEFAULT '',
    target_hours REAL NOT NULL DEFAULT 0,
    budget_cents INTEGER NOT NULL DEFAULT 0,
    notes        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'not_started',
    percent_override REAL,
    notes       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id      INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    weight      REAL NOT NULL DEFAULT 1,
    percent     REAL NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sections_kit ON sections(kit_id);

CREATE TABLE IF NOT EXISTS contributors (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    role    TEXT NOT NULL DEFAULT '',
    notes   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    work_date   TEXT NOT NULL,
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    kit_id      INTEGER REFERENCES kits(id) ON DELETE SET NULL,
    section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    hours       REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(work_date DESC);
CREATE INDEX IF NOT EXISTS idx_entries_kit ON entries(kit_id);

CREATE TABLE IF NOT EXISTS entry_contributors (
    entry_id       INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    contributor_id INTEGER NOT NULL REFERENCES contributors(id) ON DELETE CASCADE,
    hours          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (entry_id, contributor_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id      INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL DEFAULT 'photo',
    stored_name   TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime          TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    caption       TEXT NOT NULL DEFAULT '',
    doc_type      TEXT NOT NULL DEFAULT '',
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_att_entry ON attachments(entry_id);
CREATE INDEX IF NOT EXISTS idx_att_kind ON attachments(kind);

CREATE TABLE IF NOT EXISTS annotations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    number        INTEGER NOT NULL DEFAULT 1,
    x             REAL NOT NULL DEFAULT 50,
    y             REAL NOT NULL DEFAULT 50,
    label         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ann_att ON annotations(attachment_id);

CREATE TABLE IF NOT EXISTS inventory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id       INTEGER REFERENCES kits(id) ON DELETE SET NULL,
    part_number  TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    qty_expected REAL NOT NULL DEFAULT 1,
    qty_received REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    location     TEXT NOT NULL DEFAULT '',
    received_on  TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inv_kit ON inventory(kit_id);
CREATE INDEX IF NOT EXISTS idx_inv_status ON inventory(status);

CREATE TABLE IF NOT EXISTS costs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    spent_on      TEXT NOT NULL,
    vendor        TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'Other',
    description   TEXT NOT NULL DEFAULT '',
    amount_cents  INTEGER NOT NULL DEFAULT 0,
    kit_id        INTEGER REFERENCES kits(id) ON DELETE SET NULL,
    attachment_id INTEGER REFERENCES attachments(id) ON DELETE SET NULL,
    notes         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_costs_date ON costs(spent_on DESC);

CREATE TABLE IF NOT EXISTS timers (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TEXT NOT NULL,
    kit_id     INTEGER,
    section_id INTEGER,
    title      TEXT NOT NULL DEFAULT ''
);
"""


def utcnow() -> datetime:
    """Naive UTC, so stored timestamps sort and compare as plain strings."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now() -> str:
    return utcnow().isoformat(timespec="seconds")


def init_db(seed: bool = True) -> None:
    conn = connect()
    conn.executescript(DDL)
    conn.commit()
    version = int(get_meta("schema_version", "0"))
    if version < SCHEMA_VERSION:
        set_meta("schema_version", str(SCHEMA_VERSION))
    if one("SELECT id FROM project WHERE id = 1") is None:
        execute(
            "INSERT INTO project (id, name, model, started_on) VALUES (1, ?, ?, ?)",
            ("My Build", "RV-12iS", date.today().isoformat()),
        )
    if seed and scalar("SELECT COUNT(*) FROM kits") == 0:
        seed_kits()


def seed_kits() -> None:
    for ki, (kit_name, sections) in enumerate(DEFAULT_KITS):
        kit_id = execute(
            "INSERT INTO kits (name, sort_order) VALUES (?, ?)", (kit_name, ki)
        )
        executemany(
            "INSERT INTO sections (kit_id, name, sort_order) VALUES (?, ?, ?)",
            [(kit_id, name, si) for si, name in enumerate(sections)],
        )


def get_meta(key: str, default: str = "") -> str:
    row = one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def project() -> sqlite3.Row:
    row = one("SELECT * FROM project WHERE id = 1")
    if row is None:
        init_db(seed=False)
        row = one("SELECT * FROM project WHERE id = 1")
    assert row is not None
    return row


# ---------------------------------------------------------------- progress


def kit_percent(kit: sqlite3.Row | dict) -> float:
    """A kit's completion: the manual override, else its sections by weight."""
    override = kit["percent_override"] if "percent_override" in kit.keys() else None
    if override is not None:
        return max(0.0, min(100.0, float(override)))
    rows = query(
        "SELECT weight, percent FROM sections WHERE kit_id = ?", (kit["id"],)
    )
    total_weight = sum(max(0.0, float(r["weight"])) for r in rows)
    if not rows or total_weight <= 0:
        return 0.0
    done = sum(max(0.0, float(r["weight"])) * float(r["percent"]) for r in rows)
    return round(done / total_weight, 1)


def kits_with_progress() -> list[dict]:
    out = []
    for kit in query("SELECT * FROM kits ORDER BY sort_order, id"):
        pct = kit_percent(kit)
        hours = scalar("SELECT SUM(hours) FROM entries WHERE kit_id = ?", (kit["id"],), 0.0)
        spent = scalar(
            "SELECT SUM(amount_cents) FROM costs WHERE kit_id = ?", (kit["id"],), 0
        )
        n_sections = scalar("SELECT COUNT(*) FROM sections WHERE kit_id = ?", (kit["id"],))
        done_sections = scalar(
            "SELECT COUNT(*) FROM sections WHERE kit_id = ? AND percent >= 100",
            (kit["id"],),
        )
        out.append(
            {
                "id": kit["id"],
                "name": kit["name"],
                "status": kit["status"],
                "notes": kit["notes"],
                "percent_override": kit["percent_override"],
                "percent": pct,
                "hours": round(float(hours or 0), 1),
                "spent_cents": int(spent or 0),
                "sections_total": int(n_sections or 0),
                "sections_done": int(done_sections or 0),
            }
        )
    return out


def overall_percent() -> float:
    """Whole-aircraft completion: the average of kit percentages."""
    kits = kits_with_progress()
    if not kits:
        return 0.0
    return round(sum(k["percent"] for k in kits) / len(kits), 1)
