"""
Result exporters — plain CSV and SpreadsheetML (Excel 2003 XML) for search jobs.

Both formats are generated with the standard library only:

- CSV: RFC 4180 via ``csv``, prefixed with a UTF-8 BOM so Excel opens the
  file with the right encoding without an import dialog.
- XLS: SpreadsheetML 2003 (``urn:schemas-microsoft-com:office:spreadsheet``).
  Excel and LibreOffice open it natively despite the ``.xls`` extension; it
  avoids pulling in a real xlsx writer dependency for an export feature.

All cell text is XML-escaped — crawled .onion content is untrusted.
"""
import csv
import io
import re
from typing import Any, Dict, Iterable, List
from xml.sax.saxutils import escape

# One row per exported result, in stable column order.
RESULT_COLUMNS = ("url", "title", "snippet", "term_count", "depth", "paragraphs")


def term_slug(term: str) -> str:
    """Filesystem-safe slug for a filename (e.g. 'cybersec' or 'osint-tools')."""
    slug = re.sub(r"[^a-z0-9]+", "-", (term or "").lower().strip()).strip("-")
    return slug[:40] or "results"


def export_filename(job_id: str, term: str, ext: str) -> str:
    """Attachment filename, e.g. 'ognse-dark-raven-kZ9mXp-cybersec.csv'."""
    return f"{job_id}-{term_slug(term)}.{ext}"


def _normalise_cell(value: Any) -> Any:
    """Flatten a result field into a scalar cell value."""
    if isinstance(value, list):
        # Paragraphs: join on newlines — CSV quoting and SpreadsheetML
        # <Data> both preserve them inside a single cell.
        return "\n".join(str(item) for item in value) if value else ""
    if value is None:
        return ""
    return value


def _rows(results: Iterable[Dict[str, Any]]) -> list:
    """(#, url, title, snippet, term_count, depth, paragraphs) tuples, 1-based."""
    return [
        (rank, *(_normalise_cell(item.get(col)) for col in RESULT_COLUMNS))
        for rank, item in enumerate(results, start=1)
    ]


def results_to_csv(results: List[Dict[str, Any]]) -> str:
    """RFC 4180 CSV with a header row and UTF-8 BOM (Excel-friendly accents)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(("#",) + RESULT_COLUMNS)
    for row in _rows(results):
        writer.writerow(row)
    return "\ufeff" + buf.getvalue()


def _xml_cell(value: Any, cell_type: str = "String") -> str:
    """A SpreadsheetML <Cell> with the value typed and escaped."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and cell_type == "Number":
        data = f'<Data ss:Type="Number">{escape(str(value))}</Data>'
    else:
        data = f'<Data ss:Type="String">{escape(str(value))}</Data>'
    return f"<Cell>{data}</Cell>"


def _xml_row(cells: str) -> str:
    return f"<Row>{cells}</Row>"


def results_to_xls(job: Dict[str, Any]) -> str:
    """SpreadsheetML 2003 document with a 'Résumé' sheet and a 'Résultats' sheet."""
    results = job.get("result", {}).get("results", [])
    request = job.get("request", {})
    result_meta = job.get("result", {})

    summary_rows = [
        ("Job", job.get("id", "")),
        ("Terme", request.get("term", "")),
        ("Statut", job.get("status", "")),
        ("Créé le", job.get("created_at", "")),
        ("Terminé le", job.get("completed_at") or ""),
        ("Résultats", result_meta.get("total", len(results))),
        ("Pages crawlées", result_meta.get("crawled_pages", "")),
        ("Durée(s)", result_meta.get("duration_seconds", "")),
    ]

    parts = [
        '<?xml version="1.0"?>',
        '<?mso-application progid="Excel.Sheet"?>',
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet ss:Name="Résumé"><Table>',
    ]
    for label, value in summary_rows:
        parts.append(_xml_row(_xml_cell(label) + _xml_cell(value)))
    parts.append("</Table></Worksheet>")

    parts.append('<Worksheet ss:Name="Résultats"><Table>')
    parts.append(_xml_row("".join(_xml_cell(col) for col in ("#",) + RESULT_COLUMNS)))
    for row in _rows(results):
        parts.append(_xml_row(_xml_cell(row[0], "Number") + "".join(_xml_cell(v) for v in row[1:])))
    parts.append("</Table></Worksheet></Workbook>")
    return "\n".join(parts)
