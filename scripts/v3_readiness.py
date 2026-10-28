#!/usr/bin/env python3
"""Bounded, credential-free GET audit. No browser, sessions or mutation API calls.

Only the fixed entry points and /assets/ static types are reachable. Redirects
are deliberately not followed. Reports never contain response bodies or cookies.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import queue
import threading
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
TYPES = {
    '.html': {'text/html'}, '.css': {'text/css'},
    '.js': {'text/javascript', 'application/javascript'},
    '.mjs': {'text/javascript', 'application/javascript'},
    '.svg': {'image/svg+xml'}, '.ico': {'image/x-icon', 'image/vnd.microsoft.icon'},
    '.png': {'image/png'}, '.jpg': {'image/jpeg'}, '.jpeg': {'image/jpeg'},
    '.webp': {'image/webp'}, '.gif': {'image/gif'}, '.avif': {'image/avif'},
    '.woff2': {'font/woff2'}, '.woff': {'font/woff', 'application/font-woff'},
    '.ttf': {'font/ttf'}, '.otf': {'font/otf'},
}
PASS, FAIL, UNTESTED = 'pass', 'fail', 'untested'
LABELS = {PASS: '已通过', FAIL: '失败', UNTESTED: '未测'}


class UnsafeURL(ValueError):
    pass


class LimitReached(Exception):
    pass


def split_safe(value):
    # Check before urlsplit/urljoin: both can silently strip or normalise input.
    if (not isinstance(value, str) or len(value) > 2048 or
            any(ord(c) <= 32 or ord(c) >= 127 for c in value) or
            any(c in value for c in ('\\', '%', '?'))):
        raise UnsafeURL('URL 含编码、空白、查询或反斜杠；拒绝请求')
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeURL('URL 不允许 userinfo')
        if any(p in {'.', '..'} for p in parsed.path.split('/')):
            raise UnsafeURL('URL 不允许目录穿越')
        if '//' in parsed.path or ';' in parsed.path:
            raise UnsafeURL('URL 路径不受支持')
        if parsed.netloc and (not parsed.hostname or parsed.port == 0):
            raise UnsafeURL('URL 主机或端口无效')
        _ = parsed.port
        return parsed
    except ValueError:
        raise UnsafeURL('URL 格式无效或含受限内容') from None


def loopback(host):
    try:
        return ipaddress.ip_address(host).version == 4 and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def origin_tuple(parsed):
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def base_url(value, *, origin_only=False):
    parsed = split_safe(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.netloc or
            parsed.fragment or '#' in value or parsed.path not in {'', '/', '/index.html', '/demo.html', '/study.html'}):
        raise UnsafeURL('仅接受站点根 URL、index.html、demo.html 或 study.html')
    host = parsed.hostname
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host or '') or '..' in host:
        raise UnsafeURL('主机格式无效')
    if parsed.scheme == 'http' and not loopback(host):
        raise UnsafeURL('公网 URL 必须使用 HTTPS；HTTP 仅允许字面 127/8 回环地址')
    if origin_only and not loopback(host):
        raise UnsafeURL('--origin-url 仅允许字面 127/8 回环地址')
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), '/', '', ''))


def resource_url(base, reference):
    if reference.startswith(('#', 'data:')):
        return None, 'embedded'
    if len(reference) > 2048:
        raise UnsafeURL('资源 URL 过长')
    # ES module ./file is harmless; .. is still rejected before urljoin.
    while reference.startswith('./'):
        reference = reference[2:]
    split_safe(reference)
    candidate = urljoin(base, reference)
    target = split_safe(candidate)
    if target.scheme not in {'http', 'https'} or origin_tuple(target) != origin_tuple(urlsplit(base)):
        return None, 'external'
    path = target.path
    if path in {'/', '/index.html', '/demo.html', '/study.html', '/api/health'}:
        pass
    elif (not path.startswith('/assets/') or Path(path).suffix.lower() not in TYPES or
          Path(path).suffix.lower() == '.html' or
          not re.fullmatch(r'/assets/[A-Za-z0-9_./-]+', path) or
          any(part.startswith('.') for part in path.split('/')[2:])):
        raise UnsafeURL('仅允许固定入口及 /assets/ 静态类型；其它 API 和文件禁止请求')
    return urlunsplit((target.scheme, target.netloc, path, '', '')), None


class References(HTMLParser):
    """Loaded resources, not navigation, forms, iframe documents or media URLs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []
        self.unsupported = False
        self.styles = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base':
            self.unsupported = True
        if tag == 'style':
            self.in_style = True
        if attrs.get('style'):
            self.styles.append(attrs['style'])
        if tag in {'script', 'img', 'image', 'use', 'input'}:
            for name in ('src', 'href', 'xlink:href'):
                if attrs.get(name):
                    self.refs.append(attrs[name])
        if tag == 'link' and set(attrs.get('rel', '').lower().split()) & {
                'stylesheet', 'icon', 'preload', 'modulepreload', 'apple-touch-icon'}:
            if attrs.get('href'):
                self.refs.append(attrs['href'])
        if tag in {'img', 'source'} and attrs.get('srcset'):
            # Data URI srcsets contain commas; mark unsupported rather than misparse.
            if 'data:' in attrs['srcset']:
                self.unsupported = True
            else:
                self.refs.extend(part.strip().split()[0] for part in attrs['srcset'].split(',') if part.strip())
        if tag in {'video', 'audio', 'iframe', 'object', 'embed'} or (tag == 'source' and attrs.get('src')):
            self.unsupported = True
        if tag == 'video' and attrs.get('poster'):
            self.refs.append(attrs['poster'])

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)


def css_refs(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    urls = re.findall(r'url\(\s*[\'"]?([^\)\'\"\s]+)[\'"]?\s*\)', text, flags=re.I)
    urls += re.findall(r'@import\s+[\'"]([^\'\"]+)[\'"]', text, flags=re.I)
    # Escaped CSS URLs require a CSS parser; do not silently claim coverage.
    unsupported = '\\' in text or len(re.findall(r'url\s*\(', text, re.I)) != len(urls) - len(re.findall(r'@import\s+[\'"]', text, re.I))
    return urls, unsupported


def dependencies(path, raw):
    suffix = Path(path).suffix.lower()
    if path == '/' or suffix in {'.html', '.css', '.js', '.mjs', '.svg'}:
        text = raw.decode('utf-8', errors='strict')
    else:
        return [], False
    if path == '/' or suffix in {'.html', '.svg'}:
        parser = References()
        parser.feed(text)
        refs, unsupported = list(parser.refs), parser.unsupported
        for style in parser.styles:
            found, incomplete = css_refs(style)
            refs += found
            unsupported |= incomplete
        return refs, unsupported
    if suffix == '.css':
        return css_refs(text)
    # Literal ES imports only; never execute JavaScript or follow fetch() calls.
    return re.findall(r'(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)[\'"]([^\'\"]+)[\'"]', text), False


class Probe:
    def __init__(self, url, *, timeout=10, total_timeout=120, max_requests=96,
                 max_bytes=4 * 1024 * 1024, max_total_bytes=24 * 1024 * 1024,
                 slow_ms=3000, expected_mode='rules'):
        self.url = base_url(url)
        self.timeout, self.total_timeout = timeout, total_timeout
        self.max_requests, self.max_bytes, self.max_total_bytes = max_requests, max_bytes, max_total_bytes
        self.slow_ms, self.expected_mode = slow_ms, expected_mode
        self.checks, self.requests = [], []
        self.used_bytes = 0
        self.started = time.monotonic()
        # Default trust store, certificate verification and hostname checking stay on.
        self.context = ssl.create_default_context()
        self.addresses = None

    def add(self, name, status, detail, path=None):
        row = {'check': name, 'status': status, 'detail': detail}
        if path:
            row['path'] = path
        self.checks.append(row)

    def remaining(self, start):
        remaining = min(self.timeout - (time.monotonic() - start),
                        self.total_timeout - (time.monotonic() - self.started))
        if remaining <= 0:
            raise LimitReached('时间预算耗尽')
        return remaining

    def connect_socket(self, address, timeout=10, source_address=None):
        # Resolve once in a daemon worker: a stuck OS resolver cannot stall the audit.
        deadline = time.monotonic() + timeout
        if self.addresses is None:
            answers = queue.Queue(maxsize=1)
            def resolve():
                try:
                    answers.put((socket.getaddrinfo(*address, type=socket.SOCK_STREAM), None))
                except OSError as error:
                    answers.put((None, error))
            threading.Thread(target=resolve, daemon=True).start()
            try:
                self.addresses, error = answers.get(timeout=max(.001, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError('DNS deadline') from None
            if error:
                raise error
        last_error = None
        for family, kind, proto, _, sockaddr in self.addresses:
            sock = socket.socket(family, kind, proto)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('connect deadline')
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('connect deadline')
                sock.settimeout(remaining)
                return sock
            except OSError as error:
                last_error = error
                sock.close()
        raise last_error or OSError('no address')

    def get(self, url):
        # Enforce the same allowlist even if a caller bypasses the crawler.
        safe, reason = resource_url(self.url, url)
        if safe != url or reason:
            raise UnsafeURL('请求未通过同源路径白名单')
        if len(self.requests) >= self.max_requests or self.used_bytes >= self.max_total_bytes:
            raise LimitReached('请求数或总字节预算耗尽')
        start = time.monotonic()
        parsed = urlsplit(url)
        row = {'path': parsed.path, 'method': 'GET', 'status': None, 'bytes': 0}
        self.requests.append(row)
        conn = None
        watchdog = None
        try:
            cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
            kwargs = {'context': self.context} if parsed.scheme == 'https' else {}
            conn = cls(parsed.hostname, parsed.port, timeout=self.remaining(start), **kwargs)
            conn._create_connection = self.connect_socket
            conn.connect()
            if parsed.scheme == 'https':
                row['tls_verified'] = True
            sock = conn.sock
            def interrupt():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # Covers slow-drip status/header lines as well as response bodies.
            watchdog = threading.Timer(self.remaining(start), interrupt)
            watchdog.daemon = True
            watchdog.start()
            sock.settimeout(self.remaining(start))
            conn.request('GET', parsed.path, headers={
                'Accept-Encoding': 'identity', 'User-Agent': 'TROPOS-v3-readiness/1.0',
                'Origin': self.url.rstrip('/'), 'Connection': 'close',
            })
            sock.settimeout(self.remaining(start))
            response = conn.getresponse()
            row['status'] = response.status
            row['ttfb_ms'] = round((time.monotonic() - start) * 1000, 1)
            # No automatic redirects, proxies, cookies, netrc or auth handlers.
            if response.status != 200:
                raise ValueError('HTTP 非 200（重定向不会跟随）')
            limit = min(self.max_bytes, self.max_total_bytes - self.used_bytes,
                        65536 if parsed.path == '/api/health' else self.max_bytes)
            declared = response.getheader('Content-Length')
            if declared is not None and (not declared.isdigit() or int(declared) > limit):
                raise ValueError('Content-Length 无效或超过大小限制')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('不解压响应；服务器应遵守 Accept-Encoding: identity')
            content = bytearray()
            while True:
                sock.settimeout(self.remaining(start))
                chunk = response.read1(min(65536, limit - len(content) + 1))
                self.used_bytes += len(chunk)
                row['bytes'] += len(chunk)
                self.remaining(start)
                if len(content) + len(chunk) > limit:
                    raise ValueError('响应超过大小限制')
                if not chunk:
                    break
                content.extend(chunk)
                if declared is not None and len(content) == int(declared):
                    break
            if declared is not None and len(content) != int(declared):
                raise ValueError('响应被截断')
            if not content:
                raise ValueError('空响应')
            # Only retain headers needed for audit; never Set-Cookie, CSP nonces, etc.
            headers = {key: response.getheader(key, '') for key in ('Content-Type', 'Cache-Control', 'Age')}
            # Store the verdict rather than arbitrary server-provided header text.
            cache = {x.strip().lower() for x in headers['Cache-Control'].split(',')}
            row['no_store'] = 'no-store' in cache
            row['cache_fresh'] = headers['Age'] in {'', '0'}
            row['mime_ok'] = headers['Content-Type'].split(';')[0].strip().lower() in (
                {'application/json'} if parsed.path == '/api/health' else TYPES.get(Path(parsed.path).suffix.lower(), {'text/html'}))
            return bytes(content), row
        finally:
            row['elapsed_ms'] = round((time.monotonic() - start) * 1000, 1)
            if watchdog:
                watchdog.cancel()
            if conn:
                conn.close()

    def run(self):
        queue = deque([self.url + 'api/health', self.url, self.url + 'demo.html', self.url + 'study.html'])
        seen = set(queue)
        while queue:
            url = queue.popleft()
            path = urlsplit(url).path
            try:
                raw, row = self.get(url)
                self.add('http', PASS, '200；完整响应；GET，无凭据', path)
                self.add('content_type', PASS if row['mime_ok'] else FAIL, '按资源类型核对 MIME', path)
                self.add('cache', PASS if row['no_store'] and row['cache_fresh'] else FAIL,
                         '要求 Cache-Control: no-store 且 Age 缺省或为 0', path)
                self.add('latency', PASS if row['elapsed_ms'] <= self.slow_ms else FAIL,
                         f"单次完整响应 {row['elapsed_ms']} ms；阈值 {self.slow_ms:g} ms", path)
                if path == '/api/health':
                    self.health(raw, path)
                    continue
                if path.endswith('.woff2'):
                    self.add('font_signature', PASS if raw[:4] == b'wOF2' else FAIL, 'WOFF2 文件签名', path)
                refs, unsupported = dependencies(path, raw)
                if unsupported:
                    self.add('dependency_coverage', UNTESTED, '含不支持的资源语法、base 或媒体；需浏览器复核', path)
                if len(refs) > self.max_requests:
                    self.add('resource_budget', FAIL, '单个文件资源引用超过预算；未穷尽全部引用', path)
                for ref in refs[:self.max_requests]:
                    try:
                        child, reason = resource_url(url, ref)
                        if reason == 'external':
                            self.add('external_dependency', UNTESTED, '第三方依赖未请求，地址不记录', path)
                        elif child and child not in seen:
                            if len(seen) >= self.max_requests:
                                self.add('resource_budget', FAIL, '发现资源超过请求预算；资源覆盖不完整', path)
                                continue
                            seen.add(child)
                            queue.append(child)
                    except UnsafeURL:
                        self.add('resource_policy', FAIL, '依赖违反 URL/静态路径白名单；未请求，地址不记录', path)
            except LimitReached:
                self.add('resource_budget', FAIL, '请求/字节/时间预算耗尽；资源覆盖不完整', path)
                break
            except (OSError, ValueError, http.client.HTTPException, RecursionError) as error:
                # Exception messages may include attacker-controlled bodies or URL credentials.
                self.add('http', FAIL, f'读取失败（{type(error).__name__}）；无正文或敏感头记录', path)
        if self.url.startswith('https:'):
            good = bool(self.requests) and all(r.get('tls_verified') is True for r in self.requests)
            self.add('tls', PASS if good else FAIL, '所有 HTTPS 请求均使用系统信任链及主机名验证；不跟随重定向')
        else:
            self.add('tls', UNTESTED, '仅用于字面 127/8 回环 HTTP；未验证公网 HTTPS')
        self.add('browser_runtime', UNTESTED, '未执行 JavaScript；不建立会话，不调用其它 API，业务连接与渲染待人工验收')
        self.add('mobile_network', UNTESTED, '无 VPN 的真人手机 Wi-Fi/蜂窝、韩国跨境链路稳定性待实测')
        return {'url': self.url, 'checks': self.checks, 'requests': self.requests,
                'limits': {'max_requests': self.max_requests, 'max_bytes': self.max_bytes,
                           'max_total_bytes': self.max_total_bytes, 'timeout': self.timeout,
                           'total_timeout': self.total_timeout, 'slow_ms': self.slow_ms},
                'expected_mode': self.expected_mode, 'total_bytes': self.used_bytes}

    def health(self, raw, path):
        try:
            obj = json.loads(raw)
            valid = (isinstance(obj, dict) and obj.get('ok') is True and obj.get('sandbox') is True and
                     obj.get('authentication') == 'simulated-persona-session')
            version = valid and isinstance(obj.get('version'), str) and bool(re.fullmatch(r'3\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', obj['version']))
            mode = valid and obj.get('modelMode') == self.expected_mode
            self.add('health_contract', PASS if valid else FAIL, 'ok / sandbox / simulated-persona-session', path)
            self.add('version_3', PASS if version else FAIL, '要求 3.x.x 版本', path)
            self.add('model_mode', PASS if mode else FAIL, f'要求 modelMode={self.expected_mode}；仅健康声明，不是模型实测', path)
        except (ValueError, RecursionError):
            self.add('health_contract', FAIL, 'health JSON 无效；不记录正文', path)


CONTEXT_FIXED = {'Dockerfile', 'VERSION', 'index.html', 'demo.html', 'study.html',
                 'backend/fixtures.json', 'backend/life_scenarios.json',
                 'reports/evaluation.json', 'reports/intent-rules.json',
                 'assets/fonts/OFL-Manrope.txt', 'assets/fonts/OFL-NotoSansSC.txt',
                 'assets/fonts/README.md'}


def context_allowed(relative):
    path = Path(relative)
    if any(p.startswith('.') for p in path.parts):
        return False
    if relative in CONTEXT_FIXED:
        return True
    if len(path.parts) == 2 and path.parts[0] == 'backend' and path.suffix == '.py':
        return True
    if len(path.parts) == 2 and path.parts[0] == 'assets' and path.suffix in TYPES and path.suffix != '.html':
        return True
    return (len(path.parts) == 3 and path.parts[:2] == ('assets', 'fonts') and
            path.suffix in {'.woff2', '.woff', '.ttf', '.otf'})


def context_manifest(root):
    """Whitelisted source paths only. Never reads env, databases, user data or archives."""
    root = Path(root).resolve()
    paths = {Path(item) for item in CONTEXT_FIXED}
    for name in ('backend', 'assets'):
        folder = root / name
        if folder.is_symlink():
            raise ValueError('构建源目录不允许符号链接')
        for path in folder.rglob('*'):
            if path.is_symlink():
                raise ValueError('构建源不允许符号链接')
            relative = path.relative_to(root)
            if path.is_file() and context_allowed(relative.as_posix()):
                paths.add(relative)
    for relative in paths:
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('构建源文件缺失、链接或越界')
    return sorted(paths)


def deployment_checks(root):
    """Conservative checks for this repository's fixed templates, not a YAML engine."""
    root = Path(root)
    checks = []
    def add(name, ok, detail):
        checks.append({'check': name, 'status': PASS if ok else FAIL, 'detail': detail})
    try:
        docker = (root / 'Dockerfile').read_text(encoding='utf-8')
        compose = (root / 'deploy/docker-compose.yml').read_text(encoding='utf-8')
        caddy = (root / 'deploy/Caddyfile').read_text(encoding='utf-8')
        ignore = (root / 'deploy/build-context.dockerignore').read_text(encoding='utf-8')
    except OSError:
        add('deployment_files', False, '部署模板缺失或不可读')
        return checks
    add('docker_version', bool(re.search(r'^COPY\s+VERSION\s+index.html\s+demo.html\s+study.html\s+\./\s*$', docker, re.M)), 'Dockerfile 包含 VERSION 和页面')
    add('docker_user_health', 'USER tropos' in docker and '--uid 10001' in docker and
        'HEALTHCHECK' in docker and "127.0.0.1:8765/api/health" in docker, '非 root 用户及回环 health 声明')
    app = compose.split('  caddy:', 1)[0]
    add('readonly_sqlite_tmpfs', all(item in app for item in (
        'read_only: true', 'tmpfs:', '/tmp:size=64m,mode=1777', './data:/data', '/data/tropos.sqlite3',
        '"--host", "127.0.0.1"', 'network_mode: host')) and 'ports:' not in app,
        '模板：回环监听、只读根、/tmp tmpfs、独立 SQLite 可写目录；运行权限尚未验证')
    add('caddy_proxy', all(item in caddy for item in ('reverse_proxy 127.0.0.1:8765',
        'header_up Host 127.0.0.1:8765', 'header_up Origin http://127.0.0.1:8765')) and
        'condition: service_healthy' in compose, '模板：保持原有 Host/Origin 边界及健康依赖')
    add('build_context', 'context: ./build-context' in compose and
        (ignore.splitlines() or [None])[0] == '**' and all('!' + item in ignore for item in ('Dockerfile', 'VERSION', 'study.html', 'backend/*.py', 'backend/fixtures.json', 'assets/*.css', 'assets/fonts/*.woff2', 'reports/evaluation.json', 'reports/intent-rules.json')),
        '模板使用隔离 build-context 和默认拒绝的打包清单；上传前仍需实物检查')
    try:
        sources = context_manifest(root)
        add('context_sources', True, '白名单源文件齐全且无符号链接；含运行必需的两份 backend JSON 与字体许可')
    except (ValueError, OSError):
        sources = []
        add('context_sources', False, '白名单源文件缺失或发现符号链接；不能安全打包')
    staged = root / 'deploy/build-context'
    if staged.exists() or staged.is_symlink():
        safe = not staged.is_symlink()
        if safe:
            for file in staged.rglob('*'):
                relative = file.relative_to(staged).as_posix()
                if file.is_symlink() or (file.is_file() and relative != '.dockerignore' and not context_allowed(relative)):
                    safe = False
                    break
        expected = set(sources) | {Path('.dockerignore')}
        safe = safe and bool(sources)
        safe = safe and all((staged / p).is_file() and not (staged / p).is_symlink() for p in expected)
        safe = safe and (staged / '.dockerignore').read_text(encoding='utf-8') == ignore
        add('staged_context', safe, '隔离构建目录实物白名单/必需文件检查；不读取未知文件内容')
    else:
        checks.append({'check': 'staged_context', 'status': UNTESTED, 'detail': '未生成 deploy/build-context；上传包及实际构建上下文未测'})
    checks.append({'check': 'docker_runtime', 'status': UNTESTED,
                   'detail': '未调用 Docker；镜像构建、Caddy 实际配置、SQLite 写权限/重启持久化须在控制台验收'})
    return checks


def report_markdown(report):
    lines = ['# TROPOS v3 只读验收记录', '', f"UTC：{report['generated_at']}", '',
             '只证明本次 GET 与模板观察；真人手机、业务和跨境稳定性仍待验收。', '']
    for group in report['targets']:
        lines += [f"## {group['role']} — {group['url']}", '']
        for item in group['checks']:
            lines.append(f"- {LABELS[item['status']]} · {item['check']} {item.get('path', '')}：{item['detail']}")
        lines += ['', '| GET 路径 | HTTP | 字节 | 首字节 ms | 完整响应 ms |', '|---|---:|---:|---:|---:|']
        for row in group['requests']:
            lines.append(f"| {row['path']} | {row['status']} | {row['bytes']} | {row.get('ttfb_ms', '—')} | {row['elapsed_ms']} |")
        lines.append('')
    lines += ['## 本地部署模板', '']
    for item in report['deployment']:
        lines.append(f"- {LABELS[item['status']]} · {item['check']}：{item['detail']}")
    lines += ['', '汇总：' + '；'.join(f'{LABELS[k]} {v}' for k, v in report['summary'].items()), '']
    return '\n'.join(lines)


def write_report(directory, report):
    # mkdir(exist_ok=False) reserves an entire pair; existing evidence never changes.
    target = Path(directory)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    with (target / 'readiness.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    with (target / 'readiness.md').open('x', encoding='utf-8') as handle:
        handle.write(report_markdown(report))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--origin-url', help='可选；仅允许服务器本机字面 127/8 回环 URL')
    parser.add_argument('--expected-mode', choices=('rules', 'llm'), default='rules')
    parser.add_argument('--output', help='新建目录，写 readiness.json + readiness.md；已存在即拒绝')
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--total-timeout', type=float, default=120)
    parser.add_argument('--max-requests', type=int, default=96)
    parser.add_argument('--max-bytes', type=int, default=4 * 1024 * 1024)
    parser.add_argument('--max-total-bytes', type=int, default=24 * 1024 * 1024)
    parser.add_argument('--slow-ms', type=float, default=3000)
    args = parser.parse_args(argv)
    try:
        public = base_url(args.url)
        origin = base_url(args.origin_url, origin_only=True) if args.origin_url else None
        if not (0 < args.timeout <= 30 and 0 < args.total_timeout <= 300 and
                0 < args.max_requests <= 256 and 0 < args.max_bytes <= 16 * 1024 * 1024 and
                0 < args.max_total_bytes <= 64 * 1024 * 1024 and 0 < args.slow_ms <= 300000 and
                all(math.isfinite(x) for x in (args.timeout, args.total_timeout, args.slow_ms))):
            raise ValueError('检查预算超出允许范围')
        if args.output and (Path(args.output).exists() or Path(args.output).is_symlink()):
            raise ValueError('--output 目录已存在；首次结果不会被覆盖')
    except ValueError as error:
        # Safe fixed messages only; never echo the supplied URL.
        print(f'输入拒绝：{error}')
        return 2
    report = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(), 'targets': [],
              'deployment': deployment_checks(ROOT)}
    options = {key: getattr(args, key) for key in ('timeout', 'total_timeout', 'max_requests', 'max_bytes', 'max_total_bytes', 'slow_ms', 'expected_mode')}
    for role, url in [('public', public)] + ([('origin', origin)] if origin else []):
        probe = Probe(url, **options).run()
        probe['role'] = role
        if role == 'public' and not origin:
            probe['checks'].append({'check': 'origin_connection', 'status': UNTESTED,
                                    'detail': '未提供控制台本机 --origin-url；公网 health 不能定位源站/代理问题'})
        report['targets'].append(probe)
    checks = report['deployment'] + [row for target in report['targets'] for row in target['checks']]
    report['summary'] = {status: sum(row['status'] == status for row in checks) for status in LABELS}
    if args.output:
        try:
            write_report(args.output, report)
        except OSError:
            print('报告写入失败；不会覆盖已有结果。请选择新的输出目录。')
            return 2
    print(report_markdown(report))
    return 1 if report['summary'][FAIL] else 0


if __name__ == '__main__':
    raise SystemExit(main())
