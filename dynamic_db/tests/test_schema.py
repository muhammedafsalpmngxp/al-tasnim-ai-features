"""Introspection, fingerprints, change detection and dependency tracking.

These are the tests that decide whether this system is genuinely schema-adaptive
or only claims to be. The claim rests on four properties, and each one has a
test here that fails if it is lost:

  * the schema is READ, not assumed;
  * a STRUCTURAL change moves the fingerprint that guards compiled SQL;
  * a DATA change does NOT, or every morning's load would trigger a full,
    reasoning-priced recompile;
  * a change reaches only the capabilities that actually read the changed
    object.
"""

from __future__ import annotations

import pytest

from dynamic_db import changes, dependencies, introspect
from tests.conftest import requires_database
from tests import fixtures as fx


class TestFingerprints:
    def test_structure_fingerprint_is_stable_for_an_unchanged_schema(self):
        assert fx.snapshot().structure_fingerprint() == fx.snapshot().structure_fingerprint()

    def test_adding_a_column_moves_the_structure_fingerprint(self):
        before = fx.snapshot()
        after = fx.with_extra_column(before, fx.FACT_TABLE, "new_flag")
        assert before.structure_fingerprint() != after.structure_fingerprint()

    def test_removing_a_column_moves_the_structure_fingerprint(self):
        before = fx.snapshot()
        after = fx.without_column(before, fx.FACT_TABLE, "planned_qty")
        assert before.structure_fingerprint() != after.structure_fingerprint()

    def test_retyping_a_column_moves_the_structure_fingerprint(self):
        before = fx.snapshot()
        after = fx.with_column_type(before, fx.FACT_TABLE, "site_ref", "int")
        assert before.structure_fingerprint() != after.structure_fingerprint()

    def test_row_counts_do_not_move_the_structure_fingerprint(self):
        # THE SINGLE MOST IMPORTANT TEST IN THIS FILE. A new day of data arrives
        # every morning. If it moved this fingerprint, every capability would be
        # recompiled daily at full reasoning-model cost -- which is exactly what
        # compiling once is supposed to avoid.
        before = fx.snapshot()
        after = fx.with_row_counts(before, {fx.FACT_TABLE: 999_999, fx.PARENT_TABLE: 801})
        assert before.structure_fingerprint() == after.structure_fingerprint()

    def test_row_counts_do_move_the_live_fingerprint(self):
        # The rendered description and the measured hints DO describe the data,
        # so they must be rebuilt when it changes. Two fingerprints, two jobs.
        before = fx.snapshot()
        after = fx.with_row_counts(before, {fx.FACT_TABLE: 999_999})
        assert before.live_fingerprint() != after.live_fingerprint()

    def test_a_different_database_with_an_identical_shape_fingerprints_differently(self):
        before = fx.snapshot()
        after = fx.snapshot(database="otherhost:1433/OtherDb")
        assert before.structure_fingerprint() != after.structure_fingerprint()

    def test_the_dependency_fingerprint_only_covers_what_was_named(self):
        snap = fx.snapshot()
        reading_fact = snap.dependency_fingerprint(
            [fx.FACT_TABLE], [(fx.FACT_TABLE, "planned_qty")]
        )
        changed_elsewhere = fx.with_column_type(snap, fx.LOOKUP_TABLE, "unit_label", "int")
        assert (
            changed_elsewhere.dependency_fingerprint(
                [fx.FACT_TABLE], [(fx.FACT_TABLE, "planned_qty")]
            )
            == reading_fact
        )

    def test_a_vanished_table_moves_the_dependency_fingerprint(self):
        # A disappearance must MOVE the hash rather than being invisible, or a
        # capability reading a dropped table would look perfectly fresh.
        snap = fx.snapshot()
        before = snap.dependency_fingerprint([fx.LOOKUP_TABLE], [])
        after = fx.without_table(snap, fx.LOOKUP_TABLE).dependency_fingerprint(
            [fx.LOOKUP_TABLE], []
        )
        assert before != after


class TestChangeDetection:
    def test_first_run_reports_no_change_rather_than_everything_changed(self):
        report = changes.compare(None, fx.snapshot())
        assert report.first_run is True
        assert report.changed is False
        assert report.changes == []

    def test_an_unchanged_schema_reports_no_change(self):
        report = changes.compare(fx.snapshot(), fx.snapshot())
        assert report.changed is False

    def test_a_retyped_column_is_reported_with_before_and_after(self):
        before = fx.snapshot()
        after = fx.with_column_type(before, fx.FACT_TABLE, "site_ref", "int")
        report = changes.compare(before, after)
        assert report.changed is True
        entry = next(c for c in report.changes if c.type == changes.COLUMN_DATATYPE_CHANGED)
        assert entry.object.lower().endswith("site_ref")
        assert "varchar" in (entry.before or "")
        assert "int" == (entry.after or "")

    def test_a_dropped_table_is_reported(self):
        report = changes.compare(fx.snapshot(), fx.without_table(fx.snapshot(), fx.LOOKUP_TABLE))
        assert any(c.type == changes.TABLE_REMOVED for c in report.changes)

    def test_an_added_column_is_reported_but_is_not_breaking(self):
        # Adding a column cannot make working SQL stop working, so it must not
        # cost a recompile -- it is reported, and excluded from the set the
        # staleness decision intersects against.
        before = fx.snapshot()
        after = fx.with_extra_column(before, fx.FACT_TABLE, "new_flag")
        report = changes.compare(before, after)
        assert any(c.type == changes.COLUMN_ADDED for c in report.changes)
        assert report.breaking_changes() == []
        tables, columns = changes.changed_objects(report)
        assert not tables and not columns

    def test_row_counts_alone_produce_no_change_at_all(self):
        report = changes.compare(
            fx.snapshot(), fx.with_row_counts(fx.snapshot(), {fx.FACT_TABLE: 500_000})
        )
        assert report.changed is False

    def test_a_column_change_does_not_implicate_the_whole_table(self):
        # Per-column dependency tracking only pays off if a column change stays
        # a column change. Promoting it to a table change would recompile every
        # capability that reads any part of that table.
        before = fx.snapshot()
        after = fx.with_column_type(before, fx.FACT_TABLE, "share_done", "float")
        tables, columns = changes.changed_objects(changes.compare(before, after))
        assert tables == set()
        assert (fx.FACT_TABLE, "share_done") in columns

    def test_replacement_candidates_are_offered_but_never_asserted(self):
        snap = fx.snapshot()
        lost = fx.without_table(snap, fx.LOOKUP_TABLE)
        # Nothing in the remaining schema shares the lost table's columns, so
        # no candidate is invented.
        assert changes.replacement_candidates(fx.LOOKUP_TABLE, lost, ["unit_id", "unit_label"]) == []
        # A table that genuinely shares them is offered, as EVIDENCE: the text
        # states the overlap and stops there.
        clone = fx.snapshot()
        replacement = fx.table(
            "ref.unit_v2",
            [fx.column("unit_id", "int"), fx.column("unit_label", "varchar", length=20)],
            approved=False,
        )
        clone.tables[replacement.key()] = replacement
        clone.tables.pop(fx.LOOKUP_TABLE.lower())
        found = changes.replacement_candidates(fx.LOOKUP_TABLE, clone, ["unit_id", "unit_label"])
        assert found and "ref.unit_v2" in found[0]
        assert "shares" in found[0]


class TestDependencyExtraction:
    def test_tables_and_columns_are_extracted_from_the_sql_itself(self):
        footprint = dependencies.extract(fx.GOOD_SQL, fx.snapshot())
        assert set(footprint.tables) == {fx.FACT_TABLE, fx.PARENT_TABLE}
        assert (fx.FACT_TABLE, "planned_qty") in footprint.columns
        assert (fx.PARENT_TABLE, "closed_on") in footprint.columns
        # The lookup table is never read by this query, so it must not appear --
        # that is what keeps a change to it from recompiling this capability.
        assert fx.LOOKUP_TABLE not in footprint.tables

    def test_a_missing_table_is_recorded_rather_than_ignored(self):
        footprint = dependencies.extract(
            fx.GOOD_SQL, fx.without_table(fx.snapshot(), fx.PARENT_TABLE)
        )
        assert fx.PARENT_TABLE in footprint.missing_tables
        assert footprint.complete is False

    def test_a_missing_column_is_recorded(self):
        footprint = dependencies.extract(
            fx.GOOD_SQL, fx.without_column(fx.snapshot(), fx.FACT_TABLE, "planned_qty")
        )
        assert (fx.FACT_TABLE, "planned_qty") in footprint.missing_columns
        assert footprint.complete is False

    def test_an_alias_reused_for_a_cte_never_manufactures_a_missing_column(self):
        # Observed on the real baseline SQL: one statement used the same short
        # alias for a reference table in one CTE and for a different CTE lower
        # down. Resolving every reference through the physical table reported a
        # list of columns as missing that the query never claimed were there.
        sql = f"""
        WITH rc AS (SELECT 1 AS made_up_total)
        SELECT rc.made_up_total AS site_key
        FROM rc
        CROSS JOIN (SELECT u.unit_id FROM {fx.LOOKUP_TABLE} AS rc2) AS x
        """
        footprint = dependencies.extract(sql, fx.snapshot())
        assert footprint.missing_columns == []

    def test_affectedness_is_decided_per_column(self):
        footprint = dependencies.extract(fx.GOOD_SQL, fx.snapshot())
        assert dependencies.is_affected(footprint, set(), {(fx.FACT_TABLE, "planned_qty")}) is True
        # A column this query never reads
        assert dependencies.is_affected(footprint, set(), {(fx.FACT_TABLE, "share_done")}) is False
        # A table it never reads
        assert dependencies.is_affected(footprint, {fx.LOOKUP_TABLE}, set()) is False

    def test_an_incomplete_footprint_is_always_affected(self):
        footprint = dependencies.extract(
            fx.GOOD_SQL, fx.without_table(fx.snapshot(), fx.PARENT_TABLE)
        )
        assert dependencies.is_affected(footprint, set(), set()) is True

    def test_unknown_tables_are_reported_against_the_allowlist(self):
        sql = "SELECT x.a AS site_key FROM other.thing AS x"
        assert dependencies.unknown_tables(sql, [fx.FACT_TABLE]) == ["other.thing"]

    def test_an_empty_allowlist_never_rejects_on_its_own(self):
        # Having nothing to check against must not be the reason a correct
        # query is refused; the empty allowlist itself is what gets reported.
        assert dependencies.unknown_tables(fx.GOOD_SQL, []) == []


class TestIncludedTables:
    def test_only_allowlisted_tables_are_approved_and_rendered(self):
        snap = fx.snapshot()
        outsider = fx.table("ops.secret_ledger", [fx.column("id")], approved=False)
        snap.tables[outsider.key()] = outsider
        assert outsider.key() not in snap.approved_names()
        rendered = introspect.render_schema(snap)
        assert "secret_ledger" not in rendered
        assert fx.FACT_TABLE in rendered

    def test_a_missing_approved_table_renders_as_explicitly_absent(self):
        snap = fx.without_table(fx.snapshot(), fx.LOOKUP_TABLE)
        rendered = introspect.render_schema(snap, [fx.LOOKUP_TABLE])
        assert "NOT PRESENT" in rendered

    def test_the_rendered_block_carries_grain_and_scale_measurements(self):
        rendered = introspect.render_schema(fx.snapshot())
        assert "MANY ROWS PER job_code" in rendered
        assert "MEASURED ROW MULTIPLICITY" in rendered
        assert "FRACTION_1" in rendered

    def test_a_reserved_or_awkward_column_name_is_rendered_bracketed(self):
        # Unbracketed, SQL Server reports a misleading SYNTAX error rather than
        # a missing column, so the author has to be shown the quoted form.
        snap = fx.snapshot()
        snap.get(fx.FACT_TABLE).columns["plan"] = fx.column("plan", "nvarchar", length=100)
        assert "[plan]" in introspect.render_schema(snap)

    def test_the_block_is_byte_identical_when_built_twice(self):
        # The author and the verifier are shown the same block so a provider can
        # prefix-cache it. That only works if it is built identically.
        snap = fx.snapshot()
        assert introspect.render_schema(snap) == introspect.render_schema(snap)


class TestLiveIntrospection:
    @requires_database
    def test_the_live_schema_is_read_not_assumed(self):
        snap = introspect.introspect(use_cache=False)
        assert snap.approved_tables(), "the allowlist resolved to no live table"
        for fact in snap.approved_tables():
            assert fact.columns, f"{fact.name} was discovered with no columns"

    @requires_database
    def test_every_allowlisted_table_is_present_or_visibly_absent(self):
        from dynamic_db.config import get_settings

        snap = introspect.introspect()
        for name in get_settings().included_tables:
            fact = snap.get(name)
            if fact is None:
                pytest.fail(f"{name} is allowlisted but not present in the database")

    @requires_database
    def test_the_measured_profile_reports_exact_totals(self):
        # An APPROXIMATE catalogue count compared against an EXACT distinct
        # count reports a unique key as repeating. The measurement counts both
        # exactly, in one pass, so this holds.
        snap = introspect.introspect()
        for fact in snap.approved_tables():
            for value in fact.key_multiplicity.values():
                if "distinct of" not in value:
                    continue
                distinct, total = value.split(" distinct of ")
                assert int(distinct.replace(",", "")) <= int(total.replace(",", ""))
