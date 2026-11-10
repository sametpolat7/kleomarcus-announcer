"""Tests for writing files atomically."""

from pathlib import Path

import pytest

from announcer.files import write_atomically


def test_writes_the_data(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"

    write_atomically(path, b"\x00first\xff")

    assert path.read_bytes() == b"\x00first\xff"


def test_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    write_atomically(path, b"first, and longer")

    write_atomically(path, b"second")

    assert path.read_bytes() == b"second"


def test_creates_the_folder(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "folder" / "data.bin"

    write_atomically(path, b"data")

    assert path.read_bytes() == b"data"


def test_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    write_atomically(tmp_path / "data.bin", b"first")
    write_atomically(tmp_path / "data.bin", b"second")

    assert [item.name for item in tmp_path.iterdir()] == ["data.bin"]


def test_failed_write_keeps_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.bin"
    write_atomically(path, b"first")

    def fail(descriptor: int) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr("announcer.files.os.fsync", fail)
    with pytest.raises(OSError, match="disk is full"):
        write_atomically(path, b"second")

    assert path.read_bytes() == b"first"
    assert [item.name for item in tmp_path.iterdir()] == ["data.bin"]


def test_failed_first_write_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(descriptor: int) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr("announcer.files.os.fsync", fail)
    with pytest.raises(OSError, match="disk is full"):
        write_atomically(tmp_path / "data.bin", b"data")

    assert list(tmp_path.iterdir()) == []
