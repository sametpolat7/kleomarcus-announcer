"""Tests for writing files atomically."""

from pathlib import Path

import pytest

from announcer.files import write_atomically


def fail(descriptor: int) -> None:
    raise OSError("the disk is full")


def test_writes_and_replaces_the_file_and_leaves_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "folder" / "data.bin"

    write_atomically(path, b"\x00first, and longer\xff")
    write_atomically(path, b"second")

    assert path.read_bytes() == b"second"
    assert [item.name for item in path.parent.iterdir()] == ["data.bin"]


def test_failed_write_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.bin"
    write_atomically(path, b"first")

    monkeypatch.setattr("announcer.files.os.fsync", fail)
    with pytest.raises(OSError, match="disk is full"):
        write_atomically(path, b"second")

    assert path.read_bytes() == b"first"
    assert [item.name for item in tmp_path.iterdir()] == ["data.bin"]


def test_failed_first_write_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("announcer.files.os.fsync", fail)
    with pytest.raises(OSError, match="disk is full"):
        write_atomically(tmp_path / "data.bin", b"data")

    assert list(tmp_path.iterdir()) == []
