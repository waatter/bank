import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import package_meituan_connector as package


class MeituanConnectorPackageTests(unittest.TestCase):
    def fixture(self, root):
        skill = root / "official"
        scripts = skill / "passport-auth" / "scripts"
        scripts.mkdir(parents=True)
        (skill / "paotui.js").write_text("// official fixture", encoding="utf-8")
        (skill / "config.json").write_text('{"orderMode":"4"}', encoding="utf-8")
        archive_path = scripts / "mtuser-pt-passport-1.0.5.tgz"
        files = {
            "package/package.json": b'{"version":"1.0.5"}',
            "package/dist/index.js": b"// passport",
            "package/node_modules/@sec/cliguard/index.js": b"// guard",
            "package/node_modules/@sec/cliguard/package.json": b"{}",
            "package/accounts/secret.json": b"DO_NOT_PACKAGE",
        }
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        return skill

    def test_collect_is_allowlisted_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            payload, bundle = package.collect(self.fixture(Path(directory)))

        self.assertEqual(bundle.name, "mtuser-pt-passport-1.0.5.tgz")
        self.assertIn("skill/paotui.js", payload)
        self.assertIn("runtime/package/dist/index.js", payload)
        self.assertNotIn("runtime/package/accounts/secret.json", payload)
        self.assertNotIn(b"DO_NOT_PACKAGE", payload.values())

    def test_safe_name_rejects_parent_and_private_state(self):
        self.assertFalse(package.safe_name("../secret"))
        self.assertFalse(package.safe_name("runtime/state/accounts/token"))
        self.assertFalse(package.safe_name("runtime/package/.cliguard/session"))
        self.assertTrue(package.safe_name("runtime/package/dist/index.js"))

    def test_manifest_is_json_serializable_without_credentials(self):
        manifest = {
            "credentialDataIncluded": False,
            "files": {"skill/paotui.js": {"sha256": package.digest(b"x")}},
        }
        encoded = json.dumps(manifest)
        self.assertIn('"credentialDataIncluded": false', encoded)

    def test_checksum_uses_linux_compatible_lf_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "connector.zip"
            with patch("sys.argv", [
                    "package_meituan_connector.py", "--skill-dir", str(self.fixture(root)),
                    "--output", str(output), "--upstream-commit", "a" * 40]):
                package.main()

            checksum = output.with_suffix(".zip.sha256").read_bytes()
            self.assertTrue(checksum.endswith(b"\n"))
            self.assertNotIn(b"\r", checksum)


if __name__ == "__main__":
    unittest.main()
