"""Excel export, built entirely in the backend.

React never assembles the business dataset -- it asks for the file. The export
is a pure read: it writes to an in-memory buffer and never back to SQL Server.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal
from typing import Any, List, Optional, Sequence

import xlsxwriter

from app.models.daily import DailyDataset, DailyTask, QuantityStatus
from app.services.grouping_service import GroupingService

logger = logging.getLogger(__name__)

DETAIL_COLUMNS: Sequence[tuple] = (
    ("UOM", 12),
    ("WBS", 38),
    ("Activity Code", 18),
    ("Activity Description", 44),
    ("Well ID", 10),
    ("Date", 12),
    ("Task Code", 20),
    ("Progress", 10),
    ("Planned Quantity", 17),
    ("Actual Quantity", 17),
    ("Quantity Status", 16),
    ("Crew Code", 12),
    ("Daily Completed", 15),
    ("PH Name", 20),
    ("Mapping Status", 20),
    ("Data Quality Flags", 34),
    # Reference value from the activity master (dbo.mapping_master.UOM), not
    # authoritative -- UOM above is the only unit used for grouping/quantities.
    ("Activity Master UOM", 16),
)


class ExportService:
    """Produces the daily morning brief workbook."""

    def __init__(self, grouping_service: Optional[GroupingService] = None) -> None:
        self._grouping = grouping_service or GroupingService()

    @staticmethod
    def filename(report_date: date) -> str:
        return f"daily_morning_brief_{report_date.isoformat()}.xlsx"

    def build_workbook(self, dataset: DailyDataset) -> bytes:
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True, "default_date_format": "yyyy-mm-dd"})
        try:
            formats = self._formats(workbook)
            self._write_detail_sheet(workbook, formats, dataset)
            self._write_summary_sheet(workbook, formats, dataset)
            self._write_data_quality_sheet(workbook, formats, dataset)
        finally:
            workbook.close()
        data = buffer.getvalue()
        logger.info(
            "excel generated for %s: %d task row(s), %d bytes",
            dataset.report_date,
            len(dataset.tasks),
            len(data),
        )
        return data

    # ------------------------------------------------------------------
    @staticmethod
    def _formats(workbook: xlsxwriter.Workbook) -> dict:
        return {
            "title": workbook.add_format({"bold": True, "font_size": 14}),
            "note": workbook.add_format({"font_size": 9, "italic": True, "font_color": "#555555"}),
            "header": workbook.add_format(
                {
                    "bold": True,
                    "bg_color": "#1F3A5F",
                    "font_color": "#FFFFFF",
                    "border": 1,
                    "text_wrap": True,
                    "valign": "vcenter",
                }
            ),
            "text": workbook.add_format({"border": 1, "valign": "top"}),
            "number": workbook.add_format({"border": 1, "num_format": "#,##0.####"}),
            "integer": workbook.add_format({"border": 1, "num_format": "0"}),
            "date": workbook.add_format({"border": 1, "num_format": "yyyy-mm-dd"}),
            "group": workbook.add_format({"bold": True, "bg_color": "#EDF2F8", "border": 1}),
        }

    def _write_detail_sheet(self, workbook, formats, dataset: DailyDataset) -> None:
        sheet = workbook.add_worksheet("Daily Detail")
        sheet.write(0, 0, f"Daily Morning Brief - Detail - {dataset.report_date.isoformat()}", formats["title"])
        sheet.write(
            1,
            0,
            "One row per logical daily task for live wells. Quantities are shown in their own "
            "unit of measure; no conversion between units is applied.",
            formats["note"],
        )

        header_row = 3
        for index, (name, width) in enumerate(DETAIL_COLUMNS):
            sheet.write(header_row, index, name, formats["header"])
            sheet.set_column(index, index, width)

        # Ordered Status -> WBS -> Activity -> Well, matching the dashboard.
        # Status sorts by QuantityStatus definition order, not alphabetically,
        # so the sheet reads in the same sequence as the summary sections.
        status_order = {member.value: index for index, member in enumerate(QuantityStatus)}
        ordered = sorted(
            dataset.tasks,
            key=lambda t: (
                status_order[t.quantity_status.value],
                t.wbs is None,
                t.wbs or "",
                t.activity_code or "",
                t.well_id,
                t.task_code or "",
            ),
        )

        row = header_row + 1
        for task in ordered:
            self._write_task_row(sheet, formats, row, task)
            row += 1

        sheet.freeze_panes(header_row + 1, 0)
        if row > header_row + 1:
            sheet.autofilter(header_row, 0, row - 1, len(DETAIL_COLUMNS) - 1)
        else:
            sheet.write(row, 0, "No daily task records for this date.", formats["note"])

    def _write_task_row(self, sheet, formats, row: int, task: DailyTask) -> None:
        values: List[Any] = [
            task.uom_code,
            task.wbs,
            task.activity_code,
            task.activity_description,
            task.well_id,
            task.action_on,
            task.task_code,
            task.progress,
            task.planned,
            task.actual_quantity,
            task.quantity_status.value,
            task.crew_code,
            task.daily_completed,
            task.ph_name,
            task.mapping_status.value,
            ", ".join(flag.value for flag in task.data_quality_flags),
            task.activity_uom,
        ]
        for index, value in enumerate(values):
            self._write_cell(sheet, formats, row, index, value)

    @staticmethod
    def _uom_label(group) -> str:
        """The group's unit, or the units it spans when no total was reported.

        Naming the units is what keeps a blank Planned/Actual readable: the
        reader can see the total was withheld because the group mixes units,
        not because the work measured zero.
        """
        if group.quantities_summable:
            return group.uom_code or ""
        return f"mixed ({', '.join(group.uom_codes)})"

    @staticmethod
    def _write_cell(sheet, formats, row: int, col: int, value: Any) -> None:
        if value is None or value == "":
            # Missing information stays visibly missing; it is never filled in.
            sheet.write_blank(row, col, None, formats["text"])
        elif isinstance(value, bool):
            sheet.write_string(row, col, "Yes" if value else "No", formats["text"])
        elif isinstance(value, date):
            sheet.write_datetime(row, col, value, formats["date"])
        elif isinstance(value, Decimal):
            sheet.write_number(row, col, float(value), formats["number"])
        elif isinstance(value, int):
            sheet.write_number(row, col, value, formats["integer"])
        else:
            sheet.write_string(row, col, str(value), formats["text"])

    def _write_summary_sheet(self, workbook, formats, dataset: DailyDataset) -> None:
        sheet = workbook.add_worksheet("Summary")
        sheet.write(0, 0, f"Daily Morning Brief - Summary - {dataset.report_date.isoformat()}", formats["title"])
        sheet.write(
            1,
            0,
            "Grouped Validation Status -> WBS -> Activity. Well count counts "
            "distinct wells; task count counts tasks. A blank Planned/Actual "
            "where the UOM column reads 'mixed' means the group spans several "
            "units and no conversion between units is defined -- it does not "
            "mean zero.",
            formats["note"],
        )

        headers = [
            "Validation Status",
            "WBS",
            "Activity Code",
            "Activity Description",
            "UOM",
            "Wells",
            "Tasks",
            "Planned Quantity",
            "Actual Quantity",
        ]

        header_row = 3
        for index, name in enumerate(headers):
            sheet.write(header_row, index, name, formats["header"])
        widths = [18, 38, 18, 44, 14, 8, 8, 17, 17]
        for index, width in enumerate(widths):
            sheet.set_column(index, index, width)

        row = header_row + 1
        for status_group in self._grouping.build(dataset):
            status_label = status_group.status.value.replace("_", " ").title()
            for wbs_group in status_group.sorted_wbs_groups():
                sheet.write(row, 0, status_label, formats["group"])
                sheet.write(row, 1, wbs_group.wbs or "", formats["group"])
                sheet.write(row, 2, "All activities", formats["group"])
                sheet.write(row, 3, "", formats["group"])
                sheet.write(row, 4, self._uom_label(wbs_group), formats["group"])
                sheet.write_number(row, 5, wbs_group.well_count, formats["group"])
                sheet.write_number(row, 6, wbs_group.task_count, formats["group"])
                self._write_cell(sheet, formats, row, 7, wbs_group.planned_quantity)
                self._write_cell(sheet, formats, row, 8, wbs_group.actual_quantity)
                row += 1

                for activity in wbs_group.sorted_activities():
                    cells: List[Any] = [
                        status_label,
                        wbs_group.wbs,
                        activity.activity_code,
                        activity.activity_description,
                        self._uom_label(activity),
                        activity.well_count,
                        activity.task_count,
                        activity.planned_quantity,
                        activity.actual_quantity,
                    ]
                    for index, value in enumerate(cells):
                        self._write_cell(sheet, formats, row, index, value)
                    row += 1

        if row == header_row + 1:
            sheet.write(row, 0, "No daily task records for this date.", formats["note"])
        sheet.freeze_panes(header_row + 1, 0)

    def _write_data_quality_sheet(self, workbook, formats, dataset: DailyDataset) -> None:
        sheet = workbook.add_worksheet("Data Quality")
        sheet.write(0, 0, f"Data Quality - {dataset.report_date.isoformat()}", formats["title"])
        sheet.write(
            1,
            0,
            "Reported separately from the operational quantities; these conditions "
            "do not change any planned or actual figure.",
            formats["note"],
        )
        sheet.set_column(0, 0, 34)
        sheet.set_column(1, 1, 14)

        quality = self._grouping.data_quality(dataset)
        rows: List[tuple] = [
            ("Raw task_daily rows read", quality["raw_row_count"]),
            ("Logical daily tasks after grain resolution", quality["logical_task_count"]),
            ("Rows superseded by grain resolution", quality["superseded_row_count"]),
            ("Tasks with more than one row", quality["multi_row_task_count"]),
            ("Tasks with more than one actual entry", quality["duplicate_actual_task_count"]),
            ("Rows with invalid daily_data JSON", quality["invalid_json_row_count"]),
            ("Rows with unreadable actual_quantity", quality["unparseable_actual_row_count"]),
            ("Tasks carrying at least one flag", quality["affected_task_count"]),
        ]
        header_row = 3
        sheet.write(header_row, 0, "Condition", formats["header"])
        sheet.write(header_row, 1, "Count", formats["header"])
        row = header_row + 1
        for label, value in rows:
            sheet.write_string(row, 0, label, formats["text"])
            sheet.write_number(row, 1, int(value), formats["integer"])
            row += 1

        row += 1
        sheet.write(row, 0, "Flag", formats["header"])
        sheet.write(row, 1, "Tasks", formats["header"])
        row += 1
        flag_counts = quality["flag_counts"]
        if flag_counts:
            for flag, count in sorted(flag_counts.items(), key=lambda item: (-item[1], item[0])):
                sheet.write_string(row, 0, flag, formats["text"])
                sheet.write_number(row, 1, int(count), formats["integer"])
                row += 1
        else:
            sheet.write_string(row, 0, "No data-quality flags raised.", formats["text"])
            sheet.write_number(row, 1, 0, formats["integer"])
