"""METIS-LIFE-2: deterministic finite-horizon scenario planning, in integer cents.

This is bounded discrete search over user-permitted choices, not estimated utility,
probabilistic forecasting, financial advice or an authorisation to move money.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

CATEGORIES = {'living', 'work', 'health', 'moving', 'shopping', 'family', 'travel', 'education'}
IMPORTANCE = {'essential': 100, 'important': 30, 'flexible': 5}
DEFAULT_SETTINGS = {'illiquidAssets': 0, 'delayDays': 15, 'expenseShock': 1200, 'shockDay': 3, 'goalAmount': 500}
SCENE_FIELDS = {'title', 'category', 'amount', 'earliestDay', 'preferredDay', 'latestDay', 'importance',
                'allowSkip', 'allowAlternative', 'alternativeAmount', 'alternativeLabel'}
HORIZON = 30
BEAM_WIDTH = 128


def _cents(value, *, minimum=0, maximum=1_000_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('金额必须为数字，精确到分。')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < minimum or number > maximum or number != number.quantize(Decimal('.01')):
            raise ValueError('金额超出范围或超过分精度。')
        return int(number * 100)
    except (InvalidOperation, OverflowError):
        raise ValueError('金额无效。') from None


def _yuan(value):
    return float(Decimal(value) / 100)


def _integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name}须为 {low} 至 {high} 的整数。')
    return value


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError('生活规划设置包含未知字段。')
    clean = {**DEFAULT_SETTINGS, **value}
    for field in ('illiquidAssets', 'expenseShock', 'goalAmount'):
        clean[field] = _yuan(_cents(clean[field], maximum=1_000_000_000 if field == 'illiquidAssets' else 1_000_000))
    clean['delayDays'] = _integer(clean['delayDays'], 0, 30, '额外工资延迟')
    clean['shockDay'] = _integer(clean['shockDay'], 1, 30, '突发支出日')
    return clean


def validate_scene(value):
    if not isinstance(value, dict) or set(value) - SCENE_FIELDS:
        raise ValueError('生活场景包含未知字段。')
    required = {'title', 'category', 'amount', 'earliestDay', 'preferredDay', 'latestDay', 'importance'}
    if not required.issubset(value):
        raise ValueError('请补全场景用途、金额、时间和重要性。')
    if not isinstance(value['title'], str) or not 1 <= len(value['title'].strip()) <= 60:
        raise ValueError('场景名称须为 1 至 60 个字。')
    if not isinstance(value['category'], str) or value['category'] not in CATEGORIES:
        raise ValueError('请选择已支持的生活场景。')
    if not isinstance(value['importance'], str) or value['importance'] not in IMPORTANCE:
        raise ValueError('请主动确认这笔支出的重要性。')
    clean = {key: value[key] for key in required}
    clean['title'] = clean['title'].strip()
    clean['amount'] = _yuan(_cents(value['amount'], minimum=.01, maximum=1_000_000))
    for key in ('earliestDay', 'preferredDay', 'latestDay'):
        clean[key] = _integer(value[key], 1, 30, '支付日')
    if not clean['earliestDay'] <= clean['preferredDay'] <= clean['latestDay']:
        raise ValueError('支付日应满足：最早日 ≤ 首选日 ≤ 最晚日。')
    for key in ('allowSkip', 'allowAlternative'):
        clean[key] = value.get(key, False)
        if not isinstance(clean[key], bool):
            raise ValueError('跳过或替代安排必须由你明确选择。')
    if clean['importance'] == 'essential' and clean['allowSkip']:
        raise ValueError('必须支付的场景不能自动放弃；如有变化，请先更正重要性。')
    alternative = value.get('alternativeAmount') if clean['allowAlternative'] else None
    label = value.get('alternativeLabel', '') if clean['allowAlternative'] else ''
    if not isinstance(label, str) or len(label.strip()) > 100:
        raise ValueError('替代方案说明最多 100 个字。')
    if alternative is not None:
        alternative = _yuan(_cents(alternative, minimum=.01, maximum=clean['amount']))
    if clean['allowAlternative'] and (alternative is None or not label.strip()):
        raise ValueError('允许替代时，请填写替代金额和具体说明。')
    clean.update(alternativeAmount=alternative, alternativeLabel=label.strip())
    return clean


def _stress_bases(snapshot, settings):
    pts = snapshot['cash']['pts']
    if not isinstance(pts, list) or len(pts) != HORIZON + 1:
        raise ValueError('当前账本须提供包含今天的 31 个现金预测点。')
    base = [_cents_signed(n) for n in pts]
    salaries = [e for e in snapshot['cash'].get('events', []) if e.get('type') == 'salary' and e.get('amount', 0) > 0]
    definitions = [('baseline', '按当前预计到账', 0, 0),
                   ('income_delay', '工资额外延迟', settings['delayDays'], 0),
                   ('expense_shock', '额外突发支出', 0, _cents(settings['expenseShock'])),
                   ('combined', '收入延期与突发支出同时发生', settings['delayDays'], _cents(settings['expenseShock']))]
    scenarios = []
    for identity, label, delay, expense in definitions:
        path = list(base)
        for salary in salaries:
            old, new, amount = salary['day'], salary['day'] + delay, _cents(salary['amount'])
            for day in range(HORIZON + 1):
                if 1 <= old <= day:
                    path[day] -= amount
                if 1 <= new <= day:
                    path[day] += amount
        for day in range(settings['shockDay'], HORIZON + 1):
            path[day] -= expense
        scenarios.append({'id': identity, 'label': label, 'delayDays': delay, 'extraExpense': _yuan(expense),
                          'shockDay': settings['shockDay'], 'probability': None, '_pts': path})
    return scenarios


def _cents_signed(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('账本现金预测含无效金额。')
    return -_cents(-value) if value < 0 else _cents(value)


def _choices(scene, salaries):
    preferred, latest = scene['preferredDay'], scene['latestDay']
    days = {preferred, latest}
    # Discrete candidate dates are declared, not an assertion of global optimality.
    for salary in salaries:
        after = salary['day'] + 1
        if preferred <= after <= latest:
            days.add(after)
    amount, weight = _cents(scene['amount']), IMPORTANCE[scene['importance']]
    options = []
    for day in sorted(days):
        options.append({'sceneId': scene['id'], 'title': scene['title'], 'importance': scene['importance'],
                        '_amount': amount, 'day': day, 'mode': 'original' if day == preferred else 'delayed',
                        '_cost': round(weight * (day - preferred) / 30, 6),
                        'changeReason': '保留你首选的用途与日期' if day == preferred else f'在你允许的时间内移至第 {day} 天'})
        if scene['allowAlternative']:
            alt = _cents(scene['alternativeAmount'])
            options.append({'sceneId': scene['id'], 'title': scene['title'], 'importance': scene['importance'],
                            '_amount': alt, 'day': day, 'mode': 'alternative',
                            '_cost': round(weight * (1 - alt / amount) + weight * (day - preferred) / 30, 6),
                            'changeReason': scene['alternativeLabel'] + f'；第 {day} 天支付'})
    if scene['allowSkip']:
        options.append({'sceneId': scene['id'], 'title': scene['title'], 'importance': scene['importance'],
                        '_amount': 0, 'day': preferred, 'mode': 'skipped', '_cost': weight * 2,
                        'changeReason': '你已允许比较本次不支出；不会替你取消订单或服务'})
    return options


def _paths(bases, allocations, reserve, goal=0, extra_buffer=0):
    projected = []
    for scenario in bases:
        path = [v - goal + extra_buffer for v in scenario['_pts']]
        for choice in allocations:
            for day in range(choice['day'], HORIZON + 1):
                path[day] -= choice['_amount']
        minimum = min(path)
        projected.append({'scenarioId': scenario['id'], 'label': scenario['label'], '_pts': path,
                          '_min': minimum, 'min': _yuan(minimum), 'minDay': path.index(minimum),
                          'breachDays': sum(v < reserve for v in path), 'maxGap': _yuan(max(0, reserve - minimum)),
                          'negativeCashDays': sum(v < 0 for v in path), 'cashDeficit': _yuan(max(0, -minimum))})
    return projected


def _rank(bases, allocations, reserve):
    paths = _paths(bases, allocations, reserve)
    low = min(p['_min'] for p in paths)
    return (max(0, -low), max(0, reserve - low),
            sum(choice['_cost'] for choice in allocations),
            sum(max(0, reserve - v) for p in paths for v in p['_pts']),
            sum(choice['day'] for choice in allocations))


def _policy(identity, title, summary, allocations, bases, reserve, goal_request, balance):
    before_goal = _paths(bases, allocations, reserve)
    minimum = min(p['_min'] for p in before_goal)
    goal = max(0, min(goal_request, minimum - reserve, balance - reserve))
    paths = _paths(bases, allocations, reserve, goal)
    output = [{key: value for key, value in path.items() if not key.startswith('_')} | {'pts': [_yuan(v) for v in path['_pts']]} for path in paths]
    return {'id': identity, 'title': title, 'summary': summary,
            'allocations': [{key: value for key, value in choice.items() if not key.startswith('_')} |
                            {'amount': _yuan(choice['_amount'])} for choice in allocations],
            'projections': output,
            'metrics': {'worstGap': max(p['maxGap'] for p in output), 'worstDeficit': max(p['cashDeficit'] for p in output),
                        'breachDays': max(p['breachDays'] for p in output),
                        'adjustmentScore': round(sum(c['_cost'] for c in allocations), 2),
                        'goalAmount': _yuan(goal), 'deferredGoal': _yuan(goal_request - goal),
                        'expenseTotal': _yuan(sum(c['_amount'] for c in allocations))}, '_choices': allocations, '_goal': goal}


def analyze(snapshot, scenes, settings):
    settings = validate_settings(settings)
    if snapshot.get('profile', {}).get('readConsent') is not True:
        raise ValueError('请先确认读取模拟账本。')
    drafts = []
    for scene in scenes:
        if scene.get('status', 'draft') != 'draft':
            continue  # Committed expenses already exist in the bank cash path.
        clean = validate_scene({key: scene[key] for key in SCENE_FIELDS if key in scene})
        clean['id'] = scene['id']
        drafts.append(clean)
    if len(drafts) > 10:
        raise ValueError('最多同时比较 10 个待规划场景。')
    drafts.sort(key=lambda s: (-IMPORTANCE[s['importance']], s['preferredDay'], s['id']))
    bases = _stress_bases(snapshot, settings)
    salaries = [{'day': e['day'] + delay} for e in snapshot['cash'].get('events', []) if e.get('type') == 'salary'
                for delay in (0, settings['delayDays'])]
    choices = [_choices(scene, salaries) for scene in drafts]
    reserve, balance, target = _cents(snapshot['profile']['reserve']), _cents(snapshot['balance']), _cents(settings['goalAmount'])
    original = [options[0] for options in choices]
    buffer = [min(options, key=lambda o: (o['_amount'], -o['day'], o['_cost'])) for options in choices]
    beam = [([], _rank(bases, [], reserve))]
    for options in choices:
        candidates = [(prior + [choice], _rank(bases, prior + [choice], reserve)) for prior, _ in beam for choice in options]
        candidates.sort(key=lambda row: row[1])
        beam = candidates[:BEAM_WIDTH]
    balanced = beam[0][0]
    # Include the two named baselines as candidates so bounded pruning never
    # makes the recommendation worse than either under the declared ranking.
    balanced = min((balanced, original, buffer), key=lambda allocation: _rank(bases, allocation, reserve))
    policies = [
        _policy('original', '保持原安排', '按你首选的金额和日期安排生活支出，保留缺口供核对。', original, bases, reserve, target, balance),
        _policy('buffer', '缓冲优先', '仅在你允许的范围内选更低支出、更晚日期；可能牺牲更多生活安排。', buffer, bases, reserve, target, balance),
        _policy('balanced', '兼顾生活与现金', '先控制支付缺口和现金底线，再减少对重要生活安排的调整。', balanced, bases, reserve, target, balance)]
    best = policies[-1]
    sensitivity = []
    for extra in (0, 500, 1000, 2000, 5000):
        paths = _paths(bases, balanced, reserve, best['_goal'], _cents(extra))
        sensitivity.append({'buffer': extra, 'worstGap': max(p['maxGap'] for p in paths),
                            'breachDays': max(p['breachDays'] for p in paths), 'min': min(p['min'] for p in paths)})
    future_salary = any(e.get('type') == 'salary' for e in snapshot['cash'].get('events', []))
    threshold = None
    already = False
    if future_salary:
        for delay in range(31):
            stress = _stress_bases(snapshot, {**settings, 'delayDays': delay, 'expenseShock': 0})
            gap = _paths([stress[1]], balanced, reserve)[0]['maxGap']
            if gap > 0:
                already = delay == 0
                threshold = max(0, delay - 1)
                break
        else:
            threshold = 30
    committed = [s for s in scenes if s.get('status') == 'committed']
    output = {'version': 'METIS-LIFE-2', 'accountRevision': snapshot['user']['revision'],
              'financial': {'liquidAssets': snapshot['balance'], 'goalFunded': snapshot.get('goalFunded', 0),
                            'illiquidAssets': settings['illiquidAssets'], 'reserve': snapshot['profile']['reserve']},
              'stressScenarios': [{k: v for k, v in s.items() if not k.startswith('_')} for s in bases],
              'policies': [{k: v for k, v in p.items() if not k.startswith('_')} for p in policies],
              'sensitivity': sensitivity, 'breakpoint': {'extraDelayDays': threshold, 'alreadyAtRisk': already,
                  'basis': '兼顾方案的生活支出、未划转目标储蓄、无额外突发费用；仅在未来30天内逐日检验',
                  'salaryReceived': not future_salary, 'testedThroughDays': 30},
              'recommendation': {'policyId': 'balanced', 'rationale': '在已声明压力情景内，优先减少支付缺口和现金底线缺口，再减少生活调整；具体取舍仍由你选择。'},
              'search': {'method': 'bounded discrete beam search', 'beamWidth': BEAM_WIDTH,
                         'candidateCount': sum(len(c) for c in choices), 'globalOptimalityClaimed': False,
                         'importanceWeights': IMPORTANCE, 'ranking': ['worst_payment_deficit', 'worst_reserve_gap', 'adjustment_score', 'shortfall_area', 'timing']},
              'assumptions': ['合成账本固定以2026-10-20为基准；预测含今天与未来30天。',
                  '生活场景是额外、尚未登记的支出；已确认纳入的场景只在银行现金路径计一次。',
                  '现有必要账单、有效订阅和日常预算保持不变；不把预计工资当作已到账现金。',
                  '现金按每日净额计算，未模拟同一天收入与扣款的先后顺序。',
                  '压力情景是用户设定的假设，没有估计发生概率；已有延期上再追加所选天数。',
                  '非流动资产与隔离目标资金不计入主账户可支付现金，没有模拟出售、借款或目标资金赎回。',
                  '调整分数是可解释的演示规则，不是估计效用、货币福利或用户真实偏好参数。',
                  '仅在你允许的日期、替代方案与放弃范围内进行有限离散搜索，不承诺全局最优。',
                  '目标转入数额在四个压力情景均保留现金底线才建议；当前有缺口时建议额外转入0。',
                  '敏感性是在同一方案下假设增加现金缓冲，未增加实际余额、未改变目标转入额。',
                  f'已有{len(committed)}项场景纳入未来账单；任何比较都不会自动付款、取消服务或改变授权。']}
    return output
