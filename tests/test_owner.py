"""The owner (ADR-050): one file feeds the prompt, the recogniser's keyterms, and the voice."""

from __future__ import annotations

import asyncio

import pytest
import yaml

from bench.cost_ledger import CostLedger
from harness import providers
from harness.bot import PROMPT_VERSION, SYSTEM_PROMPT, system_prompt
from harness.config import CONFIGS_DIR, load_config
from harness.owner import Owner, either, load_owner
from harness.pronounce import SpelledBack, pronunciations, spelled_back


def test_the_owner_file_is_small_and_public():
    raw = yaml.safe_load((CONFIGS_DIR / "owner.yaml").read_text())
    assert set(raw) <= {"name", "first_name", "about", "said_as", "misheard", "search_hints", "keyterms", "pronounce"}
    owner = load_owner()
    assert owner.name == "Quiel Quiwa" and owner.first_name == "Quiel"
    assert set(owner.pronounce) == {"Quiel", "Quiwa"}


def test_the_prompt_names_the_owner_and_the_mishearings():
    owner = load_owner()
    assert PROMPT_VERSION == "v4"
    assert f"You belong to {owner.name}" in SYSTEM_PROMPT
    assert f'said "{owner.said_as}"' in SYSTEM_PROMPT
    assert all(m in SYSTEM_PROMPT for m in owner.misheard)
    assert f'search "{owner.name}" in quotes' in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT and "}" not in SYSTEM_PROMPT


def test_the_prompt_has_every_section_closed():
    for tag in ("role", "speech", "hearing", "tools", "research", "documents", "building", "publishing"):
        assert SYSTEM_PROMPT.count(f"<{tag}>") == 1 and SYSTEM_PROMPT.count(f"</{tag}>") == 1
    for tool in ("local_search", "read_document", "search", "fetch_content", "publish_site", "open_published_site"):
        assert tool in SYSTEM_PROMPT


def test_the_prompt_works_for_an_owner_with_only_a_name():
    prompt = system_prompt(Owner(name="Ana Cruz", first_name="Ana"))
    assert "You belong to Ana Cruz. Assume the speaker is Ana" in prompt
    assert "Always write and search the name as Ana Cruz." in prompt
    assert 'search "Ana Cruz" in quotes.' in prompt


def test_either_reads_as_speech():
    assert either(("a",)) == "a" and either(("a", "b")) == "a or b" and either(("a", "b", "c")) == "a, b or c"


def _ledger(cfg, tmp_path):
    return CostLedger.from_providers_config(cfg.providers, tmp_path / "ledger.jsonl")


def test_flux_gets_the_keyterms_only_when_switched_on(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-only")
    cfg = load_config("mac")
    cfg.providers["stages"]["stt"] = "deepgram_flux"
    stt = providers.build_stt(cfg.providers, _ledger(cfg, tmp_path))
    assert list(stt._settings.keyterm) == list(load_owner().keyterms)
    assert "keyterm=Quiel" in stt._build_query_string()
    cfg.providers["providers"]["deepgram"]["keyterms"] = False
    stt = providers.build_stt(cfg.providers, _ledger(cfg, tmp_path))
    assert not stt._settings.keyterm and "keyterm" not in stt._build_query_string()


def test_keyterms_are_priced_on_top_of_flux():
    prices = load_config("mac").providers["prices"]
    assert prices["deepgram_keyterm_per_min"] > 0


def test_cartesia_says_the_name_in_ipa_and_the_words_come_back_spelled(tmp_path, monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "test-only")
    cfg = load_config("mac")
    cfg.providers["stages"]["tts"] = "cartesia"
    tts = providers.build_tts(cfg.providers, _ledger(cfg, tmp_path))
    sent = asyncio.run(tts._text_transforms[0][1]("This site is about Quiel Quiwa.", "sentence"))
    assert "Quiel" not in sent and "Quiwa" not in sent and sent.count("<<") == 2
    back = [spelled_back(tts.spellings, w) for w in sent.split()]
    assert " ".join(back) == "This site is about Quiel Quiwa."


def test_words_come_back_in_place_with_their_punctuation():
    seen = []

    class Base:
        async def add_word_timestamps(self, word_times, context_id=None, **kw):
            seen.extend(word_times)

    class Service(SpelledBack, Base):
        spellings = {"<<k|i>>": "Quiel"}

    asyncio.run(Service().add_word_timestamps([("about", 0.1), ("<<k|i>>,", 0.2), ("<<x>>", 0.3)], "ctx"))
    assert seen == [("about", 0.1), ("Quiel,", 0.2), ("<<x>>", 0.3)]


def test_a_voice_with_no_markup_says_the_word_as_written():
    class Plain:
        @classmethod
        def format_pronunciation(cls, word, ipa):
            return None

    assert pronunciations(Plain, {"Quiel": "kiˈjɛl"}) == ([], {})
