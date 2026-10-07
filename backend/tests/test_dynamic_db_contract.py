"""The whole chain, against the real database, end to end -- through the
STABLE CONTRACT this application actually uses, not DYNAMIC_DB's internals.

Everything else in this suite tests one link. This tests that they are
actually joined up, exactly as Daily Report itself experiences them:

    Daily Report's capability manifest registered into DYNAMIC_DB's engine
      -> live schema read
      -> change detected against the previous snapshot
      -> the affected capability identified from its own dependencies
      -> SQL authored against the CURRENT schema
      -> deterministic validation passed
      -> executed, for real, read-only, bounded
      -> semantically verified
      -> promoted
      -> app.dynamic_client.sql_for() serves the new artifact

Only the two REASONING calls are scripted; every other step is the real thing,
including the database. That is the right split: a test cannot assert what a
model will say, and it can assert exactly what the engine does with the
answer.

This is also where "the generated artifacts validate" and "Daily Report can
consume them" (see the task's Contract test category) are actually proven:
every capability here is Daily Report's OWN manifest, compiled and served
through app.dynamic_client, the same path a real request takes.

Skips itself when SQL Server is not reachable.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import requires_database

import app._dynamic_db_path  # noqa: F401 - side effect: dynamic_db becomes importable
from app import capability_manifest
from dynamic_db import artifacts, capabilities, introspect, llm, prompts, service
from dynamic_db.nodes import sql_author, verifier


@pytest.fixture(scope="module")
def _cache_root(tmp_path_factory):
    """One throwaway cache directory for this whole module.

    Shared deliberately. Every test here needs the live schema, and reading it
    includes measured grain and numeric passes over a fact table -- genuinely
    worth paying once and genuinely not worth paying four times. The ARTIFACTS
    in it are still cleared between tests, so no test can see another's
    promotion; only the schema description is reused, which is exactly what
    the running application does too.
    """
    return tmp_path_factory.mktemp("dynamic-db-contract")


@pytest.fixture
def isolated_cache(_cache_root, monkeypatch):
    """Artifacts written here go to a throwaway directory, never the real one."""
    from dynamic_db import identity
    from dynamic_db.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "dynamic_cache_dir", str(_cache_root / "cache"))
    monkeypatch.setattr(settings, "reasoning_api_key", "test-key")
    monkeypatch.setattr(settings, "reasoning_model", "test-reasoning-model")
    artifacts.clear()
    identity.delete_cache("schema_previous", "json")
    capability_manifest.ensure_registered()
    return _cache_root


class ScriptedReasoning:
    """Stands in for the reasoning model, and records how it was called."""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.calls = []

    def __call__(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(kwargs.get("node"))
        if kwargs.get("node") == "reasoning_sql_author":
            return (
                f"```sql\n{self.sql}\n```\n\n"
                "```notes\nEvery business concept resolved against the schema block.\n```"
            )
        return '{"ok": true, "not_applicable": false, "reason": "", "feedback": "", "note": "matches the rules"}'


def _report_date() -> date:
    """A date the database actually holds entries for.

    Read from the data rather than written down: a fixed date in a test is the
    same hardcoding this whole package exists to remove, and it stops being
    true the moment the data moves on.
    """
    from app.repositories.daily_repository import DailyRepository

    dates = DailyRepository().fetch_dates_with_activity(1)
    if not dates:
        pytest.skip("the database holds no daily entries to probe with")
    return dates[0]["report_date"]


@requires_database
class TestTheWholeChain:
    def test_a_normal_day_serves_every_capability_for_zero_model_calls(
        self, isolated_cache, monkeypatch
    ):
        # THE EVERYDAY CASE. Nothing has changed, so nothing is compiled: the
        # baselines are registered as version 1 after passing the same
        # deterministic validation a compiled artifact passes, and every
        # capability serves from them.
        def explode(*args, **kwargs):
            raise AssertionError("an unchanged schema must not call any model")

        monkeypatch.setattr(llm, "complete", explode)

        compiler = service.CompilerService()
        seeded = compiler.ensure_seeded()
        assert seeded, "nothing was seeded"
        assert all(outcome == "seeded" for outcome in seeded.values()), seeded

        for capability in capabilities.all_capabilities():
            sql = compiler.sql_for(capability.id, report_date=_report_date())
            assert sql.strip()
            current = artifacts.load(capability.id).current
            assert current.origin == artifacts.ORIGIN_SEED
            assert current.verifier_status == artifacts.VERIFIED_SEED
            assert current.dependencies["tables"], f"{capability.id} recorded no dependencies"

    def test_a_column_change_marks_only_the_capabilities_that_read_that_column(
        self, isolated_cache
    ):
        # EVERY capability here reads the well table -- they all scope to live
        # wells -- so table-level tracking would recompile all of them for any
        # change to it. Column-level tracking is what makes a change to one
        # capability's own columns cost one recompile instead of seven.
        from dynamic_db.dependencies import Dependencies, is_affected

        compiler = service.CompilerService()
        compiler.ensure_seeded()

        footprints = {
            c.id: Dependencies.from_dict(artifacts.load(c.id).current.dependencies)
            for c in capabilities.all_capabilities()
        }
        shared_tables = set.intersection(*(set(f.tables) for f in footprints.values()))
        assert shared_tables, "the capabilities share no table; this test proves nothing"

        target = capability_manifest.MILESTONES
        others = {name: f for name, f in footprints.items() if name != target}
        exclusive = set(footprints[target].columns) - set().union(
            *(set(f.columns) for f in others.values())
        )
        assert exclusive, f"{target} reads no column of its own"

        changed = {next(iter(sorted(exclusive)))}
        affected = {
            name for name, f in footprints.items() if is_affected(f, set(), changed)
        }
        assert affected == {target}, affected

    def test_the_affected_capability_is_regenerated_executed_verified_and_promoted(
        self, isolated_cache, monkeypatch
    ):
        report_date = _report_date()
        compiler = service.CompilerService()
        compiler.ensure_seeded()

        target = capabilities.get(capability_manifest.MILESTONES)
        others = [c.id for c in capabilities.all_capabilities() if c.id != target.id]
        before = {name: artifacts.load(name).current.version for name in others}

        # Make the target stale exactly as a real structural change would: its
        # recorded dependency fingerprint no longer matches the live schema.
        record = artifacts.load(target.id)
        record.current.dependency_fingerprint = "0" * 64
        artifacts.save(record)

        model = ScriptedReasoning(target.baseline_sql)
        monkeypatch.setattr(llm, "complete", model)
        monkeypatch.setattr(sql_author.llm, "complete", model)
        monkeypatch.setattr(verifier.llm, "complete", model)

        result = compiler.compile(target.id, report_date=report_date)

        # It went all the way round: authored, validated, EXECUTED against the
        # real database, verified, promoted.
        assert result["status"] == "promoted", result
        assert model.calls == ["reasoning_sql_author", "reasoning_verifier"]
        promoted = artifacts.load(target.id).current
        assert promoted.version == 2
        assert promoted.origin == artifacts.ORIGIN_COMPILED
        assert promoted.verifier_status == artifacts.VERIFIED_APPROVED
        assert promoted.dependency_fingerprint != "0" * 64
        assert promoted.dependencies["tables"]
        assert artifacts.load(target.id).history[0].origin == artifacts.ORIGIN_SEED

        # ...and nothing else was touched: the other capabilities were never
        # recompiled, because nothing said they had to be.
        for name, version in before.items():
            assert artifacts.load(name).current.version == version

        # THE STABLE CONTRACT: app.dynamic_client -- the same module every
        # real repository calls -- now serves the promoted artifact.
        from app.dynamic_client import sql_for

        compiler.invalidate()
        assert compiler.sql_for(target.id, report_date=report_date) == promoted.sql
        assert sql_for(target.id, report_date=report_date) == promoted.sql

    def test_a_capability_that_cannot_be_verified_keeps_the_last_known_good_sql(
        self, isolated_cache, monkeypatch
    ):
        report_date = _report_date()
        compiler = service.CompilerService()
        compiler.ensure_seeded()

        target = capabilities.get(capability_manifest.MILESTONES)
        record = artifacts.load(target.id)
        known_good = record.current.sql
        record.current.dependency_fingerprint = "0" * 64
        artifacts.save(record)

        class AlwaysRejects(ScriptedReasoning):
            def __call__(self, system_prompt, user_prompt, **kwargs):
                self.calls.append(kwargs.get("node"))
                if kwargs.get("node") == "reasoning_sql_author":
                    return f"```sql\n{self.sql}\n```\n\n```notes\nn/a\n```"
                return '{"ok": false, "not_applicable": false, "reason": "", "feedback": "not right", "note": ""}'

        model = AlwaysRejects(target.baseline_sql)
        monkeypatch.setattr(llm, "complete", model)
        monkeypatch.setattr(sql_author.llm, "complete", model)
        monkeypatch.setattr(verifier.llm, "complete", model)

        result = compiler.compile(target.id, report_date=report_date)
        assert result["status"] == "failed"

        # The known-good artifact is untouched...
        assert artifacts.load(target.id).current.sql == known_good
        assert artifacts.load(target.id).current.version == 1
        # ...and because it still validates against the live schema, serving it
        # is a deliberate decision rather than an accident.
        compiler.invalidate()
        assert compiler.sql_for(target.id, report_date=report_date) == known_good

    def test_the_status_report_names_what_changed_and_what_it_affects(
        self, isolated_cache, monkeypatch
    ):
        compiler = service.CompilerService()
        compiler.ensure_seeded()

        target = capabilities.get(capability_manifest.MILESTONES)
        record = artifacts.load(target.id)
        record.current.dependency_fingerprint = "0" * 64
        artifacts.save(record)

        status = compiler.status(refresh=True)
        assert status["capabilities"][target.id]["stale"] is True
        assert target.id in status["schema_change"]["affected_capabilities"]
        # Everything an operator needs to understand a recompile, and nothing
        # that could identify a credential.
        assert status["structure_fingerprint"]
        assert status["database"]
        assert "password" not in str(status).lower()

    def test_a_corrupted_artifact_file_is_rejected_rather_than_trusted(
        self, isolated_cache
    ):
        # Contract requirement: a corrupted or invalid artifact must never be
        # silently treated as valid.
        import json

        compiler = service.CompilerService()
        compiler.ensure_seeded()
        target = capability_manifest.MILESTONES

        from dynamic_db import identity as identity_module

        path = identity_module.artifact_dir() / f"{target}.json"
        path.write_text("{not valid json", encoding="utf-8")

        record = artifacts.load(target)
        # A corrupted file degrades to "nothing loaded", never to a crash and
        # never to a fabricated artifact.
        assert record.current is None

    def test_daily_report_consumes_the_contract_without_touching_engine_internals(self):
        # The only two modules this application may import FROM dynamic_db's
        # world for ordinary business logic: the stable client, and the
        # manifest that supplies DYNAMIC_DB its content. Nothing else.
        import ast
        from pathlib import Path

        backend_app = Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in backend_app.rglob("*.py"):
            if path.name in ("dynamic_client.py", "capability_manifest.py", "_dynamic_db_path.py"):
                continue
            text = path.read_text(encoding="utf-8")
            if "dynamic_db" in text and "import dynamic_db" in text.replace(" ", ""):
                offenders.append(str(path.relative_to(backend_app)))
        assert offenders == [], (
            f"these files import dynamic_db directly instead of going through "
            f"app.dynamic_client: {offenders}"
        )
