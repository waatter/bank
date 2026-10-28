"""Public candidate packages: isolated synthetic trees only, never the real release."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from scripts import package_v3 as package


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="package-v3-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "dist" / "candidate.zip"
        self.put("VERSION", "3.0.0\n")

    def put(self, name, body="public fixture\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def build(self):
        return package.create_package(self.root, "dist/candidate.zip")

    def manifest(self):
        with zipfile.ZipFile(self.output) as archive:
            return json.loads(archive.read(package.MANIFEST_NAME))

    def assert_no_staging(self):
        self.assertEqual(list(self.output.parent.glob(".package-v3-*")), [])

    def test_allowlist_root_layout_and_public_deployment(self):
        allowed = {
            "VERSION", "README.md", "Dockerfile", ".env.example", "requirements.txt", "index.html", "demo.html", "study.html",
            "backend/app.py", "backend/fixtures.json", "assets/ui.js", "assets/fonts/font.woff2",
            "eval/cases.json", "tests/test_demo.py", "docs/guide.md", "scripts/run.sh",
            "reports/evaluation.json", "deploy/README.md", "deploy/prepare.sh", "deploy/Caddyfile",
            "deploy/docker-compose.yml", "deploy/build-context.dockerignore", "deploy/.dockerignore",
            "deploy/.env.example", "deploy/env.production.example",
        }
        for name in allowed - {"VERSION"}:
            self.put(name)
        for name in ["random.txt", "package.json", "unapproved/app.py", "deploy/mystery.yml",
                     "deploy/build-context/backend/copied.py", "deploy/other/config.yml", "assets/archive.zip"]:
            self.put(name)
        executable = self.root / "deploy/prepare.sh"
        executable.chmod(0o755)
        self.assertEqual(self.build(), self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), allowed | {package.MANIFEST_NAME})
            self.assertTrue(archive.getinfo("deploy/prepare.sh").external_attr >> 16 & 0o111)
        manifest = self.manifest()
        self.assertEqual(manifest["source_version"], "3.0.0")
        self.assertIn("docs/3.0_手机与部署验收.md", manifest["deployment"]["instructions"])
        self.assertEqual(manifest["deployment"]["build_context"], "deploy/build-context (generated after extraction)")
        self.assertIsNone(manifest["git"]["dirty"])

    def test_secrets_exports_and_excluded_directories_are_never_read(self):
        forbidden = [
            ".env", ".env.production", "backend/.env", "assets/.env.dev", "backend/config.env",
            "backend/.env.example.backup", "backend/a.sqlite", "backend/a.sqlite3-wal",
            "backend/a.db", "backend/a.db-shm", "backend/a.key", "backend/a.pem", "backend/a.pem.bak",
            "backend/api_key.txt", "backend/credentials.json", "backend/secret.txt", "backend/id_rsa",
            "backend/__pycache__/cached.py", "backend/.local/archived.py", "backend/.tmp/run.py",
            "backend/dist/old.py", "backend/releases/old.py", "backend/raw/input.json",
            "reports/user_export.json", "reports/user-data.json", "reports/export.json",
            "reports/用户原始导出.json", "reports/用户数据.json", "reports/transactions.csv",
            "reports/data.tsv", "reports/data.xlsx", "reports/data.sql", "reports/original_data.json",
            "deploy/data/customer.json", "deploy/.env.production", "deploy/server.key",
            ".local/archived/test.py", ".tmp/test.py", "dist/old.py", "releases/old.py",
        ]
        for name in forbidden:
            self.put(name, "DO_NOT_READ_SECRET")
        self.put(".env.example", "PUBLIC_PLACEHOLDER=\n")
        self.put("backend/app.py")
        real_open = package.source_file
        opened = []

        def guarded_open(root_fd, name):
            opened.append(name)
            self.assertNotIn(name, forbidden)
            return real_open(root_fd, name)

        real_scandir = os.scandir

        def guarded_scandir(fd):
            # Compare directory identity without opening the protected trees.
            info = os.fstat(fd)
            for name in [".local", ".tmp", "backend/.local", "backend/raw", "deploy/data"]:
                protected = (self.root / name).stat()
                self.assertNotEqual((info.st_dev, info.st_ino), (protected.st_dev, protected.st_ino))
            return real_scandir(fd)

        with patch.object(package, "source_file", side_effect=guarded_open), \
                patch.object(package.os, "scandir", side_effect=guarded_scandir):
            self.build()
        self.assertEqual(set(opened), {"VERSION", ".env.example", "backend/app.py"})
        with zipfile.ZipFile(self.output) as archive:
            for name in archive.namelist():
                self.assertNotIn(b"DO_NOT_READ_SECRET", archive.read(name))

    def test_manifest_and_external_checksum_match_every_byte(self):
        self.put("backend/app.py", "print('never executed')\n")
        self.build()
        with zipfile.ZipFile(self.output) as archive:
            entries = self.manifest()["files"]
            self.assertEqual({entry["path"] for entry in entries}, set(archive.namelist()) - {package.MANIFEST_NAME})
            for entry in entries:
                data = archive.read(entry["path"])
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(entry["size"], len(data))
        expected = f"{hashlib.sha256(self.output.read_bytes()).hexdigest()}  candidate.zip\n"
        self.assertEqual(Path(str(self.output) + ".sha256").read_text(), expected)
        self.assert_no_staging()

    def test_source_is_not_executed(self):
        marker = self.root / "executed.txt"
        self.put("backend/__init__.py", f"open({str(marker)!r}, 'w').write('executed')\n")
        self.put("scripts/build.sh", "exit 99\n")
        self.build()
        self.assertFalse(marker.exists())

    def test_symlink_files_directories_and_dangling_links_are_rejected(self):
        outside = self.put("outside.txt", "secret")
        for name, target, is_dir in [("assets/link.js", outside, False),
                                     ("assets/link", self.root / "outside", True),
                                     ("backend/missing.py", self.root / "missing", False),
                                     ("VERSION", outside, False),
                                     ("deploy", self.root / "outside", True)]:
            with self.subTest(name=name):
                link = self.root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                if name == "VERSION":
                    link.unlink()
                link.symlink_to(target, target_is_directory=is_dir)
                try:
                    with self.assertRaisesRegex(package.PackageError, "Symlink rejected"):
                        self.build()
                    self.assertFalse(self.output.exists())
                finally:
                    link.unlink()
                    if name == "VERSION":
                        self.put("VERSION", "3.0.0\n")

    def test_symlink_root_and_output_parent_are_rejected(self):
        link = self.root / "linked-root"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            package.create_package(link, "dist/candidate.zip")
        target = self.root / "elsewhere"
        target.mkdir()
        (self.root / "dist").symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            self.build()
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_zip_or_checksum_is_never_overwritten(self):
        for name in ["dist/candidate.zip", "dist/candidate.zip.sha256"]:
            with self.subTest(name=name):
                path = self.put(name, "keep me")
                try:
                    with patch.object(package, "collect_files", side_effect=AssertionError("must reject before reads")):
                        with self.assertRaisesRegex(package.PackageError, "overwrite"):
                            self.build()
                    self.assertEqual(path.read_text(), "keep me")
                    self.assert_no_staging()
                finally:
                    path.unlink()

    def test_dangling_output_symlink_is_not_overwritten(self):
        self.output.parent.mkdir()
        self.output.symlink_to(self.root / "nonexistent")
        with self.assertRaisesRegex(package.PackageError, "overwrite"):
            self.build()
        self.assertTrue(self.output.is_symlink())

    def test_only_major_three_versions_and_no_version_rewrite(self):
        for version in ["2.9.9", "4.0.0", "30.0.0", "3", "3.x", "3.0.0\n4.0.0", "", "3.0/evil"]:
            with self.subTest(version=version):
                self.put("VERSION", version)
                with self.assertRaisesRegex(package.PackageError, "3.\\*"):
                    self.build()
                self.assertFalse(self.output.exists())
                self.assert_no_staging()
        for version in ["3.0", "3.0.0", "3.1.2-rc.1"]:
            with self.subTest(version=version):
                self.put("VERSION", version + "\n")
                package.create_package(self.root, f"dist/{version}.zip")
                self.assertEqual((self.root / "VERSION").read_text(), version + "\n")

    def test_missing_version_fails_before_publication(self):
        (self.root / "VERSION").unlink()
        with self.assertRaisesRegex(package.PackageError, "VERSION"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_version_changed_after_validation_aborts_publication(self):
        metadata = {"available": False, "branch": None, "head": None, "dirty": None}

        def change_version(root):
            self.put("VERSION", "4.0.0\n")
            return metadata, {}

        with patch.object(package, "git_metadata", side_effect=change_version):
            with self.assertRaisesRegex(package.PackageError, "VERSION changed"):
                self.build()
        self.assertFalse(self.output.exists())
        self.assert_no_staging()

    def test_source_replaced_by_symlink_after_inventory_is_not_followed(self):
        source = self.put("backend/app.py")
        secret = self.put("outside.txt", "secret must not be read")
        collect = package.collect_files

        def replace_after_collect(fd):
            result = collect(fd)
            source.unlink()
            source.symlink_to(secret)
            return result

        with patch.object(package, "collect_files", side_effect=replace_after_collect):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse(self.output.exists())
        self.assert_no_staging()

    def test_output_collision_at_publish_preserves_other_writer_and_rolls_back_checksum(self):
        link = package.os.link

        def publish(source, destination, **kwargs):
            if destination == self.output.name:
                self.output.write_bytes(b"other writer")
            return link(source, destination, **kwargs)

        with patch.object(package.os, "link", side_effect=publish):
            with self.assertRaises(FileExistsError):
                self.build()
        self.assertEqual(self.output.read_bytes(), b"other writer")
        self.assertFalse(Path(str(self.output) + ".sha256").exists())
        self.assert_no_staging()

    def test_incomplete_zip_is_never_published(self):
        with patch.object(package.zipfile.ZipFile, "writestr", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".sha256").exists())
        self.assert_no_staging()

    def test_concurrent_publish_has_exactly_one_winner(self):
        barrier = threading.Barrier(2)
        collect = package.collect_files

        def simultaneous_collect(fd):
            result = collect(fd)
            barrier.wait(timeout=10)
            return result

        def attempt():
            try:
                self.build()
                return True
            except FileExistsError:
                return False

        with patch.object(package, "collect_files", side_effect=simultaneous_collect):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.manifest()["source_version"], "3.0.0")
        digest = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.assertTrue(Path(str(self.output) + ".sha256").read_text().startswith(digest))
        self.assert_no_staging()

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_git_branch_head_scoped_dirty_without_diff_or_secret_reads(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.root), *args], env=environment,
                                           stderr=subprocess.DEVNULL).decode().strip()

        git("init", "-b", "codex/v3.0.0")
        self.put("backend/app.py", "# public\n")
        git("add", "VERSION", "backend/app.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
        head = git("rev-parse", "HEAD")
        self.build()
        metadata = self.manifest()["git"]
        self.assertEqual(metadata["head"], head)
        self.assertEqual(metadata["branch"], "codex/v3.0.0")
        self.assertFalse(metadata["dirty"])
        self.put("backend/app.py", "# changed\n")
        self.put(".env", "secret must stay unread")
        package.create_package(self.root, "dist/changed.zip")
        with zipfile.ZipFile(self.root / "dist/changed.zip") as archive:
            manifest_text = archive.read(package.MANIFEST_NAME).decode()
        changed = json.loads(manifest_text)["git"]
        self.assertTrue(changed["dirty"])
        self.assertIn("excluded paths are not read", changed["dirty_scope"])
        self.assertNotIn("# changed", manifest_text)
        (self.root / "backend/app.py").unlink()
        package.create_package(self.root, "dist/deleted.zip")
        with zipfile.ZipFile(self.root / "dist/deleted.zip") as archive:
            self.assertTrue(json.loads(archive.read(package.MANIFEST_NAME))["git"]["dirty"])

    def test_cli_custom_root_and_failure_exit_status(self):
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(package.main(["--root", str(self.root), "--output", "dist/candidate.zip"]), 0)
        self.assertIn(str(self.output), stdout.getvalue())
        with redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(package.main(["--root", str(self.root), "--output", "dist/candidate.zip"]), 1)
        self.assertIn("overwrite", stderr.getvalue())
        self.assertEqual(package.DEFAULT_ROOT, Path(__file__).absolute().parent.parent)


if __name__ == "__main__":
    unittest.main()
