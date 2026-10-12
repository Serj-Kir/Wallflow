"""Application styling.

Wallflow ships a single dark theme that follows the platform accent colour, so
it looks native-ish on both GNOME and Windows without shipping two stylesheets.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from ..version import APP_NAME

#: Base palette.
BG = "#14151a"
BG_ALT = "#1b1c23"
SURFACE = "#202128"
SURFACE_ALT = "#272932"
BORDER = "#32353f"
TEXT = "#e6e8ee"
TEXT_DIM = "#9aa0ae"
ACCENT = "#5b8cff"
ACCENT_DIM = "#3d63b8"
DANGER = "#e5534b"
SUCCESS = "#57ab5a"
WARNING = "#c69026"


def stylesheet(accent: str = ACCENT) -> str:
    """Build the application stylesheet for the given accent colour."""
    accent = accent or ACCENT
    hover = _lighten(accent, 12)
    pressed = _darken(accent, 12)
    return f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background-color: {BG}; }}

#Sidebar {{
    background-color: {BG_ALT};
    border-right: 1px solid {BORDER};
}}
#SidebarTitle {{
    font-size: 17px;
    font-weight: 600;
    padding: 16px 14px 4px 14px;
    color: {TEXT};
}}
#SidebarVersion {{ color: {TEXT_DIM}; font-size: 11px; padding: 0 14px 12px 14px; }}

QPushButton {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: {BORDER}; }}
QPushButton:pressed {{ background-color: {BG_ALT}; }}
QPushButton:disabled {{ color: #5a5f6b; background-color: #1a1b21; border-color: #26272e; }}
QPushButton:checked {{
    background-color: {accent};
    border-color: {accent};
    color: #ffffff;
}}
QPushButton#Primary {{
    background-color: {accent};
    border-color: {accent};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: {hover}; border-color: {hover}; }}
QPushButton#Primary:pressed {{ background-color: {pressed}; }}
QPushButton#Danger {{ color: {DANGER}; }}
QPushButton#Danger:hover {{ background-color: rgba(229, 83, 75, 0.14); border-color: {DANGER}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QSpinBox:disabled {{ color: #5a5f6b; background-color: #1a1b21; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {accent};
    selection-color: #ffffff;
    outline: none;
}}

QLabel#H1 {{ font-size: 20px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Hint {{ color: {TEXT_DIM}; font-size: 11px; }}
QLabel#Warning {{ color: {WARNING}; }}
QLabel#Error {{ color: {DANGER}; }}
QLabel#Ok {{ color: {SUCCESS}; }}

QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    background-color: {SURFACE};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}

QListWidget, QListView, QTreeWidget, QTreeView {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 6px 8px;
    border-radius: 6px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {accent};
    color: #ffffff;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {SURFACE_ALT}; }}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
    background-color: {BG};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 7px 16px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}}
QTabBar::tab:selected {{
    background-color: {SURFACE};
    color: {TEXT};
    border-color: {BORDER};
    border-bottom-color: {SURFACE};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #454954; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

QSlider::groove:horizontal {{
    height: 4px; background: {BORDER}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 13px; height: 13px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {accent}; }}

QProgressBar {{
    background-color: {SURFACE};
    border: none; border-radius: 4px; height: 6px; text-align: center;
}}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 4px; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px; padding: 0 6px;
    color: {TEXT_DIM};
}}

QMenu {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px; padding: 5px;
}}
QMenu::item {{
    padding: 6px 22px 6px 14px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background-color: {accent}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QToolTip {{
    background-color: {SURFACE_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 7px; border-radius: 5px;
}}

#PlayerBar {{
    background-color: {BG_ALT};
    border-top: 1px solid {BORDER};
}}
#StatusBadge {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 2px 10px;
    color: {TEXT_DIM};
    font-size: 11px;
}}
#Card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
#Thumbnail {{
    border-radius: 6px;
    border: 1px solid {BORDER};
    background-color: {SURFACE_ALT};
}}
#ThumbnailSel {{ border: 2px solid {ACCENT}; border-radius: 6px; }}
"""


def _hex(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except (ValueError, IndexError):
        return 91, 140, 255


def _lighten(color: str, percent: int) -> str:
    r, g, b = _hex(color)
    f = percent / 100.0
    return f"#{min(255, int(r + (255 - r) * f)):02x}{min(255, int(g + (255 - g) * f)):02x}{min(255, int(b + (255 - b) * f)):02x}"


def _darken(color: str, percent: int) -> str:
    r, g, b = _hex(color)
    f = percent / 100.0
    return f"#{max(0, int(r * (1 - f))):02x}{max(0, int(g * (1 - f))):02x}{max(0, int(b * (1 - f))):02x}"


def apply(app, accent: str = ACCENT) -> None:
    """Install the stylesheet on the application."""
    app.setStyleSheet(stylesheet(accent))


__all__ = ["stylesheet", "apply", "ACCENT", "BG", "TEXT", "TEXT_DIM", "DANGER", "SUCCESS"]