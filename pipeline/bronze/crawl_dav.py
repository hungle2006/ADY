#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

DEFAULT_BASE_URL = "https://06dichvucong.dav.gov.vn"
DEFAULT_PAGE_SIZE = 50
SERVICE_PATH = "/api/services/app/quanLyGiaThuoc/GetListCongBoPublicPaging"
LANDING_PATH = "/congbogiathuoc/index"
CSV_COLUMNS = [
    "dav_row_id", "dav_drug_name", "dav_active_ingredient_raw",
    "dav_strength", "dav_dosage_form", "dav_package", "dav_unit",
    "dav_manufacturer", "dav_country", "dav_registration_number",
    "dav_declaration_date", "dav_price_vnd", "dav_retail_price_vnd",
    "dav_declaring_unit", "dav_price_type", "source_url",
]
FIELD_MAP = {
    "dav_row_id": ["id", "Id"],
    "dav_drug_name": ["tenThuoc", "TenThuoc"],
    "dav_active_ingredient_raw": ["hoatChat", "HoatChat"],
    "dav_strength": ["hamLuong", "HamLuong"],
    "dav_dosage_form": ["dangBaoChe", "DangBaoChe"],
    "dav_package": ["quyCachDongGoi", "QuyCachDongGoi"],
    "dav_unit": ["donViTinh", "DonViTinh"],
    "dav_manufacturer": ["doanhNghiepSanXuat", "DoanhNghiepSanXuat"],
    "dav_country": ["nuocSanXuat", "NuocSanXuat"],
    "dav_registration_number": ["soDangKy", "SoDangKy"],
    "dav_declaration_date": ["ngayTiepNhan", "NgayTiepNhan"],
    "dav_price_vnd": ["giaBanBuonDuKien", "GiaBanBuonDuKien"],
    "dav_retail_price_vnd": ["giaBanLeDuKien", "GiaBanLeDuKien"],
    "dav_declaring_unit": ["donViKeKhai", "DonViKeKhai"],
    "dav_price_type": ["loaiGia", "LoaiGia"],
}


class DAVError(RuntimeError):
    pass


def extract_form_token(document: str) -> str | None:
    for pattern in (
        r'<input\b(?=[^>]*\bname=["\']__RequestVerificationToken["\'])(?=[^>]*\bvalue=["\']([^"\']+)["\'])[^>]*>',
        r'<input\b(?=[^>]*\bvalue=["\']([^"\']+)["\'])(?=[^>]*\bname=["\']__RequestVerificationToken["\'])[^>]*>',
        r'<meta\b(?=[^>]*\bname=["\']csrf-token["\'])(?=[^>]*\bcontent=["\']([^"\']+)["\'])[^>]*>',
    ):
        match = re.search(pattern, document, re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    return None


def csrf_header(session: requests.Session, document: str = "") -> dict[str, str]:
    for cookie_name, header_name in (
        ("XSRF-TOKEN", "X-XSRF-TOKEN"),
        ("CSRF-TOKEN", "X-CSRF-TOKEN"),
        ("__RequestVerificationToken", "RequestVerificationToken"),
    ):
        value = session.cookies.get(cookie_name)
        if value:
            return {header_name: unquote(value)}
    token = extract_form_token(document)
    if token:
        return {"RequestVerificationToken": token}
    return {}


def prepare_session(base_url: str) -> tuple[requests.Session, str]:
    session = requests.Session()
    origin = base_url.rstrip("/")
    landing_url = origin + LANDING_PATH
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PharmacyResearchCrawler/2.0)",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": landing_url,
        "Origin": origin,
    })
    r = session.get(landing_url, timeout=45)
    r.raise_for_status()
    token_headers = csrf_header(session, r.text)
    session.headers.update(token_headers)
    if token_headers:
        print("[DAV] CSRF token da duoc server cap; da them header:", ", ".join(token_headers))
    else:
        print("[DAV] Khong tim thay CSRF token trong cookie/HTML cua trang cong khai.")
    return session, landing_url


def unwrap_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DAVError("Unexpected API response: not a JSON object")
    if isinstance(payload.get("result"), dict):
        payload = payload["result"]
    if not isinstance(payload.get("items"), list):
        raise DAVError(f"API khong tra ve items list; fields={list(payload)[:15]}")
    return payload


def make_payload(skip: int, size: int, sorting: str | None = None) -> dict[str, Any]:
    return {
        "filterAll": "",
        "CongBoGiaThuoc": {},
        "KichHoat": True,
        "skipCount": skip,
        "maxResultCount": size,
        "sorting": sorting,
    }


def get_page(session: requests.Session, base_url: str, skip: int, size: int,
             sorting: str | None = None, retries: int = 7) -> dict[str, Any]:
    api_url = base_url.rstrip("/") + SERVICE_PATH
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.post(api_url, json=make_payload(skip, size, sorting), timeout=60)
            if response.status_code in (400, 401, 403):
                message = (response.text or "").strip()[:350]
                raise DAVError(f"HTTP {response.status_code}: {message}")
            response.raise_for_status()
            return unwrap_response(response.json())
        except DAVError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            print(f"[DAV] Loi mang/JSON o skipCount={skip}, lan {attempt}/{retries}: {exc}")
            if attempt < retries:
                # Respect overloaded public server instead of hammering on 504/429.
                retry_after = response.headers.get("Retry-After") if "response" in locals() else None
                try:
                    pause = float(retry_after) if retry_after else min(3 * (2 ** (attempt - 1)), 90)
                except ValueError:
                    pause = min(3 * (2 ** (attempt - 1)), 90)
                pause = min(pause, 120) + random.uniform(0, 0.8)
                print(f"[DAV] Cho {pause:.1f}s truoc khi thu lai...", flush=True)
                time.sleep(pause)
    raise DAVError(f"Request that bai o skipCount={skip}: {last_error}")


def pick(record: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        if key in record:
            return record[key]
    return None


def convert(record: dict[str, Any], landing_url: str) -> dict[str, Any]:
    result = {column: pick(record, names) for column, names in FIELD_MAP.items()}
    result["source_url"] = landing_url
    return result


def load_csv_by_id(csv_path: Path) -> dict[str, dict[str, Any]]:
    """Đọc file CSV vào dictionary với key là dav_row_id để đối chiếu."""
    data: dict[str, dict[str, Any]] = {}
    if not csv_path.exists():
        return data
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_id = str(row.get("dav_row_id") or "").strip()
            if row_id:
                data[row_id] = row
    return data


def reconcile_snapshots(
    prev_csv: Path,
    curr_csv: Path,
    output_dir: Path,
    timestamp: str,
) -> None:
    """Đối chiếu giữa lần crawl trước và lần crawl hiện tại để tìm record bị missing hoặc thay đổi."""
    print(f"\n[DIFF] Dang doi chieu du lieu giua:")
    print(f"  - File cu : {prev_csv.name}")
    print(f"  - File moi: {curr_csv.name}")

    old_records = load_csv_by_id(prev_csv)
    new_records = load_csv_by_id(curr_csv)

    old_ids = set(old_records.keys())
    new_ids = set(new_records.keys())

    missing_ids = old_ids - new_ids
    added_ids = new_ids - old_ids
    common_ids = old_ids & new_ids

    # Kiểm tra các bản ghi có cùng ID nhưng nội dung (giá, ngày công bố,...) bị thay đổi
    updated_ids: set[str] = set()
    compare_cols = [c for c in CSV_COLUMNS if c not in ("dav_row_id", "source_url")]
    for rid in common_ids:
        if any((old_records[rid].get(col) or "") != (new_records[rid].get(col) or "") for col in compare_cols):
            updated_ids.add(rid)

    print(f"[DIFF] Ket qua doi chieu:")
    print(f"  + Ban ghi moi them (Added)        : {len(added_ids):,}")
    print(f"  ~ Ban ghi cap nhat gia/thong tin  : {len(updated_ids):,}")
    print(f"  - Ban ghi bi thieu/go bo (Missing): {len(missing_ids):,}")

    # 1. Xuất riêng file chứa các record bị missing để kiểm tra
    if missing_ids:
        missing_path = output_dir / f"dav_missing_records_{timestamp}.csv"
        with missing_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for rid in sorted(missing_ids):
                writer.writerow(old_records[rid])
        print(f"[DIFF] Da luu danh sach record bi missing tai: {missing_path.resolve()}")

    # 2. Cập nhật vào file Master tổng hợp (không bao giờ mất record cũ)
    master_path = output_dir / "dav_gia_ban_buon_MASTER.csv"
    master_records = load_csv_by_id(master_path) if master_path.exists() else old_records.copy()
    # Cập nhật toàn bộ dữ liệu mới nhất đè lên các ID tương ứng, đồng thời giữ lại ID bị missing
    master_records.update(new_records)

    with master_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for rid in master_records:
            writer.writerow(master_records[rid])
    print(f"[DIFF] Da cap nhat file tong hop Master ({len(master_records):,} ban ghi): {master_path.resolve()}")


def crawl(
    output_dir: Path,
    base_url: str,
    page_size: int,
    max_rows: int | None,
    delay: float,
    sorting: str | None = None,
) -> Path:
    if page_size < 1 or page_size > 500:
        raise ValueError("--page-size phai trong khoang 1..500")
    if max_rows is not None and max_rows <= 0:
        raise ValueError("--max-rows phai > 0")
    if delay < 0:
        raise ValueError("--delay phai >= 0")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("--base-url phai la dia chi HTTPS hop le")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Tìm snapshot gần nhất trước khi tạo file mới để lát nữa đối chiếu
    existing_snapshots = sorted(output_dir.glob("dav_gia_ban_buon_20*.csv"))
    previous_csv = existing_snapshots[-1] if existing_snapshots else None

    started_at = datetime.now(timezone.utc)
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"dav_gia_ban_buon_{timestamp}.csv"
    jsonl_path = output_dir / f"dav_gia_thuoc_raw_{timestamp}.jsonl"

    session, landing_url = prepare_session(base_url)
    crawl_successful = False
    try:
        first = get_page(session, base_url, 0, page_size, sorting=sorting)
        total_value = first.get("totalCount")
        try:
            last_total = int(total_value) if total_value is not None else None
        except (TypeError, ValueError) as exc:
            raise DAVError(f"API totalCount khong hop le: {total_value!r}") from exc
        if last_total is not None and last_total < 0:
            raise DAVError(f"API totalCount am: {last_total}")
        if last_total is None and max_rows is None:
            raise DAVError(
                "API khong tra totalCount; tu choi danh dau lan crawl day du la hoan tat"
            )
        initial_total = last_total
        print(f"[DAV] Bat dau crawl snapshot {timestamp} | totalCount={last_total}, max_rows={max_rows}")

        seen_ids: set[str] = set()
        written_count = 0
        pages_fetched = 0
        total_count_changed = False
        skip = 0

        with csv_path.open("w", encoding="utf-8-sig", newline="") as fcsv, \
             jsonl_path.open("w", encoding="utf-8") as fjson:
            
            writer = csv.DictWriter(fcsv, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            while True:
                page = first if skip == 0 else get_page(session, base_url, skip, page_size, sorting=sorting)
                items = page.get("items", [])
                pages_fetched += 1
                if not items:
                    break

                # Kiểm tra nếu DAV cập nhật thêm/bớt dữ liệu ngay trong lúc đang crawl
                curr_total_val = page.get("totalCount")
                curr_total = int(curr_total_val) if curr_total_val is not None else None
                if last_total is not None and curr_total is not None and curr_total != last_total:
                    total_count_changed = True
                    drift = abs(curr_total - last_total)
                    print(
                        f"[DAV] Phat hien DAV cap nhat du lieu khi dang crawl! "
                        f"totalCount thay doi tu {last_total:,} -> {curr_total:,} (lech {drift} ban ghi)."
                    )
                    last_total = curr_total
                    # Nếu tổng số dòng giảm (có bản ghi bị rút), lùi skipCount lại để không nhảy cóc bản ghi ở ranh giới
                    backtrack = min(skip, drift + 10)
                    if backtrack > 0:
                        skip = max(0, skip - backtrack)
                        print(f"[DAV] Tu dong lui skipCount ve {skip:,} de chong sot ban ghi.")
                        page = get_page(session, base_url, skip, page_size, sorting=sorting)
                        items = page.get("items", [])

                for record in items:
                    if max_rows is not None and written_count >= max_rows:
                        break

                    rec_id = pick(record, FIELD_MAP["dav_row_id"])
                    rec_id_str = str(rec_id) if rec_id is not None else f"no_id_{skip}_{written_count}"

                    # Lọc bỏ nếu bản ghi bị lặp lại do lùi skipCount hoặc do DAV chèn thêm bản ghi ở trang đầu
                    if rec_id_str in seen_ids:
                        continue

                    seen_ids.add(rec_id_str)
                    fjson.write(json.dumps(record, ensure_ascii=False) + "\n")
                    writer.writerow(convert(record, landing_url))
                    written_count += 1

                fcsv.flush()
                fjson.flush()

                skip += len(items)
                print(
                    f"[DAV] skip={skip:,} | Da luu={written_count:,}"
                    + (f" / {last_total:,}" if last_total is not None else "")
                )

                if (max_rows is not None and written_count >= max_rows) or len(items) < page_size:
                    break

                time.sleep(delay)

        print(f"[DAV] Hoan tat Snapshot! CSV: {csv_path.resolve()}")
        print(f"[DAV] Hoan tat Snapshot! RAW: {jsonl_path.resolve()}")

        expected_rows = (
            min(max_rows, last_total) if max_rows is not None and last_total is not None
            else max_rows if max_rows is not None
            else last_total
        )
        if expected_rows is not None and written_count != expected_rows:
            raise DAVError(
                f"Snapshot khong day du: da ghi {written_count:,}, "
                f"API/yeu cau {expected_rows:,} ban ghi; khong duoc nap vao Bronze"
            )
        if max_rows is None and total_count_changed:
            raise DAVError(
                f"DAV thay doi totalCount trong luc crawl "
                f"({initial_total:,} -> {last_total:,}); chay lai de lay snapshot on dinh"
            )

        manifest_path = output_dir / f"dav_crawl_manifest_{timestamp}.json"
        manifest = {
            "source": "DAV public drug-price API",
            "landing_url": landing_url,
            "started_at_utc": started_at.isoformat(),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "initial_total_count": initial_total,
            "final_total_count": last_total,
            "page_size": page_size,
            "pages_fetched": pages_fetched,
            "max_rows": max_rows,
            "expected_rows": expected_rows,
            "records_written": written_count,
            "snapshot_complete": max_rows is None and expected_rows == written_count,
            "csv_file": csv_path.name,
            "jsonl_file": jsonl_path.name,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        crawl_successful = True
        print(f"[DAV] Manifest crawl: {manifest_path.resolve()}")

        # Tự động đối chiếu với file của lần crawl trước nếu có
        if previous_csv is not None:
            reconcile_snapshots(previous_csv, csv_path, output_dir, timestamp)
        else:
            # Nếu là lần chạy đầu tiên nhưng có sẵn file cũ theo tên cũ thì lấy đối chiếu luôn
            legacy_csv = output_dir / "dav_gia_ban_buon_day_du.csv"
            if legacy_csv.exists():
                reconcile_snapshots(legacy_csv, csv_path, output_dir, timestamp)

        return csv_path
    finally:
        session.close()
        if not crawl_successful:
            csv_path.unlink(missing_ok=True)
            jsonl_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="DAV public drug price crawler (Snapshot & Missing Record Reconciler)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", default="output_dav")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--sorting", default=None, help="Truong sap xep API (VD: 'id ASC' hoac 'ngayTiepNhan DESC')")
    args = parser.parse_args()
    try:
        crawl(Path(args.output_dir), args.base_url, args.page_size, args.max_rows, args.delay, args.sorting)
    except (DAVError, requests.RequestException, ValueError) as exc:
        print(f"[DAV] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
