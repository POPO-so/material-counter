"""Application-wide Fluent 2 theme infrastructure for Qt Widgets.

Tokens are loaded exclusively from the project-local fluent_tokens directory.
Widgets can declare semantic roles with Qt dynamic properties; this module owns
the single QApplication palette and generated application style sheet.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


_ROOT = Path(__file__).resolve().parent
_TOKEN_ROOT = _ROOT / "fluent_tokens"
_VALID_MODES = {"system", "light", "dark", "high-contrast"}

_QSS = r"""
QWidget {
    color: @{text_primary};
}
QMainWindow, QDialog, QWidget#mainWindow {
    background-color: @{window_background};
}
QWidget[fluentRole="card"] {
    background-color: @{surface_background};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_large};
}
QPushButton, QToolButton {
    color: @{text_primary};
    background-color: @{surface_background};
    border: @{stroke_thin} solid @{border_primary};
    border-radius: @{radius_medium};
    padding: 5px 12px;
    min-height: @{control_height_medium};
}
QPushButton:hover, QToolButton:hover {
    background-color: @{surface_background_hover};
    border-color: @{border_hover};
}
QPushButton:pressed, QToolButton:pressed {
    background-color: @{surface_background_pressed};
    border-color: @{border_pressed};
}
QPushButton:focus, QToolButton:focus {
    border: @{stroke_thick} solid @{focus_border};
}
QPushButton:disabled, QToolButton:disabled {
    color: @{text_disabled};
    background-color: @{surface_disabled};
    border-color: @{border_disabled};
}
QPushButton[fluentAppearance="primary"], QToolButton[fluentAppearance="primary"] {
    color: @{text_on_brand};
    background-color: @{brand_background};
    border-color: @{brand_border};
}
QPushButton[fluentAppearance="primary"]:hover, QToolButton[fluentAppearance="primary"]:hover {
    background-color: @{brand_background_hover};
}
QPushButton[fluentAppearance="primary"]:pressed, QToolButton[fluentAppearance="primary"]:pressed {
    background-color: @{brand_background_pressed};
}
QPushButton[fluentAppearance="primary"]:focus, QToolButton[fluentAppearance="primary"]:focus {
    border: @{stroke_thick} solid @{focus_border};
}
QPushButton[fluentAppearance="primary"]:disabled, QToolButton[fluentAppearance="primary"]:disabled {
    color: @{text_disabled};
    background-color: @{surface_disabled};
    border-color: @{border_disabled};
}
QPushButton:checked, QToolButton:checked {
    background-color: @{surface_background_selected};
    border-color: @{border_selected};
}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    color: @{text_primary};
    background-color: @{canvas_background};
    border: @{stroke_thin} solid @{border_primary};
    border-radius: @{radius_medium};
    padding: 4px 8px;
    selection-background-color: @{selection_strong_background};
    selection-color: @{selection_strong_foreground};
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: @{border_hover};
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: @{stroke_thick} solid @{focus_border};
}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: @{text_disabled};
    background-color: @{surface_disabled};
    border-color: @{border_disabled};
}
QLineEdit[fluentInvalid="true"], QPlainTextEdit[fluentInvalid="true"], QTextEdit[fluentInvalid="true"],
QComboBox[fluentInvalid="true"], QSpinBox[fluentInvalid="true"], QDoubleSpinBox[fluentInvalid="true"] {
    border: @{stroke_thick} solid @{danger_border};
}
QLineEdit[fluentInvalid="true"]:focus, QPlainTextEdit[fluentInvalid="true"]:focus, QTextEdit[fluentInvalid="true"]:focus,
QComboBox[fluentInvalid="true"]:focus, QSpinBox[fluentInvalid="true"]:focus, QDoubleSpinBox[fluentInvalid="true"]:focus {
    border-color: @{focus_border};
}
QTabWidget::pane {
    background-color: @{canvas_background};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_medium};
    top: -1px;
}
QTabBar::tab {
    color: @{text_secondary};
    background-color: transparent;
    border: @{stroke_thin} solid transparent;
    border-bottom-color: @{border_subtle};
    padding: 7px 12px;
    min-height: 24px;
}
QTabBar::tab:hover {
    color: @{text_primary};
    background-color: @{subtle_hover};
}
QTabBar::tab:pressed {
    background-color: @{subtle_pressed};
}
QTabBar::tab:selected {
    color: @{text_primary};
    background-color: @{surface_background};
    border-bottom: @{stroke_thick} solid @{brand_background};
}
QTabBar::tab:focus {
    border: @{stroke_thick} solid @{focus_border};
}
QTabBar::tab:disabled {
    color: @{text_disabled};
}
QScrollArea {
    background-color: @{canvas_background};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_medium};
}
QScrollBar:vertical {
    background-color: @{surface_secondary};
    width: 12px;
    margin: 2px;
}
QScrollBar:horizontal {
    background-color: @{surface_secondary};
    height: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: @{border_accessible};
    border-radius: @{radius_circular};
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background-color: @{text_secondary};
}
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
    background-color: @{text_primary};
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}
QListWidget, QTreeWidget, QTableWidget {
    color: @{text_primary};
    background-color: @{canvas_background};
    alternate-background-color: @{surface_secondary};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_medium};
    outline: none;
    selection-background-color: @{selection_subtle_background};
    selection-color: @{selection_subtle_foreground};
}
QListWidget::item, QTreeWidget::item, QTableWidget::item {
    padding: 5px 8px;
    border-radius: @{radius_small};
}
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {
    background-color: @{subtle_hover};
}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background-color: @{selection_subtle_background};
    color: @{selection_subtle_foreground};
}
QListWidget:focus, QTreeWidget:focus, QTableWidget:focus {
    border: @{stroke_thick} solid @{focus_border};
}
QListWidget:disabled, QTreeWidget:disabled, QTableWidget:disabled {
    color: @{text_disabled};
    background-color: @{surface_disabled};
}
QWidget#heroCount {
    color: @{brand_foreground};
    font-size: @{font_large_title_size};
    font-weight: @{weight_semibold};
}
#pageTitle, #sectionTitle {
    color: @{text_primary};
    font-size: @{font_subtitle1_size};
    font-weight: @{weight_semibold};
}
#metricValue {
    color: @{text_primary};
    font-size: @{font_subtitle1_size};
    font-weight: @{weight_semibold};
}
#mutedText {
    color: @{text_secondary};
}
#videoSurface, QWidget[fluentRole="videoSurface"], #previewImage, #binaryImage {
    background-color: @{surface_secondary};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_large};
}
#deviceStatus {
    background-color: @{surface_secondary};
    color: @{text_primary};
    border: @{stroke_thin} solid @{border_subtle};
    border-radius: @{radius_medium};
    padding: 4px 8px;
}
"""


def _flatten_aliases(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}
    for key, value in node.items():
        if isinstance(value, dict) and ("token" in value or "value" in value):
            flattened[key] = value
        elif isinstance(value, dict):
            flattened.update(_flatten_aliases(value))
    return flattened


def _load_theme_data() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        themes = json.loads((_TOKEN_ROOT / "fluent2-official-web-theme-tokens.json").read_text(encoding="utf-8"))
        mapping = json.loads((_TOKEN_ROOT / "qt-token-map.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load project Fluent token resources: {exc}") from exc
    for key in ("webLightTheme", "webDarkTheme"):
        if key not in themes or not isinstance(themes[key], dict):
            raise ValueError(f"Missing official token theme {key}")
    if "aliases" not in mapping or "colors" not in mapping["aliases"]:
        raise ValueError("Invalid Qt token alias map")
    return themes, mapping


def _resolve_aliases(themes: dict[str, Any], mapping: dict[str, Any], theme_name: str) -> dict[str, str]:
    official = themes[f"web{'Light' if theme_name == 'light' else 'Dark'}Theme"]
    aliases: dict[str, str] = {}
    for category in mapping["aliases"].values():
        if not isinstance(category, dict):
            continue
        for name, spec in _flatten_aliases(category).items():
            token = spec.get("token")
            if token is not None:
                if token not in official:
                    raise ValueError(f"Qt alias {name!r} references unknown token {token!r}")
                aliases[name] = str(official[token])
            elif "value" in spec:
                aliases[name] = str(spec["value"])
    return aliases


def _qcolor(value: str) -> QColor:
    color = QColor(value)
    if not color.isValid():
        raise ValueError(f"Invalid Fluent color: {value!r}")
    return color


def _qss_color(color: QColor) -> str:
    if color.alpha() == 255:
        return color.name(QColor.NameFormat.HexRgb)
    return f"rgba({color.red()},{color.green()},{color.blue()},{color.alphaF():.4f})"


def _palette_for(aliases: dict[str, str]) -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    active_map = {
        role.Window: "window_background",
        role.WindowText: "text_primary",
        role.Base: "canvas_background",
        role.AlternateBase: "surface_secondary",
        role.Text: "text_primary",
        role.Button: "surface_background",
        role.ButtonText: "text_primary",
        role.Highlight: "selection_strong_background",
        role.HighlightedText: "selection_strong_foreground",
        role.Link: "link",
        role.LinkVisited: "brand_foreground_secondary",
        role.ToolTipBase: "tooltip_background",
        role.ToolTipText: "tooltip_foreground",
    }
    placeholder = getattr(role, "PlaceholderText", None)
    if placeholder is not None:
        active_map[placeholder] = "text_tertiary"
    for qt_role, alias in active_map.items():
        if alias in aliases:
            color = _qcolor(aliases[alias])
            palette.setColor(group.Active, qt_role, color)
            palette.setColor(group.Inactive, qt_role, color)
    disabled_map = {
        role.WindowText: "text_disabled",
        role.Text: "text_disabled",
        role.ButtonText: "text_disabled",
        role.Base: "surface_disabled",
        role.AlternateBase: "surface_disabled",
        role.Button: "surface_disabled",
        role.Highlight: "surface_disabled",
        role.HighlightedText: "text_disabled",
    }
    if placeholder is not None:
        disabled_map[placeholder] = "text_disabled"
    for qt_role, alias in disabled_map.items():
        if alias in aliases:
            palette.setColor(group.Disabled, qt_role, _qcolor(aliases[alias]))
    return palette


def _system_aliases(aliases: dict[str, str], palette: QPalette) -> dict[str, str]:
    result = dict(aliases)
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    strategy = {
        "window_background": role.Window,
        "canvas_background": role.Base,
        "surface_background": role.Button,
        "surface_background_hover": role.Button,
        "surface_background_pressed": role.Button,
        "surface_background_selected": role.Highlight,
        "surface_secondary": role.Window,
        "surface_tertiary": role.Base,
        "surface_elevated": role.Base,
        "surface_disabled": role.Button,
        "card_background": role.Base,
        "text_primary": role.WindowText,
        "text_secondary": role.Text,
        "text_tertiary": getattr(role, "PlaceholderText", role.Text),
        "text_quaternary": getattr(role, "PlaceholderText", role.Text),
        "text_disabled": role.Text,
        "text_on_brand": role.HighlightedText,
        "text_inverted": role.HighlightedText,
        "link": role.Link,
        "link_hover": role.Highlight,
        "link_pressed": role.Highlight,
        "border_primary": role.Mid,
        "border_subtle": role.Midlight,
        "border_tertiary": role.Midlight,
        "border_accessible": role.WindowText,
        "border_disabled": role.Mid,
        "border_hover": role.Highlight,
        "border_pressed": role.Highlight,
        "border_selected": role.Highlight,
        "focus_border": role.Highlight,
        "focus_inner": role.Base,
        "brand_background": role.Highlight,
        "brand_background_hover": role.Highlight,
        "brand_background_pressed": role.Highlight,
        "brand_background_selected": role.Highlight,
        "brand_background_subtle": role.Base,
        "brand_foreground": role.Link,
        "brand_foreground_secondary": role.Link,
        "brand_border": role.Highlight,
        "brand_border_subtle": role.Mid,
        "selection_strong_background": role.Highlight,
        "selection_strong_foreground": role.HighlightedText,
        "selection_subtle_background": role.Highlight,
        "selection_subtle_foreground": role.HighlightedText,
        "tooltip_background": role.ToolTipBase,
        "tooltip_foreground": role.ToolTipText,
        "danger_border": role.Highlight,
        "danger_foreground": role.WindowText,
    }
    for alias, qt_role in strategy.items():
        if alias in result:
            result[alias] = _qss_color(palette.color(group.Active, qt_role))
    for alias in ("text_disabled", "surface_disabled", "border_disabled"):
        if alias in result:
            qt_role = {"text_disabled": role.Text, "surface_disabled": role.Button, "border_disabled": role.Mid}[alias]
            result[alias] = _qss_color(palette.color(group.Disabled, qt_role))
    for alias in result:
        if alias.endswith("_background") and alias not in strategy:
            result[alias] = _qss_color(palette.color(group.Active, role.Base))
        elif alias.endswith("_foreground") and alias not in strategy:
            result[alias] = _qss_color(palette.color(group.Active, role.WindowText))
        elif alias.endswith("_border") and alias not in strategy:
            result[alias] = _qss_color(palette.color(group.Active, role.Mid))
    return result


class FluentThemeManager(QObject):
    """Own the application palette, one generated QSS, and theme preferences."""

    themeChanged = Signal(str)

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        self.mode = "system"
        self._themes, self._mapping = _load_theme_data()
        self._system_palette = QPalette(app.palette())
        self._applying = False
        self._current_mode: str | None = None
        app.installEventFilter(self)
        hints = app.styleHints()
        color_changed = getattr(hints, "colorSchemeChanged", None)
        if color_changed is not None:
            color_changed.connect(self._on_system_preference_changed)
        accessibility = getattr(hints, "accessibility", None)
        if callable(accessibility):
            signal = getattr(accessibility(), "contrastPreferenceChanged", None)
            if signal is not None:
                signal.connect(self._on_system_preference_changed)

    @property
    def current_mode(self) -> str | None:
        return self._current_mode

    def _system_is_dark(self) -> bool:
        hints = self.app.styleHints()
        get_scheme = getattr(hints, "colorScheme", None)
        scheme_type = getattr(Qt, "ColorScheme", None)
        if callable(get_scheme) and scheme_type is not None:
            scheme = get_scheme()
            if scheme == getattr(scheme_type, "Dark", object()):
                return True
            if scheme == getattr(scheme_type, "Light", object()):
                return False
        return self._system_palette.color(QPalette.ColorRole.Window).lightness() < 128

    def _system_requests_high_contrast(self) -> bool:
        hints = self.app.styleHints()
        get_accessibility = getattr(hints, "accessibility", None)
        if not callable(get_accessibility):
            return False
        accessibility = get_accessibility()
        get_preference = getattr(accessibility, "contrastPreference", None)
        if not callable(get_preference):
            return False
        preference = get_preference()
        name = getattr(preference, "name", str(preference))
        return "HighContrast" in name or name.endswith("High") or name == "High"

    def _effective_mode(self, requested: str) -> str:
        if requested == "system":
            if self._system_requests_high_contrast():
                return "high-contrast"
            return "dark" if self._system_is_dark() else "light"
        return requested

    def _on_system_preference_changed(self, *_args: object) -> None:
        self._refresh_system_palette()
        if self.mode == "system":
            self.apply("system")

    def _refresh_system_palette(self) -> None:
        style = self.app.style()
        self._system_palette = QPalette(style.standardPalette() if style is not None else self.app.palette())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if watched is self.app and not self._applying:
            event_types = {
                value
                for value in (
                    getattr(QEvent.Type, "ApplicationPaletteChange", None),
                    getattr(QEvent.Type, "ThemeChange", None),
                )
                if value is not None
            }
            if event.type() in event_types:
                self._refresh_system_palette()
                if self.mode == "system":
                    self.apply("system")
        return super().eventFilter(watched, event)

    def apply(self, mode: str) -> None:
        """Apply system, light, dark, or high-contrast styling to QApplication."""
        if mode not in _VALID_MODES:
            raise ValueError(f"Unsupported Fluent theme mode: {mode!r}")
        self.mode = mode
        if self._applying:
            return
        self._applying = True
        try:
            effective = self._effective_mode(mode)
            base_name = "dark" if effective == "dark" or (
                effective == "high-contrast" and self._system_is_dark()
            ) else "light"
            aliases = _resolve_aliases(self._themes, self._mapping, base_name)
            palette = QPalette(self._system_palette) if effective == "high-contrast" else _palette_for(aliases)
            if effective == "high-contrast":
                aliases = _system_aliases(aliases, self._system_palette)
            qss = _QSS
            for name, value in aliases.items():
                qss = qss.replace(f"@{{{name}}}", str(value))
            import re
            unresolved = sorted(set(re.findall(r"@\{([A-Za-z_][A-Za-z0-9_]*)\}", qss)))
            if unresolved:
                raise ValueError("Unknown QSS token aliases: " + ", ".join(unresolved))
            app_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
            if effective != "high-contrast":
                families = set(QFontDatabase.families())
                preferred = (
                    ("Microsoft YaHei UI", "Segoe UI Variable", "Segoe UI")
                    if sys.platform == "win32"
                    else ()
                )
                for family in preferred:
                    if family in families:
                        app_font.setFamily(family)
                        break
            self.app.setFont(app_font)
            self.app.setPalette(palette)
            self.app.setStyleSheet(qss)
            self._current_mode = effective
            self.themeChanged.emit(effective)
        finally:
            self._applying = False
