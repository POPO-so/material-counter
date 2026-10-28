"""Compatibility import surface for the native Qt desktop view."""
from qt_view import DesktopWindow, QtRootAdapter
from view_model import (_box_from_item, format_duration, frame_summary,
                        recent_events, resource_percent, roi_bounds)

__all__ = ["DesktopWindow", "QtRootAdapter", "_box_from_item", "format_duration",
           "frame_summary", "recent_events", "resource_percent", "roi_bounds"]
