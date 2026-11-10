"""The sentence announced for each athlete."""

PLACEHOLDER = "{ad}"
DEFAULT_TEMPLATE = "{ad}, antrenmanınız başlamıştır. İyi antrenmanlar dileriz."


def is_valid_template(template: str) -> bool:
    """Return whether ``template`` has a place for the athlete's name."""
    return PLACEHOLDER in template


def render(template: str, name: str) -> str:
    """Return the announcement for ``name``.

    The placeholder is replaced as plain text rather than with ``str.format``,
    so any other braces in the template are harmless.
    """
    return template.replace(PLACEHOLDER, name)
