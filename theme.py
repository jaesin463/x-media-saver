"""Windows app theme and consistent tinted PNG icons."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QWidget


ICON_DIR = Path(__file__).parent / "web" / "assets" / "icons"


def load_ui_font(app: QApplication) -> None:
    """Load bundled Paperlogy weights, falling back to a Korean system font."""
    font_dir = Path(__file__).parent / "web" / "assets" / "fonts"
    loaded = [QFontDatabase.addApplicationFont(str(font_dir / f"Paperlogy-{weight}.ttf"))
              for weight in ("4Regular", "5Medium", "6SemiBold", "7Bold")]
    if any(font_id >= 0 for font_id in loaded):
        font = QFont("Paperlogy")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        app.setFont(font)
        return
    for name in ("NotoSansKR-VF.ttf", "NotoSansKR-Regular.ttf"):
        font_file = Path("C:/Windows/Fonts") / name
        if font_file.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id >= 0:
                font = QFont("Noto Sans KR")
                font.setPixelSize(14)
                app.setFont(font)
                return
    font = QFont("Malgun Gothic")
    font.setPixelSize(14)
    app.setFont(font)


def system_is_dark() -> bool:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except (OSError, ValueError):
            pass
    app = QApplication.instance()
    return bool(app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)


def icon(name: str, color: str) -> QIcon:
    source = QPixmap(str(ICON_DIR / f"{name}.png"))
    if source.isNull():
        return QIcon()
    tinted = QPixmap(source.size())
    tinted.fill(Qt.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return QIcon(tinted)


def set_button_icon(button: QPushButton, name: str) -> None:
    button.setProperty("iconName", name)
    recolor_button(button)
    button.setIconSize(QSize(18, 18))


def recolor_button(button: QPushButton) -> None:
    name = button.property("iconName")
    if not name:
        return
    dark = bool(QApplication.instance().property("darkTheme"))
    if button.objectName() == "primary":
        color = "#08090a"
    elif button.objectName() == "nav" and button.property("active") == "true":
        color = "#e4f222" if dark else "#6b7600"
    else:
        color = "#d0d6e0" if dark else "#434952"
    button.setIcon(icon(name, color))


def apply_theme(app: QApplication, dark: bool, window: QWidget | None = None,
                font_size: int = 14) -> None:
    font_size = font_size if font_size in range(12, 17) else 14
    app.setProperty("darkTheme", dark)
    font = app.font()
    font.setPixelSize(font_size)
    font.setWeight(QFont.Weight.Medium)
    app.setFont(font)
    colors = (
        {"bg": "#08090a", "surface": "#0f1011", "surface2": "#161718",
         "text": "#f5f6f7", "muted": "#8a8f98", "border": "#23252a",
         "accent": "#e4f222", "accentHover": "#f0fc48", "nav": "#161718",
         "navText": "#d0d6e0", "selection": "#383b3f", "input": "#121416"}
        if dark else
        {"bg": "#f6f7f4", "surface": "#ffffff", "surface2": "#f0f1ed",
         "text": "#181a1c", "muted": "#626970", "border": "#dfe2dd",
         "accent": "#d9e91c", "accentHover": "#e4f222", "nav": "#eef0e8",
         "navText": "#454b51", "selection": "#e7ecc4", "input": "#ffffff"}
    )
    palette = QPalette()
    for role, value in (
        (QPalette.Window, colors["bg"]), (QPalette.WindowText, colors["text"]),
        (QPalette.Base, colors["input"]), (QPalette.AlternateBase, colors["surface2"]),
        (QPalette.Text, colors["text"]), (QPalette.Button, colors["surface"]),
        (QPalette.ButtonText, colors["text"]), (QPalette.Highlight, colors["selection"]),
        (QPalette.HighlightedText, colors["text"]),
    ):
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.PlaceholderText, QColor(colors["muted"]))
    app.setPalette(palette)
    c = colors
    app.setStyleSheet(f"""
        QWidget {{ color: {c['text']}; font-family: 'Paperlogy', 'Noto Sans KR', 'Malgun Gothic'; font-size: {font_size}px; font-weight: 500; }}
        QMainWindow, QDialog, QMessageBox, QWidget#workspace, QWidget#page {{ background: {c['bg']}; }}
        QLabel {{ background: transparent; }}
        QFrame#sidebar, QFrame#topbar, QFrame#panel, QFrame#mediaCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
        QFrame#mediaCard[selected="true"] {{ border: 1px solid {c['accent']}; background: {c['surface2']}; }}
        QLabel#authBadge {{ color: {c['muted']}; background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 5px 9px; }}
        QLabel#authBadge[authenticated="true"] {{ color: #27a644; background: {('#12251b' if dark else '#e8f7ed')}; border-color: {('#205c36' if dark else '#b9e5c7')}; }}
        QFrame#sidebar {{ border-radius: 0; border-left: 0; border-top: 0; border-bottom: 0; }}
        QFrame#topbar {{ border-radius: 0; border-top: 0; border-right: 0; border-left: 0; }}
        QLabel#pageTitle {{ font-size: {font_size + 6}px; font-weight: 700; }}
        QLabel#metric {{ font-size: {font_size + 16}px; font-weight: 600; }}
        QLabel#muted, QLabel#hint {{ color: {c['muted']}; }}
        QLabel#section {{ font-size: {font_size + 3}px; font-weight: 600; }}
        QLabel#mediaTitle {{ font-size: {font_size}px; font-weight: 600; }}
        QLabel#statusDone {{ color: #27a644; font-size: {font_size}px; }}
        QLabel#statusPending {{ color: {c['muted']}; font-size: {font_size}px; }}
        QLabel#mediaThumb {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 6px; color: {c['muted']}; font-size: {font_size}px; }}
        QLabel#avatar {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 21px; font-size: {font_size + 2}px; font-weight: 600; }}
        QPushButton {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 8px 12px; min-height: 18px; font-weight: 600; }}
        QPushButton:hover {{ background: {c['surface2']}; }}
        QPushButton[selected="true"] {{ border-color: {c['muted']}; background: {c['surface2']}; }}
        QPushButton#primary {{ background: {c['accent']}; color: #08090a; border-color: {c['accent']}; font-weight: 600; }}
        QPushButton#primary:hover {{ background: {c['accentHover']}; }}
        QPushButton:disabled {{ color: {c['muted']}; background: {c['surface2']}; border-color: {c['border']}; }}
        QPushButton#primary:disabled {{ color: {c['muted']}; background: {c['surface2']}; border-color: {c['border']}; }}
        QPushButton#nav {{ text-align: left; border: 0; background: transparent; color: {c['navText']}; padding: 11px 13px; }}
        QPushButton#nav:hover, QPushButton#nav[active="true"] {{ background: {c['nav']}; color: {c['text']}; font-weight: 600; }}
        QFrame#viewSwitch {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px; }}
        QFrame#viewSwitch QPushButton {{ border: 0; background: transparent; padding: 7px 11px; }}
        QFrame#viewSwitch QPushButton[selected="true"] {{ background: {c['surface2']}; border: 1px solid {c['border']}; }}
        QTabBar#filterTabs {{ background: transparent; border-bottom: 1px solid {c['border']}; }}
        QTabBar#filterTabs::tab {{ background: transparent; color: {c['muted']}; border: 0; border-bottom: 2px solid transparent; padding: 9px 15px; margin-right: 4px; }}
        QTabBar#filterTabs::tab:hover {{ color: {c['text']}; }}
        QTabBar#filterTabs::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
        QMenu {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
        QMenu::item {{ padding: 8px 18px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {c['selection']}; }}
        QLineEdit, QComboBox, QTextEdit, QSpinBox {{ background: {c['input']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 8px; selection-background-color: {c['selection']}; }}
        QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border-color: {c['muted']}; }}
        QCheckBox {{ spacing: 7px; color: {c['text']}; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; background: {c['surface2']}; border: 1px solid {c['muted']}; border-radius: 4px; }}
        QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
        QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']}; selection-background-color: {c['selection']}; }}
        QTableWidget {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 10px; gridline-color: {c['border']}; selection-background-color: {c['selection']}; }}
        QTableWidget QWidget {{ background: {c['surface']}; }}
        QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; padding: 10px; border: 0; border-bottom: 1px solid {c['border']}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ border: 0; background: {c['bg']}; }}
        QStatusBar {{ background: {c['surface']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
        QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; }}
    """)
    if window:
        for button in window.findChildren(QPushButton):
            recolor_button(button)
