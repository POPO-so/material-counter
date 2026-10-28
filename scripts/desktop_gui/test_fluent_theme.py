import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from fluent_theme import FluentThemeManager


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    instance.setStyleSheet("")


def _window_color(app):
    return app.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)


def test_light_and_dark_apply_distinct_palettes_and_emit_theme_changed(app):
    manager = FluentThemeManager(app)
    changes = []
    manager.themeChanged.connect(changes.append)

    manager.apply("light")
    light_window = _window_color(app)
    light_qss = app.styleSheet()
    manager.apply("dark")
    dark_window = _window_color(app)
    dark_qss = app.styleSheet()

    assert light_window != dark_window
    assert light_qss and dark_qss and light_qss != dark_qss
    assert len(changes) == 2


def test_system_mode_reapplies_when_system_preference_changes(app, monkeypatch):
    manager = FluentThemeManager(app)
    monkeypatch.setattr(manager, "_system_is_dark", lambda: False)
    manager.apply("system")
    light_window = _window_color(app)

    monkeypatch.setattr(manager, "_system_is_dark", lambda: True)
    manager._on_system_preference_changed()

    assert manager.mode == "system"
    assert _window_color(app) != light_window


def test_high_contrast_uses_effective_system_palette(app):
    system_palette = QPalette(app.palette())
    manager = FluentThemeManager(app)
    manager.apply("high-contrast")

    for group, role in (
        (QPalette.ColorGroup.Active, QPalette.ColorRole.Window),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.Base),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.Text),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.Button),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight),
        (QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText),
        (QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text),
    ):
        assert app.palette().color(group, role) == system_palette.color(group, role)


def test_single_application_qss_covers_interaction_and_validation_states(app):
    manager = FluentThemeManager(app)
    manager.apply("light")
    qss = app.styleSheet()

    for selector in (
        ":hover",
        ":pressed",
        ":focus",
        ":disabled",
        ":checked",
        ":selected",
        '[fluentInvalid="true"]',
        'QWidget[fluentRole="card"]',
        '[fluentAppearance="primary"]',
        "#heroCount",
        "#pageTitle",
        "#sectionTitle",
        "#metricValue",
        "#mutedText",
        "#videoSurface",
        'QWidget[fluentRole="videoSurface"]',
        "#previewImage",
        "#binaryImage",
        "#deviceStatus",
        "QTabWidget",
        "QScrollArea",
        "QListWidget",
        "QSpinBox",
    ):
        assert selector in qss
    assert "@{" not in qss
