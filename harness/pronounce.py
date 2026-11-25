"""Say a word the way its owner does, and keep its spelling everywhere else (ADR-050).

Pipecat swaps each word for the voice's own pronunciation markup just before the text goes to
the TTS. Cartesia then reports that markup back as the spoken word, and those words become the
context and the chat (measured Nov 16, 2026: "Quiel" came back as "<<k|i|j|ˈ|ɛ|l>>"). The mixin
here puts the real spelling back, word for word, so truncation by word index is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping

PUNCTUATION = ".,!?;:'\")"


def spelled_back(markup_to_word: Mapping[str, str], token: str) -> str:
    """The token as it should read: markup becomes the word, trailing punctuation stays."""
    core = token.rstrip(PUNCTUATION)
    word = markup_to_word.get(core)
    return token if word is None else word + token[len(core):]


class SpelledBack:
    """Mixin for a TTS service with word timestamps. Set ``spellings`` to markup -> word."""

    spellings: Mapping[str, str] = {}

    async def add_word_timestamps(self, word_times, *args, **kwargs):
        if self.spellings:
            word_times = [(spelled_back(self.spellings, w), t) for w, t in word_times]
        await super().add_word_timestamps(word_times, *args, **kwargs)


def pronunciations(service, ipa: Mapping[str, str]) -> tuple[list, dict[str, str]]:
    """The text transform for ``service`` and the markup -> word map that undoes it. Words the
    service cannot mark up, or whose IPA is more than one word, are left to be said as written."""
    usable = {w: p for w, p in ipa.items() if len(p.split()) == 1 and service.format_pronunciation(w, p)}
    if not usable:
        return [], {}
    back = {service.format_pronunciation(w, p): w for w, p in usable.items()}
    return [("*", service.pronunciation_transform_ipa(usable))], back
