import json
from pathlib import Path

from api import transition_readiness as tr


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_session(tmp_path: Path, statuses: dict[str, str], contract_valid: bool = True, capture_quality: str = "complete") -> Path:
    session_dir = tmp_path / "session-a"
    transitions = [
        {
            "id": tid,
            "status": st,
            "reason": f"{tid} -> {st}",
        }
        for tid, st in statuses.items()
    ]
    _write_json(session_dir / "transition-index.json", {"version": "1.0", "transitions": transitions})
    _write_json(
        session_dir / "human" / "collect-session-result.json",
        {
            "contract": {"valid": contract_valid, "hard_fail": False},
            "session_save_meta": {"capture_quality": capture_quality},
        },
    )
    return session_dir


def test_evaluate_session_pass_when_all_targets_are_pass(tmp_path: Path):
    statuses = {tid: "pass" for tid in tr.TARGET_TRANSITIONS}
    session_dir = _make_session(tmp_path, statuses)

    result = tr.evaluate_session(session_dir)

    assert result["pass"] is True
    assert result["missing_transition_ids"] == []
    assert result["bad_status_ids"] == []


def test_evaluate_session_fails_on_non_pass_transition(tmp_path: Path):
    statuses = {
        "T-04": "warn",
        "T-05": "pass",
        "T-14": "pass",
        "T-15": "pass",
    }
    session_dir = _make_session(tmp_path, statuses)

    result = tr.evaluate_session(session_dir)

    assert result["pass"] is False
    assert result["bad_status_ids"] == ["T-04"]
    assert any("non_pass_transitions" in r for r in result["reasons"])


def test_build_ab_readiness_requires_determinism():
    leg_ok = {
        "pass": True,
        "reasons": [],
    }

    readiness = tr.build_ab_readiness(leg_ok, leg_ok, deterministic=False, require_determinism=True)

    assert readiness["pass"] is False
    assert "ab_not_deterministic" in readiness["reasons"]


def test_evaluate_session_uses_collect_session_parsed_transition_index(tmp_path: Path):
    session_dir = tmp_path / "session-legacy"
    transitions = [
        {
            "id": tid,
            "status": "pass",
            "reason": "legacy layout",
        }
        for tid in tr.TARGET_TRANSITIONS
    ]
    _write_json(
        session_dir / "human" / "collect-session-result.json",
        {
            "contract": {
                "valid": True,
                "hard_fail": False,
                "parsed": {
                    "transition-index.json": {
                        "version": "1.0",
                        "transitions": transitions,
                    }
                },
            },
            "session_save_meta": {"capture_quality": "complete"},
        },
    )

    result = tr.evaluate_session(session_dir)

    assert result["pass"] is True
    assert result["missing_transition_ids"] == []
