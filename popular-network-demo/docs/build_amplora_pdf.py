"""Generate a professionally typeset PDF of the Amplora business plan.

Reads docs/amplora_business_plan.md and produces docs/amplora_business_plan.pdf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.platypus.tableofcontents import TableOfContents

DOCS_DIR = Path(__file__).resolve().parent
SOURCE_MD = DOCS_DIR / "amplora_business_plan.md"
OUTPUT_PDF = DOCS_DIR / "amplora_business_plan.pdf"

NAVY = HexColor("#1a3a5c")
NAVY_DARK = HexColor("#0f2540")
GOLD = HexColor("#b8956b")
INK = HexColor("#1a1a1a")
MUTED = HexColor("#5b6470")
RULE = HexColor("#c9ced6")
TABLE_HEAD_BG = HexColor("#1a3a5c")
TABLE_ROW_ALT = HexColor("#f4f6f9")
RISK_HIGH = HexColor("#b13a3a")
RISK_MED = HexColor("#a07020")


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"\s+", "-", s)


def md_inline_to_rl(text: str) -> str:
    """Convert Markdown inline formatting to ReportLab paragraph markup."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', text)
    return text


def build_styles() -> dict:
    base = getSampleStyleSheet()
    styles = {}

    styles["CoverTitle"] = ParagraphStyle(
        "CoverTitle",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=72,
        leading=78,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=8,
        spaceBefore=0,
    )
    styles["CoverSubtitle"] = ParagraphStyle(
        "CoverSubtitle",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=22,
        leading=28,
        textColor=GOLD,
        alignment=TA_CENTER,
        spaceAfter=0,
    )
    styles["CoverTagline"] = ParagraphStyle(
        "CoverTagline",
        parent=base["Normal"],
        fontName="Times-Italic",
        fontSize=14,
        leading=20,
        textColor=MUTED,
        alignment=TA_CENTER,
    )
    styles["CoverMeta"] = ParagraphStyle(
        "CoverMeta",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=18,
        textColor=INK,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    styles["CoverMetaLabel"] = ParagraphStyle(
        "CoverMetaLabel",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=14,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=0,
    )

    styles["TOCTitle"] = ParagraphStyle(
        "TOCTitle",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=28,
        textColor=NAVY,
        alignment=TA_LEFT,
        spaceAfter=18,
    )

    styles["H1"] = ParagraphStyle(
        "H1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=26,
        textColor=NAVY,
        spaceBefore=6,
        spaceAfter=6,
        keepWithNext=True,
    )
    styles["H2"] = ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=20,
        textColor=NAVY_DARK,
        spaceBefore=14,
        spaceAfter=4,
        keepWithNext=True,
    )
    styles["H3"] = ParagraphStyle(
        "H3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=GOLD,
        spaceBefore=10,
        spaceAfter=2,
        keepWithNext=True,
    )

    styles["Body"] = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Times-Roman",
        fontSize=11,
        leading=16,
        textColor=INK,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
        firstLineIndent=0,
    )
    styles["Bullet"] = ParagraphStyle(
        "Bullet",
        parent=styles["Body"],
        leftIndent=18,
        bulletIndent=4,
        spaceAfter=3,
        alignment=TA_LEFT,
    )
    styles["Callout"] = ParagraphStyle(
        "Callout",
        parent=styles["Body"],
        fontName="Times-Italic",
        textColor=MUTED,
        leftIndent=14,
        rightIndent=14,
        borderPadding=(8, 10, 8, 10),
        backColor=HexColor("#f7f4ed"),
        alignment=TA_LEFT,
        spaceBefore=6,
        spaceAfter=10,
    )
    styles["Closing"] = ParagraphStyle(
        "Closing",
        parent=base["Normal"],
        fontName="Times-Italic",
        fontSize=10,
        leading=15,
        textColor=MUTED,
        alignment=TA_LEFT,
        spaceBefore=12,
    )

    return styles


class AmploraDoc(BaseDocTemplate):
    """Custom doc template with cover and body page templates + TOC support."""

    def __init__(self, filename: str, **kwargs):
        super().__init__(
            filename,
            pagesize=LETTER,
            leftMargin=0.9 * inch,
            rightMargin=0.9 * inch,
            topMargin=0.85 * inch,
            bottomMargin=0.85 * inch,
            title="Amplora — Business Plan",
            author="Amplify Media Group",
            subject="Amplora platform business plan",
            **kwargs,
        )
        self.toc_entries: list[tuple[int, str, int, str]] = []

        page_w, page_h = LETTER

        cover_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            page_w - self.leftMargin - self.rightMargin,
            page_h - self.topMargin - self.bottomMargin,
            id="cover",
            showBoundary=0,
        )
        body_frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            page_w - self.leftMargin - self.rightMargin,
            page_h - self.topMargin - self.bottomMargin,
            id="body",
            showBoundary=0,
        )

        self.addPageTemplates([
            PageTemplate(id="Cover", frames=[cover_frame], onPage=self._on_cover),
            PageTemplate(id="Body", frames=[body_frame], onPage=self._on_body),
        ])

    def _on_cover(self, canvas_obj, _doc):
        canvas_obj.saveState()
        page_w, page_h = LETTER
        canvas_obj.setFillColor(NAVY)
        canvas_obj.rect(0, page_h - 0.55 * inch, page_w, 0.55 * inch, fill=1, stroke=0)
        canvas_obj.setFillColor(GOLD)
        canvas_obj.rect(0, page_h - 0.62 * inch, page_w, 0.07 * inch, fill=1, stroke=0)
        canvas_obj.setFillColor(NAVY)
        canvas_obj.rect(0, 0, page_w, 0.4 * inch, fill=1, stroke=0)
        canvas_obj.restoreState()

    def _on_body(self, canvas_obj, doc):
        canvas_obj.saveState()
        page_w, _ = LETTER

        canvas_obj.setStrokeColor(RULE)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(
            self.leftMargin,
            self.bottomMargin - 0.35 * inch,
            page_w - self.rightMargin,
            self.bottomMargin - 0.35 * inch,
        )

        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.setFillColor(MUTED)
        canvas_obj.drawString(
            self.leftMargin,
            self.bottomMargin - 0.55 * inch,
            "Amplora — Business Plan",
        )
        canvas_obj.drawRightString(
            page_w - self.rightMargin,
            self.bottomMargin - 0.55 * inch,
            f"Page {doc.page}",
        )

        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(MUTED)
        canvas_obj.drawString(
            self.leftMargin,
            self.pagesize[1] - self.topMargin + 0.35 * inch,
            "AMPLIFY MEDIA GROUP   /   CONFIDENTIAL",
        )
        canvas_obj.drawRightString(
            page_w - self.rightMargin,
            self.pagesize[1] - self.topMargin + 0.35 * inch,
            "May 2026",
        )
        canvas_obj.setStrokeColor(RULE)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(
            self.leftMargin,
            self.pagesize[1] - self.topMargin + 0.25 * inch,
            page_w - self.rightMargin,
            self.pagesize[1] - self.topMargin + 0.25 * inch,
        )
        canvas_obj.restoreState()

    def afterFlowable(self, flowable):
        """Capture headings for the table of contents."""
        if isinstance(flowable, Paragraph):
            style_name = flowable.style.name
            if style_name in ("H1", "H2"):
                level = 0 if style_name == "H1" else 1
                text = flowable.getPlainText()
                self.notify("TOCEntry", (level, text, self.page))


def make_toc(styles: dict) -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOCLevel0",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=22,
            leftIndent=0,
            firstLineIndent=0,
            textColor=NAVY,
            spaceBefore=4,
        ),
        ParagraphStyle(
            "TOCLevel1",
            fontName="Times-Roman",
            fontSize=10,
            leading=16,
            leftIndent=22,
            firstLineIndent=0,
            textColor=INK,
        ),
    ]
    return toc


def parse_table_block(lines: list[str], start_idx: int) -> tuple[list[list[str]], int]:
    """Parse a markdown pipe-table starting at start_idx (header line).

    Returns (rows, next_idx) where rows[0] is the header.
    """
    rows: list[list[str]] = []
    i = start_idx
    while i < len(lines) and lines[i].strip().startswith("|"):
        line = lines[i].strip()
        if re.match(r"^\|[\s\-:|]+\|$", line):
            i += 1
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows, i


def build_risk_table(rows: list[list[str]], styles: dict) -> Table:
    cell_style = ParagraphStyle(
        "RiskCell",
        parent=styles["Body"],
        fontSize=9.5,
        leading=13,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    header_style = ParagraphStyle(
        "RiskHead",
        parent=styles["Body"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=white,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    severity_style_high = ParagraphStyle(
        "SevHigh",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=RISK_HIGH,
        alignment=TA_CENTER,
    )
    severity_style_med = ParagraphStyle(
        "SevMed",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=RISK_MED,
        alignment=TA_CENTER,
    )

    table_data = []
    header = rows[0]
    table_data.append([Paragraph(md_inline_to_rl(c), header_style) for c in header])

    for r in rows[1:]:
        risk_p = Paragraph(md_inline_to_rl(r[0]), cell_style)
        sev_text = r[1].strip().lower()
        if "high" in sev_text:
            sev_p = Paragraph(r[1], severity_style_high)
        elif "med" in sev_text:
            sev_p = Paragraph(r[1], severity_style_med)
        else:
            sev_p = Paragraph(r[1], cell_style)
        mit_p = Paragraph(md_inline_to_rl(r[2]), cell_style)
        table_data.append([risk_p, sev_p, mit_p])

    table = Table(
        table_data,
        colWidths=[1.85 * inch, 0.85 * inch, 4.05 * inch],
        repeatRows=1,
    )
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, NAVY_DARK),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
    ]
    for idx in range(1, len(table_data)):
        if idx % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, idx), (-1, idx), TABLE_ROW_ALT))
        style_cmds.append(("LINEBELOW", (0, idx), (-1, idx), 0.3, RULE))
    table.setStyle(TableStyle(style_cmds))
    return table


def build_pricing_multiplier_table(styles: dict) -> Table:
    rows = [
        ["Reach Tier", "Coverage", "Multiplier"],
        ["Local Reach", "Selling publication + its territory chatbot", "1.0×"],
        ["Regional Reach", "Adjacent territories within 50 miles", "1.5×"],
        ["Network Reach", "Full network within 100 miles", "2.0×"],
        ["Maximum Reach", "Every licensed territory", "2.5–3.0×"],
    ]
    cell = ParagraphStyle(
        "PrCell",
        parent=styles["Body"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    head = ParagraphStyle(
        "PrHead",
        parent=cell,
        fontName="Helvetica-Bold",
        textColor=white,
    )
    data = [[Paragraph(c, head) for c in rows[0]]] + [
        [Paragraph(c, cell) for c in r] for r in rows[1:]
    ]
    t = Table(data, colWidths=[1.6 * inch, 3.6 * inch, 1.55 * inch], repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, NAVY_DARK),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ROW_ALT))
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    t.setStyle(TableStyle(cmds))
    return t


def build_cover(styles: dict) -> list:
    flow = []
    flow.append(Spacer(1, 1.4 * inch))

    flow.append(Paragraph(
        '<font color="#5b6470" size="10"><b>AMPLIFY MEDIA GROUP</b></font>',
        styles["CoverMetaLabel"],
    ))
    flow.append(Spacer(1, 0.2 * inch))

    flow.append(Paragraph("Amplora", styles["CoverTitle"]))
    flow.append(Spacer(1, 0.05 * inch))
    flow.append(Paragraph("Business Plan", styles["CoverSubtitle"]))

    flow.append(Spacer(1, 0.4 * inch))
    flow.append(HRFlowable(
        width="35%", thickness=1.2, color=GOLD,
        hAlign="CENTER", spaceBefore=0, spaceAfter=0,
    ))
    flow.append(Spacer(1, 0.35 * inch))

    flow.append(Paragraph(
        "A multi-tenant local commerce and marketing platform<br/>"
        "for newspaper publishers and the businesses they serve.",
        styles["CoverTagline"],
    ))

    flow.append(Spacer(1, 1.6 * inch))

    flow.append(Paragraph("DOCUMENT VERSION", styles["CoverMetaLabel"]))
    flow.append(Paragraph("v1 — Pilot launch plan with Phase 1.5 and Phase 2 roadmap", styles["CoverMeta"]))
    flow.append(Spacer(1, 0.2 * inch))
    flow.append(Paragraph("PREPARED", styles["CoverMetaLabel"]))
    flow.append(Paragraph("May 2026", styles["CoverMeta"]))
    flow.append(Spacer(1, 0.2 * inch))
    flow.append(Paragraph("CLASSIFICATION", styles["CoverMetaLabel"]))
    flow.append(Paragraph("Confidential — for partner and investor review", styles["CoverMeta"]))

    return flow


def build_toc_page(styles: dict) -> tuple[list, TableOfContents]:
    toc = make_toc(styles)
    flow = [
        Paragraph("Contents", styles["TOCTitle"]),
        HRFlowable(width="100%", thickness=0.6, color=RULE, spaceAfter=12),
        toc,
        PageBreak(),
    ]
    return flow, toc


def render_body(md_text: str, styles: dict) -> list:
    """Convert the cleaned-up markdown body into reportlab flowables."""
    flow = []

    lines = md_text.splitlines()
    i = 0
    skip_first_h1 = True
    in_list: list[str] | None = None

    def flush_list():
        nonlocal in_list
        if in_list is not None:
            for item in in_list:
                flow.append(Paragraph(
                    f'<bullet>&bull;</bullet> {md_inline_to_rl(item)}',
                    styles["Bullet"],
                ))
            flow.append(Spacer(1, 4))
            in_list = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_list()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            i += 1
            continue

        if stripped == "---":
            flush_list()
            i += 1
            continue

        if not stripped:
            flush_list()
            i += 1
            continue

        if stripped.startswith("> "):
            flush_list()
            text = stripped[2:].strip()
            text = re.sub(r"^\*\*(.+?):\*\*\s*", r"<b>\1:</b> ", text)
            flow.append(Paragraph(md_inline_to_rl(text), styles["Callout"]))
            i += 1
            continue

        if stripped.startswith("# "):
            flush_list()
            if skip_first_h1:
                skip_first_h1 = False
                i += 1
                continue
            flow.append(Paragraph(stripped[2:].strip(), styles["H1"]))
            i += 1
            continue

        if stripped.startswith("## "):
            flush_list()
            heading_text = stripped[3:].strip()
            flow.append(HRFlowable(
                width="100%", thickness=0.5, color=RULE,
                spaceBefore=18, spaceAfter=4,
            ))
            flow.append(Paragraph(heading_text, styles["H1"]))
            i += 1
            continue

        if stripped.startswith("### "):
            flush_list()
            flow.append(Paragraph(stripped[4:].strip(), styles["H2"]))
            i += 1
            continue

        if stripped.startswith("#### "):
            flush_list()
            flow.append(Paragraph(stripped[5:].strip(), styles["H3"]))
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            flush_list()
            rows, next_i = parse_table_block(lines, i)
            if len(rows[0]) == 3 and rows[0][0].lower() == "risk":
                flow.append(Spacer(1, 6))
                flow.append(build_risk_table(rows, styles))
                flow.append(Spacer(1, 6))
            else:
                flow.append(_build_generic_table(rows, styles))
                flow.append(Spacer(1, 6))
            i = next_i
            continue

        if stripped.startswith("- "):
            if in_list is None:
                in_list = []
            in_list.append(stripped[2:])
            i += 1
            continue

        if line.startswith("  - ") and in_list is not None:
            in_list.append("&nbsp;&nbsp;&nbsp;&nbsp;◦ " + line.strip()[2:])
            i += 1
            continue

        if stripped.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            if in_list is None:
                in_list = []
            num_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
            if num_match:
                num, body = num_match.group(1), num_match.group(2)
                in_list.append(f"<b>{num}.</b> {body}")
                i += 1
                continue

        if stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            flush_list()
            flow.append(Paragraph(md_inline_to_rl(stripped), styles["Closing"]))
            i += 1
            continue

        flush_list()
        para_lines = [line]
        j = i + 1
        while j < len(lines) and lines[j].strip() and not _is_block_start(lines[j]):
            para_lines.append(lines[j])
            j += 1
        text = " ".join(p.strip() for p in para_lines)
        flow.append(Paragraph(md_inline_to_rl(text), styles["Body"]))
        i = j

    flush_list()
    return flow


def _is_block_start(line: str) -> bool:
    s = line.strip()
    return (
        s.startswith("#")
        or s.startswith("- ")
        or s.startswith("> ")
        or s.startswith("|")
        or s.startswith("```")
        or s == "---"
        or bool(re.match(r"^\d+\.\s", s))
    )


def _build_generic_table(rows: list[list[str]], styles: dict) -> Table:
    cell = ParagraphStyle(
        "GCell",
        parent=styles["Body"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    head = ParagraphStyle("GHead", parent=cell, fontName="Helvetica-Bold", textColor=white)
    data = [[Paragraph(md_inline_to_rl(c), head) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(md_inline_to_rl(c), cell) for c in r])
    n_cols = len(rows[0])
    avail = LETTER[0] - 1.8 * inch
    col_w = [avail / n_cols] * n_cols
    t = Table(data, colWidths=col_w, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, NAVY_DARK),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ROW_ALT))
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    t.setStyle(TableStyle(cmds))
    return t


def main():
    md_text = SOURCE_MD.read_text(encoding="utf-8")
    styles = build_styles()

    doc = AmploraDoc(str(OUTPUT_PDF))

    story = []

    story.extend(build_cover(styles))
    story.append(PageBreak())

    story.append({"_template": "Body"})

    toc_flow, _toc = build_toc_page(styles)

    final_story = []
    for item in story:
        if isinstance(item, dict) and item.get("_template") == "Body":
            from reportlab.platypus.doctemplate import NextPageTemplate
            final_story.append(NextPageTemplate("Body"))
        else:
            final_story.append(item)
    final_story.extend(toc_flow)

    body_flow = render_body(md_text, styles)
    final_story.extend(body_flow)

    doc.multiBuild(final_story)
    print(f"Wrote {OUTPUT_PDF}")


if __name__ == "__main__":
    main()
