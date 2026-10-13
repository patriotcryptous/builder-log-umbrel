"""Printable builder log PDF, in Letter or A4, with optional photos."""
from __future__ import annotations

import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import db, files

ACCENT = colors.HexColor("#7C3AED")
INK = colors.HexColor("#1b1b1b")
MUTED = colors.HexColor("#6b6b6b")
RULE = colors.HexColor("#d8d8d8")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            textColor=MUTED,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=ACCENT,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "entry": ParagraphStyle(
            "entry",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=INK,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12.5,
            textColor=INK,
        ),
        "meta": ParagraphStyle(
            "meta",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=11.5,
            textColor=INK,
        ),
    }


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    width, _ = doc.pagesize
    canvas.drawString(doc.leftMargin, 0.52 * inch, doc.hbl_footer)
    canvas.drawRightString(
        width - doc.rightMargin, 0.52 * inch, f"Page {canvas.getPageNumber()}"
    )
    canvas.setStrokeColor(RULE)
    canvas.line(
        doc.leftMargin, 0.72 * inch, width - doc.rightMargin, 0.72 * inch
    )
    canvas.restoreState()


def builder_log_pdf(
    page_size: str = "letter",
    include_photos: bool = True,
    include_summary: bool = True,
    include_inventory: bool = False,
    include_costs: bool = False,
) -> bytes:
    """Build the log as a PDF. Photos are placed under their entries."""
    styles = _styles()
    buffer = io.BytesIO()
    size = A4 if page_size.lower() == "a4" else letter
    project = db.project()
    title = f"{project['name']} — Builder Log"

    doc = SimpleDocTemplate(
        buffer,
        pagesize=size,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.9 * inch,
        title=title,
        author=project["builder"] or "Builder",
    )
    doc.hbl_footer = f"{project['name']} · {project['model']} · printed {date.today():%Y-%m-%d}"

    story: list = []
    story.append(Paragraph(_escape(title), styles["title"]))
    facts = [
        project["model"],
        f"Kit {project['kit_number']}" if project["kit_number"] else "",
        project["n_number"],
        f"Builder: {project['builder']}" if project["builder"] else "",
        f"Started {project['started_on']}" if project["started_on"] else "",
    ]
    story.append(
        Paragraph(_escape(" · ".join(f for f in facts if f)), styles["subtitle"])
    )
    story.append(Spacer(1, 10))

    total_hours = float(db.scalar("SELECT SUM(hours) FROM entries", (), 0.0) or 0)
    n_entries = int(db.scalar("SELECT COUNT(*) FROM entries") or 0)
    story.append(
        Paragraph(
            f"{n_entries} log entries · {total_hours:,.1f} build hours · "
            f"{db.overall_percent():.0f}% complete",
            styles["body"],
        )
    )

    if include_summary:
        story.append(Paragraph("Kit progress", styles["h2"]))
        data = [["Kit", "Complete", "Hours", "Sections"]]
        for kit in db.kits_with_progress():
            data.append(
                [
                    Paragraph(_escape(kit["name"]), styles["cell"]),
                    f"{kit['percent']:.0f}%",
                    f"{kit['hours']:,.1f}",
                    f"{kit['sections_done']}/{kit['sections_total']}",
                ]
            )
        story.append(_table(data, doc, [0.46, 0.18, 0.18, 0.18]))

        people = db.query(
            "SELECT c.name, COALESCE(SUM(ec.hours), 0) AS hours"
            " FROM contributors c"
            " LEFT JOIN entry_contributors ec ON ec.contributor_id = c.id"
            " GROUP BY c.id ORDER BY hours DESC, c.name"
        )
        if people:
            story.append(Paragraph("Contributors", styles["h2"]))
            rows = [["Name", "Logged hours"]]
            for person in people:
                rows.append(
                    [
                        Paragraph(_escape(person["name"]), styles["cell"]),
                        f"{float(person['hours'] or 0):,.1f}",
                    ]
                )
            story.append(_table(rows, doc, [0.7, 0.3]))

    story.append(Paragraph("Work log", styles["h2"]))
    entries = db.query(
        "SELECT e.*, k.name AS kit_name, s.name AS section_name"
        " FROM entries e"
        " LEFT JOIN kits k ON k.id = e.kit_id"
        " LEFT JOIN sections s ON s.id = e.section_id"
        " ORDER BY e.work_date, e.id"
    )
    if not entries:
        story.append(Paragraph("No entries logged yet.", styles["meta"]))

    for entry in entries:
        head = f"{entry['work_date']} — {entry['title']}"
        story.append(Paragraph(_escape(head), styles["entry"]))
        bits = [
            entry["kit_name"] or "",
            entry["section_name"] or "",
            f"{float(entry['hours'] or 0):g} h" if entry["hours"] else "",
        ]
        people = db.query(
            "SELECT c.name, ec.hours FROM entry_contributors ec"
            " JOIN contributors c ON c.id = ec.contributor_id"
            " WHERE ec.entry_id = ? ORDER BY c.name",
            (entry["id"],),
        )
        if people:
            bits.append("with " + ", ".join(p["name"] for p in people))
        meta = " · ".join(b for b in bits if b)
        if meta:
            story.append(Paragraph(_escape(meta), styles["meta"]))
        if entry["notes"]:
            story.append(Paragraph(_escape(entry["notes"]), styles["body"]))

        if include_photos:
            photos = db.query(
                "SELECT * FROM attachments WHERE entry_id = ? AND kind = 'photo'"
                " ORDER BY id",
                (entry["id"],),
            )
            for photo in photos:
                flowable = _photo_flowable(photo, doc)
                if flowable is not None:
                    story.append(Spacer(1, 4))
                    story.append(flowable)
                    notes = db.query(
                        "SELECT number, label FROM annotations WHERE attachment_id = ?"
                        " ORDER BY number",
                        (photo["id"],),
                    )
                    caption_bits = []
                    if photo["caption"]:
                        caption_bits.append(photo["caption"])
                    for note in notes:
                        if note["label"]:
                            caption_bits.append(f"{note['number']}. {note['label']}")
                    if caption_bits:
                        story.append(
                            Paragraph(_escape(" · ".join(caption_bits)), styles["meta"])
                        )
        story.append(Spacer(1, 10))

    if include_inventory:
        story.append(PageBreak())
        story.append(Paragraph("Inventory", styles["h2"]))
        rows = [["Part", "Description", "Exp.", "Rec.", "Status"]]
        for item in db.query(
            "SELECT i.*, k.name AS kit_name FROM inventory i"
            " LEFT JOIN kits k ON k.id = i.kit_id"
            " ORDER BY k.sort_order, i.part_number, i.id"
        ):
            rows.append(
                [
                    Paragraph(_escape(item["part_number"]), styles["cell"]),
                    Paragraph(_escape(item["description"]), styles["cell"]),
                    f"{float(item['qty_expected'] or 0):g}",
                    f"{float(item['qty_received'] or 0):g}",
                    item["status"],
                ]
            )
        story.append(_table(rows, doc, [0.17, 0.44, 0.1, 0.1, 0.19]))

    if include_costs:
        story.append(Paragraph("Costs", styles["h2"]))
        rows = [["Date", "Vendor", "Category", "Amount"]]
        for cost in db.query("SELECT * FROM costs ORDER BY spent_on, id"):
            rows.append(
                [
                    cost["spent_on"],
                    Paragraph(_escape(cost["vendor"]), styles["cell"]),
                    Paragraph(_escape(cost["category"]), styles["cell"]),
                    f"${int(cost['amount_cents'] or 0) / 100:,.2f}",
                ]
            )
        total = int(db.scalar("SELECT SUM(amount_cents) FROM costs", (), 0) or 0)
        rows.append(["", "", "Total", f"${total / 100:,.2f}"])
        story.append(_table(rows, doc, [0.17, 0.35, 0.28, 0.2]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _table(data: list, doc, ratios: list[float]) -> Table:
    width = doc.width
    table = Table(data, colWidths=[width * r for r in ratios], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f6f6")]),
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _photo_flowable(photo, doc) -> RLImage | None:
    """Prefer the thumbnail: it is already rotated, small and always JPEG."""
    candidates = []
    thumb = files.thumb_path(photo["stored_name"])
    if os.path.exists(thumb):
        candidates.append(thumb)
    try:
        original = files.stored_path(photo["stored_name"])
        if os.path.exists(original) and photo["mime"] in ("image/jpeg", "image/png"):
            candidates.append(original)
    except files.UploadError:
        pass
    for path in candidates:
        try:
            image = RLImage(path)
            max_w = doc.width * 0.62
            max_h = 3.1 * inch
            scale = min(max_w / image.imageWidth, max_h / image.imageHeight, 1.0)
            image.drawWidth = image.imageWidth * scale
            image.drawHeight = image.imageHeight * scale
            image.hAlign = "LEFT"
            return image
        except Exception:
            continue
    return None
