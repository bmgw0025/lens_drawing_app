"""Build the Inno Setup installer with resilient ISCC discovery."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - this project targets Windows
    winreg = None


PROJECT_ROOT = Path(__file__).resolve().parent
INSTALLER_SCRIPT = PROJECT_ROOT / "LensDrawing_Installer.iss"
OUTPUT_DIR = PROJECT_ROOT / "installer_output"
ISCC_ENV_VAR = "INNO_SETUP_ISCC"


def _registry_candidates():
    if winreg is None:
        return

    uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)
    for root in roots:
        for view in views:
            try:
                with winreg.OpenKey(root, uninstall_key, 0, winreg.KEY_READ | view) as parent:
                    subkey_count = winreg.QueryInfoKey(parent)[0]
                    for index in range(subkey_count):
                        name = winreg.EnumKey(parent, index)
                        try:
                            with winreg.OpenKey(parent, name) as item:
                                display_name = winreg.QueryValueEx(item, "DisplayName")[0]
                                if not str(display_name).startswith("Inno Setup"):
                                    continue
                                install_dir = winreg.QueryValueEx(item, "InstallLocation")[0]
                                if install_dir:
                                    yield Path(install_dir) / "ISCC.exe"
                        except OSError:
                            continue
            except OSError:
                continue


def _iscc_candidates(explicit_path=None):
    if explicit_path:
        yield Path(explicit_path)

    env_path = os.environ.get(ISCC_ENV_VAR)
    if env_path:
        yield Path(env_path)

    path_match = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if path_match:
        yield Path(path_match)

    yield from _registry_candidates()

    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    for base in (local_app_data and Path(local_app_data) / "Programs", program_files, program_files_x86):
        if not base:
            continue
        base = Path(base)
        yield base / "Inno Setup 6" / "ISCC.exe"
        yield base / "Inno Setup 5" / "ISCC.exe"


def find_iscc(explicit_path=None):
    checked = []
    seen = set()
    for candidate in _iscc_candidates(explicit_path):
        candidate = candidate.expanduser()
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        checked.append(str(candidate))
        if candidate.is_file():
            return candidate.resolve()

    locations = "\n  - ".join(checked) if checked else "(no candidates found)"
    raise FileNotFoundError(
        "ISCC.exe was not found. Install Inno Setup 6 or set "
        f"{ISCC_ENV_VAR}. Checked:\n  - {locations}"
    )


def read_installer_version():
    content = INSTALLER_SCRIPT.read_text(encoding="utf-8-sig")
    match = re.search(r'^#define\s+MyAppVersion\s+"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise RuntimeError("MyAppVersion is missing from LensDrawing_Installer.iss")
    return match.group(1)


def validate_inputs(version):
    required = (
        PROJECT_ROOT / "dist" / "LensDrawing" / "LensDrawing.exe",
        PROJECT_ROOT / "dist" / "LensDrawing" / "_internal",
        PROJECT_ROOT / f"V{version}_版本更新说明.md",
        PROJECT_ROOT / "installer_deps" / "vc_redist.x64.exe",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Installer inputs are missing:\n  - " + "\n  - ".join(missing))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", help="Explicit path to ISCC.exe")
    args = parser.parse_args()

    if not INSTALLER_SCRIPT.is_file():
        raise FileNotFoundError(f"Installer script not found: {INSTALLER_SCRIPT}")

    version = read_installer_version()
    validate_inputs(version)
    iscc = find_iscc(args.iscc)
    expected_output = OUTPUT_DIR / f"LensDrawing_{version}_Setup.exe"

    print(f"[Inno Setup] {iscc}")
    print(f"[Version] {version}")
    result = subprocess.run(
        [str(iscc), str(INSTALLER_SCRIPT)],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    if not expected_output.is_file():
        raise FileNotFoundError(f"Compiler succeeded but output is missing: {expected_output}")

    size_mb = expected_output.stat().st_size / (1024 * 1024)
    print(f"[Done] {expected_output}")
    print(f"[Size] {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        sys.exit(1)
