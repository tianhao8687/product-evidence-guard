#!/usr/bin/env python3
"""Generate deterministic pypdfium2/PDFium build-license evidence offline.

The installed wheel is the only source.  The script never queries a package
index or another network service.  It copies the wheel's declared license
files byte-for-byte and records both their SHA-256 digests and the identity of
the bundled PDFium binary.  Host paths and generation timestamps are omitted
so the result is safe to publish and reproducible on the pinned environment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "evidence" / "pypdfium2-notices.json"
DEFAULT_BUNDLE = REPO_ROOT / "docs" / "evidence" / "licenses" / "pypdfium2"
GENERATOR_ID = "scripts/generate-pdfium-notices.py"
DISTRIBUTION_NAME = "pypdfium2"
BINARY_PATH = PurePosixPath("pypdfium2_raw/pdfium.dll")
VERSION_PATH = PurePosixPath("pypdfium2_raw/version.json")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _posix(value: object) -> str:
    return str(value).replace("\\", "/")


def _record_sha256(file: metadata.PackagePath) -> str:
    record_hash = file.hash
    if record_hash is None or record_hash.mode != "sha256":
        raise RuntimeError(f"missing SHA-256 RECORD entry: {_posix(file)}")
    padding = "=" * (-len(record_hash.value) % 4)
    return base64.urlsafe_b64decode(record_hash.value + padding).hex()


def _file_bytes(file: metadata.PackagePath) -> bytes:
    path = Path(file.locate())
    if not path.is_file():
        raise RuntimeError(f"installed distribution file is missing: {_posix(file)}")
    return path.read_bytes()


def _find_file(
    files: Iterable[metadata.PackagePath], relative_path: PurePosixPath
) -> metadata.PackagePath:
    expected = relative_path.as_posix()
    for file in files:
        if _posix(file) == expected:
            return file
    raise RuntimeError(f"installed distribution does not contain {expected}")


def _license_bundle_path(file: metadata.PackagePath) -> PurePosixPath | None:
    parts = PurePosixPath(_posix(file)).parts
    try:
        index = tuple(part.lower() for part in parts).index("licenses")
    except ValueError:
        return None
    relative = PurePosixPath(*parts[index + 1 :])
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"unsafe license path in installed wheel: {_posix(file)}")
    return relative


def _wheel_tags(distribution: metadata.Distribution) -> list[str]:
    wheel = distribution.read_text("WHEEL")
    if not wheel:
        raise RuntimeError("installed pypdfium2 distribution has no WHEEL metadata")
    tags = sorted(
        line.split(":", 1)[1].strip()
        for line in wheel.splitlines()
        if line.lower().startswith("tag:")
    )
    if not tags:
        raise RuntimeError("installed pypdfium2 WHEEL metadata has no Tag")
    return tags


def generate() -> tuple[bytes, dict[PurePosixPath, bytes]]:
    distribution = metadata.distribution(DISTRIBUTION_NAME)
    files = tuple(distribution.files or ())
    if not files:
        raise RuntimeError("installed pypdfium2 distribution has no RECORD file list")

    declared = sorted(distribution.metadata.get_all("License-File") or [])
    license_files: list[tuple[PurePosixPath, metadata.PackagePath, bytes]] = []
    for file in files:
        bundle_path = _license_bundle_path(file)
        if bundle_path is not None:
            license_files.append((bundle_path, file, _file_bytes(file)))
    license_files.sort(key=lambda item: item[0].as_posix())
    if not license_files:
        raise RuntimeError("installed pypdfium2 wheel contains no license files")

    discovered = [path.as_posix() for path, _file, _content in license_files]
    if discovered != declared:
        raise RuntimeError(
            "installed license files do not exactly match License-File metadata"
        )

    rows: list[dict[str, object]] = []
    bundle: dict[PurePosixPath, bytes] = {}
    for bundle_path, file, content in license_files:
        digest = sha256_bytes(content)
        record_digest = _record_sha256(file)
        if digest != record_digest:
            raise RuntimeError(f"RECORD hash mismatch: {bundle_path.as_posix()}")
        category = (
            "pdfium_build_license"
            if "/BUILD_LICENSES/" in f"/{bundle_path.as_posix()}"
            else "pypdfium2_distribution_license"
        )
        rows.append(
            {
                "bundle_path": bundle_path.as_posix(),
                "category": category,
                "bytes": len(content),
                "sha256": digest,
                "record_sha256_verified": True,
            }
        )
        bundle[bundle_path] = content

    binary_file = _find_file(files, BINARY_PATH)
    binary = _file_bytes(binary_file)
    binary_digest = sha256_bytes(binary)
    binary_record_digest = _record_sha256(binary_file)
    if binary_digest != binary_record_digest:
        raise RuntimeError("RECORD hash mismatch: pypdfium2_raw/pdfium.dll")

    version_file = _find_file(files, VERSION_PATH)
    try:
        pdfium_version = json.loads(_file_bytes(version_file).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid installed pypdfium2_raw/version.json") from exc

    build_count = sum(row["category"] == "pdfium_build_license" for row in rows)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "generator": GENERATOR_ID,
        "source": {
            "distribution": DISTRIBUTION_NAME,
            "distribution_version": distribution.version,
            "wheel_tags": _wheel_tags(distribution),
            "pdfium_binary": {
                "package_path": BINARY_PATH.as_posix(),
                "bytes": len(binary),
                "sha256": binary_digest,
                "record_sha256_verified": True,
            },
            "pdfium_version": pdfium_version,
        },
        "summary": {
            "license_file_count": len(rows),
            "pdfium_build_license_file_count": build_count,
            "distribution_license_file_count": len(rows) - build_count,
            "total_bytes": sum(int(row["bytes"]) for row in rows),
            "record_sha256_verified_count": len(rows),
        },
        "files": rows,
    }
    return json_bytes(manifest), bundle


def _bundle_snapshot(root: Path) -> dict[PurePosixPath, bytes]:
    if not root.is_dir():
        return {}
    return {
        PurePosixPath(path.relative_to(root).as_posix()): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_bundle(root: Path, bundle: dict[PurePosixPath, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    existing = _bundle_snapshot(root)
    extras = sorted(set(existing) - set(bundle), key=lambda path: path.as_posix())
    if extras:
        names = ", ".join(path.as_posix() for path in extras)
        raise RuntimeError(f"refusing to overwrite bundle with extra files: {names}")
    for relative, content in bundle.items():
        destination = root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify tracked evidence instead of writing it",
    )
    args = parser.parse_args(argv)

    try:
        manifest, bundle = generate()
        if args.check:
            stale: list[str] = []
            if not args.output.is_file() or args.output.read_bytes() != manifest:
                stale.append(args.output.name)
            if _bundle_snapshot(args.bundle_dir) != bundle:
                stale.append(args.bundle_dir.name)
            if stale:
                print("stale PDFium notice evidence: " + ", ".join(stale), file=sys.stderr)
                return 1
            print("pypdfium2/PDFium notice evidence is current")
            return 0

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(manifest)
        _write_bundle(args.bundle_dir, bundle)
        print(f"wrote {args.output.name} and {len(bundle)} bundled notice files")
        return 0
    except (metadata.PackageNotFoundError, OSError, RuntimeError) as exc:
        print(f"unable to generate PDFium notice evidence: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
