"""Offline check of the Bronze-to-normalizer handoff. Run from the repo root."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.bronze import crawl_icd10 as bronze
from silver_v3.safe_tools.normalize_icd10_vietnam import normalize


class IcdPipelineTest(unittest.TestCase):
    def test_crawler_to_normalized_rows(self):
        def response(session, url):
            nodes = [
                ("chapter", "I", "I", "false"),
                ("section", "A17", "A17", False),
                ("type", "A17", "A17", False),
                ("disease", "A17d", "A17†", True),
            ]
            index = next((i + 1 for i, node in enumerate(nodes)
                          if "/childs/" + node[0] + "?" in url), 0)
            model, node_id, code, leaf = nodes[index]
            return {"data": [{"model": model, "id": node_id, "is_leaf": leaf,
                              "data": {"code": code, "name": model}}]}

        with tempfile.TemporaryDirectory() as temp, patch.object(bronze, "get_json", response):
            run = Path(temp)
            bronze.crawl(run)
            self.assertEqual(json.loads((run / "icd10_crawl_manifest.json").read_text())["merged_nodes"], 4)
            normalize(run / "ICD10_nodes_bilingual.csv", run)
            with (run / "ICD10_Vietnam_normalized.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["icd_code_raw"] for r in rows], ["A17", "A17†"])
            self.assertTrue(all(r["chapter_code"] == "I" and r["section_id"] == "A17" for r in rows))
            self.assertTrue(all("SHARED_CANONICAL_CODE" in r["review_reason"] for r in rows))

    def test_empty_branch_rejected(self):
        with self.assertRaises(ValueError):
            bronze.node_list({"data": []}, "fixture")
