"""Conservative cleanup for punctuation already recognized by the ASR model."""

from __future__ import annotations

import re


# Whisper normally returns well-formed punctuation.  The small repairs below
# only join punctuation tokens that are already present in its result.  They
# deliberately do not turn words such as "comma" or "period" into symbols,
# because those words can be literal dictated content.
_SPACE_BEFORE_CLOSING = re.compile(r"\s+([,.;:!?%\]\)}])")
_SPACE_AFTER_OPENING = re.compile(r"([\[\({])\s+")


def normalize_transcript(text: str) -> str:
    """Repair harmless spacing artifacts without inventing punctuation.

    The speech model remains the source of capitalization, wording, and
    sentence punctuation.  This function has intentionally narrow behavior so
    it is safe for general desktop fields, including documents and forms.
    """

    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _SPACE_BEFORE_CLOSING.sub(r"\1", cleaned)
    return _SPACE_AFTER_OPENING.sub(r"\1", cleaned)
