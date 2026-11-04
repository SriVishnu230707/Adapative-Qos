#!/usr/bin/env python3
"""Export/verify an allowlisted source snapshot, excluding local runtime artifacts."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


ALLOWED = {
    "pyproject.toml", "MANIFEST.in", "README.md", ".gitattributes", ".gitignore", "src", "configs",
    "examples", "tests", "lab", "workloads", "experiments", "ui", "requirements", "docs",
}
EXCLUDED = {".git", ".venv", "work", "dist", "build", "__pycache__", ".pytest_cache"}


def source_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] not in ALLOWED or ".." in parts:
        raise ValueError(f"path is outside the source allowlist: {relative}")
    if any(part in EXCLUDED or part.endswith(".egg-info") for part in parts):
        raise ValueError(f"runtime artifact is not source: {relative}")
    path = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"source symlink is not allowed: {relative}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"source path escapes the project: {relative}")
    return path


def export_source(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "work"):
        raise ValueError("export output must stay inside the ignored project work directory")
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for name in sorted(ALLOWED):
            base = root / name
            paths = sorted(base.rglob("*")) if base.is_dir() else [base]
            for path in paths:
                relative = path.relative_to(root).as_posix()
                if any(part in EXCLUDED or part.endswith(".egg-info") for part in path.relative_to(root).parts):
                    continue
                checked = source_path(root, relative)
                if checked.is_file():
                    files[relative] = hashlib.sha256(checked.read_bytes()).hexdigest()
                    archive.add(checked, arcname=relative, recursive=False)
    try:
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    manifest = {"schema_version": 1, "base_revision": revision, "files": files}
    (output / "source-files.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def verify_source(root: Path, manifest: dict) -> int:
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("unsupported source manifest version")
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("empty source manifest")
    for relative, digest in manifest["files"].items():
        path = source_path(root, relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"source checksum mismatch: {relative}")
    return len(manifest["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        count = verify_source(args.root, json.loads(args.verify.read_text(encoding="utf-8")))
    else:
        count = len(export_source(args.root, args.output_dir or args.root / "work/source-sync")["files"])
    print(json.dumps({"status": "pass", "source_files": count}))


if __name__ == "__main__":
    main()
