#!/usr/bin/env python3
"""Generate deterministic, offline dependency and license evidence.

The lock file is the authority for package names, versions and artifact hashes.
License assertions are read only from distributions installed in the Python
environment running this script.  No package index or other network service is
queried.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO_ROOT / "requirements.lock"
DEFAULT_SBOM = REPO_ROOT / "docs" / "evidence" / "sbom.json"
DEFAULT_LICENSES = REPO_ROOT / "docs" / "evidence" / "license-inventory.json"
GENERATOR_ID = "scripts/generate-sbom.py"

PACKAGE_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)(?:\s*\\)?\s*$"
)
SHA256 = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")

CLASSIFIER_TO_SPDX = {
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "ISC License (ISCL)": "ISC",
    "MIT License": "MIT",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
}


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    sha256: tuple[str, ...]


@dataclass(frozen=True)
class InstalledPackage:
    name: str
    version: str
    license_value: str
    license_source: str


def canonical_name(value: str) -> str:
    """Return the normalized Python distribution name from PEP 503."""

    return re.sub(r"[-_.]+", "-", value).lower()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_lock(content: str) -> list[LockedPackage]:
    """Parse a hash-locked pip-compile requirements file."""

    parsed: list[LockedPackage] = []
    current_name: str | None = None
    current_version: str | None = None
    current_hashes: list[str] = []

    def finish() -> None:
        nonlocal current_name, current_version, current_hashes
        if current_name is None or current_version is None:
            return
        if not current_hashes:
            raise ValueError(f"locked package has no SHA-256 hashes: {current_name}")
        parsed.append(
            LockedPackage(
                name=current_name,
                version=current_version,
                sha256=tuple(sorted(set(current_hashes))),
            )
        )
        current_name = None
        current_version = None
        current_hashes = []

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        package_match = PACKAGE_LINE.fullmatch(stripped)
        if package_match:
            finish()
            current_name, current_version = package_match.groups()
            continue
        hashes = SHA256.findall(stripped)
        if hashes:
            if current_name is None:
                raise ValueError(f"orphan SHA-256 hash on lock line {line_number}")
            current_hashes.extend(value.lower() for value in hashes)

    finish()
    if not parsed:
        raise ValueError("requirements lock contains no pinned packages")

    normalized = [canonical_name(package.name) for package in parsed]
    if len(normalized) != len(set(normalized)):
        raise ValueError("requirements lock contains duplicate normalized package names")
    return sorted(parsed, key=lambda item: canonical_name(item.name))


def _clean_metadata_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned or cleaned.upper() in {"UNKNOWN", "N/A", "NONE"}:
        return None
    return cleaned


def resolve_license(fields: Mapping[str, object]) -> tuple[str, str]:
    """Resolve the best local license assertion without guessing from the web."""

    expression = _clean_metadata_value(_first(fields, "License-Expression"))
    if expression:
        return expression, "License-Expression"

    declared = _clean_metadata_value(_first(fields, "License"))
    # Some legacy wheels put the full license text in this field.  That text is
    # not a machine-readable assertion and would make the inventory needlessly
    # large, so fall through to classifiers in that case.
    if declared and len(declared) <= 200:
        return declared, "License"

    classifiers = _all(fields, "Classifier")
    license_classifiers = sorted(
        value.rsplit(" :: ", 1)[-1]
        for value in classifiers
        if value.startswith("License :: OSI Approved :: ")
    )
    mapped = sorted(
        {CLASSIFIER_TO_SPDX[value] for value in license_classifiers if value in CLASSIFIER_TO_SPDX}
    )
    if mapped:
        return " OR ".join(mapped), "Classifier"
    if license_classifiers:
        return " OR ".join(license_classifiers), "Classifier"
    return "NOASSERTION", "unresolved"


def _first(fields: Mapping[str, object], key: str) -> str | None:
    if hasattr(fields, "get"):
        value = fields.get(key)  # type: ignore[call-overload]
        return value if isinstance(value, str) else None
    return None


def _all(fields: Mapping[str, object], key: str) -> list[str]:
    get_all = getattr(fields, "get_all", None)
    if callable(get_all):
        return [str(value) for value in (get_all(key) or [])]
    value = fields.get(key)
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def installed_packages(
    distributions: Iterable[metadata.Distribution] | None = None,
) -> dict[str, InstalledPackage]:
    result: dict[str, InstalledPackage] = {}
    source = distributions if distributions is not None else metadata.distributions()
    for distribution in source:
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized = canonical_name(name)
        license_value, license_source = resolve_license(distribution.metadata)
        candidate = InstalledPackage(
            name=name,
            version=distribution.version,
            license_value=license_value,
            license_source=license_source,
        )
        existing = result.get(normalized)
        if existing is not None and existing != candidate:
            raise ValueError(f"multiple installed distributions normalize to {normalized}")
        result[normalized] = candidate
    return result


def _purl(package: LockedPackage) -> str:
    return f"pkg:pypi/{canonical_name(package.name)}@{package.version}"


def _cyclonedx_license(package: InstalledPackage) -> list[dict[str, object]]:
    if package.license_source == "License-Expression":
        return [{"expression": package.license_value}]
    return [{"license": {"name": package.license_value}}]


def build_documents(
    lock_bytes: bytes,
    locked: list[LockedPackage],
    installed: Mapping[str, InstalledPackage],
) -> tuple[dict[str, object], dict[str, object]]:
    missing: list[str] = []
    mismatched: list[str] = []
    packages: list[tuple[LockedPackage, InstalledPackage]] = []
    for package in locked:
        found = installed.get(canonical_name(package.name))
        if found is None:
            missing.append(package.name)
            continue
        if found.version != package.version:
            mismatched.append(
                f"{package.name}: locked {package.version}, installed {found.version}"
            )
            continue
        packages.append((package, found))

    if missing or mismatched:
        details = []
        if missing:
            details.append("missing=" + ", ".join(sorted(missing)))
        if mismatched:
            details.append("version_mismatch=" + "; ".join(sorted(mismatched)))
        raise ValueError("installed environment does not match requirements.lock: " + " | ".join(details))

    lock_digest = sha256_bytes(lock_bytes)
    root_ref = "pkg:pypi/product-evidence-guard@2.0.0"
    components: list[dict[str, object]] = []
    license_rows: list[dict[str, object]] = []
    for package, found in packages:
        ref = _purl(package)
        properties = [
            {"name": "product-evidence-guard:installed-version-match", "value": "true"},
            {"name": "product-evidence-guard:license-metadata-field", "value": found.license_source},
        ]
        properties.extend(
            {"name": "product-evidence-guard:lock-artifact-sha256", "value": value}
            for value in package.sha256
        )
        components.append(
            {
                "type": "library",
                "bom-ref": ref,
                "name": package.name,
                "version": package.version,
                "purl": ref,
                "licenses": _cyclonedx_license(found),
                "properties": properties,
            }
        )
        license_rows.append(
            {
                "name": package.name,
                "version": package.version,
                "license": found.license_value,
                "license_source": found.license_source,
                "purl": ref,
            }
        )

    unresolved = [
        row["name"] for row in license_rows if row["license"] == "NOASSERTION"
    ]
    sbom: dict[str, object] = {
        "$schema": "https://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "product-evidence-guard-offline-sbom-generator",
                        "version": "1",
                    }
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "product-evidence-guard",
                "version": "2.0.0",
                "purl": root_ref,
                "licenses": [{"license": {"id": "MIT"}}],
            },
            "properties": [
                {"name": "product-evidence-guard:generator", "value": GENERATOR_ID},
                {"name": "product-evidence-guard:lock-file", "value": "requirements.lock"},
                {"name": "product-evidence-guard:lock-sha256", "value": lock_digest},
                {"name": "product-evidence-guard:network-access", "value": "none"},
            ],
        },
        "components": components,
        "dependencies": [
            {"ref": root_ref, "dependsOn": [component["bom-ref"] for component in components]}
        ],
    }
    inventory: dict[str, object] = {
        "schema_version": 1,
        "generator": GENERATOR_ID,
        "inputs": {
            "requirements_lock": "requirements.lock",
            "requirements_lock_sha256": lock_digest,
            "installed_metadata": "importlib.metadata (local environment; offline)",
        },
        "summary": {
            "locked_package_count": len(locked),
            "installed_version_match_count": len(packages),
            "unresolved_license_count": len(unresolved),
            "unresolved_packages": sorted(unresolved, key=canonical_name),
        },
        "packages": license_rows,
    }
    return sbom, inventory


def json_bytes(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def generate(lock_path: Path) -> tuple[bytes, bytes]:
    lock_bytes = lock_path.read_bytes()
    locked = parse_lock(lock_bytes.decode("utf-8"))
    sbom, inventory = build_documents(lock_bytes, locked, installed_packages())
    return json_bytes(sbom), json_bytes(inventory)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _matches(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--license-output", type=Path, default=DEFAULT_LICENSES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the existing evidence is not byte-for-byte reproducible",
    )
    args = parser.parse_args(argv)

    try:
        sbom, licenses = generate(args.lock)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"supply-chain evidence error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        stale = []
        if not _matches(args.output, sbom):
            stale.append(str(args.output.name))
        if not _matches(args.license_output, licenses):
            stale.append(str(args.license_output.name))
        if stale:
            print("supply-chain evidence is missing or stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("supply-chain evidence is reproducible and current")
        return 0

    _write(args.output, sbom)
    _write(args.license_output, licenses)
    print(f"wrote {args.output.name} and {args.license_output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
