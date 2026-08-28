"""阶段运行证据的可回读格式。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import EVIDENCE_SCHEMA, FAKE_RUN_ID, TASK_ID, AdapterResult, BackendObjectRef, RouteDecision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunEvidence:
    task_id: str
    fake_run_id: str
    phase_id: str
    user_input: str
    route_decision: Mapping[str, Any]
    coverage: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    events: list[dict[str, Any]] = field(default_factory=list)
    output_refs: list[dict[str, str]] = field(default_factory=list)
    stop_reason: str | None = None
    final_declaration: str | None = None
    status: str = "running"
    started_at: str = field(default_factory=_now)
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID, not a fake run ID")
        if not FAKE_RUN_ID.fullmatch(self.fake_run_id):
            raise ValueError("fake_run_id must use the ZSK-S1/S2-FAKE- format")
        if not self.phase_id or not self.user_input:
            raise ValueError("phase_id and user_input are required")

    def record(
        self,
        action: str,
        result: AdapterResult,
        *,
        state_before: str,
        state_after: str,
        output_refs: Sequence[BackendObjectRef] = (),
    ) -> None:
        self.events.append(
            {
                "sequence": len(self.events) + 1,
                "action": action,
                "status": result.status,
                "code": result.code,
                "state_before": state_before,
                "state_after": state_after,
                "output_refs": [ref.as_dict() for ref in output_refs or result.object_refs],
                "checked": list(result.checked),
                "detail": result.detail,
            }
        )

    def finish(self, *, stop_reason: str, final_declaration: str, status: str = "complete") -> None:
        if status not in {"complete", "blocked"}:
            raise ValueError("evidence status must be complete or blocked")
        self.status = status
        self.stop_reason = stop_reason
        self.final_declaration = final_declaration
        self.completed_at = _now()

    def as_dict(self) -> dict[str, Any]:
        if self.status == "running":
            raise ValueError("unfinished evidence cannot be serialized")
        return {
            "schema_version": EVIDENCE_SCHEMA,
            "task_id": self.task_id,
            "fake_run_id": self.fake_run_id,
            "phase_id": self.phase_id,
            "user_input": self.user_input,
            "route_decision": dict(self.route_decision),
            "coverage": dict(self.coverage),
            "limitations": list(self.limitations),
            "events": list(self.events),
            "output_refs": list(self.output_refs),
            "stop_reason": self.stop_reason,
            "final_declaration": self.final_declaration,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class EvidenceRecorder:
    def __init__(self, task_id: str, fake_run_id: str, phase_id: str, user_input: str, route: RouteDecision) -> None:
        self.evidence = RunEvidence(task_id, fake_run_id, phase_id, user_input, route.as_dict())

    def record(self, action: str, result: AdapterResult, *, state_before: str, state_after: str) -> None:
        self.evidence.record(action, result, state_before=state_before, state_after=state_after)

    def add_outputs(self, refs: Sequence[BackendObjectRef]) -> None:
        self.evidence.output_refs.extend(ref.as_dict() for ref in refs)

    def set_scope(self, *, coverage: Mapping[str, Any], limitations: Sequence[str]) -> None:
        self.evidence.coverage = dict(coverage)
        self.evidence.limitations = tuple(limitations)

    def finish(self, *, stop_reason: str, final_declaration: str) -> None:
        self.evidence.finish(stop_reason=stop_reason, final_declaration=final_declaration)

    def write_json(self, path: Path) -> None:
        self.evidence.write_json(path)
