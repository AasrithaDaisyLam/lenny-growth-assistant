"""
API contracts, persistence, and the streaming path.

These run against a real Postgres, because the things worth testing here (cascade
deletes, JSONB columns, the SSE response, the internal tool's auth) do not exist in
a mocked layer.

The streaming test deliberately uses the **smalltalk** route: it touches no
dependency at all, so the full contract -- session creation, an SSE turn, the frame
vocabulary, and persistence -- can be asserted without a model and without embedding
a query. That is a direct payoff from routing smalltalk to a static reply.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.services import outcomes, session_service
from scripts.eval import _citation_coverage


@pytest.fixture(scope="module")
def client():
    # `with` runs the lifespan, so the schema exists before the first request.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_id(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return response.json()["id"]


# --- Health ------------------------------------------------------------------


def test_liveness_touches_no_dependency(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_readiness_reports_each_dependency(client: TestClient) -> None:
    body = client.get("/api/health/ready").json()
    for name in ("database", "ollama", "embed_model", "chat_model", "active_provider"):
        assert name in body["components"]
        assert "ok" in body["components"][name]


# --- Session lifecycle -------------------------------------------------------


def test_session_lifecycle(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"title": "Lifecycle"}).json()
    assert created["title"] == "Lifecycle"
    assert created["provider"]
    assert created["model"]

    listed = client.get("/api/sessions").json()
    assert any(item["id"] == created["id"] for item in listed)

    fetched = client.get(f"/api/sessions/{created['id']}").json()
    assert fetched["id"] == created["id"]

    renamed = client.patch(f"/api/sessions/{created['id']}", json={"title": "Renamed"}).json()
    assert renamed["title"] == "Renamed"

    assert client.delete(f"/api/sessions/{created['id']}").status_code == 204
    assert client.get(f"/api/sessions/{created['id']}").status_code == 404


def test_unknown_session_is_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/sessions/{missing}").status_code == 404
    assert client.get(f"/api/sessions/{missing}/messages").status_code == 404


def test_malformed_session_id_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/api/sessions/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_switching_to_an_unconfigured_provider_is_refused_with_a_reason(
    client: TestClient, session_id: str
) -> None:
    """The reason matters: a bare 400 would hide the missing credential."""
    response = client.patch(f"/api/sessions/{session_id}", json={"provider": "anthropic"})
    if response.status_code == 200:  # pragma: no cover - only when a key IS configured
        pytest.skip("ANTHROPIC_API_KEY is configured in this environment")
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "provider_unavailable"
    assert "ANTHROPIC_API_KEY" in body["message"]


def test_session_rename_is_visible_in_the_listing(client: TestClient) -> None:
    created = client.post("/api/sessions", json={}).json()
    client.patch(f"/api/sessions/{created['id']}", json={"title": "Findable"})
    listed = client.get("/api/sessions").json()
    assert any(item["id"] == created["id"] and item["title"] == "Findable" for item in listed)
    client.delete(f"/api/sessions/{created['id']}")


# --- Streaming turn ----------------------------------------------------------


def test_smalltalk_turn_streams_and_persists(client: TestClient, session_id: str) -> None:
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "hello"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    # The frame vocabulary is the contract with the frontend.
    assert "event: usage" in response.text
    assert "event: done" in response.text
    # No model was involved, so no tokens and no citations should be claimed.
    assert "event: token" not in response.text
    assert "event: error" not in response.text

    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["intent"] == "smalltalk"
    assert messages[1]["content"]


def test_first_question_titles_the_session(client: TestClient) -> None:
    created = client.post("/api/sessions", json={}).json()
    assert created["title"] is None
    client.post(f"/api/sessions/{created['id']}/messages", json={"content": "hello"})
    assert client.get(f"/api/sessions/{created['id']}").json()["title"] == "hello"
    client.delete(f"/api/sessions/{created['id']}")


def test_deleting_a_session_cascades_to_its_messages(client: TestClient) -> None:
    created = client.post("/api/sessions", json={}).json()
    client.post(f"/api/sessions/{created['id']}/messages", json={"content": "hello"})
    client.delete(f"/api/sessions/{created['id']}")
    assert client.get(f"/api/sessions/{created['id']}/messages").status_code == 404


def test_empty_message_is_rejected(client: TestClient, session_id: str) -> None:
    response = client.post(f"/api/sessions/{session_id}/messages", json={"content": ""})
    assert response.status_code == 422


# --- Artifacts ---------------------------------------------------------------


def test_artifact_is_sanitized_and_rendered_with_a_csp(client: TestClient, session_id: str) -> None:
    hostile = '<script src="https://evil.example/x.js"></script><p>chart</p>'
    created = client.post(
        f"/api/sessions/{session_id}/artifacts",
        json={"kind": "html", "title": "Hostile", "content": hostile},
    ).json()

    # Stripping is recorded, not silent.
    assert created["removed_elements"]
    assert created["version"] == 1

    render = client.get(f"/api/artifacts/{created['id']}/render")
    assert render.status_code == 200
    csp = render.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert render.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in render.text.lower()
    assert "chart" in render.text


def test_artifact_regeneration_bumps_the_version(client: TestClient, session_id: str) -> None:
    created = client.post(
        f"/api/sessions/{session_id}/artifacts",
        json={"kind": "markdown", "title": "Versioned", "content": "# One"},
    ).json()
    regenerated = client.post(f"/api/artifacts/{created['id']}/regenerate").json()
    assert regenerated["version"] == created["version"] + 1

    # `raw_content` lives on the render contract, not the summary one. Re-sanitizing
    # must preserve the original text exactly, so that is where to check it.
    original = client.get(f"/api/artifacts/{created['id']}").json()
    new_version = client.get(f"/api/artifacts/{regenerated['id']}").json()
    assert new_version["raw_content"] == original["raw_content"] == "# One"


def test_unknown_artifact_is_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/artifacts/{missing}").status_code == 404


# --- Internal tool surface ---------------------------------------------------


def test_internal_tools_require_the_shared_secret(client: TestClient) -> None:
    response = client.post("/internal/tools/search_transcripts", json={"query": "x"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_internal_tools_reject_a_wrong_secret(client: TestClient) -> None:
    response = client.post(
        "/internal/tools/search_transcripts",
        json={"query": "x"},
        headers={"X-Internal-Token": "definitely-not-the-token"},
    )
    assert response.status_code == 401


def test_internal_tools_are_not_in_the_public_schema(client: TestClient) -> None:
    """Shared-secret callbacks must not be advertised as part of the API."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/internal") for path in paths)


# --- Evaluation metric -------------------------------------------------------


async def test_citation_coverage_counts_only_genuine_grounded_answers() -> None:
    """
    A turn that abstained, failed, timed out, degraded, or was not a grounded
    question cannot carry a citation, so none of them may reach the denominator.

    This test owns its engine: the module's `TestClient` runs the app on its own
    event loop, so reusing the app's pooled connections here is a cross-loop error.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as db:
            before = await _citation_coverage(db)
            session = await session_service.create_session(db, title="coverage")

            rows = [
                ("grounded and cited [S1]", "grounded_qa", [{"marker": "S1"}], outcomes.ANSWERED),
                ("declined at the floor", "grounded_qa", [], outcomes.ABSTAINED),
                ("declined, nothing citable", "grounded_qa", [], outcomes.UNCITED),
                ("degraded, no provider", "grounded_qa", [], outcomes.DEGRADED),
                ("timed out", "grounded_qa", [], outcomes.FAILED),
                ("an essay, not a grounded answer", "ship30_essay", [], outcomes.ANSWERED),
                ("hello", "smalltalk", [], outcomes.SMALLTALK),
            ]
            for content, intent, citations, outcome in rows:
                await session_service.add_message(
                    db,
                    session.id,
                    "assistant",
                    content,
                    intent=intent,
                    citations=citations,
                    retrieval_trace={"outcome": outcome},
                )

            after = await _citation_coverage(db)
            await session_service.delete_session(db, session.id)
    finally:
        await engine.dispose()

    assert after[0] - before[0] == 1  # only the cited grounded answer
    assert after[1] - before[1] == 1
    assert after[2] - before[2] == 1  # the uncited turn is reported as declined
