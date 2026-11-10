import array
import io
import sys
import wave

from announcer.chime import CHIME_PATH, SAMPLE_RATE, render_chime


def read_samples(data: bytes) -> array.array[int]:
    """Return the samples of 16-bit mono WAV data."""
    with wave.open(io.BytesIO(data), "rb") as file:
        assert file.getnchannels() == 1
        assert file.getsampwidth() == 2
        assert file.getframerate() == SAMPLE_RATE
        samples = array.array("h", file.readframes(file.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def test_chime_is_short_audible_and_free_of_clicks() -> None:
    samples = read_samples(render_chime())

    assert 1.5 < len(samples) / SAMPLE_RATE < 2.5
    assert abs(samples[0]) <= 1
    assert abs(samples[-1]) <= 1
    assert 0.5 * 32767 < max(abs(sample) for sample in samples) < 0.7 * 32767


def test_bundled_chime_is_the_generated_one() -> None:
    bundled = read_samples(CHIME_PATH.read_bytes())
    generated = read_samples(render_chime())

    # Rounding may differ by one step between platforms, and no more.
    assert len(bundled) == len(generated)
    assert all(abs(a - b) <= 1 for a, b in zip(bundled, generated, strict=True))
