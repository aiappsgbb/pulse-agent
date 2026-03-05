"""Tests for desktop notification and TUI alert sound."""

from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# notify_desktop: audio on all toasts
# ---------------------------------------------------------------------------


def test_notify_desktop_normal_plays_default_audio():
    """Normal urgency toasts should play audio.Default."""
    mock_notification = MagicMock()
    mock_audio = MagicMock()
    mock_audio.Default = "default-sound"

    with patch.dict("sys.modules", {"winotify": MagicMock()}):
        from core import notify
        with patch.object(notify, "__builtins__", notify.__builtins__):
            # Re-import won't work cleanly; mock the import inside the function
            mock_winotify = MagicMock()
            mock_winotify.Notification.return_value = mock_notification
            mock_winotify.audio = mock_audio

            with patch("builtins.__import__", side_effect=lambda name, *a, **kw: (
                mock_winotify if name == "winotify" else __import__(name, *a, **kw)
            )):
                notify.notify_desktop("Test", "body")

    mock_notification.set_audio.assert_called_once_with("default-sound", loop=False)
    mock_notification.show.assert_called_once()


def test_notify_desktop_urgent_plays_alarm_audio():
    """Urgent toasts should play LoopingAlarm, not Default."""
    mock_notification = MagicMock()
    mock_audio = MagicMock()
    mock_audio.LoopingAlarm = "alarm-sound"

    with patch.dict("sys.modules", {"winotify": MagicMock()}):
        from core import notify
        mock_winotify = MagicMock()
        mock_winotify.Notification.return_value = mock_notification
        mock_winotify.audio = mock_audio

        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: (
            mock_winotify if name == "winotify" else __import__(name, *a, **kw)
        )):
            notify.notify_desktop("Alert", "urgent body", urgency="urgent")

    mock_notification.set_audio.assert_called_once_with("alarm-sound", loop=False)


def test_notify_desktop_no_crash_without_winotify():
    """notify_desktop should silently degrade when winotify is not installed."""
    from core.notify import notify_desktop

    with patch("builtins.__import__", side_effect=ImportError("no winotify")):
        # Should not raise
        notify_desktop("Test", "body")


# ---------------------------------------------------------------------------
# _play_alert: retro beep
# ---------------------------------------------------------------------------


def test_play_alert_calls_winsound_beep():
    """_play_alert should fire 3 Beep calls in a background thread."""
    mock_beep = MagicMock()
    mock_winsound = MagicMock()
    mock_winsound.Beep = mock_beep

    with patch.dict("sys.modules", {"winsound": mock_winsound}):
        from tui.app import _play_alert
        # Run the inner function directly (not via thread) for deterministic test
        import threading
        original_thread = threading.Thread

        captured_target = []

        def fake_thread(target=None, daemon=None):
            captured_target.append(target)
            mock_t = MagicMock()
            return mock_t

        with patch("tui.app.threading.Thread", side_effect=fake_thread):
            _play_alert()

        # Execute the captured target function
        assert len(captured_target) == 1
        captured_target[0]()

    assert mock_beep.call_count == 3
    mock_beep.assert_any_call(660, 80)
    mock_beep.assert_any_call(880, 80)
    mock_beep.assert_any_call(1320, 120)


def test_play_alert_no_crash_without_winsound():
    """_play_alert should not crash if winsound is unavailable."""
    with patch.dict("sys.modules", {"winsound": None}):
        import threading
        captured_target = []

        def fake_thread(target=None, daemon=None):
            captured_target.append(target)
            mock_t = MagicMock()
            return mock_t

        with patch("tui.app.threading.Thread", side_effect=fake_thread):
            from tui.app import _play_alert
            _play_alert()

        # Execute — should not raise even though winsound is None
        captured_target[0]()
