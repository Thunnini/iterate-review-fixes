#!/usr/bin/env python3
"""Persist per-finding review-loop state without imposing a global pass limit."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE = {"open", "fixed-pending-review"}
DEFERRED = {"deferred-recurring", "deferred-decision", "deferred-dependent"}
ACCEPTED = {"accepted-residual"}
VALID_STATUSES = ACTIVE | DEFERRED | ACCEPTED | {"resolved"}


class GuardError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GuardError(f"state file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GuardError(f"invalid JSON state file: {path}: {exc}") from exc

    if data.get("version") != 1:
        raise GuardError(f"unsupported state version: {data.get('version')!r}")
    if not isinstance(data.get("findings"), dict):
        raise GuardError("state is missing findings")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def add_event(state: dict[str, Any], action: str, **details: Any) -> None:
    state.setdefault("events", []).append({"at": now(), "action": action, **details})


def require_pass(state: dict[str, Any], expected_open: bool) -> None:
    actual = bool(state.get("pass_open"))
    if actual != expected_open:
        required = "open" if expected_open else "closed"
        raise GuardError(f"review pass must be {required}")


def get_finding(state: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    try:
        finding = state["findings"][fingerprint]
    except KeyError as exc:
        raise GuardError(f"unknown fingerprint: {fingerprint}") from exc
    if finding.get("status") not in VALID_STATUSES:
        raise GuardError(f"invalid finding status for {fingerprint}")
    return finding


def decision(state: dict[str, Any]) -> str:
    if state.get("pass_open"):
        return "review-in-progress"
    if state.get("current_pass", 0) == 0:
        return "review-required"
    statuses = {finding["status"] for finding in state["findings"].values()}
    if statuses & ACTIVE:
        return "continue"
    if statuses & DEFERRED:
        return "report-deferred"
    if statuses & ACCEPTED:
        return "finalize-with-residual"
    return "finalize-clean"


def summary(state: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {status: [] for status in sorted(VALID_STATUSES)}
    for fingerprint, finding in sorted(state["findings"].items()):
        grouped[finding["status"]].append(fingerprint)
    return {
        "decision": decision(state),
        "review_passes": state["current_pass"],
        "pass_open": state["pass_open"],
        "max_attempts_per_fingerprint": state["max_attempts_per_fingerprint"],
        "findings": grouped,
    }


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    if path.exists() and not args.force:
        raise GuardError(f"state already exists: {path}; pass --force to replace it")
    created = now()
    state: dict[str, Any] = {
        "version": 1,
        "created_at": created,
        "updated_at": created,
        "source_branch": args.source_branch,
        "source_tip": args.source_tip,
        "review_base": args.review_base,
        "temp_branch": args.temp_branch,
        "max_attempts_per_fingerprint": args.max_attempts,
        "current_pass": 0,
        "pass_open": False,
        "seen_in_current_pass": [],
        "findings": {},
        "events": [],
    }
    add_event(state, "initialized")
    save_state(path, state)
    return summary(state)


def cmd_start_pass(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    state["current_pass"] += 1
    state["pass_open"] = True
    state["seen_in_current_pass"] = []
    add_event(state, "review-pass-started", review_pass=state["current_pass"])
    save_state(path, state)
    return summary(state)


def cmd_find(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, True)
    fingerprint = args.fingerprint.strip()
    if not fingerprint:
        raise GuardError("fingerprint must not be empty")

    review_pass = state["current_pass"]
    seen = state["seen_in_current_pass"]
    finding = state["findings"].get(fingerprint)
    first_seen_this_pass = fingerprint not in seen

    if finding is None:
        finding = {
            "title": args.title,
            "root_cause": args.root_cause,
            "evidence": args.evidence,
            "status": "open",
            "attempts": 0,
            "attempt_limit": state["max_attempts_per_fingerprint"],
            "occurrences": 0,
            "first_seen_pass": review_pass,
            "last_seen_pass": review_pass,
            "fixes": [],
            "deferral": None,
            "resolution": None,
        }
        state["findings"][fingerprint] = finding
    else:
        finding["title"] = args.title
        finding["root_cause"] = args.root_cause
        finding["evidence"] = args.evidence
        finding["last_seen_pass"] = review_pass
        if finding["status"] not in DEFERRED | ACCEPTED:
            attempt_limit = finding.get("attempt_limit", state["max_attempts_per_fingerprint"])
            if finding["attempts"] >= attempt_limit:
                finding["status"] = "deferred-recurring"
                finding["deferral"] = {
                    "kind": "recurring",
                    "reason": "Finding reappeared after the per-fingerprint fix-attempt limit",
                    "at": now(),
                }
            else:
                finding["status"] = "open"
                finding["resolution"] = None

    if first_seen_this_pass:
        seen.append(fingerprint)
        finding["occurrences"] += 1
    add_event(
        state,
        "finding-recorded",
        fingerprint=fingerprint,
        review_pass=review_pass,
        status=finding["status"],
    )
    save_state(path, state)
    return {"fingerprint": fingerprint, "finding": finding, **summary(state)}


def cmd_finish_pass(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, True)
    review_pass = state["current_pass"]
    seen = set(state["seen_in_current_pass"])
    resolved: list[str] = []
    for fingerprint, finding in state["findings"].items():
        if finding["status"] == "fixed-pending-review" and fingerprint not in seen:
            finding["status"] = "resolved"
            finding["resolution"] = {
                "evidence": f"Absent from completed full review pass {review_pass}",
                "at": now(),
            }
            resolved.append(fingerprint)
    state["pass_open"] = False
    state["seen_in_current_pass"] = []
    add_event(
        state,
        "review-pass-finished",
        review_pass=review_pass,
        resolved=resolved,
    )
    save_state(path, state)
    return {"resolved": resolved, **summary(state)}


def cmd_fix(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    finding = get_finding(state, args.fingerprint)
    if finding["status"] in DEFERRED:
        raise GuardError("cannot directly fix a deferred finding without resolving or reopening it")
    if finding["status"] != "open":
        raise GuardError(f"finding is not open: {finding['status']}")
    attempt_limit = finding.get("attempt_limit", state["max_attempts_per_fingerprint"])
    if finding["attempts"] >= attempt_limit:
        raise GuardError("per-fingerprint fix-attempt limit reached; defer the finding")
    finding["attempts"] += 1
    finding["status"] = "fixed-pending-review"
    finding["fixes"].append({"attempt": finding["attempts"], "summary": args.summary, "at": now()})
    add_event(
        state,
        "fix-recorded",
        fingerprint=args.fingerprint,
        attempt=finding["attempts"],
    )
    save_state(path, state)
    return {"fingerprint": args.fingerprint, "finding": finding, **summary(state)}


def cmd_defer(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    finding = get_finding(state, args.fingerprint)
    finding["status"] = f"deferred-{args.kind}"
    finding["deferral"] = {"kind": args.kind, "reason": args.reason, "at": now()}
    add_event(state, "finding-deferred", fingerprint=args.fingerprint, kind=args.kind)
    save_state(path, state)
    return {"fingerprint": args.fingerprint, "finding": finding, **summary(state)}


def cmd_resolve(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    finding = get_finding(state, args.fingerprint)
    finding["status"] = "resolved"
    finding["resolution"] = {"evidence": args.evidence, "at": now()}
    add_event(state, "finding-resolved", fingerprint=args.fingerprint)
    save_state(path, state)
    return {"fingerprint": args.fingerprint, "finding": finding, **summary(state)}


def cmd_reopen(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    finding = get_finding(state, args.fingerprint)
    if finding["status"] not in DEFERRED | ACCEPTED:
        raise GuardError(f"only a deferred or accepted finding can be reopened: {finding['status']}")
    current_limit = finding.get("attempt_limit", state["max_attempts_per_fingerprint"])
    finding["attempt_limit"] = current_limit + args.additional_attempts
    finding["status"] = "open"
    finding.setdefault("reopenings", []).append(
        {"additional_attempts": args.additional_attempts, "reason": args.reason, "at": now()}
    )
    add_event(
        state,
        "finding-reopened",
        fingerprint=args.fingerprint,
        additional_attempts=args.additional_attempts,
    )
    save_state(path, state)
    return {"fingerprint": args.fingerprint, "finding": finding, **summary(state)}


def cmd_accept(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.state).expanduser()
    state = load_state(path)
    require_pass(state, False)
    finding = get_finding(state, args.fingerprint)
    if finding["status"] not in DEFERRED:
        raise GuardError(f"only a deferred finding can be accepted: {finding['status']}")
    finding["status"] = "accepted-residual"
    finding["acceptance"] = {"reason": args.reason, "at": now()}
    add_event(state, "finding-accepted", fingerprint=args.fingerprint)
    save_state(path, state)
    return {"fingerprint": args.fingerprint, "finding": finding, **summary(state)}


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    return summary(load_state(Path(args.state).expanduser()))


def add_state_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", required=True, help="Path to the JSON state file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize loop state")
    add_state_argument(init_parser)
    init_parser.add_argument("--source-branch", required=True)
    init_parser.add_argument("--source-tip", required=True)
    init_parser.add_argument("--review-base", required=True)
    init_parser.add_argument("--temp-branch", required=True)
    init_parser.add_argument("--max-attempts", type=int, default=2)
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=cmd_init)

    start_parser = subparsers.add_parser("start-pass", help="Start a full review pass")
    add_state_argument(start_parser)
    start_parser.set_defaults(handler=cmd_start_pass)

    find_parser = subparsers.add_parser("find", help="Record a finding in the open pass")
    add_state_argument(find_parser)
    find_parser.add_argument("--fingerprint", required=True)
    find_parser.add_argument("--title", required=True)
    find_parser.add_argument("--root-cause", required=True)
    find_parser.add_argument("--evidence", required=True)
    find_parser.set_defaults(handler=cmd_find)

    finish_parser = subparsers.add_parser("finish-pass", help="Finish the open review pass")
    add_state_argument(finish_parser)
    finish_parser.set_defaults(handler=cmd_finish_pass)

    fix_parser = subparsers.add_parser("fix", help="Record an attempted fix")
    add_state_argument(fix_parser)
    fix_parser.add_argument("--fingerprint", required=True)
    fix_parser.add_argument("--summary", required=True)
    fix_parser.set_defaults(handler=cmd_fix)

    defer_parser = subparsers.add_parser("defer", help="Defer one finding without stopping the loop")
    add_state_argument(defer_parser)
    defer_parser.add_argument("--fingerprint", required=True)
    defer_parser.add_argument("--kind", choices=("recurring", "decision", "dependent"), required=True)
    defer_parser.add_argument("--reason", required=True)
    defer_parser.set_defaults(handler=cmd_defer)

    resolve_parser = subparsers.add_parser("resolve", help="Mark a finding resolved with evidence")
    add_state_argument(resolve_parser)
    resolve_parser.add_argument("--fingerprint", required=True)
    resolve_parser.add_argument("--evidence", required=True)
    resolve_parser.set_defaults(handler=cmd_resolve)

    reopen_parser = subparsers.add_parser("reopen", help="Reopen a deferred finding after user direction")
    add_state_argument(reopen_parser)
    reopen_parser.add_argument("--fingerprint", required=True)
    reopen_parser.add_argument("--additional-attempts", type=int, default=1)
    reopen_parser.add_argument("--reason", required=True)
    reopen_parser.set_defaults(handler=cmd_reopen)

    accept_parser = subparsers.add_parser("accept", help="Record explicit user acceptance of a residual finding")
    add_state_argument(accept_parser)
    accept_parser.add_argument("--fingerprint", required=True)
    accept_parser.add_argument("--reason", required=True)
    accept_parser.set_defaults(handler=cmd_accept)

    status_parser = subparsers.add_parser("status", help="Show the next loop decision")
    add_state_argument(status_parser)
    status_parser.set_defaults(handler=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "max_attempts", 1) < 1:
        parser.error("--max-attempts must be at least 1")
    if getattr(args, "additional_attempts", 1) < 1:
        parser.error("--additional-attempts must be at least 1")
    try:
        result = args.handler(args)
    except GuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
