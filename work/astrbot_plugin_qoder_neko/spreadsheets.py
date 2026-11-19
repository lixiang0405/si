"""Read bounded XLSX cell data without evaluating formulas or following links."""

import io
import json
import zipfile
from contextlib import closing
from datetime import date, datetime, time

from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter, range_boundaries


MAX_XLSX_PARTS = 2000
MAX_XLSX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_XLSX_SHEETS = 20
MAX_XLSX_ROWS = 10000
MAX_XLSX_COLUMNS = 128
MAX_XLSX_CELLS = 50000
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NAME_HEADERS = {"姓名", "人员姓名", "员工姓名", "跟进人", "跟进人员", "业务员", "业务员姓名", "负责人"}
CUSTOMER_HEADERS = {"客户", "客户名", "客户名称", "客户全称"}
SPREADSHEET_GUIDANCE = (
    "附件是从 Excel 原文件读取的单元格资料，保留工作表、行号和列标；单列纵向合并的值已展开到对应行。"
    "按各行的姓名和客户对应关系归总，不依据截图、旧回复或未合并的空白单元格猜测归属。"
    "有程序汇总时核对其所用列名和统计范围，默认客户数量按每人去重，记录条数和全表去重客户数分开说明。"
    "列名或人员归属有歧义时先询问；涉及多个工作表时明确汇总范围。"
    "当前支持读取和归总，不声称已经改写或发送 Excel 文件。"
)


class SpreadsheetError(ValueError):
    pass


def _check_bounds(row, column):
    if not (1 <= row <= MAX_XLSX_ROWS and 1 <= column <= MAX_XLSX_COLUMNS):
        raise SpreadsheetError("这份 Excel 的行列范围太大，请分表或删除无关的空白行列后再发。")


def _check_package(data):
    """Check XML and merge sizes before openpyxl creates cell objects."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = archive.infolist()
        if len(parts) > MAX_XLSX_PARTS or sum(part.file_size for part in parts) > MAX_XLSX_EXPANDED_BYTES:
            raise SpreadsheetError("这份 Excel 解压后太大，请分成几份再发。")
        if any(part.flag_bits & 1 for part in parts):
            raise SpreadsheetError("这份 Excel 有密码，请发一个可打开的副本。")
        cells = sheets = 0
        for part in parts:
            if not part.filename.endswith(".xml"):
                continue
            with archive.open(part) as stream:
                for _, element in iterparse(stream, events=("end",)):
                    if element.tag == SHEET_NS + "sheet":
                        sheets += 1
                        if sheets > MAX_XLSX_SHEETS:
                            raise SpreadsheetError("这份 Excel 工作表太多，请分成几份再发。")
                    elif element.tag == SHEET_NS + "c":
                        if reference := element.get("r"):
                            _check_bounds(*coordinate_to_tuple(reference))
                        cells += 1
                    elif element.tag == SHEET_NS + "row" and element.get("r"):
                        _check_bounds(int(element.get("r")), 1)
                    elif element.tag == SHEET_NS + "mergeCell":
                        left, top, right, bottom = range_boundaries(element.get("ref", ""))
                        _check_bounds(top, left)
                        _check_bounds(bottom, right)
                        if bottom < top or right < left:
                            raise SpreadsheetError("这份 Excel 的合并单元格范围无效，请重新保存后再发。")
                        cells += (bottom - top + 1) * (right - left + 1)
                    if cells > MAX_XLSX_CELLS:
                        raise SpreadsheetError("这份 Excel 单元格太多，请按工作表或人员拆分后再发。")
                    element.clear()


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value).strip()


def _customer_summary(rows):
    """Only aggregate an unambiguous table with an explicit person/customer pair."""
    headers = []
    for index, row in enumerate(rows):
        names = [column for column, value in row["单元格"].items() if value in NAME_HEADERS]
        customers = [column for column, value in row["单元格"].items() if value in CUSTOMER_HEADERS]
        if names and customers:
            if len(names) != 1 or len(customers) != 1:
                return None
            headers.append((index, names[0], customers[0]))
    if len(headers) != 1:
        return None
    index, name_column, customer_column = headers[0]
    people, all_customers = {}, set()
    for row in rows[index + 1:]:
        cells = row["单元格"]
        name, customer = cells.get(name_column, ""), cells.get(customer_column, "")
        if not name and not customer:
            continue
        if (
            not name or not customer or name in {"合计", "总计", "小计"}
            or name.startswith("#") or customer.startswith("#")
        ):
            return None
        person = people.setdefault(name, {"姓名": name, "客户": [], "记录条数": 0})
        person["记录条数"] += 1
        if customer not in person["客户"]:
            person["客户"].append(customer)
        all_customers.add(customer)
    if not people:
        return None
    for person in people.values():
        person["客户数量"] = len(person["客户"])
    return {
        "姓名列": name_column + ":" + rows[index]["单元格"][name_column],
        "客户列": customer_column + ":" + rows[index]["单元格"][customer_column],
        "口径": "仅此工作表表头后各行；每个客户单元格视为一个客户值，按姓名分别去重",
        "按姓名": list(people.values()),
        "记录总数": sum(person["记录条数"] for person in people.values()),
        "去重客户数量": len(all_customers),
    }


def read_spreadsheet(data: bytes, *, max_chars: int) -> str:
    _check_package(data)
    sheets, grid_cells, value_chars = [], 0, 0
    # The in-memory view exposes merge ranges; the read-only view supplies saved formula results.
    with (
        closing(load_workbook(io.BytesIO(data), data_only=False, keep_links=False)) as workbook,
        closing(load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)) as cached,
    ):
        for sheet in workbook.worksheets:
            _check_bounds(sheet.max_row, sheet.max_column)
            grid_cells += sheet.max_row * sheet.max_column
            if grid_cells > MAX_XLSX_CELLS:
                raise SpreadsheetError("这份 Excel 的数据范围太大，请分表或删除无关的空白行列后再发。")
            vertical = {
                (area.min_row, area.min_col): area.max_row
                for area in sheet.merged_cells.ranges if area.min_col == area.max_col
            }
            expanded, rows = {}, []
            saved_rows = cached[sheet.title].iter_rows(max_row=sheet.max_row, max_col=sheet.max_column)
            for row_number, source_row in enumerate(sheet.iter_rows(), 1):
                saved_row = next(saved_rows, ())
                cells = {}
                for column, source in enumerate(source_row, 1):
                    if source.data_type == "f":
                        saved_value = saved_row[column - 1].value if column <= len(saved_row) else None
                        if saved_value is None:
                            raise SpreadsheetError(
                                f"工作表「{sheet.title}」的 {source.coordinate} 没有保存公式结果，请用 Excel 打开并重新计算、保存后再发。"
                            )
                        value = _cell_text(saved_value)
                    else:
                        value = _cell_text(source.value)
                    if (row_number, column) in vertical:
                        for merged_row in range(row_number, vertical[row_number, column] + 1):
                            expanded[merged_row, column] = value
                    value = expanded.get((row_number, column), value)
                    if value:
                        value_chars += len(value)
                        if value_chars > max_chars:
                            raise SpreadsheetError("这份 Excel 内容太多，请只保留要归总的工作表或列后再发。")
                        cells[get_column_letter(column)] = value
                if cells:
                    rows.append({"行号": row_number, "单元格": cells})
            if rows:
                result = {"工作表": sheet.title, "合并单元格": sorted(map(str, sheet.merged_cells.ranges)), "行": rows}
                if summary := _customer_summary(rows):
                    result["程序汇总"] = summary
                sheets.append(result)
    if not sheets:
        raise SpreadsheetError("这份 Excel 没有读到单元格内容；如果数据是图片，请把图片单独发来。")
    text = json.dumps({"说明": "已读取全部工作表的单元格；公式只使用文件内保存的结果，不执行公式或外部链接。",
                       "工作表": sheets}, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        raise SpreadsheetError("这份 Excel 内容太多，请按工作表或人员拆分后再发，避免遗漏客户。")
    return text
