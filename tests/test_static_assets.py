"""Regression for missing local assets in the HTTP gateway, no external requests."""
from html.parser import HTMLParser
import http.client
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.parse import urljoin, urlsplit

from backend.model import IntentPlanner, ModelConfig
from backend.server import ROOT, create_server


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'link', 'script', 'img'}:
            value = attrs.get('src') or attrs.get('href')
            if value and not value.startswith('#'):
                self.paths.add(value)


class StaticAssetsTests(unittest.TestCase):
    def test_every_runtime_asset_is_local_and_served_with_correct_type(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(port=0, db_path=Path(directory) / 'bank.sqlite',
                                   planner=IntentPlanner(ModelConfig()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                assets = set()
                for page in ['index.html', 'demo.html']:
                    parser = AssetParser()
                    parser.feed((ROOT / page).read_text())
                    assets |= {urljoin('/' + page, path) for path in parser.paths}
                # Include nested font dependencies, not just the HTML links.
                for path in list(assets):
                    if path.endswith('.css'):
                        for target in re.findall(r'url\([\'"]?([^\)\'\"]+)', (ROOT / path.lstrip('/')).read_text()):
                            if not target.startswith('data:'):
                                assets.add(urljoin(path, target))
                types = {'.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.woff2': 'font/woff2'}
                for path in sorted(assets):
                    with self.subTest(path=path):
                        self.assertFalse(urlsplit(path).netloc, 'Runtime assets must not require a CDN')
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                        connection.request('GET', path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertTrue(response.getheader('Content-Type').startswith(types[Path(path).suffix]))
                        self.assertNotIn('cdn.jsdelivr.net', response.getheader('Content-Security-Policy'))
                        self.assertGreater(len(response.read()), 0)
                        connection.close()
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                connection.request('GET', '/.env')
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
