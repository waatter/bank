"""Known synthetic mechanism regression, with independent integer-fen cash checks.

Run: python3 -m eval.life_benchmark --output reports/life-planning.json
Only the planner's public analyze entry point is used. The scoring oracle never
calls its cash-path or stress helpers. This is not a blind or statistical test.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import time

from backend.life_planner import analyze
from backend.service import BankService

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'backend/life_scenarios.json'
PERSONAS = ('A', 'B', 'C')
CATEGORIES = ('living', 'work', 'health', 'moving', 'shopping', 'family', 'travel', 'education')
POLICIES = ('original', 'buffer', 'balanced')
STRESSES = ('baseline', 'income_delay', 'expense_shock', 'combined')
LABELS = {'original': '保持原安排', 'buffer': '缓冲优先', 'balanced': '兼顾生活与现金'}
SETTINGS = {'illiquidAssets': 300000, 'delayDays': 15, 'expenseShock': 1200,
            'shockDay': 3, 'goalAmount': 500}
SEED = 20261021  # Stable case/scene identity; there is no random sampling.
WEIGHTS = {'essential': 100, 'important': 30, 'flexible': 5}
SOURCE_FILES = ('backend/life_planner.py', 'backend/life_service.py', 'backend/service.py',
                'backend/fixtures.json', 'eval/life_benchmark.py')


def cents(value):
    """Strict independent conversion; do not round away oracle mismatches."""
    if isinstance(value, bool):
        raise ValueError('Boolean is not a money amount')
    number = Decimal(str(value))
    if not number.is_finite() or number * 100 != (number * 100).to_integral_value():
        raise ValueError(f'Invalid or sub-fen amount: {value}')
    return int(number * 100)


def oracle_path(snapshot, allocations, settings, stress_id, goal_amount=0, extra_cash=0):
    """Start with the bank forecast, then account for each explicit delta once.

    Expected salary moves between future dates; it never changes today's cash.
    Goal funding is a hypothetical immediate outflow. extra_cash is solely a
    sensitivity assumption, not an observed balance or an actual deposit.
    """
    if stress_id not in STRESSES:
        raise ValueError(stress_id)
    base = [cents(value) for value in snapshot['cash']['pts']]
    if len(base) != 31:
        raise ValueError('Expected today and 30 daily forecast points')
    delay = settings['delayDays'] if stress_id in ('income_delay', 'combined') else 0
    shock = cents(settings['expenseShock']) if stress_id in ('expense_shock', 'combined') else 0
    salaries = [event for event in snapshot['cash']['events']
                if event.get('type') == 'salary' and cents(event.get('amount', 0)) > 0]
    result = []
    for day, observed in enumerate(base):
        amount = observed - cents(goal_amount) + cents(extra_cash)
        for event in salaries:
            old_day, new_day = event['day'], event['day'] + delay
            if 1 <= old_day <= day:
                amount -= cents(event['amount'])
            if 1 <= new_day <= day:
                amount += cents(event['amount'])
        if day >= settings['shockDay']:
            amount -= shock
        for allocation in allocations:
            if day >= allocation['day']:
                amount -= cents(allocation['amount'])
        result.append(amount)
    return result


def path_metrics(points, reserve):
    minimum = min(points)
    return {'min': minimum, 'minDay': points.index(minimum),
            'breachDays': sum(value < reserve for value in points),
            'maxGap': max(0, reserve - minimum),
            'negativeCashDays': sum(value < 0 for value in points),
            'cashDeficit': max(0, -minimum)}


def permission_errors(scenes, allocations):
    """Verify choices against synthetic user permissions, not catalogue ability."""
    drafts = {scene['id']: scene for scene in scenes if scene.get('status', 'draft') == 'draft'}
    errors = []
    ids = [allocation['sceneId'] for allocation in allocations]
    if len(ids) != len(drafts) or set(ids) != set(drafts):
        errors.append({'field': 'allocation_scene_ids', 'observed': ids, 'expected': sorted(drafts)})
    for allocation in allocations:
        scene = drafts.get(allocation['sceneId'])
        if scene is None:
            continue
        day, amount, mode = allocation['day'], cents(allocation['amount']), allocation['mode']
        if type(day) is not int or not scene['earliestDay'] <= day <= scene['latestDay']:
            errors.append({'field': 'permitted_date', 'sceneId': scene['id'], 'observed': day})
        if mode == 'skipped':
            valid = amount == 0 and scene.get('allowSkip') is True and scene['importance'] != 'essential'
        elif mode == 'alternative':
            valid = (scene.get('allowAlternative') is True and scene.get('alternativeAmount') is not None
                     and 0 < amount == cents(scene['alternativeAmount']))
        elif mode in ('original', 'delayed'):
            valid = amount == cents(scene['amount']) and amount > 0
            if mode == 'original':
                valid = valid and day == scene['preferredDay']
        else:
            valid = False
        if not valid:
            errors.append({'field': 'permitted_amount_or_mode', 'sceneId': scene['id'], 'observed': allocation})
    return errors


def _adjustment_score(scenes, allocations):
    by_id = {scene['id']: scene for scene in scenes}
    total = 0
    for allocation in allocations:
        scene = by_id[allocation['sceneId']]
        weight = WEIGHTS[scene['importance']]
        if allocation['mode'] == 'skipped':
            total += weight * 2
            continue
        cost = weight * (allocation['day'] - scene['preferredDay']) / 30
        if allocation['mode'] == 'alternative':
            cost += weight * (1 - cents(allocation['amount']) / cents(scene['amount']))
        total += round(cost, 6)
    return round(total, 2)


def verify_policy(snapshot, scenes, settings, policy):
    errors = permission_errors(scenes, policy['allocations'])
    reserve = cents(snapshot['profile']['reserve'])
    goal = policy['metrics']['goalAmount']
    projections = {row['scenarioId']: row for row in policy['projections']}
    if len(policy['projections']) != 4 or set(projections) != set(STRESSES):
        errors.append({'field': 'stress_grid', 'observed': sorted(projections), 'expected': list(STRESSES)})
    checked = 0
    expected_metrics = []
    before_goal_minima = []
    for stress_id in STRESSES:
        expected = oracle_path(snapshot, policy['allocations'], settings, stress_id, goal)
        metrics = path_metrics(expected, reserve)
        expected_metrics.append(metrics)
        before_goal_minima.append(min(oracle_path(snapshot, policy['allocations'], settings, stress_id)))
        observed = projections.get(stress_id)
        if observed is None:
            continue
        checked += 1
        actual = [cents(value) for value in observed['pts']]
        if actual != expected:
            errors.append({'field': 'daily_cash', 'stress': stress_id,
                           'expected_cents': expected, 'observed_cents': actual})
        for field, wanted in metrics.items():
            actual_metric = cents(observed[field]) if field in ('min', 'maxGap', 'cashDeficit') else observed[field]
            if actual_metric != wanted:
                errors.append({'field': field, 'stress': stress_id, 'expected': wanted, 'observed': actual_metric})
    expected_goal = max(0, min(cents(settings['goalAmount']), min(before_goal_minima) - reserve,
                               cents(snapshot['balance']) - reserve))
    comparisons = {
        'worstGap': (cents(policy['metrics']['worstGap']), max(m['maxGap'] for m in expected_metrics)),
        'worstDeficit': (cents(policy['metrics']['worstDeficit']), max(m['cashDeficit'] for m in expected_metrics)),
        'breachDays': (policy['metrics']['breachDays'], max(m['breachDays'] for m in expected_metrics)),
        'goalAmount': (cents(goal), expected_goal),
        'deferredGoal': (cents(policy['metrics']['deferredGoal']), cents(settings['goalAmount']) - expected_goal),
        'expenseTotal': (cents(policy['metrics']['expenseTotal']), sum(cents(a['amount']) for a in policy['allocations'])),
        'adjustmentScore': (policy['metrics']['adjustmentScore'], _adjustment_score(scenes, policy['allocations'])),
    }
    for field, (observed, expected) in comparisons.items():
        if observed != expected:
            errors.append({'field': field, 'expected': expected, 'observed': observed})
    return {'checked_paths': checked, 'errors': errors}


def verify_sensitivity(snapshot, settings, policy, rows):
    errors = []
    for row in rows:
        values = [path_metrics(oracle_path(snapshot, policy['allocations'], settings, stress,
                                          policy['metrics']['goalAmount'], row['buffer']),
                               cents(snapshot['profile']['reserve'])) for stress in STRESSES]
        expected = {'worstGap': max(v['maxGap'] for v in values),
                    'breachDays': max(v['breachDays'] for v in values), 'min': min(v['min'] for v in values)}
        for field, wanted in expected.items():
            actual = row[field] if field == 'breachDays' else cents(row[field])
            if actual != wanted:
                errors.append({'field': f'sensitivity.{field}', 'buffer': row['buffer'],
                               'expected': wanted, 'observed': actual})
    return errors


def case_specs(catalogue):
    by_category = {scene['category']: scene for scene in catalogue}
    if len(catalogue) != 8 or set(by_category) != set(CATEGORIES):
        raise ValueError('Benchmark requires exactly one catalogue template for each of eight categories')
    specs = [(category, (category,)) for category in CATEGORIES]
    specs += [('essential_mix', ('health', 'family', 'work')),
              ('flexible_mix', ('living', 'shopping', 'travel', 'education'))]
    return [(case_id, [by_category[category] for category in categories]) for case_id, categories in specs]


def run_case(persona, case_id, templates):
    record = {'id': f'{persona}/{case_id}', 'persona': persona, 'case': case_id,
              'checked_paths': 0, 'errors': [], 'incomplete': False}
    try:
        scenes = []
        for index, template in enumerate(templates):
            scene = {key: deepcopy(value) for key, value in template.items() if key not in ('id', 'description')}
            scene.update(id=f'life-eval-{SEED}-{persona}-{case_id}-{index}', status='draft')
            scenes.append(scene)
        with tempfile.TemporaryDirectory(prefix='tropos-life-eval-') as directory:
            bank = BankService(Path(directory) / 'bank.sqlite3')
            session = bank.session(persona)
            user_id = bank.authenticate(session['token'])
            profile = {**session['snapshot']['profile'], 'readConsent': True}
            bank.save_profile(user_id, profile)
            snapshot = bank.snapshot(user_id)
            original = deepcopy((snapshot, scenes, SETTINGS))
            result = analyze(snapshot, scenes, deepcopy(SETTINGS))
            if (snapshot, scenes, SETTINGS) != original:
                record['errors'].append({'field': 'analysis_mutated_inputs'})
            if bank.snapshot(user_id) != snapshot:
                record['errors'].append({'field': 'analysis_mutated_bank_snapshot'})
            if result['financial']['reserve'] != profile['reserve'] or result['financial']['liquidAssets'] != snapshot['balance']:
                record['errors'].append({'field': 'observed_liquidity_or_reserve_changed'})
            if tuple(policy['id'] for policy in result['policies']) != POLICIES:
                record['errors'].append({'field': 'policy_grid'})
            for policy in result['policies']:
                check = verify_policy(snapshot, scenes, SETTINGS, policy)
                record['checked_paths'] += check['checked_paths']
                record['errors'] += [{'policy': policy['id'], **error} for error in check['errors']]
            balanced = next(policy for policy in result['policies'] if policy['id'] == 'balanced')
            record['errors'] += verify_sensitivity(snapshot, SETTINGS, balanced, result['sensitivity'])
            # Non-liquid assets are display context, never a source of main cash.
            without_assets = analyze(snapshot, scenes, {**SETTINGS, 'illiquidAssets': 0})
            if result['policies'] != without_assets['policies'] or result['sensitivity'] != without_assets['sensitivity']:
                record['errors'].append({'field': 'illiquid_assets_changed_spendable_cash'})
            if bank.snapshot(user_id) != snapshot:
                record['errors'].append({'field': 'sensitivity_or_asset_check_mutated_bank_snapshot'})
            record.update(input={'settings': deepcopy(SETTINGS), 'scenes': scenes,
                                 'snapshot': {key: deepcopy(snapshot[key]) for key in ('balance', 'goalFunded', 'cash', 'profile', 'user')}},
                          policies=result['policies'], sensitivity=result['sensitivity'],
                          breakpoint=result['breakpoint'], search=result['search'],
                          bank_snapshot_unchanged=bank.snapshot(user_id) == snapshot)
    except Exception as exc:
        record['incomplete'] = True
        record['errors'].append({'field': 'unexpected_exception', 'type': type(exc).__name__, 'message': str(exc)})
    return record


def _source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def _git_metadata():
    def read(arguments):
        result = subprocess.run(['git', *arguments], cwd=ROOT, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    status = read(['status', '--porcelain'])
    return {'revision': read(['rev-parse', 'HEAD']), 'dirty': bool(status) if status is not None else None,
            'status': status.splitlines() if status else [], 'status_available': status is not None}


def aggregate(records):
    result = {}
    for identity in POLICIES:
        rows = [policy['metrics'] for record in records for policy in record.get('policies', []) if policy['id'] == identity]
        result[identity] = {'label': LABELS[identity], 'cases': len(rows),
                            'cases_with_reserve_gap': sum(row['worstGap'] > 0 for row in rows),
                            'cases_with_cash_deficit': sum(row['worstDeficit'] > 0 for row in rows),
                            'zero_goal_cases': sum(row['goalAmount'] == 0 for row in rows)}
        for field in ('worstGap', 'breachDays', 'adjustmentScore', 'goalAmount'):
            values = [row[field] for row in rows]
            result[identity][field] = {'mean': round(sum(values) / len(values), 2) if values else None,
                                      'min': min(values) if values else None, 'max': max(values) if values else None}
    return result


def benchmark():
    started = time.perf_counter()
    source = _source_hashes()
    catalogue_bytes = CATALOGUE.read_bytes()
    specs = case_specs(json.loads(catalogue_bytes))
    records = [run_case(persona, case_id, templates) for persona in PERSONAS for case_id, templates in specs]
    failures = [{'case': record['id'], **error} for record in records for error in record['errors']]
    if _source_hashes() != source or CATALOGUE.read_bytes() != catalogue_bytes:
        failures.append({'case': 'provenance', 'field': 'source_changed_during_run'})
    comparisons = []
    for record in records:
        policies = {policy['id']: policy['metrics'] for policy in record.get('policies', [])}
        if set(policies) != set(POLICIES):
            continue
        comparisons.append({'case': record['id'], 'balanced_minus_original': {
            field: round(policies['balanced'][field] - policies['original'][field], 2)
            for field in ('worstGap', 'breachDays', 'adjustmentScore', 'goalAmount')}})
    return {'benchmark': 'TROPOS METIS-LIFE-2 known synthetic mechanism regression',
            'generated_at': datetime.now(timezone.utc).isoformat(), 'seed': SEED,
            'random_sampling': False, 'seed_use': '稳定合成场景标识；没有随机抽样',
            'input_count': len(records), 'expected_input_count': 30,
            'expected_path_count': 360, 'checked_path_count': sum(row['checked_paths'] for row in records),
            'checked_daily_point_count': sum(row['checked_paths'] for row in records) * 31,
            'grid': {'personas': list(PERSONAS), 'single_categories': list(CATEGORIES),
                     'combined_cases': ['essential_mix', 'flexible_mix'], 'policies': list(POLICIES), 'stresses': list(STRESSES)},
            'source_sha256': hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
            'source_files_sha256': source, 'catalogue_sha256': hashlib.sha256(catalogue_bytes).hexdigest(),
            'runtime': {'seconds': round(time.perf_counter() - started, 3), 'python': platform.python_version(),
                        'platform': platform.platform()}, 'git': _git_metadata(),
            'summary': aggregate(records), 'comparisons': comparisons, 'records': records, 'failures': failures,
            'limitations': [
                '30组预先定义且已知的合成输入：3个模拟账户×（8种单场景+2种组合）；不是盲测或真实用户样本。',
                '目录的跳过、替代和日期权限在本评估中被明确设为合成用户选择；这不表示界面可以自动替用户授权。',
                '独立oracle从银行snapshot.cash.pts出发核对生活规划增量；它不独立重建银行初始账本。',
                '共核对3种方案×4种压力情景的现金恒等式、权限和指标；未估计压力发生概率、福利或因果效果。',
                '目标储蓄从第0天假设转出，仅计算建议；没有生成或确认付款，没有接入真实银行。',
                '现金敏感性是假设额外增加可支付现金，未改变实际余额、用户现金底线或目标转入数额。',
                '非流动资产只作为展示信息；另以0元复算核对它没有提高可支付现金。',
                '调整分数使用已声明的规则权重，不能当作估计效用或真实偏好；有限离散搜索不保证全局最优。',
                '只报告这些已知输入下的工程差异与失败边界，不构成LLM成绩、泛化结论、浏览器验收或真实业务有效性证据。']}


def markdown(report):
    lines = ['# TROPOS 2.0 生活规划：已知合成输入的机制核对', '',
             f"本轮覆盖 {report['input_count']} 组固定输入、{report['checked_path_count']} 条现金路径、"
             f"{report['checked_daily_point_count']} 个逐日点；记录 {len(report['failures'])} 项核对失败。", '',
             '同一组输入下比较三种安排。金额单位为人民币；下表是30组案例的算术均值，不是概率或总体效果。', '',
             '| 方案 | 最坏底线缺口均值 | 低于底线天数均值 | 调整分数均值 | 目标转入建议均值 | 仍有底线缺口的案例 |',
             '|---|---:|---:|---:|---:|---:|']
    for identity in POLICIES:
        row = report['summary'][identity]
        lines.append(f"| {row['label']} | {row['worstGap']['mean']} | {row['breachDays']['mean']} | "
                     f"{row['adjustmentScore']['mean']} | {row['goalAmount']['mean']} | {row['cases_with_reserve_gap']}/{row['cases']} |")
    lines += ['', '## 逐案例取舍', '',
              '每格依次为“最坏底线缺口／低于底线天数／调整分数／目标转入建议”。缺口保留在报告中；额外现金假设和预计工资都没有成为今天的实际余额。', '',
              '| 案例 | 保持原安排 | 缓冲优先 | 兼顾生活与现金 |', '|---|---|---|---|']
    for record in report['records']:
        by_id = {policy['id']: policy['metrics'] for policy in record.get('policies', [])}
        values = []
        for identity in POLICIES:
            row = by_id.get(identity)
            values.append('未完成' if row is None else '／'.join(str(row[field]) for field in ('worstGap', 'breachDays', 'adjustmentScore', 'goalAmount')))
        lines.append(f"| {record['id']} | " + ' | '.join(values) + ' |')
    lines += ['', '## 核对方法与失败边界', '',
              '先通过真实 BankService 的临时数据库选择账户并主动确认读取同意，再取快照。独立oracle按整数分从银行既有路径逐日重算：移除原日期的预计工资、在延期后日期加回；扣除对应压力支出、所选生活支出和第0天的目标转入建议。工资移出30天窗口时不加回。', '',
              '同时逐项核对金额、日期和跳过／替代权限，核对缺口、天数、调整分数与目标建议。比较前后的银行完整快照必须相同。另用非流动资产为0的设置复算，并核对每档现金敏感性；这些额外核对不计入主表的360条路径。', '',
              '允许替代或跳过可能减少现金压力，同时改变原有生活安排；因此调整分数和现金指标都列出，不把任何单项优势当作所有维度都更好。仍有缺口的输入说明现有资金与已准许调整不足以守住底线，不能解释为缺口已被修复。', '']
    if report['failures']:
        lines += ['本轮实际核对失败如下，完整字段保留在JSON：', '', '```json',
                  json.dumps(report['failures'], ensure_ascii=False, indent=2), '```', '']
    else:
        lines += ['本轮已知输入的核对未记录失败；这不保证未覆盖输入、浏览器流程或真实银行业务同样成立。', '']
    lines += ['## 可复跑信息', '', f"- 固定种子：`{report['seed']}`（用于稳定场景标识；没有随机抽样）。",
              f"- 源码总指纹：`{report['source_sha256']}`。",
              f"- 目录指纹：`{report['catalogue_sha256']}`。",
              f"- Git revision：`{report['git']['revision']}`；工作区 dirty：`{report['git']['dirty']}`。",
              f"- Python：`{report['runtime']['python']}`；运行耗时：{report['runtime']['seconds']} 秒。",
              '- 命令：`python3 -m eval.life_benchmark --output reports/life-planning.json`。', '',
              'JSON保留每案例的输入、三种方案、四种压力路径、敏感性、独立核对错误及逐文件源码指纹。', '', '## 解释范围', '']
    lines += [f'- {item}' for item in report['limitations']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/life-planning.json')
    args = parser.parse_args()
    report = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    args.output.with_suffix('.md').write_text(markdown(report))
    print(json.dumps({'inputs': report['input_count'], 'paths': report['checked_path_count'],
                      'failures': len(report['failures']), 'output': str(args.output)}, ensure_ascii=False))
    return 1 if report['failures'] or report['input_count'] != 30 or report['checked_path_count'] != 360 else 0


if __name__ == '__main__':
    raise SystemExit(main())
