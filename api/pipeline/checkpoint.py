"""api.pipeline.checkpoint – Checkpoint system for resumable pipeline runs.

Saves, loads, and merges per-stage checkpoint data so that:
  1. Verified findings are "locked in" and skipped in subsequent runs
  2. Pipeline can resume from any stage boundary
  3. Focused runs target only failing functions
  4. Development iteration is fast (skip S-00/S-01 when sentinels are stable)

Storage layout:
  {job_id}/checkpoints/latest.json       — full pipeline checkpoint
  {job_id}/checkpoints/s{NN}_{name}.json — per-stage checkpoint
  {job_id}/checkpoints/functions/{fn}.json — per-function checkpoint (S-02)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from api.config import ARTIFACT_CONTAINER
from api.storage import _upload_to_blob, _download_blob

logger = logging.getLogger("mcp_factory.api")


@dataclass
class FunctionCheckpoint:
    """Per-function checkpoint data from the probe loop."""
    name: str
    status: str  # "success", "error", "skipped"
    working_call: dict | None = None
    finding: str = ""
    verified: bool = False
    stage: str = "s02"
    timestamp: float = 0.0

    def is_skippable(self) -> bool:
        """True if this function can be safely skipped in subsequent runs."""
        return self.status == "success" and self.working_call is not None


@dataclass
class StageCheckpoint:
    """Per-stage checkpoint data."""
    stage_id: str  # e.g. "s00_setup"
    completed: bool = False
    timestamp: float = 0.0
    data: dict = field(default_factory=dict)


@dataclass
class PipelineCheckpoint:
    """Full pipeline checkpoint — aggregates all stage and function checkpoints."""
    job_id: str
    source_job_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Per-stage data
    stages: dict[str, StageCheckpoint] = field(default_factory=dict)

    # Per-function data (from S-02 probe loop)
    functions: dict[str, FunctionCheckpoint] = field(default_factory=dict)

    # Accumulated pipeline context
    sentinels: dict = field(default_factory=dict)
    vocab: dict = field(default_factory=dict)
    unlock_result: dict = field(default_factory=dict)
    write_unlock_block: str = ""
    winning_init_sequence: list = field(default_factory=list)
    dll_strings: dict = field(default_factory=dict)
    static_hints_block: str = ""

    def skippable_functions(self) -> set[str]:
        """Return set of function names that can be skipped."""
        return {
            name for name, fc in self.functions.items()
            if fc.is_skippable()
        }

    def success_count(self) -> int:
        return sum(1 for fc in self.functions.values() if fc.status == "success")

    def total_count(self) -> int:
        return len(self.functions)


def save_checkpoint(checkpoint: PipelineCheckpoint) -> str:
    """Save checkpoint to blob storage. Returns the blob path."""
    checkpoint.updated_at = time.time()
    blob_path = f"{checkpoint.job_id}/checkpoints/latest.json"

    data = {
        "job_id": checkpoint.job_id,
        "source_job_id": checkpoint.source_job_id,
        "created_at": checkpoint.created_at,
        "updated_at": checkpoint.updated_at,
        "sentinels": {
            (f"0x{k:08X}" if isinstance(k, int) else str(k)): v
            for k, v in checkpoint.sentinels.items()
        },
        "vocab": checkpoint.vocab,
        "unlock_result": checkpoint.unlock_result,
        "write_unlock_block": checkpoint.write_unlock_block,
        "winning_init_sequence": checkpoint.winning_init_sequence,
        "dll_strings": checkpoint.dll_strings,
        "static_hints_block": checkpoint.static_hints_block,
        "stages": {
            sid: {
                "stage_id": sc.stage_id,
                "completed": sc.completed,
                "timestamp": sc.timestamp,
                "data": sc.data,
            }
            for sid, sc in checkpoint.stages.items()
        },
        "functions": {
            name: {
                "name": fc.name,
                "status": fc.status,
                "working_call": fc.working_call,
                "finding": fc.finding,
                "verified": fc.verified,
                "stage": fc.stage,
                "timestamp": fc.timestamp,
            }
            for name, fc in checkpoint.functions.items()
        },
    }

    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER, blob_path,
            json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
        )
        logger.info("[%s] checkpoint saved: %d functions, %d stages",
                     checkpoint.job_id, len(checkpoint.functions), len(checkpoint.stages))
    except Exception as exc:
        logger.warning("[%s] checkpoint save failed: %s", checkpoint.job_id, exc)

    # Also save per-stage checkpoints
    for sid, sc in checkpoint.stages.items():
        try:
            stage_blob = f"{checkpoint.job_id}/checkpoints/{sid}.json"
            _upload_to_blob(
                ARTIFACT_CONTAINER, stage_blob,
                json.dumps(asdict(sc), indent=2).encode("utf-8"),
            )
        except Exception:
            pass

    return blob_path


def load_checkpoint(job_id: str, checkpoint_id: str = "") -> PipelineCheckpoint | None:
    """Load checkpoint from blob storage.

    If checkpoint_id is empty, loads from the job's own latest checkpoint.
    If checkpoint_id is another job_id, loads that job's checkpoint (cross-job seeding).
    """
    source_id = checkpoint_id or job_id
    blob_path = f"{source_id}/checkpoints/latest.json"

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, blob_path)
        data = json.loads(raw)
    except Exception:
        logger.debug("[%s] no checkpoint found at %s", job_id, blob_path)
        return None

    cp = PipelineCheckpoint(
        job_id=job_id,
        source_job_id=data.get("source_job_id", source_id),
        created_at=data.get("created_at", 0),
        updated_at=data.get("updated_at", 0),
        vocab=data.get("vocab", {}),
        unlock_result=data.get("unlock_result", {}),
        write_unlock_block=data.get("write_unlock_block", ""),
        winning_init_sequence=data.get("winning_init_sequence", []),
        dll_strings=data.get("dll_strings", {}),
        static_hints_block=data.get("static_hints_block", ""),
    )

    # Restore sentinels (stored as hex string keys)
    for k, v in data.get("sentinels", {}).items():
        try:
            int_key = int(k, 16) if isinstance(k, str) and k.startswith("0x") else int(k)
            cp.sentinels[int_key] = v
        except (ValueError, TypeError):
            pass

    # Restore stage checkpoints
    for sid, sdata in data.get("stages", {}).items():
        cp.stages[sid] = StageCheckpoint(
            stage_id=sdata.get("stage_id", sid),
            completed=sdata.get("completed", False),
            timestamp=sdata.get("timestamp", 0),
            data=sdata.get("data", {}),
        )

    # Restore function checkpoints
    for name, fdata in data.get("functions", {}).items():
        cp.functions[name] = FunctionCheckpoint(
            name=fdata.get("name", name),
            status=fdata.get("status", "unknown"),
            working_call=fdata.get("working_call"),
            finding=fdata.get("finding", ""),
            verified=fdata.get("verified", False),
            stage=fdata.get("stage", "s02"),
            timestamp=fdata.get("timestamp", 0),
        )

    logger.info("[%s] checkpoint loaded from %s: %d functions (%d skippable), %d stages",
                job_id, source_id, len(cp.functions), len(cp.skippable_functions()), len(cp.stages))
    return cp


def seed_context_from_checkpoint(ctx, checkpoint: PipelineCheckpoint) -> int:
    """Seed an ExploreContext with checkpoint data.

    Returns the number of findings seeded.
    """
    seeded = 0

    if checkpoint.sentinels:
        ctx.sentinels = dict(checkpoint.sentinels)

    if checkpoint.vocab:
        ctx.vocab = dict(checkpoint.vocab)

    if checkpoint.unlock_result:
        ctx.unlock_result = dict(checkpoint.unlock_result)

    if checkpoint.write_unlock_block:
        ctx.write_unlock_block = checkpoint.write_unlock_block

    if checkpoint.dll_strings:
        ctx.dll_strings = dict(checkpoint.dll_strings)

    if checkpoint.static_hints_block:
        ctx.static_hints_block = checkpoint.static_hints_block

    # Mark successful functions as already explored
    for name, fc in checkpoint.functions.items():
        if fc.is_skippable():
            ctx.already_explored.add(name)
            seeded += 1

    logger.info("[%s] seeded %d findings from checkpoint", ctx.job_id, seeded)
    return seeded


def update_checkpoint_from_context(checkpoint: PipelineCheckpoint, ctx) -> None:
    """Update checkpoint with current pipeline context after a stage completes."""
    checkpoint.sentinels = dict(ctx.sentinels)
    checkpoint.vocab = dict(ctx.vocab)
    checkpoint.unlock_result = dict(ctx.unlock_result)
    checkpoint.write_unlock_block = ctx.write_unlock_block
    checkpoint.dll_strings = dict(ctx.dll_strings)
    checkpoint.static_hints_block = ctx.static_hints_block


def record_function_checkpoint(
    checkpoint: PipelineCheckpoint,
    fn_name: str,
    status: str,
    working_call: dict | None = None,
    finding: str = "",
    verified: bool = False,
    stage: str = "s02",
) -> None:
    """Record a function-level checkpoint after probing or verification."""
    checkpoint.functions[fn_name] = FunctionCheckpoint(
        name=fn_name,
        status=status,
        working_call=working_call,
        finding=finding,
        verified=verified,
        stage=stage,
        timestamp=time.time(),
    )


def mark_stage_complete(
    checkpoint: PipelineCheckpoint,
    stage_id: str,
    data: dict | None = None,
) -> None:
    """Mark a stage as completed in the checkpoint."""
    checkpoint.stages[stage_id] = StageCheckpoint(
        stage_id=stage_id,
        completed=True,
        timestamp=time.time(),
        data=data or {},
    )


def is_stage_complete(checkpoint: PipelineCheckpoint | None, stage_id: str) -> bool:
    """Check if a stage was already completed in a loaded checkpoint."""
    if checkpoint is None:
        return False
    sc = checkpoint.stages.get(stage_id)
    return sc is not None and sc.completed


def should_skip_function(
    checkpoint: PipelineCheckpoint | None,
    fn_name: str,
    focus_functions: list[str] | None = None,
) -> bool:
    """Determine if a function should be skipped in the probe loop.

    Skip if:
    1. Checkpoint has it as skippable (success + working_call) AND
    2. It's NOT in the focus_functions list (if provided)
    """
    if checkpoint is None:
        return False

    fc = checkpoint.functions.get(fn_name)
    if fc is None or not fc.is_skippable():
        return False

    if focus_functions and fn_name in focus_functions:
        return False

    return True
