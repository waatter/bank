"""Validate locally exported manual study records; never fabricate participants.

Usage: python3 -m eval.user_trial [export.json ...] --output /tmp/trial-report
Writes /tmp/trial-report.md and .json. No network, external dependencies or inputs
from the bank application. Internal/example/emulation data remain separate.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
import sys
import uuid

SCHEMA = 'TROPOS-USER-TRIAL-1'
TASK_IDS = ('profile_correction', 'expected_vs_received', 'transfer_correction',
            'cancellation_impact', 'cancellation_confirmation', 'timeout_recovery')
ROOT_KEYS = {'schema', 'session_id', 'participant_id', 'record_kind', 'interaction_source',
             'consent', 'environment', 'created_at', 'tasks', 'exported_at', 'export_index'}
TASK_KEYS = {'id', 'status', 'started_at', 'ended_at', 'duration', 'completed', 'help_count',
             'misunderstanding_count', 'misunderstanding_notes', 'comprehension_answer',
             'comprehension_correct', 'feedback'}
TEXT_KEYS = ('misunderstanding_notes', 'comprehension_answer', 'feedback')
COUNTS = ('help_count', 'misunderstanding_count')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def keys(value, expected, label):
    require(type(value) is dict and set(value) == expected,
            f'{label}: fields must exactly match schema (extra identity fields forbidden)')


def timestamp(value, label):
    require(isinstance(value, str), f'{label}: timestamp required')
    require(bool(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', value)),
            f'{label}: UTC ISO timestamp ending Z required')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{label}: invalid calendar timestamp') from None


def nullable_text(value, label):
    require(value is None or (isinstance(value, str) and 0 < len(value.strip()) <= 1000
                             and len(value) <= 1000), f'{label}: null or 1–1000 characters required')
    if value is not None:
        require(not re.search(r'https?://|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\d{7,}', value, re.I),
                f'{label}: possible direct identifier; remove before analysis')


def validate_record(record):
    """Fail closed on malformed/unknown fields. This cannot authenticate a human."""
    keys(record, ROOT_KEYS, 'record')
    require(record['schema'] == SCHEMA, 'unsupported schema')
    require(isinstance(record['session_id'], str), 'session_id: UUID required')
    try:
        require(str(uuid.UUID(record['session_id'])) == record['session_id'], 'session_id: canonical UUID required')
    except (ValueError, AttributeError):
        raise ValueError('session_id: canonical UUID required') from None
    require(isinstance(record['participant_id'], str) and
            re.fullmatch(r'P\d{3,6}', record['participant_id'], flags=re.ASCII), 'participant_id: P + 3–6 digits required')
    require(record['record_kind'] in ('real_participant', 'internal_acceptance', 'example'), 'invalid record_kind')
    require(record['interaction_source'] in ('human_manual', 'automated_test'), 'invalid interaction_source')
    require(record['record_kind'] != 'real_participant' or record['interaction_source'] == 'human_manual',
            'automated operations cannot be real participants')
    keys(record['consent'], {'accepted', 'method', 'at'}, 'consent')
    consent = record['consent']
    require(consent['accepted'] is True, 'explicit affirmative consent required')
    method = 'participant_manual_checkbox' if record['interaction_source'] == 'human_manual' else 'internal_test_only'
    require(consent['method'] == method, 'consent method incompatible with operation source')
    created = timestamp(record['created_at'], 'created_at')
    consent_at = timestamp(consent['at'], 'consent.at')
    exported = timestamp(record['exported_at'], 'exported_at')
    require(consent_at <= created <= exported, 'invalid consent/create/export order')
    require(type(record['export_index']) is int and record['export_index'] >= 1, 'positive export_index required')
    keys(record['environment'], {'device', 'network', 'vpn', 'self_reported'}, 'environment')
    env = record['environment']
    require(env['device'] in ('desktop', 'real-phone', 'emulation'), 'invalid device')
    require(env['network'] in ('Wi-Fi', 'cellular', 'unknown'), 'invalid network')
    require(env['vpn'] in ('on', 'off', 'unknown') and env['self_reported'] is True, 'network/VPN must be self-reported')
    tasks = record['tasks']
    require(type(tasks) is list and len(tasks) == len(TASK_IDS), 'all six task slots required')
    for task, expected_id in zip(tasks, TASK_IDS):
        keys(task, TASK_KEYS, 'task')
        require(task['id'] == expected_id, 'task IDs/order must match the six-task protocol')
        status = task['status']
        require(status in ('not_started', 'in_progress', 'ended'), 'invalid task state')
        for field in COUNTS:
            value = task[field]
            require(value is None or (type(value) is int and 0 <= value <= 9007199254740991),
                    f'{expected_id}.{field}: null or safe nonnegative integer required')
        for field in TEXT_KEYS:
            nullable_text(task[field], f'{expected_id}.{field}')
        require(task['completed'] is None or type(task['completed']) is bool, 'completed must be boolean or null')
        require(task['comprehension_correct'] is None or type(task['comprehension_correct']) is bool,
                'comprehension_correct must be boolean or null')
        require(task['comprehension_correct'] is None or task['comprehension_answer'] is not None,
                'comprehension judgment requires original answer summary')
        if status == 'not_started':
            require(all(task[k] is None for k in TASK_KEYS - {'id', 'status'}),
                    'unstarted tasks cannot have results, timing or counts')
            continue
        start = timestamp(task['started_at'], f'{expected_id}.started_at')
        require(created <= start <= exported, 'task start outside session')
        if status == 'in_progress':
            require(all(task[k] is None for k in ('ended_at', 'duration', 'completed', *TEXT_KEYS, 'comprehension_correct', *COUNTS)),
                    'in-progress export must not imply a submitted result')
            continue
        end = timestamp(task['ended_at'], f'{expected_id}.ended_at')
        require(start <= end <= exported, 'invalid task end order')
        require(type(task['completed']) is bool, 'ended task requires explicit completion decision')
        duration = task['duration']
        require(type(duration) in (float, int) and math.isfinite(duration) and duration >= 0,
                'duration: raw finite nonnegative seconds required for ended tasks')
    require(sum(t['status'] == 'in_progress' for t in tasks) <= 1, 'only one task may be active')
    intervals = sorted((timestamp(t['started_at'], 'start'),
                        timestamp(t['ended_at'], 'end') if t['ended_at'] else exported)
                       for t in tasks if t['status'] != 'not_started')
    require(all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])), 'task timing overlaps')
    return record


def metric(values, expected):
    """Keep raw counts, known denominator and missingness together."""
    known = [v for v in values if v is not None]
    return {'raw': known, 'n_observed': len(known), 'n_missing': expected - len(known),
            'sum': sum(known) if known else None,
            'mean': statistics.mean(known) if known else None,
            'median': statistics.median(known) if known else None}


def summarize(records):
    """Count only explicitly labeled manual non-emulated real sessions in results."""
    for record in records:
        validate_record(record)
    require(len({r['session_id'] for r in records}) == len(records),
            'duplicate session exports: select exactly one snapshot per session; do not double-count')
    real = [r for r in records if r['record_kind'] == 'real_participant'
            and r['interaction_source'] == 'human_manual' and r['environment']['device'] != 'emulation']
    ended_participants = {r['participant_id'] for r in real if any(t['status'] == 'ended' for t in r['tasks'])}
    task_rows = []
    for task_id in TASK_IDS:
        tasks = [t for r in real for t in r['tasks'] if t['id'] == task_id]
        ended = [t for t in tasks if t['status'] == 'ended']
        n = len(ended)
        success = sum(t['completed'] is True for t in ended)
        correct = [t['comprehension_correct'] for t in ended if t['comprehension_correct'] is not None]
        task_rows.append({'id': task_id, 'n_eligible_sessions': len(tasks),
                          'n_started': sum(t['status'] != 'not_started' for t in tasks),
                          'n_ended': n, 'n_in_progress': sum(t['status'] == 'in_progress' for t in tasks),
                          'n_not_started': sum(t['status'] == 'not_started' for t in tasks),
                          'n_completed': success, 'completion_denominator': n,
                          'completion_rate': success / n if n else None,
                          'n_completed_without_help': sum(t['completed'] and t['help_count'] == 0 for t in ended),
                          'n_completed_with_help': sum(t['completed'] and t['help_count'] is not None and t['help_count'] > 0 for t in ended),
                          'n_completed_help_unknown': sum(t['completed'] and t['help_count'] is None for t in ended),
                          'help_count': metric([t['help_count'] for t in ended], n),
                          'duration': metric([t['duration'] for t in ended], n),
                          'misunderstanding_count': metric([t['misunderstanding_count'] for t in ended], n),
                          'comprehension': {'n_correct': sum(correct), 'n_incorrect': len(correct) - sum(correct),
                                            'denominator': len(correct), 'n_missing': n - len(correct),
                                            'correct_rate': sum(correct) / len(correct) if correct else None},
                          'n_feedback_observed': sum(t['feedback'] is not None for t in ended),
                          'n_feedback_missing': sum(t['feedback'] is None for t in ended)})
    excluded = [{'session_id': r['session_id'], 'record_kind': r['record_kind'],
                 'device': r['environment']['device'],
                 'reason': 'internal_acceptance' if r['record_kind'] == 'internal_acceptance' else
                           'example' if r['record_kind'] == 'example' else 'emulation'}
                for r in records if r not in real]
    return {'schema': 'TROPOS-USER-TRIAL-REPORT-1',
            'status': 'results_available' if ended_participants else 'not_conducted',
            'status_label': '已有人工自报样本结果' if ended_participants else '未开展 / 尚无符合口径的已结束真人任务',
            'n_input_sessions': len(records), 'n_eligible_sessions': len(real),
            'n_real_participants': len(ended_participants),
            'n_consented_participant_ids': len({r['participant_id'] for r in real}),
            'n_repeat_sessions': len(real) - len({r['participant_id'] for r in real}),
            'excluded_sessions': excluded, 'tasks': task_rows,
            'environment_counts': {field: {value: sum(r['environment'][field] == value for r in real)
                                          for value in sorted({r['environment'][field] for r in real})}
                                   for field in ('device', 'network', 'vpn')},
            'limitations': ['真人身份与设备网络均未技术认证；自动操作不得标为真人。',
                            '未开始、未结束与未采集不按零分；次数为原始计数，不推断总体表现。',
                            '重复参与者按编号去重人数，任务按场次统计；重复场次不是独立样本。',
                            '耗时是原始秒数（含求助与切页），不推断实际网络延迟；设备休眠或校时可能影响计时。',
                            '校验无法证明文件未被外部修改；保留原导出，不覆盖或静默修订。']}


def markdown(report):
    lines = ['# TROPOS 真实用户试用汇总', '', report['status_label'], '',
             f"输入场次：{report['n_input_sessions']}；符合口径场次：{report['n_eligible_sessions']}；"
             f"至少结束一项任务的真人匿名编号：{report['n_real_participants']}；重复场次：{report['n_repeat_sessions']}。",
             '', '内部验收、示例及设备模拟排除场次：' + str(len(report['excluded_sessions'])) + '。', '',
             '| 任务 | 开始 / 符合口径场次 | 完成 / 已结束 | 完成且无帮助 / 有帮助 / 帮助未知 | 帮助合计 / 有记录 | 原始耗时秒 / 有记录 | 理解错误合计 / 有记录 | 理解正确 / 已核对 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    def total(item):
        return '未采集 / 0' if item['sum'] is None else f"{item['sum']} / {item['n_observed']}"
    for t in report['tasks']:
        lines.append(f"| {t['id']} | {t['n_started']} / {t['n_eligible_sessions']} | "
                     f"{t['n_completed']} / {t['n_ended']} | {t['n_completed_without_help']} / {t['n_completed_with_help']} / {t['n_completed_help_unknown']} | "
                     f"{total(t['help_count'])} | {total(t['duration'])} | "
                     f"{total(t['misunderstanding_count'])} | {t['comprehension']['n_correct']} / {t['comprehension']['denominator']} |")
    lines.extend(['', '分母为 0 时无可用结果，不能读成 0% 成功。JSON 保留每项原始数字、缺失数和分母。', '',
                  '设备与网络（自报，按符合口径场次）：`' + json.dumps(report['environment_counts'], ensure_ascii=False) + '`', '',
                  *['- ' + limitation for limitation in report['limitations']], ''])
    if report['excluded_sessions']:
        lines.extend(['排除清单（不计入真人结果）：', '',
                      *[f"- {r['session_id']}：{r['reason']} / {r['device']}" for r in report['excluded_sessions']], ''])
    return '\n'.join(lines)


def load_record(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON field')
            result[key] = value
        return result
    with Path(path).open(encoding='utf-8') as handle:
        return validate_record(json.load(handle, object_pairs_hook=unique_pairs))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='*', help='One selected JSON export per session')
    parser.add_argument('--output', type=Path, help='Report stem or .md/.json path; writes both formats without overwriting')
    args = parser.parse_args(argv)
    try:
        records = []
        for index, path in enumerate(args.inputs, 1):
            try:
                records.append(load_record(path))
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError(f'input #{index}: invalid export ({error})') from None
        report = summarize(records)
        if args.output:
            base = args.output.with_suffix('') if args.output.suffix in ('.json', '.md') else args.output
            destinations = [Path(str(base) + suffix) for suffix in ('.json', '.md')]
            require(all(not p.exists() for p in destinations), 'report already exists; choose a new output stem to preserve the original')
            require(all(p.resolve() not in {Path(i).resolve() for i in args.inputs} for p in destinations), 'output must not replace input')
            base.parent.mkdir(parents=True, exist_ok=True)
            for path, content in zip(destinations, [json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', markdown(report)]):
                with path.open('x', encoding='utf-8') as handle:
                    handle.write(content)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OverflowError) as error:
        # Validation messages contain field names, never record values/feedback.
        print(f'导出校验失败：{error}。未生成试用结论。', file=sys.stderr)
        return 2
    except OSError:
        print('无法读取导出或写入报告；检查本地文件、权限及输出是否已存在。未生成试用结论。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
