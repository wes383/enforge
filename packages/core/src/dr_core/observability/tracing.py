from __future__ import annotations

import contextvars
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from dr_core.observability.client import LangfuseIngestionClient
from dr_core.observability.redaction import redact_text, sanitize_metadata

logger = logging.getLogger(__name__)

_current_observation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "enforge_observation_id",
    default=None,
)


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _identifier() -> str:
    return uuid4().hex


class TraceSpan:
    def __init__(
        self,
        tracer: "ResearchTracer",
        *,
        name: str,
        kind: str,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> None:
        self.tracer = tracer
        self.id = _identifier()
        self.name = name
        self.kind = kind
        self.parent_id = _current_observation.get()
        self.started_at = _now()
        self.started_monotonic = time.monotonic()
        self.metadata = sanitize_metadata(metadata or {})
        self.model = model
        self.model_parameters = sanitize_metadata(model_parameters or {})
        self.output: Any = None
        self.level = "DEFAULT"
        self.status_message: str | None = None
        self._usage: dict[str, int] | None = None
        self._token = None
        self._ended = False

    def __enter__(self) -> "TraceSpan":
        self._token = _current_observation.set(self.id)
        body = {
            "id": self.id,
            "traceId": self.tracer.trace_id,
            "name": self.name,
            "startTime": self.started_at,
            "parentObservationId": self.parent_id,
            "input": self.metadata.get("input"),
            "metadata": self.metadata,
        }
        if self.kind == "generation":
            body["model"] = self.model
            body["modelParameters"] = self.model_parameters
            self.tracer.record(_event("generation-create", body))
        else:
            self.tracer.record(_event("span-create", body))
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        if self._ended:
            return
        self._ended = True
        if self._token is not None:
            _current_observation.reset(self._token)
            self._token = None
        duration_ms = round((time.monotonic() - self.started_monotonic) * 1000, 3)
        metadata = {**self.metadata, "duration_ms": duration_ms}
        if exc is not None:
            self.level = "ERROR"
            self.status_message = f"{exc_type.__name__}: {redact_text(str(exc))}"[:500]
            self.output = {"error": exc_type.__name__}
        body = {
            "id": self.id,
            "traceId": self.tracer.trace_id,
            "endTime": _now(),
            "output": sanitize_metadata(self.output),
            "metadata": sanitize_metadata(metadata),
            "level": self.level,
            "statusMessage": self.status_message,
        }
        if self.kind == "generation":
            body["model"] = self.model
            if self._usage:
                body["usageDetails"] = {
                    "input": self._usage.get("input", 0),
                    "output": self._usage.get("output", 0),
                    "total": self._usage.get("total", 0),
                }
            self.tracer.record(_event("generation-update", body))
        else:
            self.tracer.record(_event("span-update", body))

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(sanitize_metadata(metadata))
        if usage:
            self.metadata.update(
                {
                    "input_tokens": usage.get("input", 0),
                    "output_tokens": usage.get("output", 0),
                    "total_tokens": usage.get("total", 0),
                }
            )
            if self.kind == "generation":
                self._usage = usage


def _event(event_type: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "type": event_type,
        "timestamp": _now(),
        "body": body,
    }


class ResearchTracer:
    def __init__(
        self,
        *,
        run_id: UUID,
        project_id: UUID,
        environment: str,
        app_version: str,
        client: LangfuseIngestionClient | None = None,
        capture: bool = False,
        include_content: bool = False,
        trace_name: str = "research.run",
        trace_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.environment = environment
        self.app_version = app_version
        self.client = client
        self.capture = capture
        self.include_content = include_content
        self.trace_id = trace_id or run_id.hex
        self.trace_name = trace_name
        self.events: list[dict[str, Any]] = []
        self.enabled = client is not None or capture
        self.started_at = _now()
        self.started_monotonic = time.monotonic()
        self._finished = False

    def record(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.events.append(event)

    def start(self, *, input_metadata: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        self.record(
            _event(
                "trace-create",
                {
                    "id": self.trace_id,
                    "name": self.trace_name,
                    "timestamp": self.started_at,
                    "sessionId": str(self.run_id),
                    "release": self.app_version,
                    "version": self.app_version,
                    "tags": ["enforge", self.environment],
                    "input": sanitize_metadata(input_metadata or {}),
                    "metadata": sanitize_metadata(
                        {
                            "run_id": str(self.run_id),
                            "project_id": str(self.project_id),
                            "environment": self.environment,
                        }
                    ),
                },
            )
        )

    def span(
        self,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TraceSpan:
        return TraceSpan(self, name=name, kind="span", metadata=metadata)

    def generation(
        self,
        name: str,
        *,
        model: str | None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> TraceSpan:
        return TraceSpan(
            self,
            name=name,
            kind="generation",
            metadata=metadata,
            model=model,
            model_parameters=model_parameters,
        )

    def score(
        self,
        name: str,
        value: float | int | str | bool,
        *,
        comment: str | None = None,
    ) -> None:
        data_type = "NUMERIC" if isinstance(value, (int, float)) else "CATEGORICAL"
        self.record(
            _event(
                "score-create",
                {
                    "id": str(uuid4()),
                    "traceId": self.trace_id,
                    "name": name,
                    "value": value,
                    "dataType": data_type,
                    "comment": redact_text(comment) if comment else None,
                },
            )
        )

    def finish(
        self,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if not self.enabled or self._finished:
            return
        self._finished = True
        duration_ms = round((time.monotonic() - self.started_monotonic) * 1000, 3)
        body: dict[str, Any] = {
            "id": self.trace_id,
            "name": self.trace_name,
            "timestamp": self.started_at,
            "sessionId": str(self.run_id),
            "release": self.app_version,
            "version": self.app_version,
            "tags": ["enforge", self.environment, status],
            "output": sanitize_metadata(
                {
                    **(output or {}),
                    "status": status,
                    "duration_ms": duration_ms,
                    **(
                        {"error": type(error).__name__}
                        if error is not None
                        else {}
                    ),
                }
            ),
            "metadata": sanitize_metadata(
                {
                    "run_id": str(self.run_id),
                    "project_id": str(self.project_id),
                    "environment": self.environment,
                }
            ),
        }
        self.record(_event("trace-create", body))

    def flush(self) -> bool:
        if self.client is None or not self.events:
            return True
        events = self.events
        self.events = []
        return self.client.send_batch(events)

    def close(self) -> None:
        self.flush()
        if self.client is not None:
            self.client.close()


def build_tracer(
    *,
    run_id: UUID,
    project_id: UUID,
    settings,
    client: LangfuseIngestionClient | None = None,
    capture: bool = False,
) -> ResearchTracer:
    if client is None and settings.langfuse_enabled:
        public_key = _secret(settings.langfuse_public_key)
        secret_key = _secret(settings.langfuse_secret_key)
        if public_key and secret_key:
            client = LangfuseIngestionClient(
                host=settings.langfuse_host,
                public_key=public_key,
                secret_key=secret_key,
                timeout_seconds=settings.langfuse_timeout_seconds,
            )
        else:
            logger.warning(
                "LANGFUSE_ENABLED=true but Langfuse keys are missing; "
                "observability is disabled"
            )
    return ResearchTracer(
        run_id=run_id,
        project_id=project_id,
        environment=settings.environment,
        app_version=settings.app_version,
        client=client,
        capture=capture,
        include_content=settings.observability_include_content,
    )


def _secret(value: Any) -> str:
    if value is None:
        return ""
    get_secret = getattr(value, "get_secret_value", None)
    return str(get_secret()).strip() if callable(get_secret) else str(value).strip()
