"""Normalize DAVfull.csv into product and price_record tables.

The source file can be UTF-16 tab-delimited even when its extension is .csv.
Run:
  python normalize_davfull.py --input "E:/ADY/data/raw/DAVfull.csv"

Outputs are written to a DAVfull_normalized folder beside the source unless
--output-dir is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


PRODUCT_FIELDS = [
    "dav_drug_name",
    "dav_active_ingredient_raw",
    "dav_strength",
    "dav_dosage_form",
    "dav_package",
    "dav_unit",
    "dav_manufacturer",
    "dav_country",
    "dav_registration_number",
]
PRICE_FIELDS = [
    "dav_declaration_date",
    "dav_price_vnd",
    "dav_retail_price_vnd",
    "dav_declaring_unit",
    "dav_price_type",
    "source_url",
]
REQUIRED_FIELDS = {
    "dav_row_id",
    "dav_drug_name",
    "dav_active_ingredient_raw",
    *PRODUCT_FIELDS,
    *PRICE_FIELDS,
}
NULL_MARKERS = {"", "NA", "N/A", "#N/A", "NULL", "NONE", "NAN"}


def read_source(path: Path) -> tuple[list[dict[str, str]], str, str]:
    """Read a delimited file with BOM-based encoding and header-based delimiter detection."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "cp1258"
        text = raw.decode(encoding)

    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = max(["\t", ",", ";", "|"], key=first_line.count)
    reader = csv.DictReader(text.splitlines(keepends=True), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("Không tìm thấy dòng tiêu đề trong file nguồn.")
    reader.fieldnames = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
    missing = REQUIRED_FIELDS.difference(reader.fieldnames)
    if missing:
        raise ValueError("Thiếu cột bắt buộc: " + ", ".join(sorted(missing)))
    rows: list[dict[str, str]] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(
                f"Dòng {line_number} có nhiều trường hơn tiêu đề. "
                "Kiểm tra dấu phân cách hoặc dấu ngoặc kép trong CSV."
            )
        rows.append({key: value or "" for key, value in row.items()})
    return rows, encoding, delimiter


def clean_text(value: Any) -> str | None:
    """Normalize Unicode to NFC, trim/collapse whitespace, and standardize null markers."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if text.upper() in NULL_MARKERS:
        return None
    return text


def parse_vnd(value: str | None) -> str | None:
    """Convert common decimal/grouping formats to a plain decimal string."""
    if value is None:
        return None
    text = value.replace("\u00a0", "").replace(" ", "")
    if not text:
        return None

    if "," in text and "." in text:
        # The final separator is assumed to be the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[1]
        # DAV prices are in VND and source precision is at most a few
        # decimal places; a three-digit suffix is treated as a thousands group.
        if len(tail) in (1, 2, 4) and text.count(",") == 1:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        groups = text.split(".")
        if all(len(group) == 3 for group in groups[1:]):
            text = "".join(groups)

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    normalized = format(amount, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def normalize_date(value: str | None) -> str | None:
    """Return an ISO date/datetime string; retain the source text if parsing fails."""
    if not value:
        return None
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate).isoformat()
    except ValueError:
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            return text


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_sql_schema(path: Path) -> None:
    sql = r"""-- SQL Server schema for the normalized DAV data.
-- Import product.csv first, then price_record.csv.
IF OBJECT_ID(N'dbo.product', N'U') IS NULL
BEGIN
CREATE TABLE dbo.product (
    product_id BIGINT NOT NULL PRIMARY KEY,
    dav_drug_name NVARCHAR(MAX) NOT NULL,
    dav_active_ingredient_raw NVARCHAR(MAX) NULL,
    dav_strength NVARCHAR(MAX) NULL,
    dav_dosage_form NVARCHAR(MAX) NULL,
    dav_package NVARCHAR(MAX) NULL,
    dav_unit NVARCHAR(50) NULL,
    dav_manufacturer NVARCHAR(MAX) NULL,
    dav_country NVARCHAR(200) NULL,
    dav_registration_number NVARCHAR(100) NULL
);
END;

IF OBJECT_ID(N'dbo.price_record', N'U') IS NULL
BEGIN
CREATE TABLE dbo.price_record (
    dav_row_id BIGINT NOT NULL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    dav_declaration_date NVARCHAR(40) NULL,
    dav_price_vnd DECIMAL(19,4) NULL,
    dav_retail_price_vnd NVARCHAR(30) NULL,
    dav_declaring_unit NVARCHAR(MAX) NULL,
    dav_price_type NVARCHAR(100) NULL,
    source_url NVARCHAR(500) NULL,
    CONSTRAINT FK_price_record_product FOREIGN KEY (product_id)
        REFERENCES dbo.product(product_id)
);
END;
"""
    path.write_text(sql, encoding="utf-8-sig")


def normalize(source: Path, output_dir: Path) -> dict[str, Any]:
    rows, source_encoding, source_delimiter = read_source(source)
    output_dir.mkdir(parents=True, exist_ok=True)

    seen_row_ids: set[str] = set()
    product_key_to_id: dict[tuple[Any, ...], int] = {}
    products: list[dict[str, Any]] = []
    price_records: list[dict[str, Any]] = []
    invalid_prices: list[str] = []
    invalid_dates: list[str] = []
    scientific_registration_count = 0
    null_counts: dict[str, int] = {}

    for source_row in rows:
        normalized = {key: clean_text(value) for key, value in source_row.items()}
        row_id = normalized.get("dav_row_id")
        if not row_id:
            raise ValueError("Có dav_row_id trống; dừng để tránh mất khả năng truy vết.")
        if row_id in seen_row_ids:
            raise ValueError(f"dav_row_id bị trùng: {row_id}")
        seen_row_ids.add(row_id)

        for column, value in normalized.items():
            if value is None:
                null_counts[column] = null_counts.get(column, 0) + 1

        registration = normalized.get("dav_registration_number")
        if registration and re.search(r"\d(?:\.\d+)?E[+-]?\d+", registration, re.I):
            scientific_registration_count += 1

        identity_values = tuple(normalized.get(field) for field in PRODUCT_FIELDS)
        # Ignore letter-case differences for matching, while keeping the first
        # cleaned spelling in product.csv for display and review.
        identity = tuple(value.casefold() if value is not None else None for value in identity_values)
        product_id = product_key_to_id.get(identity)
        if product_id is None:
            product_id = len(product_key_to_id) + 1
            product_key_to_id[identity] = product_id
            product = {field: normalized.get(field) for field in PRODUCT_FIELDS}
            product.update(product_id=product_id)
            products.append(product)

        price = parse_vnd(normalized.get("dav_price_vnd"))
        if normalized.get("dav_price_vnd") and price is None:
            invalid_prices.append(row_id)
        date_value = normalized.get("dav_declaration_date")
        normalized_date = normalize_date(date_value)
        if date_value and normalized_date == date_value and not re.match(r"^\d{4}-\d{2}-\d{2}", date_value):
            invalid_dates.append(row_id)

        price_records.append(
            {
                "dav_row_id": row_id,
                "product_id": product_id,
                "dav_declaration_date": normalized_date,
                "dav_price_vnd": price,
                "dav_retail_price_vnd": normalized.get("dav_retail_price_vnd"),
                "dav_declaring_unit": normalized.get("dav_declaring_unit"),
                "dav_price_type": normalized.get("dav_price_type"),
                "source_url": normalized.get("source_url"),
            }
        )

    write_csv(
        output_dir / "product.csv",
        ["product_id", *PRODUCT_FIELDS],
        products,
    )
    write_csv(
        output_dir / "price_record.csv",
        ["dav_row_id", "product_id", *PRICE_FIELDS],
        price_records,
    )
    make_sql_schema(output_dir / "create_tables.sql")

    report = {
        "source_file": str(source),
        "source_encoding": source_encoding,
        "source_delimiter": "TAB" if source_delimiter == "\t" else source_delimiter,
        "source_records": len(rows),
        "unique_products": len(products),
        "price_records": len(price_records),
        "products_without_active_ingredient": sum(
            p["dav_active_ingredient_raw"] is None for p in products
        ),
        "null_counts_by_source_column": null_counts,
        "registration_numbers_in_scientific_notation_preserved_as_text": scientific_registration_count,
        "unparseable_price_row_ids": invalid_prices,
        "unparseable_date_row_ids": invalid_dates,
        "product_identity_fields": PRODUCT_FIELDS,
        "notes": [
            "Each source DAV row becomes exactly one price_record, keyed by dav_row_id.",
            "Products are deduplicated by product description fields, including the complete active-ingredient text; case-only differences are treated as the same identity.",
            "Missing-value markers are converted to blank fields; numeric zero is preserved.",
            "Registration numbers that look like scientific notation are retained as text and flagged because the source may already have lost exact digits.",
        ],
    }
    (output_dir / "normalization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to DAVfull source CSV/TSV")
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: DAVfull_normalized beside the input file)",
    )
    args = parser.parse_args()
    source = Path(args.input).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy file nguồn: {source}")
    output_dir = Path(args.output_dir) if args.output_dir else source.parent / "DAVfull_normalized"
    report = normalize(source, output_dir)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print("\nSaved normalized tables in the output folder.")


if __name__ == "__main__":
    main()
