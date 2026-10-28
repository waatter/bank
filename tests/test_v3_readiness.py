"""Read-only probe regressions: adversarial local HTTP fixtures, no external I/O."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import shutil
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.v3_readiness import (FAIL, PASS, UNTESTED, Probe, UnsafeURL, base_url,
                                  context_manifest, deployment_checks, main, resource_url, write_report)

HEALTH = json.dumps({'ok': True, 'sandbox': True, 'version': '3.0.0',
                     'authentication': 'simulated-persona-session', 'modelMode': 'rules'}).encode()


@contextmanager
def fixture(overrides=None):
    routes = {
        '/': ('text/html', b'<link rel="stylesheet" href="/assets/main.css"><img src="/assets/logo.svg">'),
        '/demo.html': ('text/html', b'<script src="/assets/app.js"></script>'),
        '/api/health': ('application/json', HEALTH),
        '/study.html': ('text/html', b'<link rel=stylesheet href=/assets/main.css>'),
        '/assets/main.css': ('text/css', b'@import "nested.css"; .a { background:url("logo.svg#mark") }'),
        '/assets/nested.css': ('text/css', b'@font-face { src:url("fonts/test.woff2") }'),
        '/assets/fonts/test.woff2': ('font/woff2', b'wOF2test'),
        '/assets/logo.svg': ('image/svg+xml', b'<svg/>'),
        '/assets/app.js': ('text/javascript', b'import "./module.js"; fetch("/api/sessions",{method:"POST"})'),
        '/assets/module.js': ('text/javascript', b'/* never executed */'),
    }
    routes.update(overrides or {})
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path, dict(self.headers)))
            entry = routes.get(self.path)
            if callable(entry):
                entry(self)
                return
            if entry is None:
                self.send_response(404)
                self.end_headers()
                return
            content_type, body = entry[:2]
            status = entry[2] if len(entry) > 2 else 200
            headers = entry[3] if len(entry) > 3 else {}
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', headers.pop('Cache-Control', 'no-store'))
            self.send_header('Set-Cookie', 'secret=NEVER_RECORD_THIS')
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (OSError, ValueError):
                pass

        def do_POST(self):
            requests.append(('POST', self.path, {}))
            self.send_error(500)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class URLTests(unittest.TestCase):
    def test_public_requires_verified_https_and_root_paths(self):
        self.assertEqual(base_url('https://demo.troposagent.com/demo.html'), 'https://demo.troposagent.com/')
        self.assertEqual(base_url('http://127.0.0.1:9000'), 'http://127.0.0.1:9000/')
        for value in [
            'http://demo.troposagent.com', 'http://localhost:9000', 'http://127.0.0.1.evil.test',
            'https://x/.env', 'file:///etc/passwd', 'https://user:secret@x/', 'https://x/?secret=x',
            'https://x/?', 'https://x/#access=secret', 'https://x/../', 'https://x/%2e%2e/',
            'https://x/%252e%252e/', 'https://x/\\evil', '\nhttps://x/', 'https://x:bad/',
            'https://x:0/', 'https://x//', 'https://x/./',
        ]:
            with self.subTest(value=value), self.assertRaises(UnsafeURL):
                base_url(value)
        with self.assertRaises(UnsafeURL):
            base_url('https://demo.troposagent.com', origin_only=True)

    def test_dependency_allowlist_precedes_normalisation(self):
        base = 'https://example.test/assets/main.css'
        self.assertEqual(resource_url(base, 'fonts/a.woff2')[0], 'https://example.test/assets/fonts/a.woff2')
        self.assertEqual(resource_url(base, 'https://other.test/a.css'), (None, 'external'))
        self.assertEqual(resource_url(base, 'data:image/png;base64,abc'), (None, 'embedded'))
        for value in ['../.env', '/assets/../.env', '/assets/%2e%2e/.env',
                      '/assets/%252e%252e/.env', '/api/sessions', '/api/state',
                      '/reports/evaluation.json', '/assets/.env', '/assets/private.sqlite',
                      '/assets/a.js?token=secret', '/assets//a.js', 'https://user:pw@example.test/assets/a.js']:
            with self.subTest(value=value), self.assertRaises(UnsafeURL):
                resource_url(base, value)


class ProbeTests(unittest.TestCase):
    def test_recursive_css_font_and_js_gets_without_credentials_or_other_api(self):
        with fixture() as (url, requests):
            report = Probe(url).run()
        self.assertFalse([r for r in report['checks'] if r['status'] == FAIL])
        paths = [row[1] for row in requests]
        self.assertIn('/assets/fonts/test.woff2', paths)
        self.assertIn('/assets/module.js', paths)
        self.assertEqual(paths.count('/assets/logo.svg'), 1)
        self.assertEqual(paths.count('/api/health'), 1)
        self.assertTrue(all(method == 'GET' for method, _, _ in requests))
        self.assertEqual([p for p in paths if p.startswith('/api/')], ['/api/health'])
        for _, _, headers in requests:
            self.assertNotIn('Cookie', headers)
            self.assertNotIn('Authorization', headers)
        self.assertNotIn('NEVER_RECORD_THIS', json.dumps(report))
        self.assertNotIn('wOF2test', json.dumps(report))
        self.assertTrue(all(r['elapsed_ms'] >= 0 for r in report['requests']))
        self.assertIn('mobile_network', [r['check'] for r in report['checks'] if r['status'] == UNTESTED])

    def test_no_third_party_or_rejected_paths_are_requested(self):
        html = b'''<img src="https://third.invalid/media.png"><script src="/api/sessions"></script>
        <link rel="stylesheet" href="/assets/../.env"><img src="/assets/a.png?token=SECRET">
        <style>@import "https://other.invalid/font.css";</style>'''
        with fixture({'/': ('text/html', html)}) as (url, requests):
            report = Probe(url).run()
        self.assertFalse(any(p in {'/.env', '/api/sessions', '/assets/a.png?token=SECRET'} for _, p, _ in requests))
        self.assertIn('resource_policy', [r['check'] for r in report['checks'] if r['status'] == FAIL])
        self.assertIn('external_dependency', [r['check'] for r in report['checks'] if r['status'] == UNTESTED])
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertNotIn('third.invalid', json.dumps(report))

    def test_redirect_is_failure_without_following_even_same_origin(self):
        def redirect(h):
            h.send_response(302)
            h.send_header('Location', '/api/sessions?token=SECRET')
            h.end_headers()
        with fixture({'/': redirect}) as (url, requests):
            report = Probe(url).run()
        self.assertTrue(any(r['check'] == 'http' and r['path'] == '/' and r['status'] == FAIL for r in report['checks']))
        self.assertFalse(any('sessions' in p for _, p, _ in requests))
        self.assertNotIn('SECRET', json.dumps(report))

    def test_missing_asset_wrong_mime_cache_stale_and_font_body(self):
        with fixture({
            '/assets/logo.svg': None,
            '/assets/app.js': ('text/html', b'<html>SPA fallback</html>'),
            '/assets/fonts/test.woff2': ('font/woff2', b'fake-font'),
            '/demo.html': ('text/html', b'page', 200, {'Cache-Control': 'public, max-age=3600', 'Age': '99'}),
        }) as (url, _):
            report = Probe(url).run()
        failures = {(r['check'], r.get('path')) for r in report['checks'] if r['status'] == FAIL}
        self.assertIn(('http', '/assets/logo.svg'), failures)
        self.assertIn(('cache', '/demo.html'), failures)
        self.assertIn(('font_signature', '/assets/fonts/test.woff2'), failures)
        # app.js is no longer referenced by the demo override; test MIME independently.
        with fixture({'/assets/app.js': ('text/html', b'<html/>')}) as (url, _):
            report = Probe(url).run()
        self.assertTrue(any(r['check'] == 'content_type' and r['status'] == FAIL for r in report['checks']))

    def test_health_version_mode_contract_and_non_json(self):
        for value, failure in [(dict(version='2.0.0'), 'version_3'),
                               (dict(modelMode='llm'), 'model_mode'),
                               (dict(sandbox=False), 'health_contract')]:
            health = json.loads(HEALTH)
            health.update(value)
            with fixture({'/api/health': ('application/json', json.dumps(health).encode())}) as (url, _):
                result = Probe(url).run()
            self.assertTrue(any(r['check'] == failure and r['status'] == FAIL for r in result['checks']))
        with fixture({'/api/health': ('application/json', b'not JSON SECRET')}) as (url, _):
            result = Probe(url).run()
        self.assertTrue(any(r['check'] == 'health_contract' and r['status'] == FAIL for r in result['checks']))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_request_size_and_total_budgets_are_enforced(self):
        with fixture() as (url, requests):
            result = Probe(url, max_requests=3).run()
        self.assertLessEqual(len(requests), 3)
        self.assertTrue(any(r['check'] == 'resource_budget' and r['status'] == FAIL for r in result['checks']))
        with fixture({'/': ('text/html', b'X' * 10000)}) as (url, _):
            result = Probe(url, max_bytes=512).run()
        self.assertTrue(any(r['check'] == 'http' and r.get('path') == '/' and r['status'] == FAIL for r in result['checks']))
        self.assertEqual(next(r for r in result['requests'] if r['path'] == '/')['bytes'], 0)
        with fixture() as (url, _):
            result = Probe(url, max_total_bytes=len(HEALTH)).run()
        self.assertLessEqual(result['total_bytes'], len(HEALTH))
        self.assertTrue(any(r['check'] == 'resource_budget' for r in result['checks']))

    def test_chunked_oversize_and_truncated_content_fail(self):
        def chunked(h):
            h.send_response(200)
            h.send_header('Transfer-Encoding', 'chunked')
            h.end_headers()
            try:
                h.wfile.write(b'1000\r\n' + b'X' * 4096 + b'\r\n0\r\n\r\n')
            except OSError:
                pass
        def truncated(h):
            h.send_response(200)
            h.send_header('Content-Length', '20')
            h.end_headers()
            h.wfile.write(b'short')
        for handler in [chunked, truncated]:
            with fixture({'/': handler}) as (url, _):
                result = Probe(url, max_bytes=512).run()
            self.assertTrue(any(r['check'] == 'http' and r.get('path') == '/' and r['status'] == FAIL for r in result['checks']))

    def test_slow_body_is_bounded_by_deadline_even_when_dripping(self):
        def slow(h):
            h.send_response(200)
            h.send_header('Content-Length', '100')
            h.end_headers()
            try:
                for _ in range(100):
                    h.wfile.write(b'x')
                    h.wfile.flush()
                    time.sleep(.025)
            except OSError:
                pass
        with fixture({'/': slow}) as (url, _):
            start = time.monotonic()
            result = Probe(url, timeout=.12, total_timeout=.3).run()
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, .9)
        self.assertTrue(any(r['status'] == FAIL for r in result['checks']))

    def test_tls_verification_context_never_disabled(self):
        probe = Probe('https://example.test/')
        self.assertEqual(probe.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(probe.context.check_hostname)
        with patch('http.client.HTTPSConnection.connect', side_effect=ssl.SSLCertVerificationError('secret certificate body')):
            result = probe.run()
        self.assertTrue(any(r['check'] == 'tls' and r['status'] == FAIL for r in result['checks']))
        self.assertNotIn('secret certificate body', json.dumps(result))

    def test_slow_header_and_dns_resolution_have_deadlines(self):
        def slow_headers(h):
            try:
                h.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                for _ in range(100):
                    h.wfile.write(b'x')
                    h.wfile.flush()
                    time.sleep(.025)
            except OSError:
                pass
        with fixture({'/': slow_headers}) as (url, _):
            start = time.monotonic()
            result = Probe(url, timeout=.1, total_timeout=.3).run()
            self.assertLess(time.monotonic() - start, .9)
        self.assertTrue(any(r['status'] == FAIL for r in result['checks']))
        def slow_dns(*args, **kwargs):
            time.sleep(.5)
            raise socket.gaierror('not available')
        with patch('socket.getaddrinfo', side_effect=slow_dns):
            start = time.monotonic()
            result = Probe('https://example.test/', timeout=.02, total_timeout=.07).run()
            self.assertLess(time.monotonic() - start, .3)
        self.assertTrue(any(r['status'] == FAIL for r in result['checks']))

    def test_real_backend_all_three_pages_assets_and_ledger_unchanged(self):
        import sqlite3
        from backend.model import IntentPlanner, ModelConfig
        from backend.server import create_server
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'test.sqlite3'
            server = create_server(port=0, db_path=database, planner=IntentPlanner(ModelConfig()))
            def snapshot():
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                    return list(db.iterdump())
            before = snapshot()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = Probe(f'http://127.0.0.1:{server.server_port}/').run()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual(snapshot(), before, 'read-only GET audit must not create sessions or ledger rows')
        self.assertFalse([r for r in result['checks'] if r['status'] == FAIL])
        paths = {r['path'] for r in result['requests']}
        self.assertTrue({'/', '/demo.html', '/study.html', '/api/health',
                         '/assets/fonts/noto-sans-sc-ui.woff2', '/assets/fonts/manrope-latin.woff2'} <= paths)
        self.assertTrue({'/assets/study.css', '/assets/study.js'} <= paths)

    def test_latency_threshold_is_not_hidden_and_llm_can_be_explicit(self):
        health = json.loads(HEALTH)
        health['modelMode'] = 'llm'
        with fixture({'/api/health': ('application/json', json.dumps(health).encode())}) as (url, _):
            result = Probe(url, expected_mode='llm', slow_ms=.000001).run()
        self.assertTrue(any(r['check'] == 'model_mode' and r['status'] == PASS for r in result['checks']))
        self.assertTrue(any(r['check'] == 'latency' and r['status'] == FAIL for r in result['checks']))


class OutputAndDeploymentTests(unittest.TestCase):
    def test_template_contract_in_current_workspace(self):
        result = deployment_checks(Path(__file__).resolve().parent.parent)
        self.assertFalse([r for r in result if r['status'] == FAIL])
        self.assertIn('docker_runtime', [r['check'] for r in result if r['status'] == UNTESTED])

    def test_packaging_manifest_includes_runtime_data_study_and_rejects_symlinks(self):
        from scripts.v3_readiness import CONTEXT_FIXED
        actual = Path(__file__).resolve().parent.parent
        manifest = {p.as_posix() for p in context_manifest(actual)}
        self.assertTrue({'study.html', 'assets/study.css', 'assets/study.js',
                         'backend/fixtures.json', 'backend/life_scenarios.json',
                         'assets/fonts/OFL-Manrope.txt'} <= manifest)
        self.assertFalse(any(p.startswith(('deploy/', '.local/', 'releases/')) for p in manifest))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in CONTEXT_FIXED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic fixture')
            (root / 'backend/server.py').write_text('# synthetic code')
            (root / 'assets/private.env').write_text('DO_NOT_COPY')
            (root / 'assets/.hidden.js').write_text('DO_NOT_COPY')
            manifest = {p.as_posix() for p in context_manifest(root)}
            self.assertNotIn('assets/private.env', manifest)
            self.assertNotIn('assets/.hidden.js', manifest)
            (root / 'assets/linked.css').symlink_to(root / 'VERSION')
            with self.assertRaises(ValueError):
                context_manifest(root)

    def test_staged_context_checks_missing_runtime_file_and_rejects_unknown_files(self):
        actual = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = context_manifest(actual)
            for relative in manifest + [Path('deploy/docker-compose.yml'), Path('deploy/Caddyfile'), Path('deploy/build-context.dockerignore')]:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(actual / relative, target)
            stage = root / 'deploy/build-context'
            stage.mkdir()
            for relative in manifest:
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, target)
            shutil.copyfile(root / 'deploy/build-context.dockerignore', stage / '.dockerignore')
            def status():
                return next(r['status'] for r in deployment_checks(root) if r['check'] == 'staged_context')
            self.assertEqual(status(), PASS)
            (stage / 'backend/server.py').rename(stage / 'backend/server.saved')
            self.assertEqual(status(), FAIL)
            (stage / 'backend/server.saved').rename(stage / 'backend/server.py')
            (stage / '.env').write_text('SYNTHETIC_SECRET')
            self.assertEqual(status(), FAIL)

    def test_missing_or_regressed_templates_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(all(r['status'] == FAIL for r in deployment_checks(directory)))
            root = Path(directory)
            (root / 'deploy').mkdir()
            actual = Path(__file__).resolve().parent.parent
            for name in ['Dockerfile', 'deploy/docker-compose.yml', 'deploy/Caddyfile', 'deploy/build-context.dockerignore']:
                content = (actual / name).read_text().replace('COPY VERSION index.html demo.html study.html ./', 'COPY index.html demo.html ./').replace('context: ./build-context', 'context: ..').replace('read_only: true', 'read_only: false')
                (root / name).write_text(content)
            failures = {r['check'] for r in deployment_checks(root) if r['status'] == FAIL}
        self.assertTrue({'docker_version', 'readonly_sqlite_tmpfs', 'build_context'} <= failures)

    def test_cli_report_pair_and_refuses_overwrite_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'first'
            with fixture() as (url, requests):
                with patch('builtins.print'):
                    self.assertEqual(main([url, '--output', str(output)]), 0)
                first = {p.name: p.read_bytes() for p in output.iterdir()}
                self.assertEqual(set(first), {'readiness.json', 'readiness.md'})
                report = json.loads(first['readiness.json'])
                self.assertGreater(report['summary'][UNTESTED], 0)
                before = len(requests)
                with patch('builtins.print'):
                    self.assertEqual(main([url, '--output', str(output)]), 2)
                self.assertEqual(len(requests), before)
                with self.assertRaises(FileExistsError):
                    write_report(output, report)
                self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, first)

    def test_bad_url_and_invalid_budgets_do_not_echo_credentials_or_request(self):
        with patch.object(Probe, 'get', side_effect=AssertionError('must not request')):
            for args in [['https://user:SECRET@example.test/?token=SECRET'],
                         ['https://example.test/', '--timeout', 'nan'],
                         ['https://example.test/', '--max-requests', '0']]:
                with patch('builtins.print') as output:
                    self.assertEqual(main(args), 2)
                self.assertNotIn('SECRET', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
