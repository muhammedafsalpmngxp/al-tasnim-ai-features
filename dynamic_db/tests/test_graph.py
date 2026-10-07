"""The compile graph: routing, budgets, promotion, and failing closed.

NO REAL MODEL AND NO REAL DATABASE. Every reasoning call is a scripted reply
and every execution is a scripted result, so these tests assert the ROUTE a
compile takes rather than the quality of one model's answer -- which is the
only part of this that is engineering rather than luck.

The properties under test are the ones a wrong edge would silently cost:

  * an unaffected capability spends NOTHING;
  * the validator runs before the database, always;
  * a rejection routes back to the author carrying the reason;
  * every loop is bounded;
  * an unreadable verdict is a rejection, never an approval;
  * a failed candidate never replaces a working artifact.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from dynamic_db import artifacts, capabilities, graph, llm, prompts, state
from dynamic_db.nodes import executor as executor_module
from dynamic_db.nodes import sql_author, verifier
from tests import fixtures as fx

REPORT_DATE = date(2026, 8, 4)


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class ScriptedModel:
    """Replies in order, recording exactly how each call was routed.

    Recording the ROUTING is the point: the tests that matter here are not
    "what did it say" but "which model system was asked, by which node".
    """

    def __init__(self, replies: List[str]) -> None:
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system": kwargs.get("system"),
                "node": kwargs.get("node"),
                "capability": kwargs.get("capability"),
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        if not self.replies:
            raise AssertionError("the graph asked for more model calls than were scripted")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def author_calls(self):
        return [c for c in self.calls if c["node"] == "reasoning_sql_author"]

    @property
    def verifier_calls(self):
        return [c for c in self.calls if c["node"] == "reasoning_verifier"]


def author_reply(sql: str = fx.GOOD_SQL, notes: str = "open site -> the site table") -> str:
    return f"```sql\n{sql}\n```\n\n```notes\n{notes}\n```"


def verdict(**fields) -> str:
    import json

    payload = {"ok": True, "not_applicable": False, "reason": "", "feedback": "", "note": ""}
    payload.update(fields)
    return json.dumps(payload)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every artifact this module writes goes to a throwaway directory.

    A test that promoted into the real cache would change what the running
    application serves, which is not something a test suite may do.
    """
    from dynamic_db.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "included_tables", list(fx.snapshot().included_tables))
    monkeypatch.setattr(settings, "reasoning_model", "test-reasoning-model")
    monkeypatch.setattr(settings, "reasoning_api_key", "test-key")
    capabilities.REGISTRY[fx.TEST_CAPABILITY_ID] = fx.test_capability()
    yield
    capabilities.REGISTRY.pop(fx.TEST_CAPABILITY_ID, None)


@pytest.fixture
def executed(monkeypatch):
    """A scripted execution, recording whether the database was reached at all."""
    record = {"calls": 0, "params": None, "raise": None, "rows": 3}

    def fake_run(sql, params, limit):
        record["calls"] += 1
        record["params"] = list(params)
        if record["raise"] is not None:
            raise record["raise"]
        columns = ["site_key", "job_total"]
        sample = [{"site_key": 1, "job_total": 5}][:limit]
        return columns, sample, record["rows"], 12.3

    monkeypatch.setattr(executor_module, "_run", fake_run)
    return record


def run_compile(model: ScriptedModel, monkeypatch, *, snapshot=None, **overrides):
    monkeypatch.setattr(llm, "complete", model)
    monkeypatch.setattr(sql_author.llm, "complete", model)
    monkeypatch.setattr(verifier.llm, "complete", model)
    initial = state.initial(
        run_id="test",
        capability=fx.TEST_CAPABILITY_ID,
        probe_params=[REPORT_DATE],
        snapshot=snapshot or fx.snapshot(),
        max_sql_retries=overrides.pop("max_sql_retries", 2),
        max_verify_retries=overrides.pop("max_verify_retries", 2),
    )
    initial.update(overrides)
    return graph.run(initial)


# --------------------------------------------------------------------------


class TestUnaffectedCapabilitiesCostNothing:
    def test_a_fresh_artifact_is_reused_without_a_single_model_call(self, executed, monkeypatch):
        # THE MOST IMPORTANT ECONOMIC PROPERTY IN THIS SYSTEM. On a normal day
        # nothing has changed, and a normal day must cost zero reasoning calls.
        snap = fx.snapshot()
        _seed_current(snap)
        model = ScriptedModel([])  # any call at all would raise
        final = run_compile(model, monkeypatch, snapshot=snap)
        assert final["status"] == state.STATUS_REUSED
        assert model.calls == []
        assert executed["calls"] == 0

    def test_a_change_to_an_unread_table_does_not_make_it_stale(self, executed, monkeypatch):
        snap = fx.snapshot()
        _seed_current(snap)
        # The lookup table is not read by this capability's SQL at all.
        moved = fx.with_column_type(snap, fx.LOOKUP_TABLE, "unit_label", "int")
        final = run_compile(ScriptedModel([]), monkeypatch, snapshot=moved)
        assert final["status"] == state.STATUS_REUSED

    def test_a_row_count_change_does_not_make_it_stale(self, executed, monkeypatch):
        snap = fx.snapshot()
        _seed_current(snap)
        loaded = fx.with_row_counts(snap, {fx.FACT_TABLE: 250_000})
        final = run_compile(ScriptedModel([]), monkeypatch, snapshot=loaded)
        assert final["status"] == state.STATUS_REUSED


    def test_an_explicit_force_recompiles_a_capability_that_is_not_stale(
        self, executed, monkeypatch
    ):
        # An operator asking for a recompile gets one. The deterministic check
        # still runs and is still logged, so a forced run cannot be mistaken
        # afterwards for a change nobody can find.
        snap = fx.snapshot()
        _seed_current(snap)
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        final = run_compile(model, monkeypatch, snapshot=snap, force=True)
        assert final["status"] == state.STATUS_PROMOTED
        assert len(model.author_calls) == 1
        assert artifacts.load(fx.TEST_CAPABILITY_ID).current.version == 2


class TestAffectedCapabilitiesRecompile:
    def test_a_retyped_column_it_reads_triggers_a_recompile_and_promotion(
        self, executed, monkeypatch
    ):
        snap = fx.snapshot()
        _seed_current(snap)
        moved = fx.with_column_type(snap, fx.FACT_TABLE, "planned_qty", "float")

        model = ScriptedModel([author_reply(), verdict(ok=True, note="correct")])
        final = run_compile(model, monkeypatch, snapshot=moved)

        assert final["status"] == state.STATUS_PROMOTED
        assert len(model.author_calls) == 1
        assert len(model.verifier_calls) == 1
        record = artifacts.load(fx.TEST_CAPABILITY_ID)
        assert record.current.version == 2
        assert record.current.origin == artifacts.ORIGIN_COMPILED
        assert record.current.verifier_status == artifacts.VERIFIED_APPROVED

    def test_the_author_is_told_what_changed(self, executed, monkeypatch):
        from dynamic_db import introspect

        snap = fx.snapshot()
        _seed_current(snap)
        introspect.store_previous_snapshot(snap)  # the baseline to compare against
        moved = fx.with_column_type(snap, fx.FACT_TABLE, "planned_qty", "float")

        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch, snapshot=moved)

        prompt = model.author_calls[0]["user_prompt"]
        assert "WHAT CHANGED IN THIS DATABASE" in prompt
        assert "planned_qty" in prompt
        assert "COLUMN_DATATYPE_CHANGED" in prompt

    def test_a_vanished_table_is_reported_with_candidates_not_a_decision(
        self, executed, monkeypatch
    ):
        from dynamic_db import introspect

        snap = fx.snapshot()
        _seed_current(snap)
        introspect.store_previous_snapshot(snap)
        gone = fx.without_table(snap, fx.PARENT_TABLE)

        model = ScriptedModel(
            [
                author_reply(fx.SQL_FACT_ONLY),
                verdict(
                    ok=False, not_applicable=True, reason="nothing records whether a site is open"
                ),
            ]
        )
        final = run_compile(model, monkeypatch, snapshot=gone)

        assert final["status"] == state.STATUS_NOT_APPLICABLE
        prompt = model.author_calls[0]["user_prompt"]
        # The lost table is named, a structurally similar replacement is offered
        # where one exists -- and the prompt says plainly that it is evidence
        # rather than an answer, because a shared column name proves nothing.
        assert "AN OBJECT THIS CAPABILITY USED TO READ HAS GONE" in prompt
        assert fx.PARENT_TABLE in prompt
        assert "EVIDENCE, not an answer" in prompt


class TestValidatorRoutesBeforeTheDatabase:
    def test_the_database_is_never_reached_by_an_invalid_candidate(self, executed, monkeypatch):
        snap = fx.snapshot()
        bad = "SELECT 1 AS site_key, 2 AS job_total"  # no parameter, no grain
        model = ScriptedModel(
            [author_reply(bad), author_reply(bad), author_reply(bad)]
        )
        final = run_compile(model, monkeypatch, snapshot=snap)
        assert final["status"] != state.STATUS_PROMOTED
        assert executed["calls"] == 0, "an invalid query reached the database"

    def test_a_validation_failure_routes_back_to_the_author_with_the_reason(
        self, executed, monkeypatch
    ):
        bad = fx.GOOD_SQL.replace("AS job_total", "AS wrong_name")
        model = ScriptedModel([author_reply(bad), author_reply(), verdict(ok=True)])
        final = run_compile(model, monkeypatch)
        assert final["status"] == state.STATUS_PROMOTED
        assert len(model.author_calls) == 2
        retry_prompt = model.author_calls[1]["user_prompt"]
        assert "FAILED THESE MECHANICAL CHECKS" in retry_prompt
        assert "job_total" in retry_prompt

    def test_a_database_refusal_routes_back_with_the_database_message(
        self, executed, monkeypatch
    ):
        import pyodbc

        calls = {"n": 0}
        original = executor_module._run

        def flaky(sql, params, limit):
            calls["n"] += 1
            if calls["n"] == 1:
                raise pyodbc.Error("22018", "Conversion failed converting varchar to int")
            return ["site_key", "job_total"], [{"site_key": 1, "job_total": 2}], 2, 5.0

        monkeypatch.setattr(executor_module, "_run", flaky)
        model = ScriptedModel([author_reply(), author_reply(), verdict(ok=True)])
        final = run_compile(model, monkeypatch)
        assert final["status"] == state.STATUS_PROMOTED
        assert "Conversion failed" in model.author_calls[1]["user_prompt"]

    def test_the_executor_binds_exactly_the_declared_parameters(self, executed, monkeypatch):
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        assert executed["params"] == [REPORT_DATE]


class TestVerifierGate:
    def test_an_approval_promotes(self, executed, monkeypatch):
        final = run_compile(
            ScriptedModel([author_reply(), verdict(ok=True, note="good")]), monkeypatch
        )
        assert final["status"] == state.STATUS_PROMOTED
        assert artifacts.load(fx.TEST_CAPABILITY_ID).current.verifier_note == "good"

    def test_a_rejection_routes_back_carrying_the_instruction(self, executed, monkeypatch):
        model = ScriptedModel(
            [
                author_reply(),
                verdict(ok=False, feedback="Filter to open sites only."),
                author_reply(),
                verdict(ok=True),
            ]
        )
        final = run_compile(model, monkeypatch)
        assert final["status"] == state.STATUS_PROMOTED
        assert "Filter to open sites only." in model.author_calls[1]["user_prompt"]

    def test_the_reviewer_is_shown_what_it_already_demanded(self, executed, monkeypatch):
        # A reviewer that cannot see its own previous verdict has no way to
        # notice it is reversing itself, and two attempts spent obeying
        # opposite instructions is a correct query recorded as a failure.
        model = ScriptedModel(
            [
                author_reply(),
                verdict(ok=False, feedback="Use the business key."),
                author_reply(),
                verdict(ok=True),
            ]
        )
        run_compile(model, monkeypatch)
        second_review = model.verifier_calls[1]["user_prompt"]
        assert "YOU ALREADY REJECTED" in second_review
        assert "Use the business key." in second_review

    def test_an_unreadable_verdict_is_a_rejection_not_an_approval(self, executed, monkeypatch):
        # FAILING CLOSED. Defaulting the other way lets a trailing comma turn a
        # rejection into an approval with nothing in the log to say so.
        model = ScriptedModel(
            [
                author_reply(),
                "sure, looks fine to me",
                author_reply(),
                "{ok: true,}",
                author_reply(),
                "still not json",
            ]
        )
        final = run_compile(model, monkeypatch)
        assert final["status"] != state.STATUS_PROMOTED
        assert artifacts.load(fx.TEST_CAPABILITY_ID).current is None

    def test_a_review_outage_is_a_rejection_not_an_approval(self, executed, monkeypatch):
        model = ScriptedModel(
            [
                author_reply(),
                llm.ModelUnavailable("the provider is down"),
                author_reply(),
                llm.ModelUnavailable("the provider is down"),
                author_reply(),
                llm.ModelUnavailable("the provider is down"),
            ]
        )
        final = run_compile(model, monkeypatch)
        assert final["status"] != state.STATUS_PROMOTED

    def test_a_rejection_with_no_instruction_still_gives_the_author_something(
        self, executed, monkeypatch
    ):
        model = ScriptedModel(
            [author_reply(), verdict(ok=False, feedback=""), author_reply(), verdict(ok=True)]
        )
        run_compile(model, monkeypatch)
        assert "without naming a fix" in model.author_calls[1]["user_prompt"]

    def test_not_applicable_is_recorded_rather_than_approved_or_rejected(
        self, executed, monkeypatch
    ):
        # The third verdict. Approving would store a query that answers nothing
        # while reading downstream as a clean result; rejecting would burn
        # every attempt asking for a fix no query can make.
        model = ScriptedModel(
            [
                author_reply(),
                verdict(ok=False, not_applicable=True, reason="nothing here records a site"),
            ]
        )
        final = run_compile(model, monkeypatch)
        assert final["status"] == state.STATUS_NOT_APPLICABLE
        current = artifacts.load(fx.TEST_CAPABILITY_ID).current
        assert current.verifier_status == artifacts.VERIFIED_NOT_APPLICABLE
        assert "nothing here records a site" in current.not_applicable_reason
        assert current.usable is False


class TestBudgets:
    def test_repeated_validation_failures_stop_at_the_budget(self, executed, monkeypatch):
        bad = "SELECT 1 AS site_key, 2 AS job_total"
        model = ScriptedModel([author_reply(bad)] * 10)
        final = run_compile(model, monkeypatch, max_sql_retries=2)
        # One first attempt plus two funded rewrites.
        assert len(model.author_calls) == 3
        assert final["status"] != state.STATUS_PROMOTED

    def test_repeated_review_rejections_stop_at_the_budget(self, executed, monkeypatch):
        replies = []
        for _ in range(10):
            replies += [author_reply(), verdict(ok=False, feedback="change it")]
        model = ScriptedModel(replies)
        final = run_compile(model, monkeypatch, max_verify_retries=2)
        assert len(model.verifier_calls) == 3
        assert final["status"] != state.STATUS_PROMOTED

    def test_a_reply_with_no_sql_block_costs_an_attempt_not_a_crash(self, executed, monkeypatch):
        model = ScriptedModel(["I would suggest joining the tables."] * 5)
        final = run_compile(model, monkeypatch, max_sql_retries=1)
        assert final["status"] != state.STATUS_PROMOTED
        assert len(model.author_calls) == 2

    def test_a_review_rejection_refunds_the_rewrite_budget(self, executed, monkeypatch):
        # A semantic rejection discards a query that was mechanically fine, so
        # the work has to be redone and must be funded -- otherwise a couple of
        # unrelated syntax slips earlier leave a correctly identified defect
        # with no attempts left to fix it.
        bad = fx.GOOD_SQL.replace("AS job_total", "AS wrong")
        model = ScriptedModel(
            [
                author_reply(bad),  # validation failure: spends one rewrite
                author_reply(),  # passes
                verdict(ok=False, feedback="wrong grain"),  # refunds the rewrites
                author_reply(bad),  # spends one again
                author_reply(),
                verdict(ok=True),
            ]
        )
        final = run_compile(model, monkeypatch, max_sql_retries=1, max_verify_retries=2)
        assert final["status"] == state.STATUS_PROMOTED


class TestPromotionSafety:
    def test_a_failed_candidate_never_replaces_a_working_artifact(self, executed, monkeypatch):
        snap = fx.snapshot()
        _seed_current(snap)
        before = artifacts.load(fx.TEST_CAPABILITY_ID).current
        moved = fx.with_column_type(snap, fx.FACT_TABLE, "planned_qty", "float")

        replies = []
        for _ in range(10):
            replies += [author_reply(), verdict(ok=False, feedback="no")]
        final = run_compile(ScriptedModel(replies), monkeypatch, snapshot=moved)

        after = artifacts.load(fx.TEST_CAPABILITY_ID).current
        assert final["status"] != state.STATUS_PROMOTED
        assert after.sql == before.sql
        assert after.version == before.version

    def test_the_superseded_artifact_is_kept_in_history(self, executed, monkeypatch):
        snap = fx.snapshot()
        _seed_current(snap)
        moved = fx.with_column_type(snap, fx.FACT_TABLE, "planned_qty", "float")
        run_compile(
            ScriptedModel([author_reply(), verdict(ok=True)]), monkeypatch, snapshot=moved
        )
        record = artifacts.load(fx.TEST_CAPABILITY_ID)
        assert record.history and record.history[0].origin == artifacts.ORIGIN_SEED

    def test_a_promoted_artifact_records_everything_needed_to_audit_it(
        self, executed, monkeypatch
    ):
        run_compile(ScriptedModel([author_reply(), verdict(ok=True)]), monkeypatch)
        current = artifacts.load(fx.TEST_CAPABILITY_ID).current
        assert current.capability_id == fx.TEST_CAPABILITY_ID
        assert current.dependency_fingerprint
        assert current.dependencies["tables"]
        assert current.validator_status == "passed"
        assert current.generated_at
        assert current.prompt_version == prompts.AUTHOR_PROMPT_VERSION
        assert current.compiler_version == prompts.COMPILER_VERSION
        assert current.mapping_notes


class TestModelRouting:
    def test_both_reasoning_nodes_use_the_reasoning_system(self, executed, monkeypatch):
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        assert [c["system"] for c in model.calls] == [llm.SYSTEM_REASONING] * 2
        assert [c["node"] for c in model.calls] == [
            "reasoning_sql_author",
            "reasoning_verifier",
        ]

    def test_no_compile_call_is_ever_routed_to_the_fast_system(self, executed, monkeypatch):
        # Silently downgrading a semantic compile produces SQL that runs and
        # measures the wrong thing, which nothing downstream can detect.
        model = ScriptedModel(
            [author_reply(), verdict(ok=False, feedback="again"), author_reply(), verdict(ok=True)]
        )
        run_compile(model, monkeypatch)
        # DYNAMIC_DB has exactly one model role, so there is no "fast" system
        # to have been routed to in the first place -- every call must be
        # SYSTEM_REASONING.
        assert {c["system"] for c in model.calls} == {llm.SYSTEM_REASONING}

    def test_every_call_is_labelled_with_the_capability_it_was_spent_on(
        self, executed, monkeypatch
    ):
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        assert {c["capability"] for c in model.calls} == {fx.TEST_CAPABILITY_ID}

    def test_the_author_and_the_reviewer_are_shown_an_identical_schema_block(
        self, executed, monkeypatch
    ):
        # Byte-identical, in the same position, so a provider can serve the
        # largest part of both prompts from its prefix cache.
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        author_head = model.author_calls[0]["user_prompt"].split("\n\n")[0]
        reviewer_head = model.verifier_calls[0]["user_prompt"].split("\n\n")[0]
        assert author_head == reviewer_head

    def test_the_reviewer_is_given_the_same_rules_the_author_had(self, executed, monkeypatch):
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        # Both system prompts are built from the SAME rule text -- the exact
        # rules_text() the fixture capability returns -- so a reviewer can
        # never reject correct work for a definition it alone was shown.
        rules_text = fx.test_capability().rules_text()
        assert rules_text in model.author_calls[0]["system_prompt"]
        assert rules_text in model.verifier_calls[0]["system_prompt"]

    def test_the_reviewer_is_never_shown_the_full_result(self, executed, monkeypatch):
        executed["rows"] = 5000
        model = ScriptedModel([author_reply(), verdict(ok=True)])
        run_compile(model, monkeypatch)
        prompt = model.verifier_calls[0]["user_prompt"]
        assert "rows_returned" in prompt
        assert "at most" in prompt and "never count" in prompt


class TestModelUnavailability:
    def test_an_author_outage_fails_the_compile_rather_than_inventing_sql(
        self, executed, monkeypatch
    ):
        model = ScriptedModel([llm.ModelUnavailable("provider down")])
        final = run_compile(model, monkeypatch)
        assert final["status"] == state.STATUS_FAILED
        assert "could not be reached" in final["failure_reason"]
        assert executed["calls"] == 0


def _seed_current(snapshot, sql: str = fx.GOOD_SQL) -> None:
    """Register ``sql`` as the capability's current, verified artifact."""
    from dynamic_db import dependencies as deps
    from dynamic_db import rules

    capability = capabilities.get(fx.TEST_CAPABILITY_ID)
    footprint = deps.extract(sql, snapshot)
    artifacts.promote(
        fx.TEST_CAPABILITY_ID,
        artifacts.Artifact(
            capability_id=fx.TEST_CAPABILITY_ID,
            sql=sql,
            origin=artifacts.ORIGIN_SEED,
            schema_fingerprint=snapshot.structure_fingerprint(),
            dependency_fingerprint=snapshot.dependency_fingerprint(
                footprint.tables, footprint.columns
            ),
            dependencies=footprint.as_dict(),
            validator_status="passed",
            verifier_status=artifacts.VERIFIED_SEED,
            prompt_version=prompts.AUTHOR_PROMPT_VERSION,
            compiler_version=prompts.COMPILER_VERSION,
            rules_version=rules.version(capability.rules_text()),
        ),
    )
