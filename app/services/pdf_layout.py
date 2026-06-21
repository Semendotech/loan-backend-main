from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = A4
PAGE_MARGIN = 0.85 * inch
PAGE_BOTTOM_MARGIN = 0.85 * inch
PAGE_HEADER_HEIGHT = 1.0 * inch


def create_canvas(filepath: str) -> canvas.Canvas:
    c = canvas.Canvas(filepath, pagesize=A4)
    c.setFont("Helvetica", 9)
    return c


def start_body_y(header_height: float = PAGE_HEADER_HEIGHT) -> float:
    return PAGE_HEIGHT - header_height - 0.2 * inch


def new_page(c: canvas.Canvas, header_height: float = PAGE_HEADER_HEIGHT) -> float:
    c.showPage()
    c.setFont("Helvetica", 9)
    return start_body_y(header_height)


def ensure_space(c: canvas.Canvas, y: float, min_height: float, header_height: float = PAGE_HEADER_HEIGHT) -> float:
    if y - min_height < PAGE_BOTTOM_MARGIN:
        return new_page(c, header_height)
    return y


def inch_positions(margin_x: float, widths: list[float]) -> list[float]:
    positions = [margin_x]
    x = margin_x
    for w in widths[:-1]:
        x += w * inch
        positions.append(x)
    return positions
