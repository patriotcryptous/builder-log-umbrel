"""Server-rendered SVG charts.

Colours and type come from CSS classes in app.css so the charts follow the
light and dark themes. No inline styles, to satisfy the strict CSP.
"""
from __future__ import annotations

from html import escape


def _fmt(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def empty(message: str = "No data yet") -> str:
    return f'<p class="chart-empty">{escape(message)}</p>'


def progress_ring(percent: float, label: str = "", size: int = 132) -> str:
    """A donut showing one completion percentage."""
    pct = max(0.0, min(100.0, float(percent or 0)))
    radius = size / 2 - 11
    circumference = 2 * 3.141592653589793 * radius
    dash = circumference * pct / 100
    center = size / 2
    return (
        f'<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}"'
        f' role="img" aria-label="{escape(label or "Progress")}: {pct:.0f} percent">'
        f'<circle class="ring-track" cx="{center}" cy="{center}" r="{radius}"'
        f' fill="none" stroke-width="11" />'
        f'<circle class="ring-value" cx="{center}" cy="{center}" r="{radius}"'
        f' fill="none" stroke-width="11" stroke-linecap="round"'
        f' stroke-dasharray="{dash:.2f} {circumference:.2f}"'
        f' transform="rotate(-90 {center} {center})" />'
        f'<text class="ring-pct" x="{center}" y="{center + 2}" text-anchor="middle">'
        f"{pct:.0f}%</text>"
        f'<text class="ring-label" x="{center}" y="{center + 20}" text-anchor="middle">'
        f"{escape(label)}</text>"
        "</svg>"
    )


def bar_chart(
    data: list[tuple[str, float]],
    unit: str = "",
    height: int = 210,
    width: int = 720,
) -> str:
    """Vertical bars with value labels, e.g. hours per month."""
    data = [(str(k), float(v or 0)) for k, v in data]
    if not data or all(v == 0 for _, v in data):
        return empty()
    pad_left, pad_right, pad_top, pad_bottom = 46, 12, 18, 34
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    top = max(v for _, v in data) or 1
    step = plot_w / len(data)
    bar_w = min(46.0, step * 0.62)

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="Bar chart" preserveAspectRatio="xMidYMid meet">'
    ]
    for i in range(5):
        y = pad_top + plot_h * i / 4
        value = top * (4 - i) / 4
        parts.append(
            f'<line class="chart-grid" x1="{pad_left}" y1="{y:.1f}"'
            f' x2="{width - pad_right}" y2="{y:.1f}" />'
        )
        parts.append(
            f'<text class="chart-tick" x="{pad_left - 8}" y="{y + 4:.1f}"'
            f' text-anchor="end">{_fmt(value)}</text>'
        )
    for i, (label, value) in enumerate(data):
        bar_h = plot_h * (value / top)
        x = pad_left + step * i + (step - bar_w) / 2
        y = pad_top + plot_h - bar_h
        parts.append(
            f'<rect class="chart-bar" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}"'
            f' height="{max(bar_h, 1):.1f}" rx="3">'
            f"<title>{escape(label)}: {_fmt(value)} {escape(unit)}</title></rect>"
        )
        if value > 0:
            parts.append(
                f'<text class="chart-value" x="{x + bar_w / 2:.1f}"'
                f' y="{y - 5:.1f}" text-anchor="middle">{_fmt(value)}</text>'
            )
        parts.append(
            f'<text class="chart-label" x="{pad_left + step * i + step / 2:.1f}"'
            f' y="{height - 12}" text-anchor="middle">{escape(label)}</text>'
        )
    parts.append(
        f'<line class="chart-axis" x1="{pad_left}" y1="{pad_top + plot_h}"'
        f' x2="{width - pad_right}" y2="{pad_top + plot_h}" />'
    )
    parts.append("</svg>")
    return "".join(parts)


def area_chart(
    data: list[tuple[str, float]],
    unit: str = "",
    height: int = 210,
    width: int = 720,
) -> str:
    """A filled line, e.g. cumulative hours over time."""
    data = [(str(k), float(v or 0)) for k, v in data]
    if len(data) < 2:
        return empty("Not enough data yet")
    pad_left, pad_right, pad_top, pad_bottom = 46, 12, 18, 34
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    top = max(v for _, v in data) or 1
    span = len(data) - 1

    points = []
    for i, (_, value) in enumerate(data):
        x = pad_left + plot_w * i / span
        y = pad_top + plot_h - plot_h * (value / top)
        points.append((x, y))

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (
        f"{pad_left:.1f},{pad_top + plot_h:.1f} "
        + line
        + f" {pad_left + plot_w:.1f},{pad_top + plot_h:.1f}"
    )

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="Trend chart" preserveAspectRatio="xMidYMid meet">'
    ]
    for i in range(5):
        y = pad_top + plot_h * i / 4
        value = top * (4 - i) / 4
        parts.append(
            f'<line class="chart-grid" x1="{pad_left}" y1="{y:.1f}"'
            f' x2="{width - pad_right}" y2="{y:.1f}" />'
        )
        parts.append(
            f'<text class="chart-tick" x="{pad_left - 8}" y="{y + 4:.1f}"'
            f' text-anchor="end">{_fmt(value)}</text>'
        )
    parts.append(f'<polygon class="chart-area" points="{area}" />')
    parts.append(f'<polyline class="chart-line" points="{line}" fill="none" />')
    for (x, y), (label, value) in zip(points, data):
        parts.append(
            f'<circle class="chart-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.2">'
            f"<title>{escape(label)}: {_fmt(value)} {escape(unit)}</title></circle>"
        )
    label_every = max(1, len(data) // 9)
    for i, (label, _) in enumerate(data):
        if i % label_every == 0 or i == len(data) - 1:
            x = pad_left + plot_w * i / span
            parts.append(
                f'<text class="chart-label" x="{x:.1f}" y="{height - 12}"'
                f' text-anchor="middle">{escape(label)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def hbar_chart(data: list[tuple[str, float]], unit: str = "", width: int = 720) -> str:
    """Horizontal bars for named categories, e.g. hours per kit."""
    data = [(str(k), float(v or 0)) for k, v in data]
    data = [d for d in data if d[1] > 0]
    if not data:
        return empty()
    row_h = 30
    pad_top, pad_bottom = 8, 8
    label_w = 150
    value_w = 74
    height = pad_top + pad_bottom + row_h * len(data)
    plot_w = width - label_w - value_w
    top = max(v for _, v in data) or 1

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="Comparison chart" preserveAspectRatio="xMidYMid meet">'
    ]
    for i, (label, value) in enumerate(data):
        y = pad_top + row_h * i
        bar_w = max(2.0, plot_w * (value / top))
        parts.append(
            f'<text class="chart-label chart-label-left" x="0" y="{y + 19}">'
            f"{escape(label[:26])}</text>"
        )
        parts.append(
            f'<rect class="chart-bar" x="{label_w}" y="{y + 6}" width="{bar_w:.1f}"'
            f' height="17" rx="3"><title>{escape(label)}: {_fmt(value)}'
            f" {escape(unit)}</title></rect>"
        )
        parts.append(
            f'<text class="chart-value chart-label-left"'
            f' x="{label_w + bar_w + 8:.1f}" y="{y + 19}">{_fmt(value)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def stacked_meter(parts_data: list[tuple[str, float, str]], width: int = 720) -> str:
    """A single horizontal bar split into labelled segments (inventory status)."""
    total = sum(max(0.0, float(v)) for _, v, _ in parts_data)
    if total <= 0:
        return empty()
    height = 26
    out = [
        f'<svg class="chart meter" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="Status breakdown" preserveAspectRatio="none">'
    ]
    x = 0.0
    for label, value, css in parts_data:
        value = max(0.0, float(value))
        if value <= 0:
            continue
        seg = width * value / total
        out.append(
            f'<rect class="seg {css}" x="{x:.1f}" y="0" width="{seg:.1f}"'
            f' height="{height}"><title>{escape(label)}: {_fmt(value)}</title></rect>'
        )
        x += seg
    out.append("</svg>")
    return "".join(out)
