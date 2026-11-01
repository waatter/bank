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
    def test_async_auth_forms_keep_a_stable_form_reference(self):
        source = (ROOT / 'assets' / 'demo.js').read_text(encoding='utf-8')
        self.assertNotIn('event.currentTarget.reset()', source)
        auth_forms = (
            'loginForm', 'mfaLoginForm', 'registerForm', 'forgotPasswordForm',
            'resetPasswordForm', 'bindEmailForm', 'mfaEnableForm', 'mfaDisableForm',
        )
        for form_id in auth_forms:
            with self.subTest(form=form_id):
                marker = "$('#" + form_id + "').addEventListener('submit',async event=>{"
                start = source.index(marker)
                end = source.index("\n});", start)
                handler = source[start:end]
                self.assertIn('const form=event.currentTarget,data=new FormData(form)', handler)
                self.assertIn('form.reset()', handler)

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
                    parser.feed((ROOT / page).read_text(encoding='utf-8'))
                    assets |= {urljoin('/' + page, path) for path in parser.paths}
                # Include nested font dependencies, not just the HTML links.
                for path in list(assets):
                    if path.endswith('.css'):
                        for target in re.findall(r'url\([\'"]?([^\)\'\"]+)', (ROOT / path.lstrip('/')).read_text(encoding='utf-8')):
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


class StaticQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(
            port=0,
            db_path=Path(self.directory.name) / 'bank.sqlite',
            planner=IntentPlanner(ModelConfig()),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(self, target, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request(method, target)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_email_verification_and_reset_links_can_bootstrap_demo(self):
        token = 'trp_' + 'a' * 44
        for key in ('verify', 'reset'):
            with self.subTest(key=key):
                status, body = self.request('/demo.html?' + key + '=' + token)
                self.assertEqual(status, 200)
                self.assertIn(b'<!doctype html>', body.lower())
        status, body = self.request('/demo.html?verify=' + token, method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')

    def test_other_expected_demo_bootstrap_parameters_remain_bounded(self):
        access = 'trp_' + 'b' * 28
        for query in ('access=' + access, 'director=1', 'access=' + access + '&director=1'):
            with self.subTest(query=query):
                status, _ = self.request('/demo.html?' + query)
                self.assertEqual(status, 200)

    def test_unrecognised_repeated_or_ambiguous_queries_stay_rejected(self):
        token = 'trp_' + 'a' * 44
        rejected = (
            '/demo.html?unknown=1',
            '/demo.html?verify=short',
            '/demo.html?verify=' + token + '&verify=' + token,
            '/demo.html?verify=' + token + '&reset=' + token,
            '/demo.html?ver%69fy=' + token,
            '/index.html?verify=' + token,
            '/api/health?verify=' + token,
        )
        for target in rejected:
            with self.subTest(target=target):
                status, body = self.request(target)
                self.assertEqual(status, 400)
                self.assertIn(b'INVALID_PATH', body)


if __name__ == '__main__':
    unittest.main()
