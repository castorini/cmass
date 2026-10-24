"""Offline tests use placeholders, never benchmark questions or document IDs."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import export_system_runs as exporter
from hop_groundedness import evaluate


class HopTests(unittest.TestCase):
    def setUp(self):
        self.hops = [
            dict(dataset='example', query_id='example-q1', hop_id='example-h1', role='inclusion', doc_ids=['example-d1']),
            dict(dataset='example', query_id='example-q1', hop_id='example-h2', role='exclusion', doc_ids=['example-d1']),
            dict(dataset='example', query_id='example-q1', hop_id='example-h3', role='inclusion', doc_ids=[]),
            dict(dataset='example', query_id='example-q2', hop_id='example-h1', role='inclusion', doc_ids=['example-d2']),
        ]
        self.runs = [dict(dataset='example', model='example-model', query_id='example-q1', surfaced_docids=['example-d1']*2),
                     dict(dataset='example', model='example-model', query_id='example-q2', surfaced_docids=[])]

    def test_macro_shared_document_and_empty_evidence(self):
        row = evaluate(self.runs, self.hops)[0]
        self.assertAlmostEqual(row['hop_coverage_percent'], 100/3)
        self.assertEqual(row['hit_hops'], 2)
        self.assertEqual(row['hops_without_qrel_docs'], 1)
        self.assertEqual(row['exclusion_questions'], 1)
        self.assertEqual(row['exclusion_coverage_percent'], 100)
        self.assertEqual(row['inclusion_coverage_percent'], 25)
        self.assertEqual(row['zero_covered_questions'], 1)

    def test_missing_or_duplicate_questions_and_hops_fail(self):
        for runs, hops in [(self.runs[:1], self.hops), (self.runs*2, self.hops),
                           (self.runs, self.hops+self.hops[:1]), ([], self.hops)]:
            with self.assertRaises(ValueError): evaluate(runs, hops)

    def test_roles_are_not_mutually_exclusive_document_categories(self):
        row = evaluate(self.runs, self.hops)[0]
        self.assertEqual(row['hit_hops'], 2)
        self.assertEqual(row['exclusion_coverage_percent'], 100)
        no_exclusion = [h for h in self.hops if h['role']=='inclusion']
        self.assertIsNone(evaluate(self.runs,no_exclusion)[0]['exclusion_coverage_percent'])


class ExportTests(unittest.TestCase):
    def make_source(self, root):
        artifact = dict(metadata={'model':'example/example-model', 'tool_interface':'pyserini-rest-2tool',
                                  'supplied_docids':[], 'query':'<question>'},
                        status='completed', completion_source='assistant_text',
                        stats={'pi_search_failures':0,'timed_out':False,'search_calls':1,
                               'read_document_calls':1,'tool_calls_total':2},
                        tool_call_counts={'search':1,'read_document':1},
                        surfaced_docids=['example-d1','example-d1','example-d2'],
                        opened_docids=['example-d2','example-d3'],
                        result=[{'output':'<answer>'}], private_extra='must-not-be-exported')
        path=root/'artifact.json'
        data=json.dumps(artifact).encode();path.write_bytes(data)
        source=dict(dataset='bcp',model='gpt',query_id='example-q1',path=str(path),
                    sha256=hashlib.sha256(data).hexdigest(),outcome='completed')
        qrels=[dict(dataset='bcp',query_id='example-q1',doc_id='example-d1',relevance=1)]
        return source,qrels

    def test_allowlist_dedup_and_unjudged_pool(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(exporter.MODELS,{'gpt':'example-model'},clear=True), patch.dict(exporter.COUNTS,{'bcp':1},clear=True):
            root=Path(temp);source,qrels=self.make_source(root)
            receipt=exporter.export({'sources':[source]},qrels,root/'out')
            record=json.loads((root/'out/runs/bcp/gpt.jsonl').read_text())
            self.assertEqual(len(record['surfaced_docids']),2)
            self.assertNotIn('path',record)
            self.assertNotIn('query',record)
            self.assertNotIn('result',record)
            self.assertNotIn('must-not-be-exported',json.dumps(record))
            summary=receipt['pool_summaries'][0]
            self.assertEqual(summary['candidate_pairs'],3)
            self.assertEqual(summary['unjudged_pairs'],2)
            for name,details in receipt['files'].items():
                self.assertEqual(hashlib.sha256((root/'out'/name).read_bytes()).hexdigest(),details['sha256'])

    def test_hash_mismatch_duplicate_and_overwrite_fail(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(exporter.MODELS,{'gpt':'example-model'},clear=True), patch.dict(exporter.COUNTS,{'bcp':1},clear=True):
            root=Path(temp);source,qrels=self.make_source(root)
            with self.assertRaises(ValueError):
                exporter.export({'sources':[dict(source,sha256='0'*64)]},qrels,root/'bad')
            with self.assertRaises(ValueError):
                exporter.export({'sources':[source,source]},qrels,root/'duplicate')
            exporter.export({'sources':[source]},qrels,root/'out')
            with self.assertRaises(ValueError): exporter.export({'sources':[source]},qrels,root/'out')

    def test_public_repo_output_is_rejected(self):
        with self.assertRaises(ValueError):
            exporter.export({'sources':[]},[],Path(exporter.__file__).resolve().parent/'do-not-create')

    def test_published_aggregate_coverage_and_pool_arithmetic(self):
        root=Path(__file__).resolve().parent
        hop=json.loads((root/'hop_groundedness_results.json').read_text())
        pool=json.loads((root/'pooling_results.json').read_text())
        expected={(ds,m) for ds in exporter.COUNTS for m in exporter.MODELS}
        self.assertEqual({(r['dataset'],r['model']) for r in hop['results']},expected)
        self.assertEqual({(r['dataset'],r['model']) for r in pool['run_summaries']},expected)
        self.assertEqual(sum(r['questions'] for r in hop['results']),1269)
        for row in hop['results']:
            self.assertEqual(row['questions'],exporter.COUNTS[row['dataset']])
            self.assertLessEqual(row['hit_hops'],row['total_hops'])
            self.assertGreaterEqual(row['hop_coverage_percent'],0)
            self.assertLessEqual(row['hop_coverage_percent'],100)
        self.assertEqual(sum(r['candidate_pairs'] for r in pool['pool_summaries']),49885)
        for row in pool['pool_summaries']:
            self.assertEqual(row['candidate_pairs'],row['existing_positive_pairs']+row['unjudged_pairs'])


if __name__=='__main__': unittest.main()
