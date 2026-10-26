"""Offline checks for the bundled Python engine; never visits a game website."""
import json
import tempfile
import threading
from pathlib import Path

import collector
from optimizer import solve


def main():
    data = {'items': {
        '완제품': {'name': '완제품', 'offers': [], 'recipes': [
            {'materials': {'재료': 2}, 'fee': 10, 'output': 1}]},
        '재료': {'name': '재료', 'offers': [{'price': 100, 'quantity': 2}], 'recipes': []}}}
    assert solve(data, '완제품', 1, {})['total'] == 210
    assert '雷' in collector.search_names('雷')
    assert collector.REQUEST_DELAY == 1.5
    original_root = collector.ROOT
    with tempfile.TemporaryDirectory(prefix='gersang-check-') as folder:
        collector.ROOT = Path(folder)
        browser = collector.Collector(lambda _: None, threading.Event())
        try:
            browser.open_browser()
            assert browser.page.url == 'about:blank'
            assert browser.page.evaluate('6 * 7') == 42
        finally:
            if getattr(browser, 'context', None):
                browser.context.close()
            if getattr(browser, 'runtime', None):
                browser.runtime.stop()
            collector.ROOT = original_root
    print(json.dumps({'ok': True, 'calculation': 210, 'externalNavigation': False}))
