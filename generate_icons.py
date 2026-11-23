"""Generate a matching set of transparent, high-resolution PNG icons."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).parent / "web" / "assets" / "icons"
SHAPES = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "tasks": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "browser": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c-3 3-3 15 0 18M12 3c3 3 3 15 0 18"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M10 2h4l.5 2.2 1.5.9 2.1-.7 2 3.5-1.6 1.5v1.8l1.6 1.5-2 3.5-2.1-.7-1.5.9L14 22h-4l-.5-2.2-1.5-.9-2.1.7-2-3.5 1.6-1.5v-1.8L3.9 9.3l2-3.5 2.1.7 1.5-.9z"/>',
    "plus": '<path d="M12 4v16M4 12h16"/>',
    "paste": '<rect x="5" y="5" width="14" height="17" rx="2"/><path d="M9 5V3h6v2M8 11h8M8 15h7"/>',
    "download": '<path d="M12 3v13m-4-4 4 4 4-4M4 18v3h16v-3"/>',
    "folder": '<path d="M3 6h7l2 2h9v11H3z"/>',
    "check": '<path d="M4 12l5 5L20 6"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1"/><path d="m4 18 6-6 4 3 3-3 4 4"/>',
    "video": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    "delete": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/>',
    "play": '<path d="m8 5 11 7-11 7z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "back": '<path d="m15 4-8 8 8 8M7 12h14"/>',
    "forward": '<path d="m9 4 8 8-8 8M17 12H3"/>',
    "refresh": '<path d="M20 8a9 9 0 0 0-16-1M4 4v3h3M4 16a9 9 0 0 0 16 1m0 3v-3h-3"/>',
    "login": '<path d="M9 4H4v16h5M13 7l5 5-5 5M8 12h10"/>',
}


def render(svg: str, target: Path, size: int) -> None:
    image = QPixmap(size, size)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(target)):
        raise RuntimeError(f"Unable to save {target}")


def main() -> None:
    QApplication([])
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, shape in SHAPES.items():
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">' + shape + '</svg>'
        render(svg, ROOT / f"{name}.png", 96)
    app_icon = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
      <defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#8560f5"/><stop offset="1" stop-color="#4b24b9"/></linearGradient></defs>
      <rect x="4" y="4" width="120" height="120" rx="29" fill="url(#g)"/>
      <rect x="23" y="25" width="82" height="60" rx="11" fill="none" stroke="white" stroke-width="8"/>
      <circle cx="47" cy="46" r="7" fill="white"/>
      <path d="m30 75 22-20 14 13 12-10 20 17" fill="none" stroke="white" stroke-width="7" stroke-linejoin="round"/>
      <circle cx="88" cy="88" r="27" fill="#321485"/>
      <path d="M88 73v23m-9-9 9 9 9-9m-18 17h18" fill="none" stroke="white" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>'''
    render(app_icon, ROOT / "app-icon.png", 256)


if __name__ == "__main__":
    main()
