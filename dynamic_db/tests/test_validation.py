"""The deterministic gate: safety, schema and contract. No model is involved.

THESE ARE THE TESTS THAT MAKE THE READ-ONLY GUARANTEE A GUARANTEE. Everything
here is decided from the SQL text plus the live schema, so a model cannot argue
with any of it, and the promise that this application cannot modify the source
database does not depend on a model behaving.

The advisory checks are tested beside them, and the difference is deliberate: a
validation failure REJECTS, a schema-derived concern is EVIDENCE for the
reviewer to adjudicate.
"""

from __future__ import annotations

import pytest

from dynamic_db import sqlcheck, sqltext, validation
from tests import fixtures as fx


@pytest.fixture
def snap():
    return fx.snapshot()


@pytest.fixture
def cap():
    return fx.test_capability()


def problems(sql, cap, snap):
    return " | ".join(validation.validate(sql, cap, snap))


class TestSafety:
    def test_the_good_query_passes_cleanly(self, cap, snap):
        assert validation.validate(fx.GOOD_SQL, cap, snap) == []

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO ops.job_entry (row_id) VALUES (1)",
            "UPDATE ops.job_entry SET planned_qty = 0",
            "DELETE FROM ops.job_entry",
            "DROP TABLE ops.job_entry",
            "TRUNCATE TABLE ops.job_entry",
            "EXEC sp_who",
        ],
    )
    def test_a_write_or_execute_is_refused(self, sql, cap, snap):
        assert validation.safety_problems(sql), f"{sql!r} was not refused"

    def test_a_select_that_hides_a_write_in_a_second_statement_is_refused(self, cap, snap):
        sql = "SELECT 1 AS site_key; DROP TABLE ops.job_entry"
        assert "Multiple statements" in problems(sql, cap, snap)

    def test_a_keyword_inside_DATA_is_not_a_command(self, cap, snap):
        # A predicate matching the text 'update required' must not be rejected
        # as an UPDATE. Blanking literals before scanning is what makes this
        # hold, and getting it wrong burns a capability's whole budget.
        sql = fx.GOOD_SQL.replace(
            "WHERE j.run_on = ?", "WHERE j.run_on = ? AND j.job_code <> 'update required'"
        )
        assert validation.safety_problems(sql) == []

    def test_a_write_hidden_behind_a_quote_in_a_comment_is_still_seen(self):
        # Strip comments first and a quote inside one can swallow the statement
        # after it, hiding a second statement from every check while the server
        # still runs it.
        sql = "SELECT a AS site_key -- '\nDROP TABLE t -- '"
        assert validation.safety_problems(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM OPENROWSET('x','y','z')",
            "SELECT name FROM sys.sql_logins",
            "SELECT 1 WAITFOR DELAY '00:00:05'",
            "SELECT 1 AS a FROM ops.job_entry WHERE 1=1 EXEC xp_cmdshell 'dir'",
        ],
    )
    def test_reaching_outside_the_approved_objects_is_refused(self, sql):
        assert validation.safety_problems(sql)

    def test_credentials_in_the_text_are_refused(self, cap, snap):
        sql = fx.GOOD_SQL + " -- PWD=hunter2"
        # In a comment it is still text the query carries; nothing about a
        # credential belongs in a stored query, wherever it is written.
        assert "credential" in problems(sql.replace("--", ""), cap, snap).lower()

    def test_an_empty_query_is_refused(self, cap, snap):
        assert validation.safety_problems("   ")


class TestSchemaReferences:
    def test_a_table_outside_the_allowlist_is_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace(fx.PARENT_TABLE, "other.registry")
        assert "not an approved object" in problems(sql, cap, snap)

    def test_a_table_the_database_no_longer_has_is_refused(self, cap, snap):
        gone = fx.without_table(snap, fx.PARENT_TABLE)
        assert "does not exist" in problems(fx.GOOD_SQL, cap, gone)

    def test_a_column_the_table_does_not_have_is_refused(self, cap, snap):
        gone = fx.without_column(snap, fx.FACT_TABLE, "planned_qty")
        message = problems(fx.GOOD_SQL, cap, gone)
        assert "does not list" in message and "planned_qty" in message


class TestContract:
    def test_a_left_over_placeholder_is_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace("j.run_on = ?", "j.run_on = {{report_date}}")
        assert "Unresolved template token" in problems(sql, cap, snap)

    def test_a_missing_report_date_parameter_is_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace("WHERE j.run_on = ?", "WHERE j.run_on IS NOT NULL")
        message = problems(sql, cap, snap)
        assert "binds no parameter" in message and "report_date" in message

    def test_a_literal_date_where_the_parameter_belongs_is_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace("j.run_on = ?", "j.run_on = ? AND j.started_on >= '2026-08-01'")
        assert "literal date" in problems(sql, cap, snap)

    def test_too_many_parameters_are_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace("j.run_on = ?", "j.run_on = ? AND j.started_on <= ?")
        assert "2 ? marker" in problems(sql, cap, snap)

    def test_a_missing_required_output_alias_is_refused(self, cap, snap):
        sql = fx.GOOD_SQL.replace("AS job_total", "AS total_amount")
        message = problems(sql, cap, snap)
        assert "required output alias" in message and "job_total" in message

    def test_a_capability_that_must_resolve_grain_is_refused_without_it(self, cap, snap):
        sql = (
            f"SELECT TRY_CONVERT(int, j.site_ref) AS site_key, j.planned_qty AS job_total "
            f"FROM {fx.FACT_TABLE} AS j WHERE j.run_on = ?"
        )
        assert "no ranking, grouping or distinct count" in problems(sql, cap, snap)

    def test_a_capability_with_no_parameters_must_bind_none(self, snap):
        unparameterised = fx.test_capability(parameters=())
        assert "carries 1 ? marker" in problems(fx.GOOD_SQL, unparameterised, snap)


class TestExecutionContract:
    def test_a_single_row_capability_that_returns_many_is_refused(self):
        cap = fx.test_capability(single_row=True)
        found = validation.execution_problems(cap, 7, ["site_key", "job_total"])
        assert any("EXACTLY ONE row" in p for p in found)

    def test_a_single_row_capability_that_returns_none_is_refused(self):
        # Zero rows is not a clean result for a capability contracted to always
        # answer: it means nothing was measured.
        cap = fx.test_capability(single_row=True)
        assert validation.execution_problems(cap, 0, ["site_key", "job_total"])

    def test_an_alias_the_result_set_does_not_actually_carry_is_refused(self, cap):
        # The text can look right and the result set still not carry the name.
        found = validation.execution_problems(cap, 3, ["site_key"])
        assert any("job_total" in p for p in found)

    def test_a_correct_result_passes(self, cap):
        assert validation.execution_problems(cap, 3, ["site_key", "job_total"]) == []


class TestDeterministicConcerns:
    """Advisory, never fatal -- evidence the reviewer adjudicates."""

    def test_a_clean_query_raises_no_concern(self, cap, snap):
        assert sqlcheck.check(fx.GOOD_SQL, snap, cap) == []

    def test_reading_a_repeating_table_without_collapsing_it_is_flagged(self, cap, snap):
        sql = (
            f"SELECT TRY_CONVERT(int, j.site_ref) AS site_key, j.planned_qty AS job_total "
            f"FROM {fx.FACT_TABLE} AS j WHERE j.run_on = ?"
        )
        found = " ".join(sqlcheck.check(sql, snap, cap))
        assert "many rows per job_code" in found

    def test_comparing_a_measured_fraction_against_a_percentage_is_flagged(self, cap, snap):
        sql = fx.GOOD_SQL.replace("WHERE j.run_on = ?", "WHERE j.run_on = ? AND j.share_done > 50")
        found = " ".join(sqlcheck.check(sql, snap, cap))
        assert "0-1" in found and "silent" in found

    def test_joining_mismatched_declared_types_without_a_conversion_is_flagged(self, cap, snap):
        # varchar on one side, int on the other. SQL Server converts implicitly
        # and RAISES on the first value that will not convert -- months later,
        # on one bad row, taking the whole report down.
        sql = (
            f"SELECT s.site_id AS site_key, COUNT(*) AS job_total "
            f"FROM {fx.FACT_TABLE} AS j "
            f"JOIN {fx.PARENT_TABLE} AS s ON s.site_id = j.site_ref "
            f"WHERE j.run_on = ? GROUP BY s.site_id"
        )
        found = " ".join(sqlcheck.check(sql, snap, cap))
        assert "no conversion" in found

    def test_the_same_join_with_a_conversion_is_not_flagged(self, cap, snap):
        sql = (
            f"SELECT s.site_id AS site_key, COUNT(*) AS job_total "
            f"FROM {fx.FACT_TABLE} AS j "
            f"JOIN {fx.PARENT_TABLE} AS s ON s.site_id = TRY_CONVERT(int, j.site_ref) "
            f"WHERE j.run_on = ? GROUP BY s.site_id"
        )
        assert not any("no conversion" in c for c in sqlcheck.check(sql, snap, cap))
        assert not any("no conversion" in c for c in sqlcheck.check(fx.GOOD_SQL, snap, cap))

    def test_scoping_on_an_empty_table_is_flagged(self, cap, snap):
        empty = fx.with_row_counts(snap, {fx.PARENT_TABLE: 0})
        found = " ".join(sqlcheck.check(fx.GOOD_SQL, empty, cap))
        assert "0 rows" in found

    def test_partitioning_on_a_row_unique_key_is_flagged(self, cap, snap):
        # Partitioning by a surrogate row id leaves every row in its own group,
        # so the ranking collapses nothing while looking exactly like one that
        # does. Decidable, because the schema declares the key.
        sql = fx.GOOD_SQL.replace(
            "PARTITION BY TRY_CONVERT(int, j.site_ref), j.sched_ref, j.job_code",
            "PARTITION BY j.row_id",
        )
        found = " ".join(sqlcheck.check(sql, snap, cap))
        assert "unique per ROW" in found

    def test_the_checker_never_raises_on_unparseable_text(self, cap, snap):
        # Advisory evidence must never be able to break a compile, whatever it
        # is handed.
        assert isinstance(sqlcheck.check("((((", snap, cap), list)
        assert isinstance(sqlcheck.check("SELECT", snap, cap), list)

    def test_a_concern_is_not_raised_through_an_alias_shared_with_a_cte(self, cap, snap):
        # The checker resolves an alias only when it unambiguously names one
        # physical table. An alias a statement also uses for a CTE resolves to
        # nothing, so the reviewer is never sent to fix a column the query
        # never claimed belonged to that table.
        sql = (
            f"WITH s AS (SELECT 1 AS site_id) "
            f"SELECT s.site_id AS site_key, COUNT(*) AS job_total "
            f"FROM {fx.FACT_TABLE} AS j CROSS JOIN s "
            f"WHERE j.run_on = ? AND s.site_id = j.site_ref GROUP BY s.site_id"
        )
        assert not any("no conversion" in c for c in sqlcheck.check(sql, snap, cap))


class TestSqlText:
    def test_parameter_markers_ignore_comments_and_literals(self):
        sql = "SELECT '?' AS a -- ?\nFROM t WHERE x = ?"
        assert sqltext.parameter_markers(sql) == 1

    def test_a_documented_include_directive_is_not_an_unresolved_placeholder(self):
        # A file that explains the include directive in its own header comment
        # is not a file with a placeholder left in it.
        sql = "/* uses the {{include:...}} directive */\nSELECT 1 AS a"
        assert sqltext.placeholders(sql) == []
        assert sqltext.placeholders("SELECT {{x}} AS a") == ["{{x}}"]

    def test_bracketed_identifiers_survive_analysis(self):
        sql = "SELECT t.[PDO Well ID] AS a FROM ops.job_entry AS t"
        assert ("t", "pdo well id") in sqltext.qualified_column_references(sql)
