"""Synthetic schema fixtures only. These tests are not participant evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import http.client
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

from eval.user_trial import TASK_IDS, load_record, markdown, summarize, validate_record

ROOT = Path(__file__).resolve().parents[1]
UTF8_ENV = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}


def fixture(kind='internal_acceptance', device='desktop', participant='P001', ended=True):
    """Entirely fabricated in memory for validation; never a user trial result."""
    at = datetime(2026, 10, 27, tzinfo=timezone.utc)
    stamp = lambda seconds: (at + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
    tasks = [dict(id=task_id, status='not_started', started_at=None, ended_at=None,
                  duration=None, completed=None, help_count=None, misunderstanding_count=None,
                  misunderstanding_notes=None, comprehension_answer=None, comprehension_correct=None,
                  feedback=None) for task_id in TASK_IDS]
    if ended:
        tasks[0].update(status='ended', started_at=stamp(1), ended_at=stamp(11), duration=10.125,
                        completed=False, help_count=2, misunderstanding_count=None)
    return dict(schema='TROPOS-USER-TRIAL-1', session_id=str(uuid.uuid4()), participant_id=participant,
                record_kind=kind, interaction_source='human_manual',
                consent=dict(accepted=True, method='participant_manual_checkbox', at=stamp(0)),
                environment=dict(device=device, network='unknown', vpn='unknown', self_reported=True),
                created_at=stamp(0), exported_at=stamp(20), export_index=1, tasks=tasks)


class ValidationTests(unittest.TestCase):
    def test_same_origin_static_page_and_assets_are_served(self):
        from backend.server import create_server
        from backend.model import IntentPlanner, ModelConfig
        with tempfile.TemporaryDirectory() as directory:
            server=create_server(port=0,db_path=Path(directory)/'synthetic.sqlite3',planner=IntentPlanner(ModelConfig()))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                for path, mime in [('/study.html','text/html'),('/assets/study.js','text/javascript'),
                                   ('/assets/study.css','text/css'),('/assets/typography.css','text/css')]:
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    connection.request('GET',path); response=connection.getresponse()
                    content=response.read().decode(); connection.close()
                    with self.subTest(path=path):
                        self.assertEqual(response.status,200)
                        self.assertTrue(response.getheader('Content-Type').startswith(mime))
                        if path == '/study.html': self.assertIn("connect-src 'none'",content)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_valid_internal_partial_record(self):
        r = validate_record(fixture())
        self.assertEqual(r['tasks'][0]['duration'], 10.125)
        self.assertIsNone(r['tasks'][1]['completed'])

    def test_no_consent_and_automated_real_are_rejected(self):
        for update in [lambda r: r['consent'].update(accepted=False),
                       lambda r: r['consent'].update(method='prechecked'),
                       lambda r: r.update(record_kind='real_participant', interaction_source='automated_test')]:
            with self.subTest(update=update):
                r = fixture(); update(r)
                with self.assertRaises(ValueError):
                    validate_record(r)

    def test_unstarted_completed_or_in_progress_results_rejected(self):
        for changes in [dict(status='not_started'), dict(started_at=None),
                        dict(status='in_progress'), dict(completed=None)]:
            r = fixture(); r['tasks'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_record(r)

    def test_raw_duration_and_count_types(self):
        for field, values in [('duration', [-1, True, '10', float('nan'), float('inf'), None]),
                              ('help_count', [-1, True, 1.5, '0', 9007199254740992]),
                              ('misunderstanding_count', [-1, False, 2.3])]:
            for value in values:
                r = fixture(); r['tasks'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_record(r)

    def test_chronology_invalid_dates_and_overlap(self):
        bad = [lambda r: r.update(exported_at='2026-10-26T00:00:00Z'),
               lambda r: r['consent'].update(at='2026-10-28T00:00:00Z'),
               lambda r: r['tasks'][0].update(ended_at='2026-10-27T00:00:00Z'),
               lambda r: r.update(created_at='2026-02-31T00:00:00Z'),
               lambda r: r.update(created_at='2026-10-27T00:00:00'),
               lambda r: r['tasks'][1].update({**r['tasks'][0], 'id':TASK_IDS[1]})]
        for update in bad:
            r=fixture(); update(r)
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_record(r)

    def test_requires_six_distinct_slots_and_no_unknown_identity_fields(self):
        for update in [lambda r: r.update(name='forbidden'), lambda r: r['environment'].update(address='forbidden'),
                       lambda r: r.update(participant_id='Real Name'), lambda r: r.update(participant_id='P１２３'),
                       lambda r: r['tasks'].pop(), lambda r: r['tasks'][1].update(id=TASK_IDS[0]),
                       lambda r: r['tasks'][0].update(account_number='forbidden')]:
            r=fixture(); update(r)
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_record(r)

    def test_obvious_identifiers_and_unsubstantiated_comprehension_rejected(self):
        for value in ['Call 12345678901', 'person@example.com', 'https://private.example']:
            r=fixture(); r['tasks'][0]['feedback']=value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_record(r)
        r=fixture(); r['tasks'][0]['comprehension_correct']=True
        with self.assertRaises(ValueError): validate_record(r)

    def test_duplicate_json_keys_and_malformed_root_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'internal.json'
            p.write_text('{"schema":"old","schema":"new"}')
            with self.assertRaises(ValueError): load_record(p)
        for value in [None, [], 'text', {'schema': 'x'}]:
            with self.subTest(value=value), self.assertRaises(ValueError): validate_record(value)


class SummaryTests(unittest.TestCase):
    def test_no_input_no_fabricated_sample_no_zero_rate(self):
        r=summarize([])
        self.assertEqual(r['status'],'not_conducted')
        self.assertEqual(r['n_real_participants'],0)
        for t in r['tasks']:
            self.assertIsNone(t['completion_rate'])
            self.assertIsNone(t['help_count']['sum'])
            self.assertEqual(t['completion_denominator'],0)
        self.assertIn('未开展',markdown(r))

    def test_examples_internal_and_emulation_do_not_create_real_results(self):
        # Testing labels is not recruiting, observing, or claiming a real human.
        records=[fixture(), fixture('example'), fixture('real_participant','emulation')]
        r=summarize(records)
        self.assertEqual(r['n_input_sessions'],3)
        self.assertEqual(r['n_real_participants'],0)
        self.assertEqual(r['status'],'not_conducted')
        self.assertEqual({e['reason'] for e in r['excluded_sessions']},{'example','internal_acceptance','emulation'})

    def test_result_gate_requires_at_least_one_ended_task(self):
        r=fixture('real_participant',ended=False)
        self.assertEqual(summarize([r])['status'],'not_conducted')
        r['tasks'][0].update(status='in_progress',started_at='2026-10-27T00:00:01Z')
        report=summarize([r])
        self.assertEqual(report['n_real_participants'],0)
        self.assertEqual(report['tasks'][0]['n_in_progress'],1)
        self.assertIsNone(report['tasks'][0]['completion_rate'])

    def test_missing_not_zero_and_all_denominators_raw(self):
        a,b=fixture('real_participant'),fixture('real_participant',device='real-phone',participant='P002')
        b['tasks'][0].update(completed=True,help_count=None,misunderstanding_count=0,
                             comprehension_answer='风格不会改变意愿',comprehension_correct=True)
        r=summarize([a,b]); t=r['tasks'][0]
        self.assertEqual(r['n_real_participants'],2)
        self.assertEqual((t['n_completed'],t['completion_denominator'],t['completion_rate']),(1,2,.5))
        self.assertEqual(t['help_count'],dict(raw=[2],n_observed=1,n_missing=1,sum=2,mean=2,median=2))
        self.assertEqual(t['misunderstanding_count']['raw'],[0])
        self.assertEqual(t['misunderstanding_count']['n_missing'],1)
        self.assertEqual(t['comprehension']['denominator'],1)
        self.assertEqual(t['n_completed_help_unknown'],1)
        self.assertEqual(t['n_completed_without_help'],0)
        self.assertEqual(t['n_completed_with_help'],0)
        self.assertEqual(t['duration']['raw'],[10.125,10.125])
        self.assertEqual(r['environment_counts']['network'],{'unknown':2})

    def test_completed_with_zero_help_differs_from_unknown_or_positive_help(self):
        records=[fixture('real_participant',participant=f'P{i:03}') for i in range(1,4)]
        for record, help_count in zip(records,[0,2,None]):
            record['tasks'][0].update(completed=True,help_count=help_count)
        task=summarize(records)['tasks'][0]
        self.assertEqual([task[k] for k in ('n_completed_without_help','n_completed_with_help','n_completed_help_unknown')],[1,1,1])

    def test_duplicate_session_snapshots_fail_not_double_count(self):
        r=fixture()
        later=deepcopy(r); later['export_index']=2
        with self.assertRaisesRegex(ValueError,'duplicate session'): summarize([r,later])

    def test_repeated_participant_is_explicit_not_independent_people(self):
        r=summarize([fixture('real_participant'),fixture('real_participant')])
        self.assertEqual(r['n_real_participants'],1)
        self.assertEqual(r['n_repeat_sessions'],1)
        self.assertEqual(r['tasks'][0]['n_ended'],2)

    def test_cli_no_input_writes_both_reports_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'not-conducted.md'
            cmd=[sys.executable,'-m','eval.user_trial','--output',str(output)]
            r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',env=UTF8_ENV)
            self.assertEqual(r.returncode,0,r.stderr)
            data=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(data['status'],'not_conducted')
            self.assertEqual(data['n_input_sessions'],0)
            self.assertIn('未开展',output.read_text(encoding='utf-8'))
            original=output.read_bytes()
            self.assertEqual(subprocess.run(cmd,cwd=ROOT,capture_output=True,env=UTF8_ENV).returncode,2)
            self.assertEqual(output.read_bytes(),original)

    def test_cli_invalid_input_fail_closed_no_feedback_in_error(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'bad.json'; output=Path(directory)/'report'
            p.write_text('{"name":"PRIVATE_SENTINEL"}', encoding='utf-8')
            r=subprocess.run([sys.executable,str(ROOT/'eval/user_trial.py'),str(p),'--output',str(output)],
                             cwd=ROOT,capture_output=True,text=True,encoding='utf-8',env=UTF8_ENV)
            self.assertEqual(r.returncode,2)
            self.assertNotIn('PRIVATE_SENTINEL',r.stderr+r.stdout)
            self.assertIn('fields must exactly match schema',r.stderr)
            self.assertFalse(output.with_suffix('.json').exists())

    def test_frontend_internal_export_is_valid_and_excluded(self):
        # Cross-language schema contract; no clicks and no real-participant label.
        js="""const {createTrial}=require('./assets/study.js');
let ms=0; const clock=()=>({wall:new Date(Date.UTC(2026,9,27)+ms).toISOString(),monotonic:ms});
const t=createTrial({session_id:'01234567-89ab-4cde-8fab-0123456789ab',participant_id:'P999',record_kind:'internal_acceptance',interaction_source:'automated_test',consent:true,environment:{device:'emulation',network:'unknown',vpn:'unknown'}},clock);
t.start('profile_correction');ms=15125;t.end('profile_correction',{completed:false,help_count:null,misunderstanding_count:1,misunderstanding_notes:'模拟误解',comprehension_answer:'模拟错误回答',comprehension_correct:false,feedback:null});
console.log(JSON.stringify(t.exportRecord()));"""
        result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True,
                              encoding='utf-8',check=True)
        record=validate_record(json.loads(result.stdout))
        self.assertEqual(record['tasks'][0]['duration'],15.125)
        self.assertEqual(summarize([record])['n_real_participants'],0)


if __name__ == '__main__':
    unittest.main()
