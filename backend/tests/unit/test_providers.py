"""Fallback selection: ordering, availability, and honest reporting of failure."""

from __future__ import annotations

from app.services import provider_service
from app.services.provider_service import Resolution, candidate_chain, pick_available


def test_candidate_chain_prefers_the_selected_provider() -> None:
    assert candidate_chain("ollama", ["anthropic"]) == ["ollama", "anthropic"]


def test_candidate_chain_deduplicates_repeats() -> None:
    # The shipped default lists ollama in both places; it must be probed once.
    assert candidate_chain("ollama", ["ollama"]) == ["ollama"]
    assert candidate_chain("ollama", ["anthropic", "ollama"]) == ["ollama", "anthropic"]


def test_pick_available_returns_the_first_usable_candidate() -> None:
    assert pick_available(["a", "b"], {}) == "a"
    assert pick_available(["a", "b"], {"a": "down"}) == "b"


def test_pick_available_returns_none_when_everything_is_down() -> None:
    assert pick_available(["a", "b"], {"a": "down", "b": "down"}) is None


def test_resolution_reports_every_attempted_provider_with_its_reason() -> None:
    resolution = Resolution(
        provider=None,
        model=None,
        attempted=[
            {"provider": "ollama", "reason": "Ollama unreachable"},
            {"provider": "anthropic", "reason": "ANTHROPIC_API_KEY is not set"},
        ],
    )
    summary = resolution.reason_summary()
    assert "ollama: Ollama unreachable" in summary
    assert "anthropic: ANTHROPIC_API_KEY is not set" in summary


async def test_resolve_provider_falls_back_to_the_next_candidate(monkeypatch) -> None:
    async def fake_check(name: str):
        if name == "ollama":
            return (False, "Ollama unreachable", [])
        return (True, None, ["claude-sonnet-4-5"])

    monkeypatch.setattr(provider_service, "check_provider", fake_check)
    monkeypatch.setattr(provider_service.settings, "provider_fallback_chain", "ollama,anthropic")

    resolution = await provider_service.resolve_provider("ollama")

    assert resolution.ok is True
    assert resolution.provider == "anthropic"
    assert resolution.fell_back is True
    assert resolution.attempted == [{"provider": "ollama", "reason": "Ollama unreachable"}]


async def test_resolve_provider_degrades_when_nothing_is_available(monkeypatch) -> None:
    async def fake_check(name: str):
        return (False, f"{name} down", [])

    monkeypatch.setattr(provider_service, "check_provider", fake_check)

    resolution = await provider_service.resolve_provider("ollama")

    assert resolution.ok is False
    assert resolution.provider is None
    assert "ollama: ollama down" in resolution.reason_summary()


async def test_resolve_provider_does_not_fall_back_when_the_first_works(monkeypatch) -> None:
    async def fake_check(name: str):
        return (True, None, [])

    monkeypatch.setattr(provider_service, "check_provider", fake_check)

    resolution = await provider_service.resolve_provider("ollama")

    assert resolution.provider == "ollama"
    assert resolution.fell_back is False
    assert resolution.attempted == []
