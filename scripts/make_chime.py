"""Write the bundled chime, ``src/announcer/assets/chime.wav``.

Run with ``uv run scripts/make_chime.py`` after changing the sound in
``announcer.chime``.
"""

from announcer.chime import CHIME_PATH, render_chime


def main() -> None:
    CHIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHIME_PATH.write_bytes(render_chime())
    print(f"Wrote {CHIME_PATH}")


if __name__ == "__main__":
    main()
