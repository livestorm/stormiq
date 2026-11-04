"""Render AI outputs (markdown) as PDF with reportlab."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from typing import List


def analysis_markdown_to_pdf_bytes(markdown_text: str, title: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError(
            f"PDF export unavailable. Install with: `{sys.executable} -m pip install reportlab`"
        ) from exc

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#0F1D21"),
    )
    heading_style = ParagraphStyle(
        "Heading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#12262B"),
        spaceBefore=8,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title=title,
    )

    story = []
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "icons" / "Logo-Livestorm-Primary.png"
    if logo_path.exists():
        image_reader = ImageReader(str(logo_path))
        original_width, original_height = image_reader.getSize()
        target_width = 40 * mm
        target_height = target_width * (float(original_height) / float(original_width)) if original_width else 10 * mm
        logo = Image(str(logo_path), width=target_width, height=target_height)
        logo.hAlign = "LEFT"
        story.extend([logo, Spacer(1, 14)])

    story.extend([Paragraph(title, title_style), Spacer(1, 8)])

    cleaned_lines: List[str] = []
    skipped_summary_heading = False
    summary_heading_tokens = {
        "event summary",
        "# event summary",
        "resume de l evenement",
        "# resume de l evenement",
        "resume evenement",
        "# resume evenement",
        "résumé de l'événement",
        "# résumé de l'événement",
        "resume de l'evenement",
        "# resume de l'evenement",
    }

    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()
        normalized = (
            stripped.lower()
            .replace("’", "'")
            .replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("ù", "u")
        )
        if not skipped_summary_heading and normalized in summary_heading_tokens:
            skipped_summary_heading = True
            continue
        cleaned_lines.append(raw_line)

    for line in [line.strip() for line in cleaned_lines]:
        if not line:
            story.append(Spacer(1, 5))
            continue
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if escaped.startswith("### "):
            story.append(Paragraph(escaped[4:], heading_style))
        elif escaped.startswith("## "):
            story.append(Paragraph(escaped[3:], heading_style))
        elif escaped.startswith("# "):
            story.append(Paragraph(escaped[2:], heading_style))
        elif escaped.startswith("- "):
            story.append(Paragraph(f"&bull; {escaped[2:]}", body_style))
        else:
            story.append(Paragraph(escaped, body_style))

    doc.build(story)
    return buffer.getvalue()
