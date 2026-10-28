"""ETHOS-R2: local, deterministic planning explanations; never payment authority.

All assessment answers are declarations, not a validated psychometric instrument.
Amounts are yuan; debt is payments due in the next 30 days, not total debt stock.
Missing facts remain unknown. Thresholds below are prototype design choices.
"""
import math

VERSION = 'ETHOS-R2'
FIELDS = {
    'horizon': ('使用期限', {'short': '一年以内（含一年）', 'medium': '一至三年（含三年）', 'long': '三年以上'}),
    'liquidityNeed': ('用钱灵活性', {'high': '随时可能动用', 'medium': '部分资金可锁定', 'low': '三年以上不动用'}),
    'lossTolerance': ('本金亏损接受程度', {'none': '不接受本金亏损', 'small': '可接受假设亏损 5%', 'medium': '可接受假设亏损 10%', 'high': '可接受假设亏损 20%'}),
    'lossReaction': ('假设亏损后的选择', {'sell': '止损退出', 'review': '核对原因再决定', 'hold': '能接受且继续持有'}),
    'experience': ('自述经验', {'none': '暂无经验', 'basic': '有基础经验', 'experienced': '较有经验'}),
    'knowledge': ('非保本产品会否亏本金', {'possible': '可能亏损本金', 'impossible': '认为不可能亏损本金', 'unsure': '不确定是否会亏损本金'}),
    'clarification': ('矛盾答案复核', {'safety': '以本金安全为先', 'confirmed': '已理解假设情景并确认原回答'}),
}
ROLES = {
    'review': {'name': '待完善/复核', 'headline': '先把情况说清楚', 'description': '补齐声明或核对未知数据后，再安排简单规划。', 'primaryView': 'profile', 'actionLabel': '完善与复核画像'},
    'cash': {'name': '现金修复', 'headline': '先补上眼前的现金缺口', 'description': '先核对必要支付和现金预留，资金安排仍须通过 ARGOS。', 'primaryView': 'bills', 'actionLabel': '核对现金缺口'},
    'buffer': {'name': '弹性缓冲', 'headline': '先给收入和开支留出余地', 'description': '围绕可随时动用的现金做简单规划，重新核对收入与缓冲。', 'primaryView': 'life', 'actionLabel': '安排现金缓冲'},
    'debt': {'name': '负债减压', 'headline': '先理清近期还款压力', 'description': '核对未来 30 天还款与收入，优先安排必要还款。', 'primaryView': 'bills', 'actionLabel': '核对还款安排'},
    'goal': {'name': '近期目标', 'headline': '先照顾一年内要用的钱', 'description': '按近期用途安排现金和目标进度，避免目标金额倒逼风险。', 'primaryView': 'life', 'actionLabel': '查看近期目标'},
    'steady': {'name': '稳健积累', 'headline': '按能接受的节奏积累', 'description': '结合期限、灵活用钱需求与声明，进行简单的收支和目标规划。', 'primaryView': 'actions', 'actionLabel': '查看积累安排'},
    'growth': {'name': '长期规划', 'headline': '为三年以上的目标留出时间', 'description': '在现金与期限约束内讨论长期规划，不推荐具体产品。', 'primaryView': 'console', 'actionLabel': '查看长期规划'},
}
BANDS = ['未评估', '本金安全优先', '低波动意愿', '中等波动意愿', '较高波动意愿', '高波动意愿']
LIMITS = {
    'horizon': {'short': 2, 'medium': 3, 'long': 5},
    'liquidityNeed': {'high': 2, 'medium': 3, 'low': 5},
    'lossTolerance': {'none': 1, 'small': 2, 'medium': 3, 'high': 5},
    'lossReaction': {'sell': 2, 'review': 4, 'hold': 5},
}
CHECKS = [
    '所有问卷字段均为用户声明；5%/10%/20%仅是假设亏损情景，不是收益、亏损概率或经验证的量表。',
    '本原型设计：当前余额或30天最低预测余额低于现金预留，或预留后余量为负，视为现金缺口。',
    '本原型设计：未来30天还款达到预计月收入60%（含），或零收入仍有还款，先处理债务压力。',
    '本原型设计：余量小于30天日常支出与现金预留20%的较大值为缓冲薄；小于90天日常支出与现金预留的较大值，能力上限为3档。',
    '本原型设计：亏损接受程度上限为1/2/3/5档；退出/复核/持有反应上限为2/4/5档；期限和流动性上限均为2/3/5档。',
    '本原型设计：缺口能力上限1档；债务压力、收入不稳定、工资未到账冲击或薄缓冲上限2档；知识错误、不确定或无经验只做解释与简单规划，上限2档。',
    '所有上限取较低值；收益目标与展示风格不参与风险上调，风险档位不等同授信评级。',
    '画像角色不新增金融操作授权；钱的约束仍由 ARGOS 执行，每笔金融操作沿用原有确认流程。',
]


def contradictory(assessment):
    return ((assessment.get('lossTolerance') in ('medium', 'high') and assessment.get('lossReaction') == 'sell')
            or (assessment.get('lossTolerance') == 'none' and assessment.get('lossReaction') == 'hold'))


def validate_assessment(value):
    """Validate a supplied assessment; absence is handled by profile callers.

    Raises ValueError for partial/unknown answers, including unresolved conflicts.
    Does not fill defaults or silently reinterpret the user's declarations.
    """
    if not isinstance(value, dict):
        raise ValueError('风险声明格式无效，请逐项填写。')
    if any(key not in FIELDS for key in value):
        raise ValueError('风险声明包含未知字段，请核对。')
    for key, (label, options) in FIELDS.items():
        if key == 'clarification' and key not in value:
            continue
        if not isinstance(value.get(key), str) or value[key] not in options:
            raise ValueError('请确认' + label + '。')
    if contradictory(value) and 'clarification' not in value:
        raise ValueError('亏损接受程度与情景选择存在矛盾，请确认以本金安全为先，或理解情景后确认原回答。')
    return {key: value[key] for key in FIELDS if key in value}


def _number(value):
    return type(value) in (int, float) and abs(value) <= 1e308 and math.isfinite(value)


def assess_profile(*, profile=None, balance=None, income=None, debt=None, daily=None,
                   min=None, free=None, shocked=None, salaryReceived=None):
    """Return the JSON-compatible R2 view using one cash/profile snapshot.

    complete describes declaration completeness; reviewRequired also covers missing
    or inconsistent cash facts. Unknown inputs never become zero or risk answers.
    """
    p = profile if isinstance(profile, dict) else {}
    raw = p.get('assessment')
    a = raw if isinstance(raw, dict) else {}
    checks, reasons = list(CHECKS), []
    try:
        validate_assessment(raw)
        complete = True
    except ValueError as error:
        complete = False
        reasons.append('尚未填写风险声明；旧画像可继续保存，不补填风险答案。' if 'assessment' not in p else str(error))
    facts = {'balance': balance, 'income': income, 'debt': debt, 'daily': daily, 'min': min, 'free': free, 'reserve': p.get('reserve')}
    names = {'balance': '当前余额', 'income': '预计月收入', 'debt': '30天还款', 'daily': '日常支出', 'min': '30天最低预测余额', 'free': '预留后余量', 'reserve': '现金预留'}
    unknown = [names[k] for k, v in facts.items() if not _number(v) or (k in ('income', 'debt', 'daily', 'reserve') and v < 0)]
    for key, label, value, options in [('incomeStability', '收入稳定性', p.get('incomeStability'), ('stable', 'mixed', 'uncertain')),
                                       ('goalType', '当前目标', p.get('goalType'), ('protect', 'recover', 'accumulate', 'grow'))]:
        if value not in options:
            unknown.append(label)
    if type(shocked) is not bool:
        unknown.append('工资冲击状态')
    if type(salaryReceived) is not bool:
        unknown.append('工资到账状态')
    known = not unknown
    if unknown:
        reasons.append('待核对数据：' + '、'.join(unknown) + '；未知值不按零处理。')
    if known and (abs(free - (min - p['reserve'])) > .011 or min > balance + .011):
        known = False
        reasons.append('最低余额、当前余额与预留后余量不一致，请使用同一快照复核。')
    capacity, capacity_detail = 0, '现金事实待核对，暂不判断承受能力。'
    role, caps = 'review', []
    if known:
        gap = free < 0 or min < p['reserve'] or balance < p['reserve']
        debt_pressure = debt > 0 and (income == 0 or debt >= income * .6)
        thin = free < max(daily * 30, p['reserve'] * .2)
        delayed = shocked and not salaryReceived
        unstable = p['incomeStability'] != 'stable' or income == 0 or delayed
        capacity = 1 if gap else (2 if debt_pressure or thin or unstable else (3 if free < max(daily * 90, p['reserve']) else 5))
        capacity_detail = '按本原型现金规则，承受能力上限为' + str(capacity) + '档；不含未登记资产或支出。'
        caps.append(capacity)
        if gap:
            role = 'cash'
            reasons.append('当前余额或30天预测低于现金预留，优先修复现金缺口。')
        elif debt_pressure:
            role = 'debt'
            reasons.append('近期还款触及本原型债务压力阈值，优先于收入缓冲和长期目标。')
        elif unstable or thin:
            role = 'buffer'
            reasons.append('收入不稳定、工资尚未到账冲击或现金缓冲较薄，先留出周转余地。')
        elif a.get('horizon') == 'short':
            role = 'goal'
            reasons.append('你声明一年内需要使用这笔钱，先安排近期目标。')
        else:
            role = 'steady'
            reasons.append('当前现金与还款未触及优先阈值，可按声明安排积累。')
        if delayed:
            reasons.append('工资冲击只更新现金能力与安排，不改写期限、亏损接受程度或其他风险偏好。')
    for key, limits in LIMITS.items():
        value = a.get(key)
        if isinstance(value, str) and value in limits:
            caps.append(limits[value])
    simple = a.get('experience') == 'none' or a.get('knowledge') in ('impossible', 'unsure')
    if simple:
        caps.append(2)
        reasons.append('暂无经验或对非保本本金亏损的理解需解释；先说明可能亏损本金，仅做简单收支和目标规划，不作授信判断。')
    conflict = contradictory(a)
    if conflict:
        if a.get('clarification') == 'safety':
            caps.append(1)
            reasons.append('矛盾情景已选择本金安全优先，按1档约束，保留原声明。')
        elif a.get('clarification') == 'confirmed':
            reasons.append('已确认理解矛盾情景；保留原声明，仍取各项上限较低值。')
        else:
            checks.append('需要复核矛盾情景后再完成评估。')
    level = sorted(caps)[0] if complete and known else 0
    if a.get('lossTolerance') == 'none':
        level = 1
    if not complete or not known:
        role = 'review'
    elif role == 'steady' and a.get('horizon') == 'long' and a.get('liquidityNeed') == 'low' and level >= 3 and not simple:
        role = 'growth'
        reasons.append('期限超过三年、流动性要求较低且约束允许，进入长期规划；目标金额不提高风险档位。')
    dimensions = []
    for key, (label, options) in FIELDS.items():
        value = a.get(key)
        display = options.get(value, '待声明') if isinstance(value, str) else '待声明'
        detail = '用户声明；不自动推定或验证。'
        if key in LIMITS and isinstance(value, str) and value in LIMITS[key]:
            detail += '本原型设计：该声明对应上限' + str(LIMITS[key][value]) + '档。'
        if key == 'clarification' and not conflict:
            detail = '仅在亏损接受程度与情景选择矛盾时要求复核；当前无需复核。'
            if key not in a:
                display = '无需复核'
        dimensions.append({'key': key, 'label': label, 'value': display, 'detail': detail})
    dimensions.extend([
        {'key': 'incomeStability', 'label': '收入稳定性', 'value': {'stable': '稳定', 'mixed': '有波动', 'uncertain': '不确定'}.get(p.get('incomeStability'), '待声明') if isinstance(p.get('incomeStability'), str) else '待声明', 'detail': '用户声明；工资事件不替你修改。'},
        {'key': 'capacity', 'label': '当前现金承受能力', 'value': '待核对' if capacity == 0 else str(capacity) + '档上限', 'detail': capacity_detail},
    ])
    detail = ('信息未齐或事实待核对，暂不评估风险档位。' if level == 0 else
              '本原型取用户意愿、现金能力、期限、流动性与理解情况的较低上限；仅用于规划解释，不推荐具体产品。')
    if a.get('lossTolerance') == 'none':
        detail = '你声明不接受本金亏损，固定为1档；不承诺任何产品保本，其他信息待核对时仍需复核。'
    return {'version': VERSION, 'complete': complete, 'reviewRequired': not complete or not known,
            'role': dict(key=role, **ROLES[role]), 'riskBand': {'level': level, 'label': BANDS[level], 'detail': detail},
            'dimensions': dimensions, 'checks': checks, 'reasons': reasons}
