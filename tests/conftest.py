"""Keep every test on fake tool backends, whatever the shell or providers.yaml says."""

from __future__ import annotations

import pytest

from servers.common import FAKE_TOOL_STAGES


@pytest.fixture(autouse=True)
def fake_tool_stages(monkeypatch):
    monkeypatch.setenv("ME3_TOOL_STAGES", FAKE_TOOL_STAGES)
