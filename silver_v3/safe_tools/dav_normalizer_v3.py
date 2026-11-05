#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DAV Normalizer v2
=================
Purpose
-------
Normalize Vietnamese DAV drug data into RxNorm-mapping-ready tables without
putting RxCUI or prior mapping scores into the normalized DAV outputs.

Main outputs
------------
1. dav_product_normalized_v2.csv
2. dav_component_normalized_v2.csv
3. dav_component_normalized_v2_READY.csv
4. dav_component_normalized_v2_REVIEW_QUEUE.csv
5. dav_form_route_v2.csv
6. dav_normalization_qc_v2.csv
7. DAV_Normalized_v2_Package.zip

Typical input
-------------
--dav dav_mapping_ready.csv

Optional component source
-------------------------
--components rxnorm_dav_mapping_clean.csv

The optional component source is used only to reuse already split ingredient
components. The output intentionally excludes rxcui, match_score, matched,
RxNorm name/TTY/SAB, etc., to avoid circular validation.

If --components is omitted, the tool splits DAV ingredient text directly.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------

def strip_accents(s: str) -> str:
    source = str(s or "").replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", source)
        if unicodedata.category(c) != "Mn"
    )

def clean_text(s: str) -> str:
    s = str(s or "")
    s = s.replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()

def norm_ascii(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(clean_text(s)).lower()).strip()

def parse_num(s: str) -> Optional[float]:
    """Ambiguous locale numeric data cannot be converted without review."""
    from decimal import Decimal, InvalidOperation
    value = re.sub(r"\s+", "", str(s or ""))
    if not value or not re.fullmatch(r"\d[\d.,]*", value):
        return None
    if re.fullmatch(r"\d{1,3}[,.]\d{3}", value) and value[0] != "0":
        return None
    if "." in value and "," in value:
        return None
    if value.count(".") + value.count(",") > 1:
        return None
    try:
        return float(Decimal(value.replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None

def fnum(x: Optional[float]) -> str:
    if x is None:
        return ""
    if abs(x - round(x)) < 1e-12:
        return str(int(round(x)))
    return f"{x:.12g}"

# ---------------------------------------------------------------------
# Unit parsing
# ---------------------------------------------------------------------

UNIT_MAP = {
    "mg": "MG",
    "g": "G",
    "mcg": "MCG",
    "ug": "MCG",
    "ml": "ML",
    "l": "L",
    "iu": "UNT",
    "ui": "UNT",
    "i.u.": "UNT",
    "u": "UNT",
    "meq": "MEQ",
    "mmol": "MMOL",
    "mci": "MCI",
    "ci": "CI",
    "kbq": "KBQ",
    "mbq": "MBQ",
    "gbq": "GBQ",
    "bq": "BQ",
    "%": "%",
    "dose": "EACH",
    "lieu": "EACH",
    "nhat": "EACH",
    "vien": "EACH",
    "ong": "EACH",
    "lo": "EACH",
    "chai": "EACH",
    "goi": "EACH",
}

URE = r"(?:mg|mcg|µg|μg|ug|g|ml|l|iu|ui|i\.u\.|u|meq|mmol|mci|ci|kbq|mbq|gbq|bq|%)"
DURE = r"(?:ml|g|mg|mcg|µg|μg|ug|l|dose|liều|lieu|nhát|nhat|viên|vien|ống|ong|lọ|lo|chai|gói|goi)"

STRENGTH_RE = re.compile(
    rf"(?<![\w-])(?P<v>\d[\d\s.,]*)\s*(?P<u>{URE})"
    rf"(?:\s*/\s*(?:(?P<dv>\d[\d\s.,]*)\s*)?(?P<du>{DURE}))?",
    re.I,
)

RANGE_RE = re.compile(
    rf"(?P<lo>\d[\d\s.,]*)\s*[-~]\s*(?P<hi>\d[\d\s.,]*)\s*(?P<u>{URE})",
    re.I,
)

def canonical_unit(u: str) -> str:
    raw = str(u or "").strip().lower()
    if raw in ("µg", "μg"):
        return "MCG"
    z = norm_ascii(raw).replace(" ", "")
    return UNIT_MAP.get(z, z.upper())

def mass_to_mg(value: Optional[float], unit_name: str) -> Optional[float]:
    if value is None:
        return None
    if unit_name == "MG":
        return value
    if unit_name == "G":
        return value * 1000.0
    if unit_name == "MCG":
        return value / 1000.0
    return None

# ---------------------------------------------------------------------
# Segment parsing
# ---------------------------------------------------------------------

def remove_parenthetical(s: str) -> str:
    out = []
    depth = 0
    for ch in clean_text(s):
        if ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth = max(0, depth - 1)
            out.append(" ")
        elif depth == 0:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)

def split_top_level(s: str) -> List[str]:
    """
    Split candidate components outside parentheses. A spaced '+' separates
    components; ionic charges and decimal commas are preserved. A newline
    separates only two lines that both contain a strength, not wrapped names.
    """
    s = str(s or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return []

    parts, buf = [], []
    depth = 0

    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue

        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue

        split_here = False

        if depth == 0 and ch == ";":
            split_here = True
        elif depth == 0 and ch == "+":
            split_here = (i > 0 and i + 1 < len(s)
                          and s[i - 1].isspace() and s[i + 1].isspace())
        elif depth == 0 and ch == "\n":
            following = s[i + 1:].split("\n", 1)[0].strip()
            split_here = bool(STRENGTH_RE.search("".join(buf))
                              and STRENGTH_RE.search(following)
                              and not following.startswith("("))
        elif depth == 0 and ch == ",":
            prev = s[i - 1] if i else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            # Decimal comma is not a component separator.
            if not (prev.isdigit() and nxt.isdigit()):
                split_here = True

        if split_here:
            p = clean_text("".join(buf)).strip(" ,;")
            if p:
                parts.append(p)
            buf = []
        else:
            buf.append(ch)

    p = clean_text("".join(buf)).strip(" ,;")
    if p:
        parts.append(p)

    return parts

def extract_strengths(s: str, outside_parentheses: bool = True) -> List[Dict[str, object]]:
    t = remove_parenthetical(s) if outside_parentheses else clean_text(s)
    result = []

    for m in STRENGTH_RE.finditer(t):
        result.append({
            "raw": m.group(0),
            "value": parse_num(m.group("v")),
            "unit": canonical_unit(m.group("u")),
            "den_value": (
                parse_num(m.group("dv"))
                if m.group("dv")
                else (1.0 if m.group("du") else None)
            ),
            "den_unit": canonical_unit(m.group("du")) if m.group("du") else "",
            "start": m.start(),
            "end": m.end(),
        })
    return result

def extract_range(s: str):
    m = RANGE_RE.search(clean_text(s))
    if not m:
        return None
    return (
        parse_num(m.group("lo")),
        parse_num(m.group("hi")),
        canonical_unit(m.group("u")),
        m.group(0),
    )

def extract_precise_ingredient(segment: str) -> str:
    """
    Capture salt/ester/form text from Vietnamese phrase 'dưới dạng ...'.
    This field is evidence only; it is NOT automatically asserted as RxNorm PIN.
    """
    seg = clean_text(segment)
    m = re.search(r"dưới\s+dạng\s+([^);,]+)", seg, re.I)
    if not m:
        return ""

    x = STRENGTH_RE.sub(" ", m.group(1))
    x = re.sub(
        r"\b(?:tương đương|tương ứng)\b.*$",
        "",
        x,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", x).strip(" ,;:-")

def extract_equivalent_strength(segment: str) -> str:
    """
    Extract strength explicitly stated inside a parenthetical
    'tương đương/tương ứng ...' clause.
    """
    for pm in re.finditer(r"\(([^()]*)\)", clean_text(segment)):
        inner = pm.group(1)
        if re.search(r"tương\s*(?:đương|ứng)", inner, re.I):
            arr = extract_strengths(inner, outside_parentheses=False)
            if arr:
                return str(arr[0]["raw"])
    return ""

def context_denominator(segment: str) -> Tuple[Optional[float], str]:
    """
    Examples:
      'Mỗi 5 ml chứa ...'
      'Tuýp 10g chứa ...'
    """
    z = norm_ascii(segment)
    patterns = [
        r"(?:moi|trong)\s*(\d[\d.,]*)\s*(ml|g)\s*(?:co|chua)",
        r"(?:tuyp|chai|lo|ong)\s*(\d[\d.,]*)\s*(ml|g)\s*(?:co|chua)",
    ]
    for pat in patterns:
        m = re.search(pat, z)
        if m:
            return parse_num(m.group(1)), canonical_unit(m.group(2))
    return None, ""

# ---------------------------------------------------------------------
# Dose form / route hints
# ---------------------------------------------------------------------

def form_route_hint(form: str) -> Tuple[str, str, str]:
    """
    DAV-side grouping only.
    route_hint is a candidate-narrowing hint, NOT a final RxNorm route assertion.
    """
    z = norm_ascii(form)

    if "duoi luoi" in z:
        return "sublingual_product", "sublingual", "HIGH"
    if "mat" in z and any(k in z for k in ["nho", "tra"]):
        return "ophthalmic_product", "ophthalmic", "HIGH"
    if "tai" in z and "nho" in z:
        return "otic_product", "otic", "HIGH"
    if "mui" in z and any(k in z for k in ["nho", "xit"]):
        return "nasal_product", "nasal", "HIGH"
    if "am dao" in z:
        return "vaginal_product", "vaginal", "HIGH"
    if "truc trang" in z or "dat hau mon" in z:
        return "rectal_product", "rectal", "HIGH"
    if "mieng dan" in z or "dan qua da" in z:
        return "transdermal_product", "transdermal", "HIGH"
    if any(k in z for k in ["hit", "khi dung"]):
        return "inhalation_product", "respiratory", "HIGH"
    if any(k in z for k in ["boi", "kem", "gel", "thuoc mo", "ngoai da"]):
        return "topical_product", "topical", "MEDIUM"
    if "tiem truyen" in z:
        return "infusion_product", "parenteral", "HIGH"
    if "tiem" in z:
        return "injection_product", "parenteral", "HIGH"
    if "vien nang" in z:
        return "oral_capsule", "oral", "HIGH"
    if "vien nen" in z:
        return "oral_tablet", "oral", "HIGH"
    if any(k in z for k in ["dung dich uong", "siro", "hon dich uong"]):
        return "oral_liquid", "oral", "HIGH"
    if any(k in z for k in ["com pha", "bot pha", "thuoc bot"]) and "tiem" not in z:
        return "powder_or_granule", "oral_or_other", "MEDIUM"

    return "other", "", "LOW"

def is_discrete_unit_form(form: str) -> bool:
    z = norm_ascii(form)
    return any(k in z for k in [
        "vien nen", "vien nang", "vien ngam",
        "vien nhai", "vien dat", "goi",
    ])

# ---------------------------------------------------------------------
# Strength normalization
# ---------------------------------------------------------------------

def review_strength(reason: str, raw: str, source_rule: str) -> Dict[str, str]:
    """Keep evidence while withholding a numeric comparison for ambiguity."""
    return {
        "component_strength_raw": clean_text(raw),
        "strength_value": "", "strength_unit": "",
        "denominator_value": "", "denominator_unit": "",
        "strength_normalized": "", "strength_compare_value": "",
        "strength_compare_unit": "", "strength_type": "AMBIGUOUS",
        "strength_parse_status": reason, "strength_source_rule": source_rule,
        "strength_range_low": "", "strength_range_high": "",
    }


def ingredient_search_fields(text: str) -> Dict[str, str]:
    """Lexical search aid only: never equate salts, synonyms or ingredient IDs."""
    original = clean_text(text)
    complex_text = any(x in norm_ascii(original)
                       for x in ("tuong duong", "tuong ung", "duoi dang", "(", ")", "+", ";"))
    name = original
    if not complex_text:
        matches = list(STRENGTH_RE.finditer(original))
        if len(matches) == 1 and not original[matches[0].end():].strip(" ."):
            name = original[:matches[0].start()].strip(" :-")
        elif matches:
            complex_text = True
    return {
        "ingredient_name_for_search": name,
        "ingredient_key_for_search": norm_ascii(name),
        "ingredient_name_parse_status": (
            "MISSING" if not name else
            "COMPLEX_SOURCE_TEXT_REVIEW" if complex_text else "LEXICAL_ONLY"
        ),
    }

def normalize_component_strength(
    segment: str,
    raw_strength: str,
    raw_ingredient: str,
    component_count: int,
    component_index: int,
    dosage_form: str,
    source_rule: str,
) -> Dict[str, str]:

    # 1) Range
    rg = extract_range(segment or "")
    if rg:
        lo, hi, u, raw = rg
        return {
            "component_strength_raw": raw,
            "strength_value": "",
            "strength_unit": u,
            "denominator_value": "",
            "denominator_unit": "",
            "strength_normalized": f"{fnum(lo)}-{fnum(hi)} {u}",
            "strength_compare_value": "",
            "strength_compare_unit": "",
            "strength_type": "RANGE",
            "strength_parse_status": "REVIEW_RANGE",
            "strength_source_rule": source_rule,
            "strength_range_low": fnum(lo),
            "strength_range_high": fnum(hi),
        }

    # 2) Prefer explicit strength outside parentheses.
    arr = extract_strengths(segment or "", outside_parentheses=True)
    if not arr and segment:
        arr = extract_strengths(segment, outside_parentheses=False)
        if arr:
            return review_strength("REVIEW_PARENTHETICAL_STRENGTH_BASIS",
                                   segment, source_rule)

    # 3) Sequence fallback.
    if not arr and component_count > 1:
        arr2 = extract_strengths(raw_strength, outside_parentheses=True)
        if len(arr2) == component_count:
            arr = [arr2[component_index]]
            source_rule = "SEQUENCE_FROM_STRENGTH"
        else:
            arr2 = extract_strengths(raw_ingredient, outside_parentheses=True)
            if len(arr2) == component_count:
                arr = [arr2[component_index]]
                source_rule = "SEQUENCE_FROM_INGREDIENT"

    if not arr:
        return {
            "component_strength_raw": "",
            "strength_value": "",
            "strength_unit": "",
            "denominator_value": "",
            "denominator_unit": "",
            "strength_normalized": "",
            "strength_compare_value": "",
            "strength_compare_unit": "",
            "strength_type": "MISSING",
            "strength_parse_status": "REVIEW_MISSING",
            "strength_source_rule": source_rule,
            "strength_range_low": "",
            "strength_range_high": "",
        }

    # Multiple strengths can describe a mixture, a salt/base equivalence,
    # or a container. Never silently choose the last number as the dose.
    if len(arr) > 1:
        return review_strength("REVIEW_MULTIPLE_STRENGTHS", segment, source_rule)
    e = arr[0]
    v = e["value"]
    u = str(e["unit"])
    dv = e["den_value"]
    du = str(e["den_unit"])
    raw = str(e["raw"])
    if du and dv is not None and float(dv) <= 0:
        return review_strength("REVIEW_NONPOSITIVE_DENOMINATOR", raw, source_rule)
    if v is None or (dv is None and du):
        return {
            "component_strength_raw": raw, "strength_value": "", "strength_unit": u,
            "denominator_value": "", "denominator_unit": du,
            "strength_normalized": "", "strength_compare_value": "",
            "strength_compare_unit": "", "strength_type": "AMBIGUOUS",
            "strength_parse_status": "REVIEW_AMBIGUOUS_NUMBER_LOCALE",
            "strength_source_rule": source_rule, "strength_range_low": "", "strength_range_high": "",
        }

    nearby = norm_ascii(
        (segment or "")[max(0, int(e["start"]) - 25): int(e["end"]) + 25]
    )

    # Percentage conversion only when basis is explicit.
    if u == "%":
        if "w/v" in nearby or "w-v" in nearby:
            x = (v or 0) * 10.0
            return {
                "component_strength_raw": raw,
                "strength_value": fnum(v),
                "strength_unit": "%",
                "denominator_value": "",
                "denominator_unit": "",
                "strength_normalized": f"{fnum(x)} MG/ML",
                "strength_compare_value": fnum(x),
                "strength_compare_unit": "MG/ML",
                "strength_type": "PERCENT_WV",
                "strength_parse_status": "PARSED_PERCENT_CONVERTED",
                "strength_source_rule": source_rule,
                "strength_range_low": "",
                "strength_range_high": "",
            }

        if "w/w" in nearby or "w-w" in nearby:
            x = (v or 0) * 10.0
            return {
                "component_strength_raw": raw,
                "strength_value": fnum(v),
                "strength_unit": "%",
                "denominator_value": "",
                "denominator_unit": "",
                "strength_normalized": f"{fnum(x)} MG/G",
                "strength_compare_value": fnum(x),
                "strength_compare_unit": "MG/G",
                "strength_type": "PERCENT_WW",
                "strength_parse_status": "PARSED_PERCENT_CONVERTED",
                "strength_source_rule": source_rule,
                "strength_range_low": "",
                "strength_range_high": "",
            }

        return {
            "component_strength_raw": raw,
            "strength_value": fnum(v),
            "strength_unit": "%",
            "denominator_value": "",
            "denominator_unit": "",
            "strength_normalized": f"{fnum(v)} %",
            "strength_compare_value": fnum(v),
            "strength_compare_unit": "%",
            "strength_type": "PERCENT_UNSPECIFIED",
            "strength_parse_status": "REVIEW_PERCENT_BASIS",
            "strength_source_rule": source_rule,
            "strength_range_low": "",
            "strength_range_high": "",
        }

    # Context denominator: "Mỗi 5 ml chứa ..."
    if not du:
        cdv, cdu = context_denominator(segment or "")
        if cdv and cdu in ("ML", "G"):
            dv, du = cdv, cdu

    derived_each = False
    if not du and is_discrete_unit_form(dosage_form):
        dv, du = 1.0, "EACH"
        derived_each = True

    mg = mass_to_mg(v, u)

    if du and dv:
        strength_type = "CONCENTRATION" if du in ("ML", "L", "G", "MG", "MCG") else "AMOUNT_PER_UNIT"
        compare_denominator = float(dv)
        compare_den_unit = du
        if du == "L":
            compare_denominator *= 1000
            compare_den_unit = "ML"
        elif du in ("MG", "MCG"):
            compare_denominator /= 1000 if du == "MG" else 1000000
            compare_den_unit = "G"
        # Mass conversion also applies to tablets/capsules (EACH).
        cv = (mg if mg is not None else float(v)) / compare_denominator
        cu = f"{'MG' if mg is not None else u}/{compare_den_unit}"

        normalized = f"{fnum(cv)} {cu}"

    else:
        strength_type = (
            "ACTIVITY_OR_NONMASS"
            if u in ("UNT", "MCI", "CI", "KBQ", "MBQ", "GBQ", "BQ", "MEQ", "MMOL")
            else "AMOUNT"
        )

        if mg is not None:
            cv, cu = mg, "MG"
        else:
            cv, cu = v, u

        normalized = f"{fnum(cv)} {cu}".strip()

    return {
        "component_strength_raw": raw,
        "strength_value": fnum(v),
        "strength_unit": u,
        "denominator_value": fnum(dv) if dv else "",
        "denominator_unit": du,
        "strength_normalized": normalized,
        "strength_compare_value": fnum(cv),
        "strength_compare_unit": cu,
        "strength_type": strength_type,
        "strength_parse_status": (
            "PARSED_WITH_DERIVED_EACH"
            if derived_each
            else "PARSED_EXACT"
        ),
        "strength_source_rule": source_rule + ("+DERIVED_EACH" if derived_each else ""),
        "strength_range_low": "",
        "strength_range_high": "",
    }

# ---------------------------------------------------------------------
# Input column resolution
# ---------------------------------------------------------------------

def choose(row: Dict[str, str], *names: str) -> str:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return str(row[n])
    return ""

def normalize_dav_row(row: Dict[str, str], fallback_id: int) -> Dict[str, str]:
    """
    Adapts common DAV column names to the internal schema.
    """
    return {
        "dav_row_id": choose(row, "dav_row_id", "row_id", "id") or str(fallback_id),
        "dav_registration_number": choose(
            row,
            "dav_registration_number",
            "so_dang_ky",
            "so_gplh",
            "registration_number",
        ),
        "dav_registration_number_base": choose(
            row,
            "dav_registration_number_base",
            "registration_number_base",
        ),
        "dav_drug_name": choose(
            row,
            "dav_drug_name",
            "ten_thuoc",
            "drug_name",
            "name",
        ),
        "dav_active_ingredient_raw": choose(
            row,
            "dav_active_ingredient_raw",
            "hoat_chat",
            "active_ingredient",
            "ingredient",
        ),
        "dav_strength": choose(
            row,
            "dav_strength",
            "ham_luong",            "nong_do_ham_luong",
            "strength",
        ),
        "dav_dosage_form": choose(
            row,
            "dav_dosage_form",
            "dang_bao_che",
            "dosage_form",
        ),
        "dav_dosage_form_norm_vi": choose(
            row,
            "dav_dosage_form_norm_vi",
            "dosage_form_norm_vi",
        ),
        "dav_package": choose(
            row,
            "dav_package",
            "quy_cach_dong_goi",
            "package",
        ),
        "dav_unit": choose(
            row,
            "dav_unit",
            "don_vi",
            "unit",
        ),
        "dav_manufacturer": choose(
            row,
            "dav_manufacturer",
            "co_so_san_xuat",
            "manufacturer",
        ),
        "dav_country": choose(
            row,
            "dav_country",
            "nuoc_san_xuat",
            "country",
        ),
        "dav_declaration_date": choose(
            row,
            "dav_declaration_date",
            "ngay_cong_bo",
            "declaration_date",
        ),
        "dav_price_vnd": choose(
            row,
            "dav_price_vnd",
            "gia",
            "gia_ke_khai",
            "price_vnd",
            "price",
        ),
    }

# ---------------------------------------------------------------------
# Component preparation
# ---------------------------------------------------------------------

def build_components_from_existing(
    rows: List[Dict[str, str]]
) -> Dict[str, List[Dict[str, str]]]:

    grouped = defaultdict(list)

    for row in rows:
        did = choose(row, "dav_row_id", "row_id")
        if not did:
            continue

        comp = {
            "dav_row_id": did,
            "component_order": choose(row, "component_order") or "1",
            "ingredient_component_clean": choose(
                row,
                "dav_ingredient_component",
                "ingredient_component_clean",
                "ingredient_component",
            ),
            "ingredient_normalized_candidate": choose(
                row,
                "dav_ingredient_normalized",
                "ingredient_normalized_candidate",
            ),
        }
        grouped[did].append(comp)

    for did in grouped:
        grouped[did].sort(
            key=lambda x: int(x["component_order"])
            if str(x["component_order"]).isdigit()
            else 999
        )

    return grouped

def build_components_from_raw(
    products: List[Dict[str, str]]
) -> Dict[str, List[Dict[str, str]]]:

    grouped = defaultdict(list)

    for p in products:
        parts = split_top_level(p["dav_active_ingredient_raw"])

        if not parts:
            parts = [p["dav_active_ingredient_raw"]] if p["dav_active_ingredient_raw"] else [""]

        for i, part in enumerate(parts, start=1):
            grouped[p["dav_row_id"]].append({
                "dav_row_id": p["dav_row_id"],
                "component_order": str(i),
                "ingredient_component_clean": clean_text(part),
                # Raw-only mode cannot safely assert an RxNorm-normalized ingredient name.
                "ingredient_normalized_candidate": clean_text(part),
            })

    return grouped

# ---------------------------------------------------------------------
# Main normalization
# ---------------------------------------------------------------------

PRODUCT_HEADERS = [
    "dav_row_id",
    "dav_registration_number",
    "dav_registration_number_base",
    "dav_drug_name_raw",
    "dav_drug_name_normalized",
    "dav_active_ingredient_raw",
    "component_count",
    "dav_strength_raw",
    "dav_dosage_form_raw",
    "dav_dosage_form_norm_vi",
    "dav_form_group",
    "dav_route_hint",
    "route_hint_confidence",
    "route_hint_status",
    "dav_package",
    "dav_unit",
    "dav_manufacturer",
    "dav_country",
    "dav_declaration_date",
    "dav_price_vnd",
    "product_normalization_status",
]

COMPONENT_HEADERS = [
    "dav_row_id",
    "component_order",
    "dav_registration_number",
    "dav_drug_name",
    "dav_active_ingredient_raw",
    "ingredient_component_clean",
    "ingredient_normalized_candidate",
    "ingredient_normalization_status",
    "ingredient_name_for_search",
    "ingredient_key_for_search",
    "ingredient_name_parse_status",
    "precise_ingredient_raw",
    "equivalent_strength_raw",
    "strength_basis_review",
    "dav_strength_raw",
    "component_strength_source_text",
    "component_strength_raw",
    "strength_value",
    "strength_unit",
    "denominator_value",
    "denominator_unit",
    "strength_normalized",
    "strength_compare_value",
    "strength_compare_unit",
    "strength_type",
    "strength_parse_status",
    "strength_source_rule",
    "strength_range_low",
    "strength_range_high",
    "dav_dosage_form_raw",
    "dav_dosage_form_norm_vi",
    "dav_form_group",
    "dav_route_hint",
    "route_hint_confidence",
    "dav_unit",
    "mapping_readiness_v2",
    "review_reason",
]

FORM_HEADERS = [
    "dav_dosage_form_raw",
    "dav_dosage_form_norm_vi",
    "row_count",
    "dav_form_group",
    "dav_route_hint",
    "route_hint_confidence",
    "route_hint_status",
    "rxnorm_df_canonical",
    "rxnorm_df_rxcui",
    "rxnorm_df_verification_status",
]

def normalize(
    dav_csv: Path,
    output_dir: Path,
    component_csv: Optional[Path] = None,
) -> None:

    output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------ Load DAV ------------------
    # Local exports can be UTF-16 TSV; crawler/Silver files are UTF-8 CSV.
    with dav_csv.open("rb") as source:
        bom = source.read(3)
    encoding = "utf-16" if bom.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    with dav_csv.open("r", encoding=encoding, newline="") as f:
        header = f.readline()
        f.seek(0)
        reader = csv.DictReader(f, delimiter="\t" if "\t" in header else ",")
        fields = reader.fieldnames or []
        if (not fields or len(fields) != len(set(fields)) or any(not name for name in fields)
                or not set(fields).intersection({"dav_active_ingredient_raw", "hoat_chat", "active_ingredient", "ingredient"})):
            raise ValueError("DAV input has missing/duplicate headers or no supported ingredient field")
        raw_dav = list(reader)
        if not raw_dav or any(None in row or any(value is None for value in row.values()) for row in raw_dav):
            raise ValueError("DAV input is empty or contains malformed CSV/TSV rows")

    products = [
        normalize_dav_row(row, i + 1)
        for i, row in enumerate(raw_dav)
    ]
    product_by_id = {p["dav_row_id"]: p for p in products}

    # ------------------ Components ------------------
    if component_csv:
        with component_csv.open("r", encoding="utf-8-sig", newline="") as f:
            component_source_rows = list(csv.DictReader(f))
        groups = build_components_from_existing(component_source_rows)
        component_source_mode = "PRE_SPLIT_COMPONENT_SOURCE"
    else:
        groups = build_components_from_raw(products)
        component_source_mode = "RAW_DAV_SPLIT"

    # Ensure every product has at least one component row.
    for p in products:
        if not groups.get(p["dav_row_id"]):
            groups[p["dav_row_id"]] = [{
                "dav_row_id": p["dav_row_id"],
                "component_order": "1",
                "ingredient_component_clean": clean_text(p["dav_active_ingredient_raw"]),
                "ingredient_normalized_candidate": clean_text(p["dav_active_ingredient_raw"]),
            }]

    # ------------------ Product output ------------------
    product_rows = []

    for p in products:
        fg, route, conf = form_route_hint(p["dav_dosage_form"])
        ncomp = len(groups.get(p["dav_row_id"], [])) or 1

        status = "READY_FOR_COMPONENT_NORMALIZATION"
        if not clean_text(p["dav_active_ingredient_raw"]):
            status = "REVIEW_MISSING_INGREDIENT"
        elif not clean_text(p["dav_dosage_form"]):
            status = "REVIEW_MISSING_DOSAGE_FORM"

        product_rows.append({
            "dav_row_id": p["dav_row_id"],
            "dav_registration_number": p["dav_registration_number"],
            "dav_registration_number_base": p["dav_registration_number_base"],
            "dav_drug_name_raw": p["dav_drug_name"],
            "dav_drug_name_normalized": norm_ascii(p["dav_drug_name"]),
            "dav_active_ingredient_raw": p["dav_active_ingredient_raw"],
            "component_count": str(ncomp),
            "dav_strength_raw": p["dav_strength"],
            "dav_dosage_form_raw": p["dav_dosage_form"],
            "dav_dosage_form_norm_vi": (
                p["dav_dosage_form_norm_vi"]
                or norm_ascii(p["dav_dosage_form"])
            ),
            "dav_form_group": fg,
            "dav_route_hint": route,
            "route_hint_confidence": conf,
            "route_hint_status": (
                "DERIVED_FROM_DAV_FORM_TEXT_NOT_RXNORM_VERIFIED"
                if route
                else "REVIEW_ROUTE"
            ),
            "dav_package": p["dav_package"],
            "dav_unit": p["dav_unit"],
            "dav_manufacturer": p["dav_manufacturer"],
            "dav_country": p["dav_country"],
            "dav_declaration_date": p["dav_declaration_date"],
            "dav_price_vnd": p["dav_price_vnd"],
            "product_normalization_status": status,
        })

    # ------------------ Component output ------------------
    component_rows = []

    for did, comps in groups.items():
        p = product_by_id.get(did)
        if not p:
            continue

        raw_ing = p["dav_active_ingredient_raw"]
        raw_str = p["dav_strength"]
        form = p["dav_dosage_form"]
        n = len(comps)

        ingredient_segments = split_top_level(raw_ing)
        if len(ingredient_segments) != n:
            ingredient_segments = []

        strength_segments = split_top_level(raw_str)
        if len(strength_segments) != n:
            strength_segments = []

        fg, route, conf = form_route_hint(form)

        for i, comp in enumerate(comps):
            # Ingredient text used for salt / equivalence evidence.
            ingredient_seg = (
                ingredient_segments[i]
                if ingredient_segments
                else raw_ing
            )

            # Strength alignment priority:
            # 1) exact count of strength segments
            # 2) single-component strength field
            # 3) matching ingredient segment
            # 4) later sequence fallback inside strength parser
            if strength_segments:
                strength_seg = strength_segments[i]
                strength_rule = "STRENGTH_FIELD_SEGMENT"
            elif n == 1:
                strength_seg = raw_str or raw_ing
                strength_rule = "SINGLE_COMPONENT_STRENGTH_FIELD"
            elif ingredient_segments:
                strength_seg = ingredient_segments[i]
                strength_rule = "INGREDIENT_FIELD_SEGMENT"
            else:
                strength_seg = ""
                strength_rule = "UNALIGNED"

            st = normalize_component_strength(
                segment=strength_seg,
                raw_strength=raw_str,
                raw_ingredient=raw_ing,
                component_count=n,
                component_index=i,
                dosage_form=form,
                source_rule=strength_rule,
            )

            precise = extract_precise_ingredient(ingredient_seg)
            equiv = extract_equivalent_strength(ingredient_seg)
            equivalence_present = bool(re.search(r"tương\s*(?:đương|ứng)", ingredient_seg, re.I))

            comp_clean = clean_text(comp["ingredient_component_clean"])
            ing_norm = clean_text(comp["ingredient_normalized_candidate"])

            if component_source_mode == "PRE_SPLIT_COMPONENT_SOURCE":
                ing_status = (
                    "DIRECT"
                    if norm_ascii(comp_clean) == norm_ascii(ing_norm)
                    else "STANDARDIZED_NAME_CANDIDATE"
                )
            else:
                ing_status = "RAW_DAV_COMPONENT"

            if equivalence_present:
                basis_review = "REVIEW_BOSS_EQUIVALENT_STRENGTH_PRESENT"
            elif precise:
                basis_review = "PRECISE_FORM_PRESENT_VERIFY_BOSS_LATER"
            else:
                basis_review = "NO_EQUIVALENT_STRENGTH_TEXT"

            review = []

            if not ing_norm:
                review.append("INGREDIENT_MISSING")

            if st["strength_parse_status"].startswith("REVIEW"):
                review.append(st["strength_parse_status"])

            if n > 1 and not strength_seg:
                review.append("COMPONENT_STRENGTH_ALIGNMENT")

            if equivalence_present:
                review.append("BOSS_EQUIVALENT_STRENGTH")

            if not form:
                review.append("DOSAGE_FORM_MISSING")

            if component_source_mode == "RAW_DAV_SPLIT":
                # Ingredient standardization still needs external terminology validation.
                review.append("INGREDIENT_STANDARDIZATION_NOT_VALIDATED")

            readiness = (
                "REVIEW_BEFORE_RXNORM_MAPPING"
                if review
                else "READY_FOR_RXNORM_CANDIDATE"
            )

            component_rows.append({
                **ingredient_search_fields(comp_clean),
                "dav_row_id": did,
                "component_order": comp["component_order"],
                "dav_registration_number": p["dav_registration_number"],
                "dav_drug_name": p["dav_drug_name"],
                "dav_active_ingredient_raw": raw_ing,
                "ingredient_component_clean": comp_clean,
                "ingredient_normalized_candidate": ing_norm,
                "ingredient_normalization_status": ing_status,
                "precise_ingredient_raw": precise,
                "equivalent_strength_raw": equiv,
                "strength_basis_review": basis_review,
                "dav_strength_raw": raw_str,
                "component_strength_source_text": strength_seg,
                "component_strength_raw": st["component_strength_raw"],
                "strength_value": st["strength_value"],
                "strength_unit": st["strength_unit"],
                "denominator_value": st["denominator_value"],
                "denominator_unit": st["denominator_unit"],
                "strength_normalized": st["strength_normalized"],
                "strength_compare_value": st["strength_compare_value"],
                "strength_compare_unit": st["strength_compare_unit"],
                "strength_type": st["strength_type"],
                "strength_parse_status": st["strength_parse_status"],
                "strength_source_rule": st["strength_source_rule"],
                "strength_range_low": st["strength_range_low"],
                "strength_range_high": st["strength_range_high"],
                "dav_dosage_form_raw": form,
                "dav_dosage_form_norm_vi": (
                    p["dav_dosage_form_norm_vi"]
                    or norm_ascii(form)
                ),
                "dav_form_group": fg,
                "dav_route_hint": route,
                "route_hint_confidence": conf,
                "dav_unit": p["dav_unit"],
                "mapping_readiness_v2": readiness,
                "review_reason": "|".join(dict.fromkeys(review)),
            })

    # ------------------ Form-route dictionary ------------------
    form_counts = Counter(
        (
            r["dav_dosage_form_raw"],
            r["dav_dosage_form_norm_vi"],
        )
        for r in product_rows
    )

    form_rows = []

    for (raw, norm), count in form_counts.most_common():
        fg, route, conf = form_route_hint(raw)

        form_rows.append({
            "dav_dosage_form_raw": raw,
            "dav_dosage_form_norm_vi": norm,
            "row_count": str(count),
            "dav_form_group": fg,
            "dav_route_hint": route,
            "route_hint_confidence": conf,
            "route_hint_status": (
                "DERIVED_HINT_NOT_RXNORM_VERIFIED"
                if route
                else "REVIEW_ROUTE"
            ),
            "rxnorm_df_canonical": "",
            "rxnorm_df_rxcui": "",
            "rxnorm_df_verification_status": "TO_BE_CURATED_AGAINST_RXNORM_DF",
        })

    # ------------------ CSV writer ------------------
    def write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    product_path = output_dir / "dav_product_normalized_v2.csv"
    component_path = output_dir / "dav_component_normalized_v2.csv"
    form_path = output_dir / "dav_form_route_v2.csv"

    write_csv(product_path, PRODUCT_HEADERS, product_rows)
    write_csv(component_path, COMPONENT_HEADERS, component_rows)
    write_csv(form_path, FORM_HEADERS, form_rows)

    ready_rows = [
        r for r in component_rows
        if r["mapping_readiness_v2"] == "READY_FOR_RXNORM_CANDIDATE"
    ]

    review_rows = [
        r for r in component_rows
        if r["mapping_readiness_v2"] == "REVIEW_BEFORE_RXNORM_MAPPING"
    ]

    ready_path = output_dir / "dav_component_normalized_v2_READY.csv"
    review_path = output_dir / "dav_component_normalized_v2_REVIEW_QUEUE.csv"

    write_csv(ready_path, COMPONENT_HEADERS, ready_rows)
    write_csv(review_path, COMPONENT_HEADERS, review_rows)

    # ------------------ QC ------------------
    parse_counts = Counter(r["strength_parse_status"] for r in component_rows)
    review_reasons = Counter()

    for r in review_rows:
        for reason in filter(None, r["review_reason"].split("|")):
            review_reasons[reason] += 1

    qc_path = output_dir / "dav_normalization_qc_v2.csv"

    qc = [
        ["metric", "value"],
        ["component_source_mode", component_source_mode],
        ["product_rows_total", len(product_rows)],
        ["component_rows_total", len(component_rows)],
        ["multi_ingredient_products", sum(
            1 for r in product_rows
            if int(r["component_count"]) > 1
        )],
        ["unique_dav_dosage_form_strings", len(form_rows)],
        ["ready_for_rxnorm_candidate", len(ready_rows)],
        ["review_before_rxnorm_mapping", len(review_rows)],
        [
            "ready_rate_percent",
            round(len(ready_rows) * 100 / len(component_rows), 2)
            if component_rows else 0,
        ],
    ]

    qc += [
        [f"strength_parse__{k}", v]
        for k, v in parse_counts.most_common()
    ]

    qc += [
        [f"review_reason__{k}", v]
        for k, v in review_reasons.most_common()
    ]

    with qc_path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(qc)

    # ------------------ Package ------------------
    zip_path = output_dir / "DAV_Normalized_v2_Package.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for pth in [
            product_path,
            component_path,
            ready_path,
            review_path,
            form_path,
            qc_path,
        ]:
            z.write(pth, arcname=pth.name)

    # ------------------ Console summary ------------------
    print("=" * 72)
    print("DAV NORMALIZATION COMPLETED")
    print("=" * 72)
    print(f"Input DAV:             {dav_csv}")
    print(f"Component source:      {component_csv or 'RAW DAV SPLIT'}")
    print(f"Products:              {len(product_rows)}")
    print(f"Components:            {len(component_rows)}")
    print(f"READY:                 {len(ready_rows)}")
    print(f"REVIEW:                {len(review_rows)}")
    print(f"Output directory:      {output_dir}")
    print(f"ZIP package:           {zip_path}")
    print("=" * 72)

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Normalize Vietnamese DAV drug data for RxNorm mapping."
    )

    parser.add_argument(
        "--dav",
        required=True,
        help="Path to DAV CSV, e.g. dav_mapping_ready.csv",
    )

    parser.add_argument(
        "--components",
        default=None,
        help=(
            "Optional pre-split DAV component CSV. "
            "Example: rxnorm_dav_mapping_clean.csv. "
            "Only DAV-side ingredient/component columns are reused."
        ),
    )

    parser.add_argument(
        "--output-dir",
        default="dav_normalized_v2_output",
        help="Output directory.",
    )

    args = parser.parse_args()

    dav_csv = Path(args.dav).expanduser().resolve()
    component_csv = (
        Path(args.components).expanduser().resolve()
        if args.components
        else None
    )
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not dav_csv.exists():        raise FileNotFoundError(f"DAV input not found: {dav_csv}")

    if component_csv and not component_csv.exists():
        raise FileNotFoundError(f"Component input not found: {component_csv}")

    normalize(
        dav_csv=dav_csv,
        component_csv=component_csv,
        output_dir=output_dir,
    )

if __name__ == "__main__":
    main()
