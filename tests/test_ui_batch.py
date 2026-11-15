"""Offline tests for inexpensive Tk activity/event batching."""
import queue
import unittest
from unittest.mock import MagicMock

from main import App, MAX_UI_EVENTS_PER_TICK


class UIBatchTests(unittest.TestCase):
    def test_many_activity_lines_use_one_widget_mutation_batch(self):
        app = App.__new__(App)
        widget = MagicMock()
        widget.index.return_value = "1010.0"
        app.log_widget = widget

        app.put_logs(["First update", "Second update", "Third update"])

        widget.insert.assert_called_once()
        payload = widget.insert.call_args.args[1]
        self.assertEqual(payload.count("\n"), 3)
        self.assertLess(payload.index("First update"), payload.index("Second update"))
        self.assertLess(payload.index("Second update"), payload.index("Third update"))
        self.assertEqual(widget.configure.call_count, 2)
        widget.configure.assert_any_call(state="normal")
        widget.configure.assert_any_call(state="disabled")
        widget.delete.assert_called_once_with("1.0", "10.0")
        widget.see.assert_called_once_with("end")

    def test_empty_log_batch_never_touches_widget(self):
        app = App.__new__(App)
        app.log_widget = MagicMock()
        app.put_logs(())
        app.log_widget.assert_not_called()
        app.log_widget.configure.assert_not_called()
        app.log_widget.insert.assert_not_called()

    def test_pump_preserves_activity_order_and_one_final_status(self):
        app = App.__new__(App)
        app.events = queue.Queue()
        app.status = MagicMock()
        app.put_logs = MagicMock()
        app.refresh = MagicMock()
        app.root = MagicMock()

        for event in (
            ("status", "First"),
            ("status", "Next action in 1 minute."),
            ("refresh", ""),
            ("status", "Second"),
            ("status", "Third"),
        ):
            app.events.put(event)

        app.pump()

        app.status.set.assert_called_once_with("Third")
        app.put_logs.assert_called_once_with(["First", "Second", "Third"])
        app.refresh.assert_called_once_with()
        app.root.after.assert_called_once_with(250, app.pump)

    def test_pump_keeps_tk_work_bounded_under_backlog(self):
        app = App.__new__(App)
        app.events = queue.Queue()
        app.status = MagicMock()
        app.put_logs = MagicMock()
        app.refresh = MagicMock()
        app.root = MagicMock()
        for n in range(MAX_UI_EVENTS_PER_TICK + 2):
            app.events.put(("status", f"Event {n}"))

        app.pump()
        self.assertEqual(len(app.put_logs.call_args.args[0]), MAX_UI_EVENTS_PER_TICK)
        app.root.after.assert_called_with(25, app.pump)
        self.assertEqual(app.events.qsize(), 2)
        app.put_logs.reset_mock()
        app.root.after.reset_mock()

        app.pump()
        app.put_logs.assert_called_once_with([
            f"Event {MAX_UI_EVENTS_PER_TICK}",
            f"Event {MAX_UI_EVENTS_PER_TICK + 1}",
        ])
        app.root.after.assert_called_once_with(250, app.pump)


if __name__ == "__main__":
    unittest.main()
