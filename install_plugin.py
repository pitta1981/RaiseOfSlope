#!/usr/bin/env python3
"""Install the Raise Of Slopes QGIS plugin.

This script can either:
  - copy the plugin folder into the QGIS plugins directory (development install),
  - or create a ZIP archive appropriate for "Install from ZIP" in QGIS.

The plugin folder name is determined from metadata.txt ("TheRaiseOfSlopes").

Usage:
    python install_plugin.py [--dest DEST] [--zip]

Options:
    --dest DEST   explicit path to QGIS plugins folder. If omitted, the
                  script will try to determine a reasonable default based on
                  the platform and the default QGIS profile.
    --zip         create a zip archive instead of copying the folder.
"""

import argparse
import os
import platform
import shutil
import sys
import zipfile

PLUGIN_NAME = "TheRaiseOfSlopes"
SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))


EXCLUDED_DIR_PREFIXES = {
    ".git",
    ".vscode",
    "__pycache__",
    "qgis_plugin",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    ".git",
    ".gitignore",
    ".gitmodules",
}


def _normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_excluded_dir(rel_dir: str) -> bool:
    rel = _normalize_rel_path(rel_dir).strip("/")
    if not rel:
        return False
    parts = rel.split("/")
    if any(part in EXCLUDED_DIR_PREFIXES for part in parts):
        return True
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXCLUDED_DIR_PREFIXES)


def _is_excluded_file(rel_file: str, filename: str) -> bool:
    if filename in EXCLUDED_FILE_NAMES or filename.endswith((".pyc", ".pyo", ".zip")):
        return True
    rel = _normalize_rel_path(rel_file).strip("/")
    parts = rel.split("/")
    if any(part in EXCLUDED_FILE_NAMES or part in EXCLUDED_DIR_PREFIXES for part in parts):
        return True
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXCLUDED_DIR_PREFIXES)


def default_qgis_plugin_dir() -> str:
    """Return a sensible default plugins directory for the current OS."""
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Darwin":
        return os.path.join(
            home,
            "Library",
            "Application Support",
            "QGIS",
            "QGIS3",
            "profiles",
            "default",
            "python",
            "plugins",
        )
    elif system == "Linux":
        return os.path.join(
            home,
            ".local",
            "share",
            "QGIS",
            "QGIS3",
            "profiles",
            "default",
            "python",
            "plugins",
        )
    elif system == "Windows":
        return os.path.join(
            home,
            "AppData",
            "Roaming",
            "QGIS",
            "QGIS3",
            "profiles",
            "default",
            "python",
            "plugins",
        )
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def copy_plugin(dest_dir: str) -> None:
    src = SOURCE_ROOT
    dest = os.path.join(dest_dir, PLUGIN_NAME)

    print(f"Copying plugin from {src} to {dest}")
    if os.path.exists(dest):
        print("Destination already exists; removing it first...")
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "*.zip",
            ".DS_Store",
            ".gitignore",
            ".gitmodules",
            "qgis_plugin",
        ),
    )
    print("Plugin installed successfully.")
    print("Riavvia QGIS per ricaricare il plugin.")


def zip_plugin(dest_filename: str) -> None:
    src = SOURCE_ROOT
    print(f"Creating zip archive {dest_filename}")

    with zipfile.ZipFile(dest_filename, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src):
            rel_root = os.path.relpath(root, src)
            if rel_root == ".":
                rel_root = ""

            # Avoid descending into excluded directories (e.g. legacy nested plugin).
            kept_dirs = []
            for d in dirs:
                cand = _normalize_rel_path(os.path.join(rel_root, d))
                if not _is_excluded_dir(cand):
                    kept_dirs.append(d)
            dirs[:] = kept_dirs

            if rel_root and _is_excluded_dir(rel_root):
                continue

            for f in files:
                rel_path = _normalize_rel_path(os.path.join(rel_root, f)) if rel_root else f
                if _is_excluded_file(rel_path, f):
                    continue

                abs_path = os.path.join(root, f)
                
                # QGIS expects plugin files to be inside a folder named after the plugin.
                arcname = os.path.join(PLUGIN_NAME, rel_path)
                zf.write(abs_path, arcname)
    print("Zip archive created.")


def main():
    parser = argparse.ArgumentParser(description="Install the Raise Of Slopes QGIS plugin")
    parser.add_argument(
        "--dest",
        help="Explicit QGIS plugins directory. Overrides platform guess.",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create a zip archive instead of copying the folder.",
    )

    args = parser.parse_args()

    if args.zip:
        fname = f"{PLUGIN_NAME}.zip"
        zip_plugin(fname)
        return

    dest_dir = args.dest or default_qgis_plugin_dir()
    if not os.path.isdir(dest_dir):
        print(f"Destination directory does not exist: {dest_dir}")
        print("Please create it or specify --dest explicitly.")
        sys.exit(1)
    copy_plugin(dest_dir)


if __name__ == "__main__":
    main()
