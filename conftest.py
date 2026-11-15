import pytest


@pytest.fixture(autouse=True)
def no_llm_by_default(monkeypatch):
    """No test reaches the network unless it installs its own fake."""
    monkeypatch.setenv("LLM_PROVIDER", "none")
