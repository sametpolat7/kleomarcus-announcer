"""Tests for letting only one copy of the app run."""

from pathlib import Path

from announcer.single_instance import claim


def test_first_copy_claims_and_the_second_is_turned_away(tmp_path: Path) -> None:
    path = tmp_path / "app" / "announcer.lock"

    first = claim(path)
    assert first is not None
    try:
        assert claim(path) is None
        assert claim(path) is None
    finally:
        first.close()


def test_claim_is_free_again_when_the_first_copy_has_ended(tmp_path: Path) -> None:
    path = tmp_path / "announcer.lock"
    first = claim(path)
    assert first is not None
    first.close()

    second = claim(path)

    assert second is not None
    second.close()


def test_mark_left_behind_by_a_copy_that_crashed_does_not_count(tmp_path: Path) -> None:
    path = tmp_path / "announcer.lock"
    path.write_bytes(b"left behind")

    claimed = claim(path)

    assert claimed is not None
    claimed.close()
