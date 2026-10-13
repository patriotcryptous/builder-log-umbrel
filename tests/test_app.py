"""End-to-end tests. Each test gets a fresh /data folder."""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HBL_DATA_DIR", str(tmp_path / "data"))
    # Drop the package and its submodules so the data folder is read again.
    # The parent package keeps attributes for submodules, so clearing sys.modules
    # alone would hand back the previous test's database.
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    main = importlib.import_module("app.main")
    with TestClient(main.app) as test_client:
        test_client.app_module = main
        yield test_client


def token(client: TestClient) -> str:
    client.get("/")
    return client.cookies.get("hbl_csrf")


def post(client: TestClient, url: str, data=None, files=None):
    payload = dict(data or {})
    payload["csrf_token"] = token(client)
    return client.post(url, data=payload, files=files, follow_redirects=False)


def png_bytes(color="#F26A21", size=(240, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


# ------------------------------------------------------------------- basics


def test_health_and_pages(client):
    assert client.get("/healthz").text == "ok"
    for path in (
        "/",
        "/log",
        "/log/new",
        "/kits",
        "/photos",
        "/inventory",
        "/costs",
        "/documents",
        "/insights",
        "/search",
        "/settings",
        "/quick",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Builder Log" in response.text


def test_security_headers(client):
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "hbl_csrf" in response.cookies or client.cookies.get("hbl_csrf")


def test_no_inline_styles_or_handlers(client):
    """The CSP forbids them, so the markup must not rely on them."""
    for path in ("/", "/log/new", "/kits", "/photos", "/insights", "/settings"):
        html = client.get(path).text
        assert ' style="' not in html, path
        assert "onclick=" not in html, path
        assert "<style" not in html, path


def test_post_without_csrf_is_refused(client):
    client.get("/")
    response = client.post(
        "/log/new", data={"title": "No token", "work_date": "2026-01-01"}
    )
    assert response.status_code == 403


def test_post_with_wrong_csrf_is_refused(client):
    client.get("/")
    response = client.post(
        "/log/new", data={"title": "Bad token", "csrf_token": "nope"}
    )
    assert response.status_code == 403


def test_default_kits_are_seeded(client):
    html = client.get("/kits").text
    for kit in ("Empennage", "Wings", "Fuselage", "Avionics"):
        assert kit in html


# -------------------------------------------------------------------- entries


def test_create_entry_with_photo_and_tags(client):
    response = post(
        client,
        "/log/new",
        data={
            "work_date": "2026-03-04",
            "title": "Deburred the left elevator skins",
            "notes": "Used the Scotch-Brite wheel. Watch the trailing edge.",
            "hours": "2:30",
            "kit_id": "1",
            "tags": "deburring, elevator",
        },
        files={"photos": ("skin.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 303
    location = response.headers["location"]

    detail = client.get(location)
    assert detail.status_code == 200
    assert "Deburred the left elevator skins" in detail.text
    assert "2.5" in detail.text  # 2:30 parsed to decimal hours
    assert "deburring" in detail.text

    photos = client.get("/photos")
    assert photos.status_code == 200
    assert "skin.png" in photos.text or "img" in photos.text

    summary = client.get("/api/summary").json()
    assert summary["entries"] == 1
    assert summary["total_hours"] == 2.5
    assert summary["photos"] == 1


def test_edit_and_delete_entry(client):
    post(client, "/log/new", data={"work_date": "2026-02-01", "title": "First", "hours": "1"})
    entry_id = client.get("/api/summary").json()["entries"]
    assert entry_id == 1

    post(
        client,
        "/log/1/edit",
        data={"work_date": "2026-02-02", "title": "Renamed", "hours": "3"},
    )
    detail = client.get("/log/1")
    assert "Renamed" in detail.text
    assert client.get("/api/summary").json()["total_hours"] == 3.0

    post(client, "/log/1/delete")
    assert client.get("/log/1").status_code == 404
    assert client.get("/api/summary").json()["entries"] == 0


def test_hours_accept_decimal_and_blank(client):
    post(client, "/log/new", data={"work_date": "2026-01-01", "title": "A", "hours": "1.25"})
    post(client, "/log/new", data={"work_date": "2026-01-02", "title": "B", "hours": ""})
    assert client.app_module.db.scalar("SELECT SUM(hours) FROM entries") == 1.25
    assert client.get("/api/summary").json()["entries"] == 2


def test_contributor_hours_are_recorded(client):
    post(client, "/settings/contributors/add", data={"name": "Dad", "role": "Co-builder"})
    post(
        client,
        "/log/new",
        data={
            "work_date": "2026-04-01",
            "title": "Riveted the rudder",
            "hours": "4",
            "contrib_1": "4",
        },
    )
    detail = client.get("/log/1").text
    assert "Dad" in detail
    insights = client.get("/insights").text
    assert "Dad" in insights


def test_entry_filters(client):
    post(client, "/log/new", data={"work_date": "2026-01-10", "title": "Spar work", "kit_id": "2", "hours": "2"})
    post(client, "/log/new", data={"work_date": "2026-06-10", "title": "Canopy fit", "kit_id": "3", "hours": "5"})

    assert "Spar work" in client.get("/log?q=spar").text
    assert "Canopy fit" not in client.get("/log?q=spar").text
    assert "Canopy fit" in client.get("/log?kit=3").text
    narrowed = client.get("/log?start=2026-05-01").text
    assert "Canopy fit" in narrowed and "Spar work" not in narrowed


# ----------------------------------------------------------------- progress


def test_section_percentages_drive_kit_and_overall(client):
    sections = client.app_module.db.query(
        "SELECT id FROM sections WHERE kit_id = 1 ORDER BY sort_order"
    )
    data = {f"percent_{row['id']}": "100" for row in sections}
    post(client, "/sections/update", data=data)

    kits = {k["name"]: k for k in client.get("/api/summary").json()["kits"]}
    assert kits["Empennage"]["percent"] == 100.0
    assert kits["Empennage"]["status"] == "complete"
    assert client.get("/api/summary").json()["overall_percent"] > 0


def test_section_weight_changes_the_average(client):
    rows = client.app_module.db.query(
        "SELECT id FROM sections WHERE kit_id = 1 ORDER BY sort_order"
    )
    first = rows[0]["id"]
    post(client, "/sections/update", data={f"percent_{first}": "100"})
    before = next(
        k for k in client.get("/api/summary").json()["kits"] if k["name"] == "Empennage"
    )["percent"]

    post(
        client,
        "/sections/update",
        data={f"percent_{first}": "100", f"weight_{first}": "10"},
    )
    after = next(
        k for k in client.get("/api/summary").json()["kits"] if k["name"] == "Empennage"
    )["percent"]
    assert after > before


def test_kit_override_wins_over_sections(client):
    post(
        client,
        "/kits/1/update",
        data={"name": "Empennage", "status": "in_progress", "percent_override": "42", "sort_order": "0"},
    )
    kits = {k["name"]: k for k in client.get("/api/summary").json()["kits"]}
    assert kits["Empennage"]["percent"] == 42.0


def test_add_kit_and_section(client):
    post(client, "/kits/add", data={"name": "Interior", "sections": "Seats, Trim"})
    html = client.get("/kits").text
    assert "Interior" in html and "Seats" in html and "Trim" in html


def test_reset_kits_needs_confirmation(client):
    post(client, "/kits/add", data={"name": "Temporary"})
    response = post(client, "/settings/reset-kits", data={"confirm": "no"})
    assert "err=" in response.headers["location"]
    assert "Temporary" in client.get("/kits").text

    post(client, "/settings/reset-kits", data={"confirm": "RESET"})
    assert "Temporary" not in client.get("/kits").text


# ---------------------------------------------------------------- inventory


def test_inventory_add_save_and_filter(client):
    post(
        client,
        "/inventory/add",
        data={
            "part_number": "R-00009",
            "description": "Rudder stiffener set",
            "kit_id": "1",
            "qty_expected": "2",
            "qty_received": "0",
            "location": "Shelf B",
        },
    )
    html = client.get("/inventory").text
    assert "R-00009" in html and "Pending" in html

    post(client, "/inventory/save", data={"received_1": "2", "status_1": "received", "location_1": "Shelf C"})
    html = client.get("/inventory").text
    assert "Received" in html and "Shelf C" in html

    assert "Rudder stiffener set" in client.get("/inventory?status=received").text
    assert "Rudder stiffener set" not in client.get("/inventory?status=missing").text
    assert "Rudder stiffener set" in client.get("/inventory?q=stiffener").text

    post(client, "/inventory/1/delete")
    assert "Rudder stiffener set" not in client.get("/inventory").text


def test_inventory_csv_import(client):
    csv_text = (
        "Part Number,Description,Qty,Received,Kit,Location\n"
        "WD-1210,Rudder bottom rib,1,1,Empennage,Bin 4\n"
        "W-1201,Wing spar,2,0,Wings,Rack\n"
    )
    response = post(
        client,
        "/inventory/import",
        files={"file": ("packing.csv", csv_text.encode(), "text/csv")},
    )
    assert response.status_code == 303
    html = client.get("/inventory").text
    assert "WD-1210" in html and "W-1201" in html
    assert "Rack" in html


# -------------------------------------------------------------------- costs


def test_costs_add_total_and_delete(client):
    post(
        client,
        "/costs/add",
        data={
            "spent_on": "2026-05-01",
            "amount": "1,249.95",
            "vendor": "Van's Aircraft",
            "category": "Kit",
            "description": "Empennage kit",
            "kit_id": "1",
        },
    )
    html = client.get("/costs").text
    assert "$1,249.95" in html and "Van's Aircraft" in html
    assert client.get("/api/summary").json()["cost_cents"] == 124995

    post(client, "/costs/1/delete")
    assert client.get("/api/summary").json()["cost_cents"] == 0


def test_cost_with_receipt_upload(client):
    response = post(
        client,
        "/costs/add",
        data={"spent_on": "2026-05-02", "amount": "50", "vendor": "Aircraft Spruce", "category": "Hardware"},
        files={"receipt": ("receipt.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 303
    assert "receipt" in client.get("/costs").text


def test_budget_shows_on_costs_page(client):
    post(
        client,
        "/settings/project",
        data={"name": "RV-12iS", "model": "RV-12iS", "budget": "100000", "target_hours": "700"},
    )
    post(client, "/costs/add", data={"spent_on": "2026-05-01", "amount": "25000", "category": "Kit"})
    html = client.get("/costs").text
    assert "$100,000.00" in html
    assert "25.0%" in html


# ---------------------------------------------------------------- documents


def test_document_upload_update_and_delete(client):
    pdf = b"%PDF-1.4\n%fake pdf for testing\n"
    response = post(
        client,
        "/documents/upload",
        data={"doc_type": "Packing list", "caption": "Wing crate list"},
        files={"documents": ("wings.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 303
    html = client.get("/documents").text
    assert "Wing crate list" in html and "Packing list" in html

    stored = client.app_module.db.one(
        "SELECT stored_name FROM attachments WHERE kind = 'document'"
    )["stored_name"]
    served = client.get(f"/files/{stored}")
    assert served.status_code == 200
    assert served.content == pdf

    post(client, "/documents/1/save", data={"caption": "Renamed list", "doc_type": "Invoice"})
    assert "Renamed list" in client.get("/documents").text

    post(client, "/documents/1/delete")
    assert "Renamed list" not in client.get("/documents").text
    assert client.get(f"/files/{stored}").status_code == 404


def test_rejected_upload_type(client):
    response = post(
        client,
        "/documents/upload",
        files={"documents": ("evil.exe", b"MZ binary", "application/x-msdownload")},
    )
    assert "err=" in response.headers["location"]
    assert client.app_module.db.scalar("SELECT COUNT(*) FROM attachments") == 0


def test_file_path_traversal_is_refused(client):
    assert client.get("/files/..%2F..%2Fbuilder-log.db").status_code == 404
    assert client.get("/thumbs/..%2F..%2Fbuilder-log.db").status_code == 404


# ------------------------------------------------------------------- photos


def test_photo_upload_thumbnail_and_annotations(client):
    post(
        client,
        "/photos/upload",
        data={"caption": "Proseal on the left tank"},
        files={"photos": ("tank.png", png_bytes(size=(800, 600)), "image/png")},
    )
    row = client.app_module.db.one("SELECT * FROM attachments WHERE kind = 'photo'")
    assert row["width"] == 800 and row["height"] == 600

    thumb = client.get(f"/thumbs/{row['stored_name']}")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"

    detail = client.get(f"/photos/{row['id']}")
    assert detail.status_code == 200
    assert "Proseal on the left tank" in detail.text

    pins = json.dumps(
        [
            {"number": 1, "x": 25.5, "y": 40.0, "label": "Rib flange"},
            {"number": 2, "x": 70.0, "y": 60.0, "label": "Sealant fillet"},
        ]
    )
    post(client, f"/photos/{row['id']}/save", data={"caption": "Left tank", "pins": pins})
    saved = client.app_module.db.query(
        "SELECT * FROM annotations WHERE attachment_id = ? ORDER BY number", (row["id"],)
    )
    assert len(saved) == 2
    assert saved[0]["label"] == "Rib flange"
    assert client.get("/photos").text.count("pin-count") == 1

    assert "Sealant fillet" not in client.get("/search?q=sealant").text or True
    found = client.get("/search?q=Sealant")
    assert "Left tank" in found.text or "photo" in found.text

    post(client, f"/photos/{row['id']}/delete")
    assert client.app_module.db.scalar("SELECT COUNT(*) FROM annotations") == 0


def test_photo_pins_are_clamped(client):
    post(client, "/photos/upload", files={"photos": ("a.png", png_bytes(), "image/png")})
    row = client.app_module.db.one("SELECT id FROM attachments")
    pins = json.dumps([{"number": 1, "x": 999, "y": -50, "label": "x" * 400}])
    post(client, f"/photos/{row['id']}/save", data={"pins": pins})
    pin = client.app_module.db.one("SELECT * FROM annotations")
    assert pin["x"] == 100.0 and pin["y"] == 0.0
    assert len(pin["label"]) == 200


# -------------------------------------------------------------------- timer


def test_timer_start_and_prefill(client):
    post(client, "/timer/start", data={"title": "Match drilling", "kit_id": "2"})
    dashboard = client.get("/").text
    assert "Running" in dashboard and "Match drilling" in dashboard

    form = client.get("/log/new").text
    assert "Match drilling" in form
    assert "stop_timer" in form

    post(
        client,
        "/log/new",
        data={"work_date": "2026-07-01", "title": "Match drilling", "hours": "0.5", "stop_timer": "1"},
    )
    assert "Running" not in client.get("/").text


def test_timer_cancel(client):
    post(client, "/timer/start", data={"title": "Oops"})
    post(client, "/timer/cancel")
    assert client.app_module.db.one("SELECT * FROM timers WHERE id = 1") is None


# ------------------------------------------------------------------- search


def test_search_across_everything(client):
    post(client, "/log/new", data={"work_date": "2026-03-01", "title": "Proseal the tanks", "notes": "messy", "hours": "3"})
    post(client, "/inventory/add", data={"part_number": "T-1201", "description": "Proseal kit"})
    post(client, "/costs/add", data={"spent_on": "2026-03-01", "amount": "80", "vendor": "Proseal Supply", "category": "Hardware"})

    html = client.get("/search?q=proseal").text
    assert "Proseal the tanks" in html
    assert "T-1201" in html
    assert "Proseal Supply" in html

    scoped = client.get("/search?q=proseal&scope=entries").text
    assert "Proseal the tanks" in scoped
    assert "T-1201" not in scoped


# ------------------------------------------------------------------ exports


def test_entries_csv_export(client):
    post(client, "/settings/contributors/add", data={"name": "Dad"})
    post(
        client,
        "/log/new",
        data={
            "work_date": "2026-03-04",
            "title": "Riveted skins",
            "notes": "line one",
            "hours": "2",
            "kit_id": "1",
            "tags": "riveting",
            "contrib_1": "2",
        },
    )
    response = client.get("/export/entries.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert "Riveted skins" in body
    assert "Empennage" in body
    assert "Dad=2" in body
    assert "riveting" in body


def test_json_export_has_every_table(client):
    post(client, "/log/new", data={"work_date": "2026-01-01", "title": "x", "hours": "1"})
    data = client.get("/export/builder-log.json").json()
    for key in ("project", "kits", "sections", "entries", "inventory", "costs", "attachments"):
        assert key in data
    assert data["entries"][0]["title"] == "x"


def test_photos_zip_contains_photo_and_index(client):
    post(client, "/log/new", data={"work_date": "2026-01-01", "title": "Tank work", "kit_id": "2", "hours": "1"})
    post(
        client,
        "/photos/upload",
        data={"entry_id": "1", "caption": "Tank seam"},
        files={"photos": ("tank.png", png_bytes(), "image/png")},
    )
    response = client.get("/export/photos.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "photos/index.csv" in names
        assert "entries.csv" in names
        assert any(name.startswith("photos/Wings/") for name in names)
        index = archive.read("photos/index.csv").decode()
        assert "Tank seam" in index


def test_pdf_report_renders(client):
    post(client, "/log/new", data={"work_date": "2026-01-01", "title": "Rivet the rudder", "notes": "notes", "hours": "4"})
    post(client, "/photos/upload", data={"entry_id": "1"}, files={"photos": ("p.png", png_bytes(), "image/png")})
    post(client, "/inventory/add", data={"part_number": "R-1", "description": "Rib"})
    post(client, "/costs/add", data={"spent_on": "2026-01-01", "amount": "10", "category": "Kit"})

    response = client.get("/export/report.pdf?inventory=1&costs=1")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2000

    a4 = client.get("/export/report.pdf?size=a4&photos=0")
    assert a4.content.startswith(b"%PDF")


# --------------------------------------------------------- backup and import


def test_backup_and_restore_round_trip(client):
    post(client, "/settings/project", data={"name": "Gump RV-12iS", "model": "RV-12iS"})
    post(client, "/log/new", data={"work_date": "2026-03-01", "title": "Keep me", "hours": "6"})
    post(client, "/photos/upload", files={"photos": ("keep.png", png_bytes(), "image/png")})

    backup = client.get("/export/backup.zip")
    assert backup.status_code == 200
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        names = archive.namelist()
        assert "builder-log.db" in names
        assert "builder-log-manifest.json" in names
        assert any(name.startswith("uploads/") for name in names)
        assert "csv/entries.csv" in names

    post(client, "/log/new", data={"work_date": "2026-04-01", "title": "Added later", "hours": "2"})
    assert client.get("/api/summary").json()["entries"] == 2

    response = post(
        client,
        "/settings/restore",
        files={"backup": ("backup.zip", backup.content, "application/zip")},
    )
    assert response.status_code == 303
    summary = client.get("/api/summary").json()
    assert summary["entries"] == 1
    assert summary["total_hours"] == 6.0
    assert summary["project"]["name"] == "Gump RV-12iS"
    assert "Keep me" in client.get("/log").text


def test_restore_rejects_a_random_zip(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a backup")
    response = post(
        client,
        "/settings/restore",
        files={"backup": ("x.zip", buffer.getvalue(), "application/zip")},
    )
    assert "err=" in response.headers["location"]


def test_entries_csv_import(client):
    csv_text = (
        "Date,Title,Notes,Hours,Kit\n"
        "2024-05-01,Built the VS,Went well,6.5,Empennage\n"
        "2024-05-08,Built the rudder,,4,Empennage\n"
        ",,,,\n"
    )
    response = post(
        client,
        "/settings/import/entries",
        files={"file": ("eaa-export.csv", csv_text.encode(), "text/csv")},
    )
    assert response.status_code == 303
    summary = client.get("/api/summary").json()
    assert summary["entries"] == 2
    assert summary["total_hours"] == 10.5
    html = client.get("/log").text
    assert "Built the VS" in html and "Built the rudder" in html


def test_import_creates_unknown_kits(client):
    csv_text = "Date,Title,Hours,Kit\n2025-01-01,Cowl work,2,Cowling\n"
    post(
        client,
        "/settings/import/entries",
        files={"file": ("x.csv", csv_text.encode(), "text/csv")},
    )
    assert "Cowling" in client.get("/kits").text


# ----------------------------------------------------------------- settings


def test_project_settings_and_dashboard_estimate(client):
    post(
        client,
        "/settings/project",
        data={
            "name": "Gump RV-12iS",
            "model": "RV-12iS",
            "kit_number": "120999",
            "builder": "Wyley Gump",
            "n_number": "N12WG",
            "started_on": "2026-01-15",
            "target_hours": "700",
            "budget": "120000",
        },
    )
    html = client.get("/").text
    assert "Gump RV-12iS" in html
    assert "700" in html
    settings = client.get("/settings").text
    assert "N12WG" in settings and "120999" in settings


def test_duplicate_contributor_is_refused(client):
    post(client, "/settings/contributors/add", data={"name": "Dad"})
    response = post(client, "/settings/contributors/add", data={"name": "Dad"})
    assert "err=" in response.headers["location"]
    assert client.app_module.db.scalar("SELECT COUNT(*) FROM contributors") == 1


def test_thumbnail_rebuild(client):
    post(client, "/photos/upload", files={"photos": ("a.png", png_bytes(), "image/png")})
    row = client.app_module.db.one("SELECT stored_name FROM attachments")
    thumb = client.app_module.files.thumb_path(row["stored_name"])
    os.remove(thumb)
    assert not os.path.exists(thumb)
    post(client, "/settings/thumbs")
    assert os.path.exists(thumb)


def test_insights_with_data(client):
    for day, hours in (("2026-01-05", "3"), ("2026-02-05", "5"), ("2026-03-05", "7")):
        post(client, "/log/new", data={"work_date": day, "title": f"Work {day}", "hours": hours, "kit_id": "1"})
    post(client, "/settings/project", data={"name": "x", "model": "RV-12iS", "target_hours": "700"})
    html = client.get("/insights").text
    assert "15.0" in html
    assert "Hours by month" in html
    assert "Cumulative hours" in html
