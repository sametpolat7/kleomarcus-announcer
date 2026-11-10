PLACEHOLDER = "{ad_soyad}"
DEFAULT_TEMPLATE = (
    f"{PLACEHOLDER}, antrenmanınız başlamıştır. İyi antrenmanlar dileriz."
)


def is_valid_template(template: str) -> bool:
    return PLACEHOLDER in template


def render(template: str, name: str) -> str:
    """The placeholder is replaced as plain text rather than with ``str.format``,
    so any other braces in the template are harmless.
    """
    return template.replace(PLACEHOLDER, name)
