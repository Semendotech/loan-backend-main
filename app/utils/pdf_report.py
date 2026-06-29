"""
Shared ReportLab styling for all dashboard PDF reports.

Mirrors the letterhead/summary/table/footer design originally built for
get_payments_report, so every PDF report (defaulters, uncollected dues,
active loans, overdue, cleared loans, payments) looks identical.
"""

from io import BytesIO
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

EAT = ZoneInfo("Africa/Nairobi")

# ---- Brand colors ----
NAVY     = colors.HexColor("#0f2942")
SLATE    = colors.HexColor("#475569")
LIGHT_BG = colors.HexColor("#f8fafc")
BORDER   = colors.HexColor("#cbd5e1")
GOLD     = colors.HexColor("#c9a84c")

INSTITUTION_NAME = "KODONGO SAVINGS & CREDIT"
INSTITUTION_TAGLINE = "Trusted Financial Solutions"
FOOTER_DISCLAIMER = "This report is for internal use only. Kodongo Savings & Credit."


def _get_styles():
    base = getSampleStyleSheet()
    return {
        "base": base,
        "inst": ParagraphStyle("Inst", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=NAVY, leading=20),
        "tag": ParagraphStyle("Tag", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=GOLD, leading=10),
        "rt": ParagraphStyle("RT", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, leading=11, alignment=TA_RIGHT),
        "rs": ParagraphStyle("RS", parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=SLATE, leading=10, alignment=TA_RIGHT),
        "label": ParagraphStyle("Lbl", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, leading=10),
        "sl": ParagraphStyle("SL", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, leading=10, alignment=TA_CENTER),
        "sv": ParagraphStyle("SV", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, leading=16, alignment=TA_CENTER),
        "footer": ParagraphStyle("Ftr", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7, textColor=SLATE, leading=10, alignment=TA_CENTER),
    }


def _build_letterhead(styles, report_title: str, subtitle_lines: list[str]):
    """Left: institution name + tagline. Right: report title + date lines."""
    left_tbl = Table(
        [[Paragraph(INSTITUTION_NAME, styles["inst"])],
         [Paragraph(INSTITUTION_TAGLINE, styles["tag"])]],
        colWidths=[None],
    )
    left_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    right_rows = [[Paragraph(report_title, styles["rt"])]]
    for line in subtitle_lines:
        right_rows.append([Paragraph(line, styles["rs"])])
    right_tbl = Table(right_rows, colWidths=[None])
    right_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%", "40%"])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return hdr


def _build_summary_strip(styles, stats: list[tuple[str, str]]):
    """stats: list of (label, value) pairs, rendered as equal-width bordered columns."""
    if not stats:
        return None
    n = len(stats)
    width_pct = f"{100 // n}%"
    label_row = [Paragraph(label, styles["sl"]) for label, _ in stats]
    value_row = [Paragraph(value, styles["sv"]) for _, value in stats]
    tbl = Table([label_row, value_row], colWidths=[width_pct] * n)
    line_after = [("LINEAFTER", (i, 0), (i, -1), 0.5, BORDER) for i in range(n - 1)]
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        *line_after,
    ]))
    return tbl


def _style_data_table(rows, col_widths, right_align_cols=None, center_align_cols=None):
    """rows[0] is the header row. right_align_cols/center_align_cols are 0-based column indices."""
    right_align_cols = right_align_cols or []
    center_align_cols = center_align_cols or []
    tbl = Table(rows, repeatRows=1, colWidths=col_widths)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), SLATE),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, BORDER),
        ("LINEBELOW", (0, 1), (-1, -2), 0.35, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for c in right_align_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for c in center_align_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "CENTER"))
    tbl.setStyle(TableStyle(style))
    return tbl


def render_pdf_report(
    *,
    report_title: str,
    subtitle_lines: list[str],
    stats: list[tuple[str, str]],
    table_header: list[str] | None,
    table_rows: list[list[str]],
    col_widths: list[float] | None,
    right_align_cols: list[int] | None = None,
    center_align_cols: list[int] | None = None,
    no_data_message: str = "No data found for this report.",
    filename: str,
):
    """
    Assemble a full branded PDF report and return it as a StreamingResponse.

    table_rows should NOT include the header row; pass it separately via table_header.
    If table_rows is empty, no_data_message is shown instead of a table.
    """
    styles = _get_styles()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
    )

    story = []

    # Letterhead
    story.append(_build_letterhead(styles, report_title, subtitle_lines))
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=10))

    # Summary strip
    summary = _build_summary_strip(styles, stats)
    if summary is not None:
        story.append(summary)
        story.append(Spacer(1, 14))

    # Data table
    if not table_rows:
        story.append(Paragraph(no_data_message, styles["base"]["Normal"]))
    else:
        rows = [table_header] + table_rows if table_header else table_rows
        tbl = _style_data_table(
            rows,
            col_widths,
            right_align_cols=right_align_cols,
            center_align_cols=center_align_cols,
        )
        story.append(tbl)

    # Footer
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        f"Generated on {_dt.now(EAT).strftime('%d %B %Y at %H:%M EAT')}. {FOOTER_DISCLAIMER}",
        styles["footer"],
    ))

    doc.build(story)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
