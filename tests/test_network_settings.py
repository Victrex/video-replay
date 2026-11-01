import os
import sys
import tempfile
import unittest
from unittest import mock

import camera_selector
import network_discovery


class SettingsPathTests(unittest.TestCase):
    def test_source_settings_are_saved_next_to_the_module(self):
        expected = os.path.join(
            os.path.dirname(os.path.abspath(network_discovery.__file__)),
            "network_cameras.json",
        )
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(network_discovery._settings_path(), expected)

    def test_frozen_settings_are_saved_next_to_the_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "VideoReplay.exe")
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.object(sys, "executable", executable):
                    self.assertEqual(
                        network_discovery._settings_path(),
                        os.path.join(directory, "network_cameras.json"),
                    )

    def test_settings_round_trip(self):
        settings = {
            "credentials": {"127.0.0.1": {"user": "test", "password": "test"}},
            "manual_urls": ["http://127.0.0.1:1234/video"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "network_cameras.json")
            with mock.patch.object(network_discovery, "SETTINGS_FILE", path):
                network_discovery.save_settings(settings)
                self.assertEqual(network_discovery.load_settings(), settings)

    def test_write_errors_are_not_silenced(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(network_discovery, "SETTINGS_FILE", directory):
                with self.assertRaises(OSError):
                    network_discovery.save_settings({})


class SelectorSettingsTests(unittest.TestCase):
    def test_manual_camera_remains_available_if_saving_fails(self):
        selector = camera_selector.CameraSelector.__new__(
            camera_selector.CameraSelector
        )
        selector.root = object()
        selector._settings = {"manual_urls": []}
        selector._manual = []
        selector._checked = {}
        selector._render = mock.Mock()
        url = "http://127.0.0.1:1234/video"

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(network_discovery, "SETTINGS_FILE", directory):
                with mock.patch.object(
                    camera_selector.simpledialog, "askstring", return_value=url
                ):
                    with mock.patch.object(
                        camera_selector.messagebox, "showwarning"
                    ) as warning:
                        selector._add_manual()

        self.assertEqual(selector._settings["manual_urls"], [url])
        self.assertEqual(selector._manual[0].url, url)
        self.assertTrue(selector._checked[f"url:{url}"])
        selector._render.assert_called_once()
        warning.assert_called_once()
        self.assertIn("no se conservará", warning.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
