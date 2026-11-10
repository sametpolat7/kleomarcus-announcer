"""The chime that is played before every announcement.

The sound is synthesised here, so the app ships no third-party audio.
``scripts/make_chime.py`` writes it to ``assets/chime.wav``, and that bundled
file is what the app plays.
"""

import array
import io
import math
import sys
import wave
from pathlib import Path

CHIME_PATH = Path(__file__).parent / "assets" / "chime.wav"

SAMPLE_RATE = 22_050  # samples per second; mono, 16-bit

_NOTES = (523.25, 659.25, 783.99)  # C5, E5 and G5 in hertz: a rising major chord
_NOTE_SPACING = 0.28  # seconds from the start of one note to the next
_NOTE_LENGTH = 1.3  # seconds that each note rings
_ATTACK = 0.005  # seconds for a note to reach full strength, to avoid a click
_DECAY = 4.0  # how fast a note dies away
_OVERTONE = 0.3  # strength of the octave above each note; gives a bell colour
_FADE_OUT = 0.03  # seconds of fade at the very end, to finish on silence
_PEAK = 0.6  # loudest sample, as a share of full scale


def render_chime() -> bytes:
    """Return the chime as the contents of a WAV file."""
    note_samples = round(_NOTE_LENGTH * SAMPLE_RATE)
    starts = [
        round(index * _NOTE_SPACING * SAMPLE_RATE) for index in range(len(_NOTES))
    ]
    samples = [0.0] * (starts[-1] + note_samples)

    for start, frequency in zip(starts, _NOTES, strict=True):
        for index in range(note_samples):
            seconds = index / SAMPLE_RATE
            angle = 2 * math.pi * frequency * seconds
            envelope = min(seconds / _ATTACK, 1.0) * math.exp(-_DECAY * seconds)
            tone = math.sin(angle) + _OVERTONE * math.sin(2 * angle)
            samples[start + index] += envelope * tone

    fade_samples = round(_FADE_OUT * SAMPLE_RATE)
    for index in range(fade_samples):
        samples[-1 - index] *= index / fade_samples

    scale = _PEAK * 32767 / max(abs(sample) for sample in samples)
    frames = array.array("h", (round(sample * scale) for sample in samples))
    if sys.byteorder == "big":
        frames.byteswap()  # WAV data is little-endian

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(SAMPLE_RATE)
        file.writeframes(frames.tobytes())
    return buffer.getvalue()
