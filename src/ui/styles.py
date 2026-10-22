"""
Modern QSS styles for Game Save Guardian.
Optimized dark gaming theme with emerald/teal accents.
"""

from src.utils.paths import get_assets_dir

DARK_THEME_QSS = """
/* Global Window Styles */
QWidget {
    background-color: #0f172a; /* Slate 900 */
    color: #f8fafc; /* Slate 50 */
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    font-size: 13px;
    selection-background-color: #059669; /* Emerald 600 */
    selection-color: #ffffff;
}

QLabel {
    background-color: transparent;
}

/* Sidebar Navigation */
QFrame#sidebar {
    background-color: #1e293b; /* Slate 800 */
    border-right: 1px solid #334155; /* Slate 700 */
}

QPushButton.nav-btn {
    text-align: left;
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 500;
    border-radius: 8px;
    border: none;
    background-color: transparent;
    color: #94a3b8; /* Slate 400 */
}

QPushButton.nav-btn:hover {
    background-color: #334155;
    color: #ffffff;
}

QPushButton.nav-btn:checked {
    background-color: #064e3b; /* Emerald 900 */
    color: #34d399; /* Emerald 400 */
    font-weight: 600;
    border-left: 3px solid #10b981;
}

/* Metric / Dashboard Cards */
QFrame.metric-card {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px;
}

QLabel.card-title {
    color: #94a3b8;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
}

QLabel.card-value {
    color: #ffffff;
    font-size: 20px;
    font-weight: 700;
}

/* Action Buttons */
QPushButton.primary-btn {
    background-color: #059669; /* Emerald 600 */
    color: #ffffff;
    border-radius: 6px;
    padding: 9px 18px;
    font-weight: 600;
    border: 1px solid #10b981;
}

QPushButton.primary-btn:hover {
    background-color: #10b981;
}

QPushButton.primary-btn:pressed {
    background-color: #047857;
}

QPushButton.secondary-btn {
    background-color: #334155;
    color: #f1f5f9;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 500;
    border: 1px solid #475569;
}

QPushButton.secondary-btn:hover {
    background-color: #475569;
}

QPushButton.danger-btn {
    background-color: #991b1b; /* Red 800 */
    color: #ffffff;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 500;
    border: 1px solid #dc2626;
}

QPushButton.danger-btn:hover {
    background-color: #dc2626;
}

/* Input Fields */
QLineEdit, QSpinBox, QComboBox {
    background-color: #1e293b;
    color: #ffffff;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 7px 10px;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #10b981;
}

QComboBox::drop-down {
    border: none;
    padding-right: 10px;
}

/* Tables */
QTableWidget {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    gridline-color: #334155;
}

QHeaderView::section {
    background-color: #0f172a;
    color: #94a3b8;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #334155;
    font-weight: 600;
}

QTableWidget::item {
    padding: 6px;
    border-bottom: 1px solid #334155;
}

QTableWidget::item:selected {
    background-color: #064e3b;
    color: #34d399;
}

/* Status Badges */
QLabel.badge-active {
    background-color: #064e3b;
    color: #34d399;
    border: 1px solid #10b981;
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
}

QLabel.badge-paused {
    background-color: #78350f;
    color: #fbbf24;
    border: 1px solid #f59e0b;
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
}

QLabel.badge-error {
    background-color: #7f1d1d;
    color: #f87171;
    border: 1px solid #ef4444;
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
}

/* ScrollBars */
QScrollBar:vertical {
    background: #0f172a;
    width: 8px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #334155;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #475569;
}
QScrollBar:horizontal {
    background: #0f172a;
    height: 8px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #334155;
    min-width: 24px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background: #475569;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
    border: none;
    background: none;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* Splitter */
QSplitter::handle {
    background-color: #1e293b;
}
QSplitter::handle:horizontal {
    width: 4px;
}
QSplitter::handle:hover {
    background-color: #10b981;
}

/* CheckBoxes */
QCheckBox {
    spacing: 10px;
    color: #f1f5f9;
    font-size: 13px;
    background: transparent;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #475569;
    border-radius: 4px;
    background-color: #1e293b;
}

QCheckBox::indicator:hover {
    border-color: #10b981;
    background-color: #334155;
}

QCheckBox::indicator:checked {
    border-color: #10b981;
    background-color: #059669;
    image: url("{{CHECK_ICON_URL}}");
}

QCheckBox::indicator:disabled {
    border-color: #334155;
    background-color: #0f172a;
}

/* Radio Buttons */
QRadioButton {
    spacing: 10px;
    color: #f1f5f9;
    font-size: 13px;
    background: transparent;
}

QRadioButton::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #475569;
    border-radius: 10px;
    background-color: #1e293b;
}

QRadioButton::indicator:hover {
    border-color: #10b981;
    background-color: #334155;
}

QRadioButton::indicator:checked {
    border-color: #10b981;
    background-color: #1e293b;
    image: url("{{RADIO_DOT_URL}}");
}

QRadioButton::indicator:disabled {
    border-color: #334155;
    background-color: #0f172a;
}

/* Progress Bar */
QProgressBar {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 7px;
    height: 14px;
    text-align: center;
    color: #ffffff;
    font-weight: 600;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10b981);
    border-radius: 6px;
}
"""


def get_stylesheet(theme_name: str = "dark") -> str:
    """Return dark gaming QSS stylesheet with resolved asset paths."""
    assets_dir = get_assets_dir()
    check_icon = (assets_dir / "check_icon.png").as_posix()
    radio_dot = (assets_dir / "radio_dot.png").as_posix()

    qss = DARK_THEME_QSS.replace("{{CHECK_ICON_URL}}", check_icon).replace("{{RADIO_DOT_URL}}", radio_dot)
    return qss

