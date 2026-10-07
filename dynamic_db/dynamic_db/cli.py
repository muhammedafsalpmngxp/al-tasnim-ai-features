"""Operator commands for DYNAMIC_DB.

    python -m dynamic_db.cli status                what is compiled, and is it stale
    python -m dynamic_db.cli inspect [--json]       tables/columns/relationships found
    python -m dynamic_db.cli fingerprint            structure + live fingerprint only
    python -m dynamic_db.cli schema [--tables ...]  the live schema as the agents see it
    python -m dynamic_db.cli changes                what moved since the last run
    python -m dynamic_db.cli validate               are the current artifacts still valid
    python -m dynamic_db.cli seed                   register the baseline SQL, no model
    python -m dynamic_db.cli compile [--capability X] [--force]
    python -m dynamic_db.cli run [--report-date D]  the FULL bootstrap pipeline, gated by fingerprint
    python -m dynamic_db.cli artifacts [--capability X] [--sql]
    python -m dynamic_db.cli audit                  physical names in generic code
    python -m dynamic_db.cli llm-config [--explain] [--json]
    python -m dynamic_db.cli logs [--run-id ID] [--tail N]
    python -m dynamic_db.cli refresh                drop the cached schema description
    python -m dynamic_db.cli watch [--full]         one table-ledger pass: record what changed
    python -m dynamic_db.cli tables [--status S]    every table the ledger knows, with its triage
    python -m dynamic_db.cli history [--table T] [--since YYYY-MM-DD]
                                                    what happened in the database, over time

EVERY COMMAND EXCEPT ``compile`` AND ``run`` IS FREE. Deciding whether
anything is stale, reading the schema, listing what is compiled and auditing
the source all happen without a single model call, so an operator can ask as
often as they like. Only a genuine compile spends anything, and only for the
capabilities that actually need it.

Nothing here prints a credential, a connection string or an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from typing import List, Optional

from dynamic_db import (
    artifacts,
    audit,
    bootstrap,
    capabilities,
    changes,
    introspect,
    ledger,
    llm,
    llm_config,
    logging_setup,
    rules,
)
from dynamic_db.service import get_service


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_status(args) -> int:
    status = get_service().status(refresh=args.refresh)
    if args.json:
        print(json.dumps(status, indent=2, default=str))
        return 0
    print(f"database              : {status.get('database', '(unreadable)')}")
    print(f"mode                  : {status['mode']}")
    print(f"structure fingerprint : {status.get('structure_fingerprint', '-')}")
    print(f"live fingerprint      : {status.get('live_fingerprint', '-')}")
    models = status.get("models", {})
    reasoning = models.get("reasoning", {})
    print(
        f"reasoning model       : {reasoning.get('model')} "
        f"(configured={reasoning.get('configured')}, "
        f"fallback={models.get('allow_reasoning_fallback')})"
    )
    missing = status.get("approved_tables_missing") or []
    if missing:
        print(f"MISSING from the database: {', '.join(missing)}")
    change = status.get("schema_change") or {}
    if change.get("first_run"):
        print("schema change         : first run, no baseline to compare")
    elif change.get("changed"):
        print(f"schema change         : YES, {len(change.get('changes', []))} change(s)")
    else:
        print("schema change         : none")
    print()
    for name, entry in (status.get("capabilities") or {}).items():
        flag = "STALE " if entry.get("stale") else "fresh "
        print(
            f"  {flag} {name:<20} v{entry.get('version', '-')} "
            f"{entry.get('origin', '-'):<9} {entry.get('verifier_status', '-'):<14} "
            f"{entry.get('stale_reason', '')[:60]}"
        )
    return 0


def _cmd_inspect(args) -> int:
    snapshot = introspect.introspect(use_cache=not args.refresh)
    payload = {
        "database": snapshot.database,
        "approved_tables": [
            {
                "name": t.name,
                "row_count": t.row_count,
                "columns": len(t.columns),
                "primary_key": t.primary_key,
                "foreign_keys": t.foreign_keys,
                "repeating_key": t.repeating_key,
            }
            for t in snapshot.approved_tables()
        ],
        "structure_fingerprint": snapshot.structure_fingerprint(),
        "live_fingerprint": snapshot.live_fingerprint(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"database: {snapshot.database}")
    for table in payload["approved_tables"]:
        print(
            f"  {table['name']:<32} {table['row_count'] if table['row_count'] is not None else '?':>10} rows, "
            f"{table['columns']} column(s), {len(table['foreign_keys'])} FK(s)"
        )
    return 0


def _cmd_fingerprint(args) -> int:
    snapshot = introspect.introspect(use_cache=not args.refresh)
    payload = {
        "database": snapshot.database,
        "structure_fingerprint": snapshot.structure_fingerprint(),
        "live_fingerprint": snapshot.live_fingerprint(),
    }
    print(json.dumps(payload, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in payload.items()))
    return 0


def _cmd_schema(args) -> int:
    snapshot = introspect.introspect(use_cache=not args.refresh)
    text = introspect.render_schema(snapshot, args.tables) if args.tables else introspect.render_schema(snapshot)
    print(text)
    print()
    print(f"-- structure fingerprint : {snapshot.structure_fingerprint()}")
    print(f"-- live fingerprint      : {snapshot.live_fingerprint()}")
    print(f"-- rendered              : {len(text):,} characters")
    return 0


def _cmd_changes(args) -> int:
    snapshot = introspect.introspect(use_cache=not args.refresh)
    previous = introspect.load_previous_snapshot()
    report = changes.compare(previous, snapshot)
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def _cmd_validate(args) -> int:
    """Whether every registered capability's CURRENT artifact still fits the
    live schema. No model call: this is the same deterministic check the
    bootstrap gate and the serving path both use."""
    from dynamic_db import validation

    snapshot = introspect.introspect(use_cache=not args.refresh)
    get_service().ensure_seeded(snapshot)
    problems_found = False
    for capability in capabilities.all_capabilities():
        record = artifacts.load(capability.id)
        if record.current is None:
            print(f"  {capability.id:<20} NO ARTIFACT")
            problems_found = True
            continue
        if record.current.not_applicable:
            print(f"  {capability.id:<20} not applicable: {record.current.not_applicable_reason}")
            continue
        problems = validation.validate(record.current.sql, capability, snapshot)
        if problems:
            print(f"  {capability.id:<20} INVALID: {problems[0][:120]}")
            problems_found = True
        else:
            print(f"  {capability.id:<20} valid (v{record.current.version}, {record.current.origin})")
    return 1 if problems_found else 0


def _cmd_seed(args) -> int:
    results = get_service().ensure_seeded()
    if not results:
        print("every capability already has an artifact; nothing to seed")
        return 0
    for name, outcome in results.items():
        print(f"  {name:<20} {outcome}")
    return 0


def _cmd_compile(args) -> int:
    service = get_service()
    report_date = _parse_date(args.report_date)
    if args.capability:
        results = [service.compile(args.capability.upper(), report_date=report_date, force=args.force)]
    else:
        results = service.compile_all(report_date=report_date, force=args.force)
    failed = 0
    for result in results:
        print(json.dumps(result, indent=2, default=str))
        if result.get("status") == "failed":
            failed += 1
    return 1 if failed else 0


def _cmd_run(args) -> int:
    """The full fingerprint-gated bootstrap pipeline -- exactly what a
    downstream application runs at its own startup, runnable here on its
    own so DYNAMIC_DB can be exercised without starting anything else."""
    report_date = _parse_date(args.report_date)
    orchestrator = bootstrap.get_orchestrator()
    status = orchestrator.run(report_date=report_date)
    if args.json:
        print(json.dumps(status, indent=2, default=str))
    else:
        print(f"run_id   : {status['run_id']}")
        print(f"state    : {status['state']}")
        print(f"database : {status['database']}")
        print(f"schema changed : {status['schema_changed']} ({status.get('change_summary', '')})")
        print()
        print("Dynamic DB Run Summary")
        print("----------------------")
        for step in status["steps"]:
            duration = f"{step['duration_ms']:.1f} ms" if step.get("duration_ms") is not None else "-"
            print(f"  {step['name']:<24} {step['status']:<8} {duration:>12}  {step.get('detail', '')}")
        print(f"  {'TOTAL':<24} {'':<8} {status['duration_ms']:>9.1f} ms")
        if status.get("error"):
            print()
            print(f"ERROR: {status['error']['type']}: {status['error']['message']}")
    return 0 if status["ready"] else 1


def _cmd_artifacts(args) -> int:
    names = [args.capability.upper()] if args.capability else list(capabilities.all_ids())
    for name in names:
        record = artifacts.load(name)
        if record.current is None:
            print(f"{name}: nothing compiled")
            continue
        print(json.dumps(record.current.summary(), indent=2))
        if args.sql:
            print(record.current.sql)
        print()
    return 0


def _cmd_audit(args) -> int:
    findings = audit.run()
    blocking = audit.blocking(findings)
    for finding in findings:
        marker = "BLOCKING" if finding in blocking else "advisory"
        print(f"[{marker}] {finding}")
    print()
    print(f"{len(findings)} finding(s), {len(blocking)} blocking")
    return 1 if blocking else 0


def _cmd_llm_config(args) -> int:
    from dynamic_db.config import get_settings

    settings = get_settings()
    model = settings.reasoning_model or ""
    family = llm_config.classify_model(model)

    if args.explain:
        rows = llm_config.explain(model, max_tokens=settings.reasoning_max_tokens)
        if args.json:
            print(json.dumps({"model": model, "model_family": family, "parameters": rows}, indent=2))
            return 0
        print(f"Model: {model or '(not configured)'}  Family: {family}")
        print()
        for row in rows:
            print(f"Parameter    : {row['parameter']}")
            print(f"Purpose      : {row['purpose']}")
            print(f"Supported by : {row['supported']}")
            print(f"Current value: {row['current_value']}")
            print(f"Default      : {row['default']}")
            print(f"Valid range  : {row['valid_range']}")
            print(f"Zero meaning : {row['zero_meaning']}")
            print(f"Effect       : {row['effect']}")
            print()
        return 0

    status = llm.status()
    if args.json:
        print(json.dumps(status, indent=2, default=str))
        return 0
    print(f"reasoning model     : {status['reasoning']['model']}")
    print(f"provider            : {status['reasoning']['provider']}")
    print(f"model family        : {status['reasoning']['model_family']}")
    print(f"configured          : {status['reasoning']['configured']}")
    print(f"fallback allowed    : {status['allow_reasoning_fallback']}")
    print(f"fallback model      : {status['reasoning_fallback_model']}")
    print(f"usage sink attached : {status['usage_sink_registered']}")
    print()
    print("Run with --explain for the full parameter table.")
    return 0


def _cmd_logs(args) -> int:
    if args.run_id:
        record = logging_setup.read_run_record(args.run_id)
        if record is None:
            print(f"no run record found for {args.run_id}")
            return 1
        print(json.dumps(record, indent=2, default=str))
        return 0
    records = logging_setup.list_run_records(limit=args.tail)
    for record in records:
        print(
            f"{record.get('started_at', '?'):<26} {record.get('run_id', '?'):<24} "
            f"{record.get('status', '?'):<8} {record.get('duration_ms', 0):>8.0f} ms  "
            f"{record.get('operation', '')}"
        )
    return 0


def _cmd_refresh(args) -> int:
    snapshot = introspect.refresh()
    rules.reload()
    get_service().invalidate()
    print(
        f"re-read {len(snapshot.approved_tables())} approved table(s); "
        f"structure {snapshot.structure_fingerprint()[:16]}"
    )
    return 0


def _print_event(event) -> None:
    detail = ""
    if event.get("column"):
        detail = f".{event['column']}"
        if event.get("before") or event.get("after"):
            detail += f"  {event.get('before') or '-'} -> {event.get('after') or '-'}"
    elif event.get("before") or event.get("after"):
        detail = f"  {event.get('before') or '-'} -> {event.get('after') or '-'}"
    status = f"  [{event['status']}/{event.get('kind', '')}]" if event.get("status") else ""
    note = f"  {event['note']}" if event.get("note") else ""
    print(f"{event.get('at', '?'):<26} {event['type']:<20} {event.get('table', '')}{detail}{status}{note}")


def _cmd_watch(args) -> int:
    result = ledger.observe(full=args.full)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    schemas = ", ".join(result["schemas"]) or "(none)"
    print(f"watched schemas : {schemas}")
    print(f"tracking since  : {result.get('tracking_since')}")
    print("tables          : " + ", ".join(f"{k} {v}" for k, v in sorted(result["counts"].items())))
    if result["events"]:
        print()
        for event in result["events"]:
            _print_event(event)
    else:
        print("no change since the last pass")
    if result["candidates"]:
        print()
        print("awaiting review (never approved automatically -- add to INCLUDED_TABLES to approve):")
        for candidate in result["candidates"]:
            print(f"  {candidate['table']:<40} {candidate['kind']:<20} {candidate['note']}")
    return 0


def _cmd_tables(args) -> int:
    registry = ledger.load_registry()
    entries = list((registry.get("tables") or {}).values())
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]
    if args.json:
        print(json.dumps(entries, indent=2, default=str))
        return 0
    if not registry:
        print("the ledger has not run yet -- run: python -m dynamic_db.cli watch")
        return 1
    for entry in entries:
        columns = entry.get("columns")
        print(
            f"{entry['name']:<40} {entry.get('status', '?'):<10} {entry.get('kind', ''):<19} "
            f"{len(columns) if columns is not None else '?':>4} col  "
            f"created {str(entry.get('create_date') or '?')[:10]}  "
            f"changed {str(entry.get('last_changed') or '?')[:10]}  {entry.get('note', '')}"
        )
    return 0


def _cmd_history(args) -> int:
    events = ledger.load_events(table=args.table, since=args.since, limit=args.tail)
    if args.json:
        print(json.dumps(events, indent=2, default=str))
        return 0
    if not events:
        print("no recorded changes" + (f" for {args.table}" if args.table else ""))
    for event in events:
        _print_event(event)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="dynamic_db.cli", description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="show INFO logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="what is compiled, and is it stale")
    p.add_argument("--refresh", action="store_true", help="re-read the schema first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("inspect", help="tables/columns/relationships found in the live database")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_inspect)

    p = sub.add_parser("fingerprint", help="structure + live fingerprint only")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_fingerprint)

    p = sub.add_parser("schema", help="the live schema as the agents see it")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--tables", nargs="*", help="limit to these objects")
    p.set_defaults(func=_cmd_schema)

    p = sub.add_parser("changes", help="what moved since the last run")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=_cmd_changes)

    p = sub.add_parser("validate", help="are the current artifacts still valid against the live schema")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("seed", help="register the baseline SQL as version 1")
    p.set_defaults(func=_cmd_seed)

    p = sub.add_parser("compile", help="compile affected capabilities")
    p.add_argument("--capability")
    p.add_argument("--report-date", help="YYYY-MM-DD, used to probe the candidate")
    p.add_argument("--force", action="store_true", help="recompile even when nothing is stale")
    p.set_defaults(func=_cmd_compile)

    p = sub.add_parser("run", help="the full fingerprint-gated bootstrap pipeline")
    p.add_argument("--report-date", help="YYYY-MM-DD, used to probe any recompile")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("artifacts", help="what is currently serving each capability")
    p.add_argument("--capability")
    p.add_argument("--sql", action="store_true", help="print the SQL too")
    p.set_defaults(func=_cmd_artifacts)

    p = sub.add_parser("audit", help="physical names in generic code")
    p.set_defaults(func=_cmd_audit)

    p = sub.add_parser("llm-config", help="what generation controls the reasoning model supports")
    p.add_argument("--explain", action="store_true", help="the full parameter table")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_llm_config)

    p = sub.add_parser("logs", help="list runs, or show one run's full record")
    p.add_argument("--run-id")
    p.add_argument("--tail", type=int, default=20)
    p.set_defaults(func=_cmd_logs)

    p = sub.add_parser("refresh", help="drop the cached schema description")
    p.set_defaults(func=_cmd_refresh)

    p = sub.add_parser("watch", help="one table-ledger pass: record what changed, table by table")
    p.add_argument("--full", action="store_true", help="re-read every column, not only changed tables")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("tables", help="every table the ledger knows, with its status and triage")
    p.add_argument(
        "--status",
        choices=[ledger.STATUS_APPROVED, ledger.STATUS_CANDIDATE, ledger.STATUS_OBSERVED,
                 ledger.STATUS_IGNORED, ledger.STATUS_REMOVED],
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_tables)

    p = sub.add_parser("history", help="what happened in the database, over time")
    p.add_argument("--table", help="only events whose table name contains this")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--tail", type=int, help="only the last N events")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_history)

    args = parser.parse_args(argv)
    logging_setup.configure(component="dynamic_db")
    if args.verbose:
        import logging as _logging

        _logging.getLogger().setLevel("INFO")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
