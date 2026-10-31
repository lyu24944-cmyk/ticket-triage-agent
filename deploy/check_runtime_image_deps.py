"""Image-build gate for the Python packages Trivy flags as HIGH.

PyPI's current pip still vendors msgpack 1.1.2 and urllib3 2.7.0, and its
``pip/_vendor/bom.cdx.json`` also lists setuptools 70.3.0. Trivy treats that
third-party SBOM as installed packages (target name ``Python``), even when
site-packages already has a patched urllib3 and does not install msgpack or
setuptools. The runtime image does not need pip, so ``--strip-pip`` removes
that tree after dependencies are installed.

Floors match the fixed versions for CVE-2026-114613, CVE-2025-47273,
CVE-2026-97687, and CVE-2026-97689. Missing msgpack or setuptools is fine;
an older install is not. urllib3 is a direct dependency and must be present.
"""

from __future__ import annotations

import importlib.metadata as md
import shutil
import sys
import sysconfig
from pathlib import Path

FLOORS: dict[str, tuple[tuple[int, int, int], bool]] = {
    "urllib3": ((2, 8, 0), True),
    "msgpack": ((1, 2, 3), False),
    "setuptools": ((78, 1, 1), False),
}


def parse_version(ver: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for piece in ver.split(".")[:3]:
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits or "0"))
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def floor_errors(installed: dict[str, str | None]) -> list[str]:
    errors: list[str] = []
    for name, (floor, required) in FLOORS.items():
        ver = installed.get(name)
        if ver is None:
            if required:
                errors.append(f"{name} is not installed; need >={'.'.join(map(str, floor))}")
            continue
        if parse_version(ver) < floor:
            errors.append(f"{name} {ver} is below {'.'.join(map(str, floor))}")
    return errors


def pip_vendor_bom(purelib: Path) -> Path:
    return purelib / "pip" / "_vendor" / "bom.cdx.json"


def strip_pip(purelib: Path, scripts: Path) -> None:
    """Remove pip and the vendored SBOM Trivy scans. Leave the app's site-packages."""
    targets = [purelib / "pip", *purelib.glob("pip-*.dist-info")]
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    names = {
        "pip",
        "pip3",
        f"pip{sys.version_info.major}",
        f"pip{sys.version_info.major}.{sys.version_info.minor}",
    }
    for name in names:
        path = scripts / name
        if path.is_symlink() or path.exists():
            path.unlink()


def installed_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name in FLOORS:
        try:
            found[name] = md.version(name)
        except md.PackageNotFoundError:
            found[name] = None
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    purelib = Path(sysconfig.get_path("purelib"))
    scripts = Path(sysconfig.get_path("scripts"))
    if "--strip-pip" in args:
        strip_pip(purelib, scripts)
    errors: list[str] = []
    bom = pip_vendor_bom(purelib)
    if bom.is_file():
        errors.append(f"pip vendor SBOM still in image: {bom}")
    if (purelib / "pip").exists():
        errors.append(f"pip package still in image: {purelib / 'pip'}")
    errors.extend(floor_errors(installed_versions()))
    if errors:
        for item in errors:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    present = {name: ver for name, ver in installed_versions().items() if ver}
    print("OK: runtime python deps", present)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
