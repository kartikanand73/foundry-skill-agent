"""Zip webapp/ for App Service Linux zip deploy.

Why not PowerShell's Compress-Archive: its entries carry no Unix permission bits, so on Linux the
extracted files are unreadable and Kudu's rsync fails (exit 23 -> "Kudu Status 400").
Here every entry uses forward slashes and mode 0644 (dirs 0755).

usage: python make_zip.py <webapp_dir> <out.zip>
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv"}


def build(src: Path, out: Path) -> list[str]:
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts) or p.suffix == ".pyc":
                continue
            arc = rel.as_posix()
            if p.is_dir():
                info = zipfile.ZipInfo(arc + "/")
                info.external_attr = (0o40755 << 16) | 0x10
                z.writestr(info, b"")
            else:
                info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
                names.append(arc)
    return names


if __name__ == "__main__":
    files = build(Path(sys.argv[1]), Path(sys.argv[2]))
    print("zipped:", ", ".join(files))
