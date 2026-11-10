"""Tests for the announcement template."""

import pytest

from announcer.template import DEFAULT_TEMPLATE, is_valid_template, render


def test_default_template_is_valid() -> None:
    assert is_valid_template(DEFAULT_TEMPLATE)


@pytest.mark.parametrize("template", ["", "Antrenmanınız başlamıştır.", "{Ad}"])
def test_template_without_the_placeholder_is_invalid(template: str) -> None:
    assert not is_valid_template(template)


def test_render_puts_the_name_in_every_placeholder() -> None:
    assert (
        render(DEFAULT_TEMPLATE, "Ayşe Yılmaz")
        == "Ayşe Yılmaz, antrenmanınız başlamıştır. İyi antrenmanlar dileriz."
    )
    assert (
        render("{ad_soyad}! {ad_soyad}, sıra sizde.", "Mehmet")
        == "Mehmet! Mehmet, sıra sizde."
    )


def test_render_leaves_other_braces_alone() -> None:
    assert render("{ad_soyad} {salon} {} {", "Zeynep") == "Zeynep {salon} {} {"
