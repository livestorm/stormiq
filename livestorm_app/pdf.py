"""Render AI outputs (markdown) as PDF with reportlab."""

from __future__ import annotations

import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, List

# Every PDF is headed by the "StormIQ for Livestorm" lockup (CLAUDE.md rule 6,
# docs/brand.md): the StormIQ wordmark, a thin divider, "for" and Livestorm's
# official on-light logo, embedded unchanged as rendered from the brand kit's
# SVG (frontend/src/assets/livestorm/logo-on-light.svg). PDFs are always light.
WORDMARK_COLOR = "#12262B"
WORDMARK_IQ = "#0B42C3"
LOCKUP_MUTED = "#5D6D79"
LOCKUP_RULE = "#D5DCDE"
LIVESTORM_LOGO_PNG = Path(__file__).resolve().parent / "assets" / "livestorm-logo-on-light.png"
LIVESTORM_LOGO_RATIO = 922 / 155  # width / height of the brand kit's file
# Same wording as the app's sidebar notice (frontend/src/App.vue), and its
# French translation for French exports.
PDF_FOOTER_TEXT = (
    "StormIQ · Early-access helper, not an official Livestorm product. "
    "Review outputs before relying on them."
)
PDF_FOOTER_TEXT_FR = (
    "StormIQ · Outil en accès anticipé, pas un produit officiel Livestorm. "
    "Vérifiez les résultats avant de vous y fier."
)

# `***x***` (bold italic) is matched first so its third asterisks don't print.
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_RULE_RE = re.compile(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}")


def inline_markdown_to_markup(line: str) -> str:
    """Escape a line for a reportlab Paragraph and render `**bold**` as bold."""
    escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = _BOLD_ITALIC_RE.sub(r"<b><i>\1</i></b>", escaped)
    return _BOLD_RE.sub(r"<b>\1</b>", escaped)


def is_horizontal_rule(line: str) -> bool:
    """A markdown separator line (`---`, `***`, `___`), which is not text."""
    return bool(_RULE_RE.fullmatch(line.strip()))


def _lockup_flowable() -> Any:
    """The "StormIQ for Livestorm" lockup: the wordmark "Storm" + "IQ" (IQ in
    blue), a thin divider, "for" and the official Livestorm logo."""
    from reportlab.lib import colors
    from reportlab.platypus import Flowable

    class StormIQLockup(Flowable):
        def __init__(self, font_size: float = 15.0, logo_height: float = 13.0) -> None:
            super().__init__()
            self.font_size = font_size
            self.logo_height = logo_height
            self.hAlign = "LEFT"

        def _parts(self):
            from reportlab.pdfbase.pdfmetrics import stringWidth

            word = stringWidth("StormIQ", "Helvetica-Bold", self.font_size)
            for_width = stringWidth("for", "Helvetica", self.font_size * 0.7)
            rule_x = word + 10
            for_x = rule_x + 10
            logo_x = for_x + for_width + 6
            logo_width = self.logo_height * LIVESTORM_LOGO_RATIO
            return rule_x, for_x, logo_x, logo_width

        def wrap(self, avail_width: float, avail_height: float):  # noqa: D401 - reportlab API
            _, _, logo_x, logo_width = self._parts()
            self.width = logo_x + logo_width
            self.height = max(self.font_size, self.logo_height) + 4
            return self.width, self.height

        def draw(self) -> None:
            from reportlab.pdfbase.pdfmetrics import stringWidth

            canvas = self.canv
            rule_x, for_x, logo_x, logo_width = self._parts()
            mid = self.height / 2
            text_y = mid - self.font_size * 0.35
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", self.font_size)
            canvas.setFillColor(colors.HexColor(WORDMARK_COLOR))
            canvas.drawString(0, text_y, "Storm")
            canvas.setFillColor(colors.HexColor(WORDMARK_IQ))
            canvas.drawString(stringWidth("Storm", "Helvetica-Bold", self.font_size), text_y, "IQ")
            canvas.setStrokeColor(colors.HexColor(LOCKUP_RULE))
            canvas.setLineWidth(0.75)
            canvas.line(rule_x, mid - 7, rule_x, mid + 7)
            canvas.setFont("Helvetica", self.font_size * 0.7)
            canvas.setFillColor(colors.HexColor(LOCKUP_MUTED))
            canvas.drawString(for_x, mid - self.font_size * 0.25, "for")
            canvas.drawImage(
                str(LIVESTORM_LOGO_PNG), logo_x, mid - self.logo_height / 2,
                width=logo_width, height=self.logo_height, mask="auto",
            )
            canvas.restoreState()

    return StormIQLockup()


def footer_text(language: str = "English") -> str:
    """The "not an official Livestorm product" footer, in the PDF's language."""
    return PDF_FOOTER_TEXT_FR if str(language or "").strip().lower() == "french" else PDF_FOOTER_TEXT


# The frame pads its content by 6 pt; the footer lines up with the body text.
_FRAME_PADDING = 6


def _footer_drawer(text: str) -> Any:
    def draw_footer(canvas: Any, doc: Any) -> None:
        from reportlab.lib import colors

        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#5D6D79"))
        canvas.drawString(doc.leftMargin + _FRAME_PADDING, doc.bottomMargin / 2, text)
        canvas.restoreState()

    return draw_footer


def build_pdf_story(markdown_text: str, title: str) -> List[Any]:
    """The flowables of a PDF: the "StormIQ for Livestorm" lockup, the title, then the markdown."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import HRFlowable, Paragraph, Spacer
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

    story: List[Any] = [_lockup_flowable(), Spacer(1, 14)]
    story.extend([Paragraph(inline_markdown_to_markup(title), title_style), Spacer(1, 8)])

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
        if is_horizontal_rule(line):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#C8D3DA"), spaceBefore=4, spaceAfter=4))
            continue
        markup = inline_markdown_to_markup(line)
        if markup.startswith("### "):
            story.append(Paragraph(markup[4:], heading_style))
        elif markup.startswith("## "):
            story.append(Paragraph(markup[3:], heading_style))
        elif markup.startswith("# "):
            story.append(Paragraph(markup[2:], heading_style))
        elif markup.startswith("- "):
            story.append(Paragraph(f"&bull; {markup[2:]}", body_style))
        else:
            story.append(Paragraph(markup, body_style))
    return story


def analysis_markdown_to_pdf_bytes(markdown_text: str, title: str, language: str = "English") -> bytes:
    story = build_pdf_story(markdown_text, title)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

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
    draw_footer = _footer_drawer(footer_text(language))
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
