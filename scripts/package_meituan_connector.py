"""Build a credential-free private deployment bundle for the official Paotui client.

The upstream repository does not currently publish a license file. This helper
keeps provider artifacts out of the TROPOS source/release archive and creates a
separate operator-managed bundle for a private server deployment.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import zipfile


SOURCE_URL = "https://github.com/meituan/MT-Paotui-For-Client"
SKILL_FILES = ("paotui.js", "config.json")
PASSPORT_PREFIXES = (
    "package/package.json",
    "package/dist/index.js",
    "package/node_modules/@sec/cliguard/",
)
FORBIDDEN_PARTS = {"accounts", "tmp", ".config", ".cliguard", "mail-outbox"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def zip_info(name):
    info = zipfile.ZipInfo(name, date_time=(2026, 10, 31, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def safe_name(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts and
            not any(part in FORBIDDEN_PARTS for part in path.parts))


def collect(skill_dir):
    payload = {}
    for name in SKILL_FILES:
        source = skill_dir / name
        if not source.is_file() or source.is_symlink():
            raise SystemExit("missing or unsafe official Skill file: " + str(source))
        payload["skill/" + name] = source.read_bytes()

    bundles = sorted((skill_dir / "passport-auth" / "scripts").glob("mtuser-pt-passport-*.tgz"))
    if len(bundles) != 1:
        raise SystemExit("expected exactly one bundled pt-passport archive")
    with tarfile.open(bundles[0], "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.issym() or member.islnk():
                continue
            allowed = (member.name in PASSPORT_PREFIXES[:2] or
                       member.name.startswith(PASSPORT_PREFIXES[2]))
            if not allowed or not safe_name(member.name):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit("cannot read passport payload: " + member.name)
            payload["runtime/" + member.name] = stream.read()
    required = {
        "runtime/package/package.json",
        "runtime/package/dist/index.js",
        "runtime/package/node_modules/@sec/cliguard/index.js",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise SystemExit("incomplete pt-passport bundle: " + ", ".join(missing))
    return payload, bundles[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--upstream-commit", required=True)
    args = parser.parse_args()
    payload, passport = collect(args.skill_dir.resolve())
    manifest = {
        "schema": 1,
        "source": SOURCE_URL,
        "upstreamCommit": args.upstream_commit,
        "passportBundle": passport.name,
        "nodeRequirement": ">=18",
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "credentialDataIncluded": False,
        "distributionNotice": "Private deployment bundle; review upstream terms before redistribution.",
        "files": {name: {"sha256": digest(data), "bytes": len(data)}
                  for name, data in sorted(payload.items())},
    }
    payload["connector-manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w") as archive:
            for name, data in sorted(payload.items()):
                if not safe_name(name):
                    raise SystemExit("unsafe output path: " + name)
                archive.writestr(zip_info(name), data)
        temporary_path.replace(args.output)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    checksum = digest(args.output.read_bytes())
    # Write bytes so Windows cannot translate LF to CRLF. Linux sha256sum treats
    # a trailing CR as part of the referenced filename.
    args.output.with_suffix(args.output.suffix + ".sha256").write_bytes(
        (checksum + "  " + args.output.name + "\n").encode("ascii"))
    print("Created", args.output)
    print("SHA256", checksum)


if __name__ == "__main__":
    main()
