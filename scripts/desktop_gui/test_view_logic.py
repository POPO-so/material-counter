import unittest

from view import DesktopWindow, _box_from_item, format_duration, frame_summary, resource_percent, recent_events, roi_bounds


class ViewLogicTests(unittest.TestCase):
    def test_format_duration_matches_dashboard_clock_format(self):
        self.assertEqual(format_duration(3661.9), "01:01:01")
        self.assertEqual(format_duration(None), "--")

    def test_resource_percent_rejects_unavailable_or_out_of_range_values(self):
        self.assertEqual(resource_percent(37.5), 37.5)
        self.assertIsNone(resource_percent(None))
        self.assertIsNone(resource_percent(101))

    def test_box_from_item_accepts_xywh_and_corner_coordinates(self):
        self.assertEqual(_box_from_item({"x": 10, "y": 20, "width": 30, "height": 40}), (10, 20, 40, 60))
        self.assertEqual(_box_from_item({"bbox": [2, 3, 8, 9]}), (2, 3, 8, 9))
        self.assertEqual(_box_from_item({"x_min": 4, "y_min": 5, "x_max": 14, "y_max": 15}), (4, 5, 14, 15))
        self.assertEqual(_box_from_item({"detection": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}}), (1, 2, 3, 4))
        self.assertIsNone(_box_from_item({"id": 4}))

    def test_frame_summary_uses_result_count_and_detected_regions(self):
        self.assertEqual(frame_summary({"total_count": 12, "detections": [{}, {}]}), (12, 2))
        self.assertEqual(frame_summary({"count": 8, "detection_count": 3}), (8, 3))
        self.assertEqual(frame_summary({}), (None, None))

    def test_roi_bounds_reads_configured_roi_without_inventing_one(self):
        self.assertEqual(roi_bounds({"roi": {"x_min": 5, "y_min": 6, "x_max": 25, "y_max": 26}}), (5, 6, 25, 26))
        self.assertEqual(roi_bounds({"roi": [5, 6, 25, 26]}), (5, 6, 25, 26))
        self.assertEqual(roi_bounds({"roi_top": 96, "roi_bottom": 144}), (0, 96, 320, 144))
        self.assertIsNone(roi_bounds({}))

    def test_centroid_supports_nested_detection_coordinates(self):
        self.assertEqual(DesktopWindow._center_from_item({"centroid_x": 36, "centroid_y": 50}), (36, 50))
        self.assertEqual(DesktopWindow._center_from_item({"detection": {"centroid_x": 0.5, "centroid_y": 0.5}}), (160, 120))

    def test_count_axis_x_draws_a_vertical_line_at_line_x(self):
        class Draw:
            def __init__(self): self.lines = []
            def line(self, coords, **kwargs): self.lines.append((coords, kwargs))

        draw = Draw()
        DesktopWindow._draw_config_line(draw, {"count_axis": "x", "line_x": 73})
        self.assertEqual(draw.lines, [((73, 0, 73, 239), {"fill": "#ff4d4d", "width": 2})])

    def test_recent_events_keeps_only_latest_thirty_in_order(self):
        events = [{"message": str(index)} for index in range(35)]
        self.assertEqual(recent_events(events), events[-30:])
        self.assertEqual(recent_events(None), [])

if __name__ == "__main__":
    unittest.main()
