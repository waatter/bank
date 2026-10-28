#!/usr/bin/env python3
"""Create a public v3 release candidate without executing application code.

Example (run only after review):
    python3 scripts/package_v3.py --output dist/tropos-3.0.0-rc1.zip

Relative output paths are relative to --root (default: this project's root).
The ZIP is the publication commit marker: its checksum is published first, then
the complete ZIP is hard-linked into place without replacing either destination.
Git dirtiness covers package-eligible sources only, so excluded secrets are never
read just to determine whether the working tree is dirty.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import uuid
import zipfile


DEFAULT_ROOT = Path(__file__).absolute().parent.parent
SOURCE_DIRS = frozenset({"backend", "assets", "eval", "tests", "docs", "scripts", "reports"})
ROOT_FILES = frozenset({"README.md", "VERSION", "index.html", "demo.html", "study.html",
                        "Dockerfile", ".env.example", "requirements.txt"})
DEPLOY_FILES = frozenset({"README.md", "prepare.sh", "docker-compose.yml", "docker-compose.yaml",
                          "compose.yml", "compose.yaml", "Caddyfile", "Dockerfile", "nginx.conf",
                          "build-context.dockerignore", ".dockerignore", ".env.example", "env.production.example"})
PUBLIC_SUFFIXES = frozenset({".py", ".js", ".mjs", ".cjs", ".html", ".css", ".json", ".md",
                             ".txt", ".svg", ".ico", ".png", ".jpg", ".jpeg", ".webp",
                             ".gif", ".avif", ".woff", ".woff2", ".ttf", ".otf", ".sh",
                             ".ps1", ".cmd", ".bat", ".yml", ".yaml", ".toml", ".ini", ".cfg"})
EXCLUDED_DIRS = frozenset({".local", ".tmp", "dist", "releases", "__pycache__", "node_modules",
                           "data", "raw", "exports", "export", "backups", "backup"})
PRIVATE_NAME = re.compile(
    r"(?:^|[._ -])(?:secrets?|credentials?|tokens?|keys?|passwords?|passwd|private|exports?|raw|"
    r"user[-_ ]?(?:data|exports?)|original[-_ ]?data)(?:$|[._ -])", re.I)
PRIVATE_BINARY = re.compile(r"\.(?:sqlite[^.]*|db|key|pem|p12|pfx|jks|keystore)(?:$|[.~-])", re.I)
VERSION_PATTERN = re.compile(r"3\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
MANIFEST_NAME = "PACKAGE_MANIFEST.json"


class PackageError(ValueError):
    """The requested package cannot be safely published."""


def excluded(parts):
    """Path-only checks; excluded directories are never opened or traversed."""
    for part in parts:
        lower = part.lower()
        if lower == ".env.example" or (tuple(parts) == ("deploy", ".dockerignore") and lower == ".dockerignore"):
            continue
        if lower.startswith(".") or ".env" in lower or lower in EXCLUDED_DIRS:
            return True
        if PRIVATE_NAME.search(lower) or PRIVATE_BINARY.search(lower):
            return True
        if lower.startswith(("id_rsa", "id_ed25519", "id_ecdsa")):
            return True
        if any(word in part for word in ("原始", "用户导出", "用户数据", "密钥", "凭据")):
            return True
    return False


def eligible(name):
    parts = PurePosixPath(name).parts
    if not parts or any(p in {".", ".."} or "\\" in p or "\n" in p or "\r" in p for p in parts):
        return False
    # Public template/config dotfiles are handled before the hidden-path rule.
    if excluded(parts):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES
    if parts[0] == "deploy":
        return len(parts) == 2 and parts[1] in DEPLOY_FILES
    return parts[0] in SOURCE_DIRS and (
        parts[-1] == ".env.example" or PurePosixPath(name).suffix.lower() in PUBLIC_SUFFIXES)


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def directory(path, create=False):
    """Walk absolute components with openat/O_NOFOLLOW, including output parents."""
    absolute = Path(os.path.abspath(path))
    fd = os.open(absolute.anchor, _DIR_FLAGS)
    try:
        for component in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(component, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def source_file(root_fd, name):
    parts = PurePosixPath(name).parts
    parent = os.dup(root_fd)
    fd = None
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIR_FLAGS, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PackageError(f"Not a regular source file: {name}")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            yield stream
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def collect_files(root_fd):
    files = []

    def visit(fd, prefix=""):
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            if not prefix:
                if name not in SOURCE_DIRS | ROOT_FILES | {"deploy"}:
                    continue
            elif prefix == "deploy" and name not in DEPLOY_FILES:
                continue
            if excluded(PurePosixPath(relative).parts):
                continue
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise PackageError(f"Symlink rejected: {relative}")
            if stat.S_ISDIR(info.st_mode):
                if name in ROOT_FILES or prefix == "deploy":
                    raise PackageError(f"Expected a regular source file: {relative}")
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                try:
                    visit(child, relative)
                finally:
                    os.close(child)
            elif eligible(relative):
                if not stat.S_ISREG(info.st_mode):
                    raise PackageError(f"Not a regular source file: {relative}")
                files.append(relative)

    visit(root_fd)
    if "VERSION" not in files:
        raise PackageError("VERSION is required")
    return sorted(files)


def git_metadata(root):
    """Read object metadata only: no status, diff, filters, hooks or source execution."""
    environment = dict({k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")

    def git(*args):
        result = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-C", str(root), *args], env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=True)
        return result.stdout

    scope = "package-eligible source files compared with HEAD; excluded paths are not read"
    unavailable = {"available": False, "branch": None, "head": None, "dirty": None,
                   "dirty_scope": scope}
    try:
        # Do not attribute a nested export directory to an unrelated parent repository.
        top = git("rev-parse", "--show-toplevel").decode().strip()
        if Path(top) != root:
            return unavailable, {}
        head = git("rev-parse", "--verify", "HEAD").decode().strip()
        try:
            branch = git("symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
        except subprocess.CalledProcessError:
            branch = None
        tree = git("ls-tree", "-rz", "--full-tree", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return unavailable, {}
    entries = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        properties, path = entry.split(b"\t", 1)
        mode, kind, oid = properties.decode().split()
        name = os.fsdecode(path)
        if eligible(name):
            entries[name] = (mode, oid) if kind == "blob" else (mode, None)
    return {"available": True, "branch": branch, "head": head, "dirty": False,
            "dirty_scope": scope}, entries


def _exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _new_file(fd, name):
    return os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fd), "wb")


def _zip_info(name, executable=False):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
    return info


def create_package(root, output):
    root = Path(os.path.abspath(root))
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != ".zip" or "\n" in output.name or "\r" in output.name:
        raise PackageError("--output must name a .zip file")
    checksum_name = output.name + ".sha256"
    temporary = f".package-v3-{uuid.uuid4().hex}"
    temporary_sum = temporary + ".sha256"
    with directory(root) as root_fd, directory(output.parent, create=True) as output_fd:
        if _exists(output_fd, output.name) or _exists(output_fd, checksum_name):
            raise PackageError("Refusing to overwrite an existing ZIP or checksum")
        files = collect_files(root_fd)
        with source_file(root_fd, "VERSION") as stream:
            version_bytes = stream.read(257)
        try:
            version = version_bytes.decode("utf-8").strip()
        except UnicodeError as exc:
            raise PackageError("VERSION must contain a public 3.* version") from exc
        if len(version_bytes) > 256 or not VERSION_PATTERN.fullmatch(version):
            raise PackageError("Only 3.* source versions are permitted")
        git, head_files = git_metadata(root)
        manifest = {"schema_version": 1, "source_version": version, "git": git,
                    "layout": "Project files are at ZIP root; deployment files remain in deploy/.",
                    "deployment": {
                        "build_context": "deploy/build-context (generated after extraction)",
                        "instructions": "From extracted root, follow deploy/README.md and "
                        "docs/3.0_手机与部署验收.md to generate the sanitized build context. "
                        "For updates preserve existing production configuration and data; "
                        "do not run the first-install deploy/prepare.sh. The packager never deploys.",
                        "excluded": ["deploy/data", "deploy/build-context", "all .env except .env.example"],
                    },
                    "policy": {"source_directories": sorted(SOURCE_DIRS),
                               "root_files": sorted(ROOT_FILES), "deploy_files": sorted(DEPLOY_FILES),
                               "data_exports": "Raw/user exports, CSV/TSV, databases and archives excluded; "
                               "only public source/document/asset suffixes admitted.",
                               "manifest_self_hash": "Excluded; outer .zip.sha256 covers the manifest."},
                    "files": []}
        observed = {}
        checksum_published = False
        zip_published = False
        try:
            with _new_file(output_fd, temporary) as target:
                with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name in files:
                        with source_file(root_fd, name) as stream:
                            before = os.fstat(stream.fileno())
                            sha256 = hashlib.sha256()
                            object_hash = hashlib.new("sha256" if len(git.get("head") or "") == 64 else "sha1")
                            object_hash.update(f"blob {before.st_size}\0".encode())
                            executable = bool(before.st_mode & 0o111)
                            size = 0
                            captured_version = bytearray() if name == "VERSION" else None
                            with archive.open(_zip_info(name, executable), "w", force_zip64=True) as member:
                                while True:
                                    block = stream.read(1024 * 1024)
                                    if not block:
                                        break
                                    member.write(block)
                                    sha256.update(block)
                                    object_hash.update(block)
                                    size += len(block)
                                    if captured_version is not None:
                                        captured_version.extend(block)
                            after = os.fstat(stream.fileno())
                            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size:
                                raise PackageError(f"Source changed while packaging: {name}")
                            if captured_version is not None and captured_version != version_bytes:
                                raise PackageError("VERSION changed while packaging")
                            observed[name] = ("100755" if executable else "100644", object_hash.hexdigest())
                            manifest["files"].append({"path": name, "size": size, "sha256": sha256.hexdigest()})
                    if git["available"]:
                        git["dirty"] = observed != head_files
                    archive.writestr(_zip_info(MANIFEST_NAME),
                                     json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                target.flush()
                os.fsync(target.fileno())
            digest = hashlib.sha256()
            with os.fdopen(os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=output_fd), "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            with _new_file(output_fd, temporary_sum) as stream:
                stream.write(f"{digest.hexdigest()}  {output.name}\n".encode())
                stream.flush()
                os.fsync(stream.fileno())
            # Hard links provide atomic, exclusive publication, even for non-cooperating writers.
            os.link(temporary_sum, checksum_name, src_dir_fd=output_fd, dst_dir_fd=output_fd,
                    follow_symlinks=False)
            checksum_published = True
            os.fsync(output_fd)
            os.link(temporary, output.name, src_dir_fd=output_fd, dst_dir_fd=output_fd,
                    follow_symlinks=False)
            zip_published = True
            os.fsync(output_fd)
        finally:
            if checksum_published and not zip_published:
                # Only remove the checksum this invocation created, never another writer's file.
                try:
                    published = os.stat(checksum_name, dir_fd=output_fd, follow_symlinks=False)
                    staged = os.stat(temporary_sum, dir_fd=output_fd, follow_symlinks=False)
                    if (published.st_dev, published.st_ino) == (staged.st_dev, staged.st_ino):
                        os.unlink(checksum_name, dir_fd=output_fd)
                except FileNotFoundError:
                    pass
            for name in (temporary, temporary_sum):
                try:
                    os.unlink(name, dir_fd=output_fd)
                except FileNotFoundError:
                    pass
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="project root (default: directory above scripts/)")
    parser.add_argument("--output", type=Path, required=True,
                        help="new .zip path, relative to --root unless absolute")
    args = parser.parse_args(argv)
    try:
        output = create_package(args.root, args.output)
    except (PackageError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Package rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Created {output}\nChecksum {output}.sha256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
