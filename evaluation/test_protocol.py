"""Offline regression tests; all example content is placeholder data."""
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metrics import aggregate
from prepare_inputs import prepare
from protocol import generation_messages, judge_request, prompt, render, verify_dsqa_verdict
from scripts.deobfuscate import decode_row


class ProtocolTests(unittest.TestCase):
    def verdict(self, indices, strict, relaxed):
        return dict(answers=[dict(text=f"<member {i}>", gold_indices=idx) for i, idx in enumerate(indices)],
                    strict_correct=strict, relaxed_correct=relaxed, reasoning="<rationale>")

    def test_dsqa_complete_subset_extra_and_empty(self):
        gold = ["<member A>", "<member B>"]
        for indices, strict, relaxed in [([[1], [0]], True, True), ([[0]], False, True),
                                         ([[0], []], False, False), ([], False, False)]:
            self.assertEqual(verify_dsqa_verdict(self.verdict(indices, strict, relaxed), gold), (strict, relaxed))

    def test_reject_inconsistent_or_malformed_judgment(self):
        for v in [self.verdict([[0]], True, True), self.verdict([[True]], False, True),
                  self.verdict([[0, 0]], False, True), self.verdict([[2]], False, True)]:
            with self.assertRaises(ValueError):
                verify_dsqa_verdict(v, ["<member A>", "<member B>"])

    def test_generation_conditions(self):
        for dataset in ("bcp", "trqa", "hotpotqa", "dsqa"):
            messages = generation_messages(dataset, "closedbook", "<question>")
            self.assertEqual([x["role"] for x in messages], ["system", "user"])
            self.assertEqual(messages[1]["content"], "<question>")
            self.assertIn("no tools", messages[0]["content"])
            self.assertEqual("complete set" in messages[0]["content"], dataset == "dsqa")
            text = generation_messages(dataset, "piika", "<question>")[0]["content"]
            self.assertNotIn("STRICT EVIDENCE POLICY", text)
            self.assertIn("Exact Answer:", text)
            self.assertEqual("complete set" in text, dataset == "dsqa")

    def test_judge_requests_are_tool_free_and_single_pass(self):
        for ds, gold in [("bcp", "<gold>"), ("dsqa", [dict(member="<gold>", aliases=[], value="")])]:
            body = judge_request(ds, "{{response}}", "<response>", gold)
            self.assertEqual(body["tools"], [])
            self.assertEqual(body["tool_choice"], "none")
            self.assertFalse(body["parallel_tool_calls"])
            self.assertEqual(body["reasoning"], {"effort": "high"})
            self.assertEqual(len(body["input"]), 1)
            self.assertIn("{{response}}", body["input"][0]["content"])
        self.assertEqual(render("{{question}} {{response}}", question="{{response}}", response="<response>"),
                         "{{response}} <response>")

    def test_answer_set_decoding_preserves_source(self):
        marker = "example"
        key = hashlib.sha256(marker.encode()).digest()
        def encode(s):
            return base64.b64encode(bytes(v ^ key[i % len(key)] for i, v in enumerate(s.encode()))).decode()
        members = [dict(member="<member>", aliases=["<alias>"], value="")]
        row = dict(id=encode("<id>"), answer_set=encode(json.dumps(members)), canary=marker)
        original = dict(row)
        self.assertEqual(decode_row(row)["answer_set"], members)
        self.assertEqual(row, original)

    def test_private_preparation_and_no_overwrite(self):
        rows = {
            "queries.jsonl": [dict(id="1", question="<question>\n", answer="<answer>")],
            "qrels.jsonl": [dict(query_id="1", doc_id="<doc>", relevance=1)],
            "answer_sets.jsonl": [dict(id="1", answer_set=[dict(member="<answer>", aliases=[], value="")])],
        }
        with tempfile.TemporaryDirectory() as tmp, patch("prepare_inputs.COUNTS", {"dsqa": 1}), \
                patch("prepare_inputs.QREL_COUNTS", {"dsqa": 1}), \
                patch("prepare_inputs.fetch", side_effect=lambda ds, name: (rows[name], "<hash>")):
            out = Path(tmp) / "private"
            prepare("dsqa", out)
            self.assertEqual((out / "generation/questions.tsv").read_text(), "1\t<question>\n")
            self.assertNotIn("<answer>", (out / "generation/questions.tsv").read_text())
            gold = json.loads((out / "evaluation/ground_truth.jsonl").read_text())
            self.assertFalse(gold["ordered"])
            self.assertEqual(gold["answer_set"], rows["answer_sets.jsonl"][0]["answer_set"])
            with self.assertRaises(FileExistsError):
                prepare("dsqa", out)

    def test_recall_macro_union_and_unanswered(self):
        records = [dict(query_id="1", outcome="completed", correct=True, surfaced_docids=["<a>", "<a>", "<b>"],
                        search_calls=2, read_document_calls=1),
                   dict(query_id="2", outcome="model_unanswered", correct=False, surfaced_docids=[],
                        search_calls=1, read_document_calls=0)]
        qrels = [dict(query_id="1", doc_id=d, relevance=1) for d in ["<a>", "<b>", "<c>", "<d>"]]
        qrels.append(dict(query_id="2", doc_id="<z>", relevance=1))
        result = aggregate(records, qrels)
        self.assertEqual(result["recall_at_all_percent"], 25)
        self.assertEqual(result["accuracy_percent"], 50)
        self.assertEqual(result["mean_tool_calls"], 2)
        self.assertEqual(result["model_unanswered"], 1)
        with self.assertRaises(ValueError):
            aggregate(records + records[:1], qrels)
        bad = copy.deepcopy(records)
        bad[1]["outcome"] = "infrastructure_error"
        with self.assertRaises(ValueError):
            aggregate(bad, qrels)

    def test_reported_aggregates(self):
        result = json.loads(Path(__file__).with_name("results.json").read_text())
        self.assertEqual(len(result["accuracy"]), 24)
        self.assertEqual(len(result["retrieval"]), 12)
        keys = {(r["dataset"], r["model"], r["condition"]) for r in result["accuracy"]}
        self.assertEqual(len(keys), 24)
        for row in result["accuracy"]:
            n = row["questions"]
            self.assertEqual(row["completed_answers"] + row["model_unanswered"], n)
            names = ["strict", "relaxed"] if row["dataset"] == "dsqa" else [""]
            for name in names:
                prefix = name + "_" if name else ""
                self.assertAlmostEqual(row[prefix + "accuracy_percent"], 100*row[prefix + "correct"]/n)
        for row in result["retrieval"]:
            self.assertEqual(row["search_calls"] + row["read_calls"], row["total_tool_calls"])
            self.assertAlmostEqual(row["mean_tool_calls"], row["total_tool_calls"]/row["questions"])
        self.assertEqual(sum(r["failed_subset"] for r in result["qrels_only_sol"]), 34)


if __name__ == "__main__":
    unittest.main()
