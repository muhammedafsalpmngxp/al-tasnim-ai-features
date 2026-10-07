"""Serving, seeding, model routing, and the standing audit.

These cover the parts of the generic engine that are easy to state and easy
to lose quietly:

  * no physical name has crept into a generic prompt or into the graph;
  * a compile call is labelled at the call site;
  * DYNAMIC_DB has exactly one model role, and no silent fallback;
  * a capability marked not-applicable is refused, never silently answered.

Business-rule regression tests and the shared expense tracker's own tests
live in the DOWNSTREAM APPLICATION's test suite (e.g. Daily Report's
``backend/tests/test_dynamic_db_contract.py``), because this engine has no
concept of any particular application's business rules or expense workbook.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict

import pytest

from dynamic_db import artifacts, audit, capabilities, llm, llm_config, prompts, service
from tests import fixtures as fx

REPORT_DATE = date(2026, 8, 4)


class TestPromptsCarryNoPhysicalKnowledge:
    def test_no_prompt_names_any_approved_object(self):
        from dynamic_db.config import get_settings

        text = (
            prompts.author_system("")
            + prompts.verifier_system("")
            + prompts.PLATFORM_PREAMBLE
            + prompts.CAPABILITY_CONTRACT
            + prompts.TSQL_KNOWLEDGE
            + prompts.MEASUREMENT_RULES
            + prompts.READING_THE_RULES
        ).lower()
        for qualified in get_settings().included_tables:
            assert qualified not in text
            table = qualified.split(".", 1)[-1]
            if len(table) > 4:
                assert table not in text, f"{table} appears in a generic prompt"

    def test_the_prompts_tell_the_author_to_resolve_concepts_not_copy_names(self):
        text = prompts.author_system("")
        assert "resolve" in text.lower()
        assert "HISTORY, not as a guarantee" in text

    def test_the_prompts_forbid_inventing_a_definition(self):
        text = prompts.author_system("") + prompts.verifier_system("")
        assert "NOT YET DEFINED" in text
        assert "not_applicable" in text

    def test_the_rule_slot_is_always_filled(self):
        for text in (prompts.author_system("R"), prompts.verifier_system("R")):
            assert "<<<" not in text
            assert "R" in text

    def test_an_empty_rule_selection_is_stated_rather_than_left_blank(self):
        assert "(no rules were supplied)" in prompts.author_system("")

    def test_the_engine_has_no_concept_of_which_or_how_many_rule_documents_exist(self):
        # The whole point of collapsing business/daily rules into one generic
        # slot: this engine's prompt layer takes a single opaque string,
        # never a count or a list of documents.
        import inspect as pyinspect

        assert list(pyinspect.signature(prompts.author_system).parameters) == ["rules_text"]
        assert list(pyinspect.signature(prompts.verifier_system).parameters) == ["rules_text"]


class TestStaticAudit:
    def test_no_physical_name_has_crept_into_this_engines_own_code(self):
        findings = audit.run()
        blocking = audit.blocking(findings)
        assert not blocking, "\n".join(str(f) for f in blocking)

    def test_the_audit_would_actually_catch_one(self):
        # An audit nobody has seen fail is an audit nobody should trust.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dynamic_db").mkdir()
            offender = root / "dynamic_db" / "leaky_prompt.py"
            offender.write_text(
                'PROMPT = "Resolve the daily task concept against ops.job_entry."\n',
                encoding="utf-8",
            )
            findings = audit.run(root=root, extra_names=[fx.FACT_TABLE])
            assert audit.blocking(findings)

    def test_a_field_name_that_merely_contains_a_table_name_is_not_a_finding(self):
        # Substring matching reported dozens of application field names as
        # database references. Whole-token matching is what makes the audit
        # readable enough to act on.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            innocent = root / "model.py"
            innocent.write_text("job_entry_id = 1\nunit_label_text = 'x'\n", encoding="utf-8")
            findings = audit.run(root=root, extra_names=[fx.FACT_TABLE, "job_entry"])
            assert audit.blocking(findings) == []

    def test_a_caller_auditing_its_own_tree_can_pass_its_own_zones(self):
        # A downstream application auditing ITS own source (its manifest
        # module) has its own strict zone and its own allowed paths -- this
        # engine's defaults must never be hardcoded into that call.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "myapp").mkdir()
            (root / "myapp" / "prompts.py").write_text(
                f'X = "{fx.FACT_TABLE}"\n', encoding="utf-8"
            )
            findings = audit.run(
                root=root,
                extra_names=[fx.FACT_TABLE],
                allowed_paths=(),
                strict_zone="myapp/",
            )
            assert audit.blocking(findings)


class TestModelRouting:
    def test_dynamic_db_has_exactly_one_model_role(self):
        assert not hasattr(llm, "SYSTEM_FAST")
        assert llm.SYSTEM_REASONING == "reasoning"

    def test_an_unconfigured_reasoning_system_refuses_rather_than_guessing(self, monkeypatch):
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "reasoning_api_key", "")
        with pytest.raises(llm.ModelUnavailable):
            llm.complete("s", "u", system=llm.SYSTEM_REASONING, node="reasoning_sql_author")

    def test_the_fallback_switch_is_off_by_default(self):
        from dynamic_db.config import get_settings

        assert get_settings().allow_reasoning_fallback is False

    def test_an_unknown_system_is_a_programming_error_not_a_default(self):
        with pytest.raises(ValueError):
            llm.route("whatever")

    def test_a_verdict_can_be_read_out_of_a_fenced_or_prefaced_reply(self):
        assert llm.extract_json('```json\n{"ok": true}\n```') == {"ok": True}
        assert llm.extract_json('Sure: {"ok": false, "feedback": "x"}')["ok"] is False

    def test_an_unreadable_reply_is_None_and_not_an_empty_verdict(self):
        # None is distinguishable from {}. Every caller fails closed on it.
        assert llm.extract_json("not json at all") is None
        assert llm.extract_json("") is None

    def test_a_compile_call_is_labelled_at_the_call_site(self, monkeypatch):
        recorded: Dict[str, Any] = {}
        llm.set_usage_sink(lambda **kwargs: recorded.update(kwargs))
        try:
            class FakeResponse:
                status_code = 200

                def json(self):
                    return {
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                    }

            monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse())
            from dynamic_db.config import get_settings

            settings = get_settings()
            monkeypatch.setattr(settings, "reasoning_api_key", "k")
            monkeypatch.setattr(settings, "reasoning_model", "m")
            llm.complete(
                "s", "u", system=llm.SYSTEM_REASONING, node="reasoning_verifier", capability="X"
            )
        finally:
            llm.set_usage_sink(None)
        assert recorded["system"] == llm.SYSTEM_REASONING
        assert recorded["node"] == "reasoning_verifier"
        assert recorded["capability"] == "X"

    def test_a_sink_that_raises_never_breaks_a_call(self, monkeypatch):
        def exploding(**kwargs):
            raise RuntimeError("the sink is broken")

        llm.set_usage_sink(exploding)
        try:
            class FakeResponse:
                status_code = 200

                def json(self):
                    return {
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }

            monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse())
            from dynamic_db.config import get_settings

            settings = get_settings()
            monkeypatch.setattr(settings, "reasoning_api_key", "k")
            monkeypatch.setattr(settings, "reasoning_model", "m")
            text = llm.complete("s", "u", system=llm.SYSTEM_REASONING, node="reasoning_verifier")
            assert text == "ok"
        finally:
            llm.set_usage_sink(None)


class TestLlmConfig:
    def test_the_configured_project_model_never_silently_changes_its_request_shape(self):
        # A custom/internal model alias must never be guessed into a family --
        # guessing wrong would silently change what is actually sent to a
        # real, currently-working configuration.
        assert llm_config.classify_model("gpt-5.6-luna") == llm_config.FAMILY_UNKNOWN
        assert llm_config.wants_temperature("gpt-5.6-luna") is True
        extras = llm_config.payload_extras("gpt-5.6-luna", max_tokens=4000, json_object=False)
        assert extras == {"max_tokens": 4000}

    def test_a_real_reasoning_model_never_receives_temperature_or_penalties(self):
        assert llm_config.classify_model("o3-mini") == llm_config.FAMILY_REASONING
        assert llm_config.wants_temperature("o3-mini") is False
        extras = llm_config.payload_extras("o3-mini", max_tokens=2000, json_object=False)
        assert extras == {"max_completion_tokens": 2000}
        assert "presence_penalty" not in extras and "temperature" not in extras

    def test_a_real_chat_model_gets_the_zero_defaulted_penalty_terms(self):
        assert llm_config.classify_model("gpt-4o-mini") == llm_config.FAMILY_CHAT
        extras = llm_config.payload_extras("gpt-4o-mini", max_tokens=900, json_object=True)
        assert extras["presence_penalty"] == 0.0
        assert extras["frequency_penalty"] == 0.0
        assert extras["max_tokens"] == 900
        assert extras["response_format"] == {"type": "json_object"}

    def test_no_parameter_is_reported_as_supported_for_an_unknown_model(self):
        rows = llm_config.resolve("some-custom-alias")
        assert all(r.supported == "unknown" for r in rows)

    def test_a_parameter_that_cannot_legally_be_zero_reports_the_minimum_instead(self):
        n_spec = next(p for p in llm_config.PARAM_CATALOG if p.name == "n")
        assert n_spec.legal_zero is False
        assert "1" in n_spec.zero_meaning or "minimum" in n_spec.zero_meaning.lower()

    def test_seed_is_never_defaulted_to_zero(self):
        # Unlike temperature, 0 is not a neutral/disabled value for a seed --
        # it is just an arbitrary one. Zero-defaulting it would fabricate a
        # specific deterministic seed nobody asked for.
        seed_spec = next(p for p in llm_config.PARAM_CATALOG if p.name == "seed")
        assert seed_spec.sent_by_this_client is False

    def test_explain_never_invents_a_parameter_the_catalog_does_not_document(self):
        rows = llm_config.explain("gpt-4o-mini", max_tokens=900)
        names = {r["parameter"] for r in rows}
        assert names == {p.name for p in llm_config.PARAM_CATALOG}


class TestServing:
    def test_mode_off_serves_the_registered_baseline_and_never_compiles(self, monkeypatch):
        from dynamic_db.config import get_settings

        capabilities.register(fx.test_capability())
        try:
            settings = get_settings()
            monkeypatch.setattr(settings, "dynamic_sql_mode", service.MODE_OFF)
            compiler = service.CompilerService()

            def explode(*args, **kwargs):
                raise AssertionError("mode=off must never read the schema or compile")

            monkeypatch.setattr(compiler, "_snapshot_now", explode)
            sql = compiler.sql_for(fx.TEST_CAPABILITY_ID, report_date=REPORT_DATE)
            assert sql == fx.GOOD_SQL
        finally:
            capabilities.unregister(fx.TEST_CAPABILITY_ID)

    def test_a_capability_with_no_baseline_is_refused_rather_than_answered(self, monkeypatch):
        from dynamic_db.config import get_settings

        capabilities.register(fx.test_capability(baseline_sql=""))
        try:
            settings = get_settings()
            monkeypatch.setattr(settings, "dynamic_sql_mode", service.MODE_OFF)
            compiler = service.CompilerService()
            with pytest.raises(service.CapabilityUnavailable):
                compiler.sql_for(fx.TEST_CAPABILITY_ID, report_date=REPORT_DATE)
        finally:
            capabilities.unregister(fx.TEST_CAPABILITY_ID)

    def test_a_not_applicable_capability_is_refused_rather_than_answered(
        self, tmp_path, monkeypatch
    ):
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
        capabilities.register(fx.test_capability())
        try:
            artifacts.mark_not_applicable(
                fx.TEST_CAPABILITY_ID,
                "this database does not record the concept",
                artifacts.Artifact(
                    capability_id=fx.TEST_CAPABILITY_ID, sql="", origin=artifacts.ORIGIN_COMPILED
                ),
            )
            compiler = service.CompilerService()
            monkeypatch.setattr(compiler, "_snapshot_now", lambda **k: fx.snapshot())
            monkeypatch.setattr(compiler, "ensure_seeded", lambda snapshot=None: {})
            with pytest.raises(service.CapabilityUnavailable) as exc:
                compiler.sql_for(fx.TEST_CAPABILITY_ID, report_date=REPORT_DATE)
            assert "not applicable" in str(exc.value)
        finally:
            capabilities.unregister(fx.TEST_CAPABILITY_ID)

    def test_a_baseline_that_does_not_fit_the_schema_is_not_seeded_as_verified(
        self, tmp_path, monkeypatch
    ):
        # The seed path runs the SAME validator a compiled artifact runs.
        # Registering a baseline that does not fit would defeat the gate on
        # the very first request.
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
        capabilities.register(fx.test_capability(baseline_sql="SELECT 1 AS not_a_real_column"))
        try:
            compiler = service.CompilerService()
            results = compiler.ensure_seeded(fx.snapshot())
            assert "rejected" in results[fx.TEST_CAPABILITY_ID]
            assert artifacts.load(fx.TEST_CAPABILITY_ID).current is None
        finally:
            capabilities.unregister(fx.TEST_CAPABILITY_ID)

    def test_status_reports_staleness_without_calling_any_model(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
        capabilities.register(fx.test_capability())
        try:
            compiler = service.CompilerService()
            monkeypatch.setattr(compiler, "_snapshot_now", lambda **k: fx.snapshot())

            def explode(*args, **kwargs):
                raise AssertionError("status must never call a model")

            monkeypatch.setattr(llm, "complete", explode)
            status = compiler.status()
            assert fx.TEST_CAPABILITY_ID in status["capabilities"]
            assert status["capabilities"][fx.TEST_CAPABILITY_ID]["stale"] is True
        finally:
            capabilities.unregister(fx.TEST_CAPABILITY_ID)

    def test_status_never_returns_a_secret(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
        compiler = service.CompilerService()
        monkeypatch.setattr(compiler, "_snapshot_now", lambda **k: fx.snapshot())
        blob = json.dumps(compiler.status(), default=str).lower()
        for secret in (settings.db_password or "", settings.reasoning_api_key or ""):
            if secret:
                assert secret.lower() not in blob
        assert "pwd=" not in blob and "password" not in blob
