import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from collector import Collector, ROOT
from desktop.package_portable import package


class DesktopTests(unittest.TestCase):
    def test_portable_package_excludes_personal_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            files = ['제작으로부자되자.exe', 'app.ico', 'index.html',
                     'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll',
                     'desktop/WebView2-LICENSE.txt', 'desktop/포터블-사용방법.txt',
                     '.venv/Lib/site-packages/playwright/driver/LICENSE',
                     'data/browser/Cookies', 'data/history.json', 'data/database.json']
            for name in files:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test')
            engine, runtime, destination = base / 'engine', base / 'runtime', base / 'package'
            engine.mkdir()
            runtime.mkdir()
            (engine / 'engine.exe').write_text('engine')
            (runtime / 'msedgewebview2.exe').write_text('runtime')
            with patch('desktop.package_portable.__file__', str(source / 'desktop/package_portable.py')):
                package(engine, runtime, destination)
                self.assertEqual(list((destination / 'data').iterdir()), [])
                self.assertFalse((destination / '.venv').exists())
                self.assertTrue((destination / 'engine/engine.exe').is_file())
                with self.assertRaises(FileExistsError):
                    package(engine, runtime, destination)

    def test_desktop_uses_visible_external_edge_even_with_desktop_token(self):
        runtime = Mock()
        context = runtime.chromium.launch_persistent_context.return_value
        page = Mock()
        context.pages = [page]
        collector = Collector(lambda _: None, threading.Event())
        with patch.dict('os.environ', {'GERSANG_DESKTOP_TOKEN': 'desktop-test'}), patch('playwright.sync_api.sync_playwright') as start:
            start.return_value.start.return_value = runtime
            collector.open_browser()
        runtime.chromium.launch_persistent_context.assert_called_once_with(
            str(ROOT / 'data' / 'browser'), channel='msedge', headless=False,
            viewport={'width': 1280, 'height': 900})
        self.assertIs(collector.page, page)
        page.goto.assert_not_called()

    def test_external_edge_creates_page_when_needed(self):
        runtime = Mock()
        context = runtime.chromium.launch_persistent_context.return_value
        context.pages = []
        collector = Collector(lambda _: None, threading.Event())
        with patch('playwright.sync_api.sync_playwright') as start:
            start.return_value.start.return_value = runtime
            collector.open_browser()
        self.assertIs(collector.page, context.new_page.return_value)

    def test_external_edge_launch_failure_releases_runtime(self):
        runtime = Mock()
        runtime.chromium.launch_persistent_context.side_effect = RuntimeError('launch failed')
        collector = Collector(lambda _: None, threading.Event())
        with patch('playwright.sync_api.sync_playwright') as start:
            start.return_value.start.return_value = runtime
            with self.assertRaisesRegex(RuntimeError, 'launch failed'):
                collector.open_browser()
        runtime.stop.assert_called_once()
        self.assertIsNone(collector.runtime)


if __name__ == '__main__':
    unittest.main()
