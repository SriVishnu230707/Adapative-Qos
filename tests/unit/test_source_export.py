import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("source_export", Path(__file__).resolve().parents[2] / "lab/export-source.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_source_snapshot_excludes_keys_disks_venvs_and_git(tmp_path):
    for name in ("src/package.py", "README.md", "work/key", "work/disk.vdi", ".venv/file", ".git/config", "src/package.egg-info/metadata", "src/__pycache__/cached"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic test data", encoding="utf-8")
    manifest = exporter.export_source(tmp_path, tmp_path / "work/export")
    assert set(manifest["files"]) == {"src/package.py", "README.md"}
    assert exporter.verify_source(tmp_path, manifest) == 2
    with tarfile.open(tmp_path / "work/export/source.tar.gz") as archive:
        assert set(archive.getnames()) == set(manifest["files"])
    (tmp_path / "src/package.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        exporter.verify_source(tmp_path, manifest)


@pytest.mark.parametrize("name", ["../outside", "work/key", "/etc/shadow", "src/../../outside"])
def test_source_verifier_rejects_paths_outside_allowlist(tmp_path, name):
    with pytest.raises(ValueError):
        exporter.verify_source(tmp_path, {"schema_version": 1, "files": {name: "unused"}})


@pytest.mark.parametrize("name", ["src/..\\work/key", "src/./package.py",
                                 "src//package.py", "src/package.py:stream"])
def test_source_paths_require_canonical_portable_spelling(tmp_path, name):
    with pytest.raises(ValueError, match="canonical"):
        exporter.source_path(tmp_path, name)


def test_source_export_refuses_public_output_path(tmp_path):
    with pytest.raises(ValueError, match="ignored project work"):
        exporter.export_source(tmp_path, tmp_path / "dist")


def test_source_verifier_rejects_symlinked_parent(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "work/key").write_text("synthetic test value")
    try:
        (tmp_path / "src/link").symlink_to(tmp_path / "work", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks requires additional Windows privileges")
    with pytest.raises(ValueError, match="symlink"):
        exporter.verify_source(tmp_path, {"schema_version": 1, "files": {"src/link/key": "unused"}})
