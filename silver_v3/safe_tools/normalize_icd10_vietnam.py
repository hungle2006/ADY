#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, csv, re, unicodedata
from collections import Counter
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

def _col_index(ref):
    m = re.match(r"([A-Z]+)", ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1

def read_first_sheet_xlsx(path):
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS}si"):
                t = si.find(f"{NS}t")
                if t is not None:
                    shared.append(t.text or "")
                else:
                    shared.append("".join((rt.text or "") for rt in si.findall(f"{NS}r/{NS}t")))

        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        relmap = {x.attrib["Id"]: x.attrib["Target"] for x in rels}
        first = wb.find(f"{NS}sheets")[0]
        target = relmap[first.attrib[rid_ns]].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")

        rows = []
        max_col = 0
        for _, elem in ET.iterparse(z.open(target), events=("end",)):
            if elem.tag == f"{NS}row":
                vals = {}
                for c in elem.findall(f"{NS}c"):
                    idx = _col_index(c.attrib.get("r", "A1"))
                    t = c.attrib.get("t")
                    v = c.find(f"{NS}v")
                    val = ""
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        tt = c.find(f".//{NS}t")
                        val = tt.text if tt is not None else ""
                    elif v is not None:
                        val = v.text or ""
                    vals[idx] = val
                    max_col = max(max_col, idx)
                row = [""] * (max_col + 1)
                for idx, val in vals.items():
                    if idx >= len(row):
                        row.extend([""] * (idx - len(row) + 1))
                    row[idx] = val
                rows.append(row)
                elem.clear()

    width = max(len(r) for r in rows) if rows else 0
    rows = [r + [""] * (width - len(r)) for r in rows]
    headers = rows[0]
    return [dict(zip(headers, r)) for r in rows[1:]]

def _xlsx_col_name(n):
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s

def _xml_escape(v):
    from xml.sax.saxutils import escape
    return escape("" if v is None else str(v), {'"': '&quot;'})

def _sheet_xml(headers, rows, widths=None, freeze=True):
    widths = widths or {}
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    ]
    if freeze:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        parts.append("<cols>")
        for idx, width in sorted(widths.items()):
            parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
        parts.append("</cols>")
    parts.append("<sheetData>")

    all_rows = [headers] + rows
    for r_idx, row in enumerate(all_rows, start=1):
        parts.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = f"{_xlsx_col_name(c_idx)}{r_idx}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                txt = _xml_escape(value)
                style = ' s="1"' if r_idx == 1 else ''
                parts.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{txt}</t></is></c>')
        parts.append("</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)

def write_simple_xlsx(path, sheets):
    import zipfile
    from pathlib import Path
    path = Path(path)

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    ]
    for i in range(1, len(sheets)+1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append("</Types>")

    workbook = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
        '<sheets>'
    ]
    for i, (name, _, _, _) in enumerate(sheets, start=1):
        workbook.append(f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
    workbook.append('</sheets></workbook>')

    wb_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    ]
    for i in range(1, len(sheets)+1):
        wb_rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
    wb_rels.append(f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    wb_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>'
        '</styleSheet>'
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", "".join(workbook))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
        z.writestr("xl/styles.xml", styles)
        for i, (_, headers, rows, widths) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(headers, rows, widths=widths))

def strip_accents(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c))

def clean_text(s):
    s = str(s or "").replace("\u00a0"," ").replace("–","-").replace("—","-")
    return re.sub(r"\s+"," ",s).strip()

def norm_text(s):
    s = strip_accents(clean_text(s)).lower().replace("đ","d")
    s = re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def strip_icd_marker(code):
    code = clean_text(code).upper().replace(" ","")
    markers = ""
    for marker in ("*","†"):
        if marker in code:
            markers += marker
            code = code.replace(marker,"")
    review = []
    if "/" in code:
        if re.fullmatch(r"[A-Z][0-9]{2}/[0-9A-Z]{1,4}", code):
            code = code.replace("/", ".")
            review.append("SOURCE_SLASH_NORMALIZED_TO_DOT")
        else:
            review.append("NONSTANDARD_SLASH_IN_CODE")
    return code, markers, review

def canonical_icd10(code):
    code, markers, review = strip_icd_marker(code)
    if "." not in code and re.fullmatch(r"[A-Z][0-9]{2}[0-9A-Z]{1,4}", code):
        code = code[:3] + "." + code[3:]
    valid = bool(re.fullmatch(r"[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?", code))
    nodot = code.replace(".","")
    return code, nodot, nodot[:3], markers, valid, review

def load_source(path):
    if path.suffix.lower() == ".xlsx":
        records = read_first_sheet_xlsx(path)
    else:
        with path.open("rb") as stream:
            bom = stream.read(2)
        encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        with path.open(encoding=encoding, newline="") as stream:
            delimiter = "\t" if "\t" in stream.readline() else ","
            stream.seek(0)
            records = list(csv.DictReader(stream, delimiter=delimiter))
    if not records:
        raise ValueError("Empty ICD input")
    if {"icd_code", "code_level", "disease_name_vi"} <= records[0].keys():
        return records, 0
    required = {"model", "id", "parent_model", "parent_id", "name_vi", "name_en", "code"}
    if not required <= records[0].keys():
        raise ValueError("Expected ICD10_Vietnam flat export or ICD10_nodes_bilingual.csv")
    by_key = {(r["model"], r["id"]): r for r in records}
    if len(by_key) != len(records):
        raise ValueError("Duplicate ICD tree node identity")
    flat = []
    for node in records:
        current = node
        path_nodes = {}
        seen = set()
        while True:
            key = current["model"], current["id"]
            if key in seen:
                raise ValueError(f"Cycle at {key}")
            seen.add(key)
            path_nodes.setdefault(current["model"], current)
            parent = current["parent_model"], current["parent_id"]
            if not any(parent):
                break
            if parent not in by_key:
                raise ValueError(f"Missing ICD parent {parent} for {key}")
            current = by_key[parent]
        if node["model"] not in {"type", "disease"}:
            continue
        row = dict(icd_code=node["code"], code_level=node["model"], disease_name_vi=node["name_vi"],
                   disease_name_en=node["name_en"], source=node.get("source"), source_node_id=node["id"],
                   source_parent_model=node["parent_model"], source_parent_id=node["parent_id"])
        for level in ("chapter", "section", "type"):
            ancestor = path_nodes.get(level, {})
            if level == "section":
                row["section_id"] = ancestor.get("id")
            else:
                row[level + "_code"] = ancestor.get("code") or ancestor.get("id")
            for lang in ("vi", "en"):
                row[level + "_name_" + lang] = ancestor.get("name_" + lang)
        flat.append(row)
    if not flat:
        raise ValueError("ICD tree contains no type/disease nodes")
    return flat, len(records) - len(flat)

def normalize(input_xlsx: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    raw, structural = load_source(input_xlsx)
    rows = []
    review_counts = Counter()

    for i, r in enumerate(raw, start=1):
        code, nodot, code3, marker, valid, review = canonical_icd10(r.get("icd_code",""))
        type_code, _, _, type_marker, _, type_review = canonical_icd10(r.get("type_code",""))
        review += [f"TYPE_{x}" for x in type_review]
        if not valid:
            review.append("INVALID_CANONICAL_ICD10_FORMAT")
        if marker:
            review.append("ICD10_SPECIAL_MARKER_PRESERVED")
        for x in set(review):
            review_counts[x] += 1

        disease_vi = clean_text(r.get("disease_name_vi",""))
        disease_en = clean_text(r.get("disease_name_en",""))

        rows.append({
            "source_row_id": i,
            "icd_code_raw": clean_text(r.get("icd_code","")),
            "icd_code_canonical": code,
            "icd_code_nodot": nodot,
            "icd_code3": code3,
            "icd_code_marker": marker,
            "mapping_key_icd10": nodot,
            "disease_name_vi": disease_vi,
            "disease_name_vi_norm": norm_text(disease_vi),
            "disease_name_en": disease_en,
            "disease_name_en_norm": norm_text(disease_en),
            "code_level": clean_text(r.get("code_level","")).lower(),
            "chapter_code": clean_text(r.get("chapter_code","")),
            "chapter_name_vi": clean_text(r.get("chapter_name_vi","")),
            "chapter_name_en": clean_text(r.get("chapter_name_en","")),
            "section_id": clean_text(r.get("section_id","")).upper(),
            "section_name_vi": clean_text(r.get("section_name_vi","")),
            "section_name_en": clean_text(r.get("section_name_en","")),
            "type_code_raw": clean_text(r.get("type_code","")),
            "type_code_canonical": type_code,
            "type_code_marker": type_marker,
            "type_name_vi": clean_text(r.get("type_name_vi","")),
            "type_name_en": clean_text(r.get("type_name_en","")),
            "source": clean_text(r.get("source","")),
            "source_url": clean_text(r.get("source_url","")),
            "source_api_endpoint": clean_text(r.get("source_api_endpoint","")),
            "normalization_status": "REVIEW" if review else "READY",
            "review_reason": "|".join(dict.fromkeys(review)),
            "source_node_id": r.get("source_node_id", ""),
            "source_parent_model": r.get("source_parent_model", ""),
            "source_parent_id": r.get("source_parent_id", ""),
        })

    frequency = Counter(r["icd_code_canonical"] for r in rows if r["icd_code_canonical"])
    collisions = [r for r in rows if frequency[r["icd_code_canonical"]] > 1]
    for row in collisions:
        row["normalization_status"] = "REVIEW"
        row["review_reason"] = (row["review_reason"] + "|SHARED_CANONICAL_CODE").lstrip("|")
    review_counts["SHARED_CANONICAL_CODE"] = len(collisions)
    headers = list(rows[0])
    with (output_dir / "ICD10_canonical_collisions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(collisions)
    csv_path = output_dir / "ICD10_Vietnam_normalized.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader(); w.writerows(rows)

    qc = [
        ["metric","value"],
        ["input_rows",len(raw)],
        ["source_rows_total",len(raw) + structural],
        ["hierarchy_nodes_excluded",structural],
        ["canonical_collision_groups",sum(n > 1 for n in frequency.values())],
        ["output_rows",len(rows)],
        ["unique_icd_code_canonical",len({r["icd_code_canonical"] for r in rows})],
        ["ready_rows",sum(r["normalization_status"]=="READY" for r in rows)],
        ["review_rows",sum(r["normalization_status"]=="REVIEW" for r in rows)],
        ["special_marker_star",sum("*" in r["icd_code_marker"] for r in rows)],
        ["special_marker_dagger",sum("†" in r["icd_code_marker"] for r in rows)],
    ]
    qc += [[f"review_reason__{k}",v] for k,v in review_counts.most_common()]
    qc_path = output_dir / "ICD10_Vietnam_normalization_qc.csv"
    with qc_path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(qc)

    xlsx_rows = [[r[h] for h in headers] for r in rows]
    xlsx_path = output_dir / "ICD10_Vietnam_Normalized.xlsx"
    widths = {1:12,2:18,3:18,4:16,5:12,6:12,7:18,8:38,9:32,10:38,11:32,12:14,13:14,14:38,15:38,16:14,17:38,18:38,19:18,20:18,21:14,22:34,23:34,24:24,25:45,26:45,27:16,28:36}
    write_simple_xlsx(xlsx_path, [
        ("ICD10_NORMALIZED", headers, xlsx_rows, widths),
        ("QC_SUMMARY", qc[0], qc[1:], {1:40,2:20}),
    ])

    print("DONE")
    print("Input rows:", len(raw))
    print("Output rows:", len(rows))
    print("READY:", sum(r["normalization_status"]=="READY" for r in rows))
    print("REVIEW:", sum(r["normalization_status"]=="REVIEW" for r in rows))
    print("Excel:", xlsx_path)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Flat ICD CSV/TSV/XLSX or ICD10_nodes_bilingual.csv")
    ap.add_argument("--output-dir", default="icd10_vietnam_normalized_output")
    args = ap.parse_args()
    normalize(Path(args.input).resolve(), Path(args.output_dir).resolve())
