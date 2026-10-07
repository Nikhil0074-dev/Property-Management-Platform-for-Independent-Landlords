import textwrap
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_pdf(title: str, lines) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    y = h - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, title)
    y -= 32
    c.setFont("Helvetica", 11)
    for line in lines:
        for part in textwrap.wrap(str(line), 90) or [""]:
            if y < 60:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = h - 60
            c.drawString(50, y, part)
            y -= 16
    c.save()
    return buf.getvalue()
