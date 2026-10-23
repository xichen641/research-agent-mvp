import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from research_agent.chat.diagnostics import read_run_diagnostics, runtime_configuration
from research_agent.chat.domain import ChatRunEventRecord, ChatRunRecord
from research_agent.config import Settings
from research_agent.shared.errors import AppError


class Reader:
    def __init__(
        self, run: ChatRunRecord | None, events: list[ChatRunEventRecord]
    ) -> None:
        self.run = run
        self.events = events

    async def get_run(self, run_id):
        return self.run

    async def list_events(self, run_id, *, after_sequence=0):
        return self.events


@pytest.mark.asyncio
async def test_run_diagnostics_allowlist_excludes_questions_and_stream_text() -> None:
    run_id = uuid4()
    at = datetime.now(UTC)
    run = ChatRunRecord(
        id=run_id,
        session_id=uuid4(),
        status="SUCCEEDED",
        error_code=None,
        created_at=at,
        completed_at=at,
        question="private question",
    )
    config = runtime_configuration(
        Settings(
            llm_api_key="secret-key",
            llm_base_url="https://user:password@provider.example/v1",
        )
    ).model_dump(mode="json")
    config["secret_key"] = "hidden-config-value"
    events = [
        ChatRunEventRecord(
            run_id=run_id,
            sequence=1,
            event_type="runtime_configuration",
            payload=config,
            created_at=at,
        ),
        ChatRunEventRecord(
            run_id=run_id,
            sequence=2,
            event_type="query_planned",
            payload={"intent": "explain_method", "private_field": "hidden-paper-text"},
            created_at=at,
        ),
        ChatRunEventRecord(
            run_id=run_id,
            sequence=3,
            event_type="ui_part",
            payload={"part": {"delta": "private answer"}},
            created_at=at,
        ),
        ChatRunEventRecord(
            run_id=run_id,
            sequence=4,
            event_type="model_call_observed",
            payload={"private_response": "provider-body"},
            created_at=at,
        ),
    ]
    result = await read_run_diagnostics(Reader(run, events), run_id)
    encoded = json.dumps(result)
    assert not any(
        secret in encoded
        for secret in (
            "private question",
            "secret-key",
            "password",
            "hidden-config-value",
            "hidden-paper-text",
            "private answer",
            "provider-body",
        )
    )
    assert result["invalid_measurements"] == 1
    assert result["model_calls"] == []


@pytest.mark.asyncio
async def test_missing_run_uses_existing_not_found_error() -> None:
    with pytest.raises(AppError) as error:
        await read_run_diagnostics(Reader(None, []), uuid4())
    assert error.value.code == "CHAT_RUN_NOT_FOUND"
