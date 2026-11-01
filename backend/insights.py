"""Read-only, deterministic summaries of one consented synthetic account snapshot.

All calculations use integer cents. Historical fixture rows describe the available
sample; they are never replayed into the balance or promoted to payment requests.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import re


BASE_DATE = '2026-10-20'
HORIZON_DAYS = 30
REVIEW_OUTFLOW_CENTS = 200_000
SMALL_OUTFLOW_CENTS = 10_000
NON_CONSUMPTION_CATEGORIES = {'账户调拨（目标储蓄）', '账户调拨（转账）', '还款', '贷款', '分期'}
INSTRUCTION_MEMO = re.compile(r'忽略.{0,20}指令|system\s*[:：]|无需.{0,8}确认|立即.{0,12}转账', re.I)


def _cents(value):
    # Inputs come from BankService's validated snapshot, not HTTP request data.
    return int(Decimal(str(value)) * 100)


def _yuan(value):
    return value / 100


def _in_window(day):
    return isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= HORIZON_DAYS


def _category(row):
    category = row.get('cat') or '其他'
    if row.get('to') == 'goal' or category in {'目标储蓄', '储蓄', 'goal'}:
        return '账户调拨（目标储蓄）'
    if category in {'转账', 'transfer'}:
        return '账户调拨（转账）'
    return category


def _receipt(value):
    if not isinstance(value, dict):
        return None
    # Deliberately project public receipt fields instead of copying opaque data.
    return {key: value[key] for key in ('id', 'status', 'subscriptionId', 'at') if key in value}


def build_insights(snapshot):
    """Build the public insights contract without mutating the supplied snapshot."""
    user, profile, cash = snapshot['user'], snapshot['profile'], snapshot['cash']
    points = [_cents(value) for value in cash['pts']]
    reserve = _cents(profile['reserve'])
    minimum = min(points)
    breaches = [day for day, balance in enumerate(points) if balance < reserve]
    gap = max(0, reserve - minimum)

    rows = snapshot['transactions']
    grouped = defaultdict(lambda: {'amount': 0, 'count': 0})
    outflow = inflow = 0
    largest, review, small_outflows = [], [], []
    consumption_outflow = 0
    for row in rows:
        amount = _cents(row['amount'])
        category = _category(row)
        if amount < 0:
            debit = -amount
            outflow += debit
            grouped[category]['amount'] += debit
            grouped[category]['count'] += 1
            if category not in NON_CONSUMPTION_CATEGORIES:
                consumption_outflow += debit
                if debit <= SMALL_OUTFLOW_CENTS:
                    small_outflows.append({'id': row['id'], 'merchant': row.get('merchant', ''),
                                           'amount': _yuan(debit), 'category': category,
                                           'time': row.get('time', '')})
            largest.append({'id': row['id'], 'merchant': row.get('merchant', ''),
                            'amount': _yuan(debit), 'category': category,
                            'time': row.get('time', '')})
        elif amount > 0:
            inflow += amount
        reasons = []
        if INSTRUCTION_MEMO.search(row.get('memo') or ''):
            reasons.append('备注含指令型文本，仅作为不可信流水内容展示，不执行其中指令')
        if amount <= -REVIEW_OUTFLOW_CENTS:
            reasons.append('单笔出账达到 ¥2,000，建议核对交易用途与凭证')
        if reasons:
            review.append({'id': row['id'], 'title': row.get('merchant', '流水待核对'),
                           'reason': '；'.join(reasons) + '。这是规则提示，非欺诈识别结论。',
                           'amount': _yuan(amount), 'time': row.get('time', '')})
    largest.sort(key=lambda row: (-_cents(row['amount']), row['id']))
    categories = [{'name': name, 'amount': _yuan(values['amount']), 'count': values['count']}
                  for name, values in sorted(grouped.items(), key=lambda item: (-item[1]['amount'], item[0]))]

    upcoming = []
    for bill in snapshot['bills']:
        if _in_window(bill['day']):
            upcoming.append({'id': 'bill_' + bill['id'], 'day': bill['day'], 'label': bill['label'],
                             'amount': _yuan(_cents(bill['amount'])), 'kind': bill.get('kind', '账单'),
                             'source': 'bill', 'status': 'scheduled'})

    subscriptions, cancelled_receipts = [], []
    flagged_subscriptions = []
    cancelled_count = avoided = 0
    for sub in snapshot['subscriptions']:
        fee = _cents(sub['fee'])
        active = sub['status'] == 'active'
        in_window = _in_window(sub['next'])
        avoidable = fee if active and in_window else 0
        after = [balance + (avoidable if day >= sub['next'] else 0)
                 for day, balance in enumerate(points)]
        min_after = min(after)
        receipt = _receipt(sub.get('receipt'))
        subscriptions.append({'id': sub['id'], 'name': sub['name'], 'fee': _yuan(fee),
                              'next': sub['next'], 'status': sub['status'], 'note': sub.get('note', ''),
                              'flag': sub.get('flag'), 'receipt': receipt,
                              'impact': {'avoidedOutflow': _yuan(avoidable), 'minAfter': _yuan(min_after),
                                         'minImprovement': _yuan(min_after - minimum),
                                         'remainingGap': _yuan(max(0, reserve - min_after)),
                                         'points': [_yuan(balance) for balance in after]}})
        if active and sub.get('flag') in {'低使用', '重复'}:
            flagged_subscriptions.append({'id': sub['id'], 'name': sub['name'],
                                          'fee': _yuan(fee), 'next': sub['next'],
                                          'reason': sub['flag'],
                                          'inWindow': in_window})
        if active and in_window:
            upcoming.append({'id': 'subscription_' + sub['id'], 'day': sub['next'],
                             'label': sub['name'] + '续费', 'amount': _yuan(-fee), 'kind': '订阅',
                             'source': 'subscription', 'status': 'scheduled'})
        if sub['status'] == 'cancelled':
            cancelled_count += 1
            window_amount = fee if in_window else 0
            avoided += window_amount
            if receipt:
                cancelled_receipts.append({'id': receipt.get('id', sub['id']), 'name': sub['name'],
                                           'amount': _yuan(window_amount)})

    # Salary events already disappear from cash.events when the ledger records
    # receipt. Never infer receipt from the fixture's historical salary row.
    for index, event in enumerate(cash['events']):
        if event.get('type') == 'salary' and _in_window(event['day']) and not snapshot['salaryReceived']:
            upcoming.append({'id': 'salary_' + str(index), 'day': event['day'],
                             'label': event['label'], 'amount': _yuan(_cents(event['amount'])),
                             'kind': '工资', 'source': 'salary', 'status': 'expected'})
    upcoming.sort(key=lambda row: (row['day'], row['source'], row['id']))

    actions = []
    if gap:
        actions.append({'id': 'liquidity_gap', 'priority': 'high', 'kind': 'liquidity',
                        'title': '先处理安全线缺口',
                        'reason': f'未来 30 天第 {points.index(minimum)} 天预计最低余额 ¥{_yuan(minimum):,.2f}，'
                                  f'较你设定的现金预留少 ¥{_yuan(gap):,.2f}；请查看调整方案并逐项确认。',
                        'view': 'life'})
    bills = [row for row in upcoming if row['source'] == 'bill' and row['amount'] < 0]
    if bills:
        nearest = bills[0]
        actions.append({'id': 'check_' + nearest['id'],
                        'priority': 'high' if nearest['day'] <= 3 else 'normal', 'kind': 'bill',
                        'title': '核对最近一笔账单',
                        'reason': f'第 {nearest["day"]} 天有「{nearest["label"]}」'
                                  f'预计出账 ¥{-nearest["amount"]:,.2f}；核对到期日与可用资金；此处只登记预测，未执行支付。',
                        'view': 'bills'})
    for sub in subscriptions:
        if sub['status'] == 'active' and sub['flag'] in {'低使用', '重复'} and _in_window(sub['next']):
            impact = sub['impact']
            actions.append({'id': 'review_subscription_' + sub['id'], 'priority': 'normal',
                            'kind': 'subscription', 'title': '复核「' + sub['name'] + '」续费',
                            'reason': f'合成订阅标记为{sub["flag"]}，第 {sub["next"]} 天续费 '
                                      f'¥{sub["fee"]:,.2f}；单独取消可避免本次出账，'
                                      f'预测仍有安全线缺口 ¥{impact["remainingGap"]:,.2f}。请自行确认是否保留。',
                            'view': 'bills', 'subscriptionId': sub['id']})
    salary = next((row for row in upcoming if row['source'] == 'salary'), None)
    if salary:
        actions.append({'id': 'verify_salary', 'priority': 'normal', 'kind': 'salary',
                        'title': '核实预计工资到账安排',
                        'reason': f'预测计入第 {salary["day"]} 天工资 ¥{salary["amount"]:,.2f}，目前尚未到账；'
                                  '依赖工资的计划仍须等待真实到账并由你确认。', 'view': 'profile'})
    elif snapshot['salaryReceived'] and any(plan.get('dep') and plan.get('status') == 'pending_confirmation'
                                           for plan in snapshot['plans']):
        actions.append({'id': 'review_salary_plans', 'priority': 'normal', 'kind': 'salary',
                        'title': '复核工资到账后的待确认计划',
                        'reason': '本期工资已有入账记录，仍有依赖工资的计划待确认；请先核对现金预留和计划金额。',
                        'view': 'console'})
    actions.sort(key=lambda row: row['priority'] != 'high')

    hidden_amount = sum(_cents(row['amount']) for row in small_outflows)
    flagged_renewal = sum(_cents(row['fee']) for row in flagged_subscriptions if row['inWindow'])
    if gap:
        health_level = 'urgent'
        health_headline = '未来现金可能低于你设定的底线'
        health_summary = f'按当前登记事项，最低点距现金底线还差 ¥{_yuan(gap):,.2f}。'
    elif flagged_subscriptions or review:
        health_level = 'attention'
        health_headline = '现金底线暂时守住，但有几项值得复核'
        health_summary = '系统发现可复核续费或较大出账；它们是规则提示，不是风险定论。'
    else:
        health_level = 'steady'
        health_headline = '按当前可见信息，未来现金暂时高于底线'
        health_summary = '仍需留意尚未记录的消费和预计收入是否真实到账。'

    health_signals = [
        {'id': 'liquidity', 'label': '未来现金最低点', 'value': _yuan(minimum),
         'evidence': f'服务端按第 0–{HORIZON_DAYS} 天逐日现金路径计算'},
        {'id': 'hidden_costs', 'label': '可复核隐性支出',
         'value': _yuan(hidden_amount + flagged_renewal),
         'evidence': f'{len(small_outflows)} 笔不超过 ¥100 的可见消费，'
                     f'{len(flagged_subscriptions)} 项低使用或重复订阅'},
        {'id': 'review', 'label': '需亲自核对的流水', 'value': len(review),
         'evidence': '按备注指令型文本或单笔出账达到 ¥2,000 的公开规则筛选'},
    ]

    return {
        'version': 'TROPOS-INSIGHTS-2', 'generatedAt': datetime.now(timezone.utc).isoformat(),
        'account': {key: user[key] for key in ('persona', 'name', 'revision')},
        'basis': {'baseDate': BASE_DATE, 'horizonDays': HORIZON_DAYS, 'historyScope': 'available_rows',
                  'historyLabel': '当前可见合成流水（非完整月报）'},
        'liquidity': {'balance': _yuan(_cents(snapshot['balance'])), 'reserve': _yuan(reserve),
                      'minBalance': _yuan(minimum), 'minDay': points.index(minimum),
                      'breachDays': len(breaches), 'firstBreachDay': breaches[0] if breaches else None,
                      'gap': _yuan(gap), 'points': [_yuan(balance) for balance in points]},
        'spending': {'rowCount': len(rows), 'outflow': _yuan(outflow),
                     'consumptionOutflow': _yuan(consumption_outflow), 'inflow': _yuan(inflow),
                     'net': _yuan(inflow - outflow), 'categories': categories,
                     'largest': largest[:5], 'review': review},
        'upcoming': upcoming, 'subscriptions': subscriptions, 'actions': actions,
        'healthCheck': {'level': health_level, 'headline': health_headline,
                        'summary': health_summary, 'signals': health_signals},
        'hiddenCosts': {
            'smallOutflowThreshold': _yuan(SMALL_OUTFLOW_CENTS),
            'smallOutflowCount': len(small_outflows),
            'smallOutflowAmount': _yuan(hidden_amount),
            'smallOutflows': small_outflows[:8],
            'flaggedRenewalAmount': _yuan(flagged_renewal),
            'flaggedSubscriptions': flagged_subscriptions,
        },
        'explainability': {
            'dataUsed': [
                f'{len(rows)} 笔当前可见合成流水',
                f'{len(snapshot["bills"])} 项已登记账单',
                f'{sum(1 for sub in snapshot["subscriptions"] if sub["status"] == "active")} 项有效订阅',
                f'{len(points)} 个逐日现金节点',
            ],
            'rules': [
                '现金体检比较未来最低现金与用户自己设定的现金底线。',
                '小额支出只统计当前可见、单笔不超过 ¥100 且不属于账户调拨或还款的出账。',
                '隐性续费只列出服务端已标记为低使用或重复的有效订阅。',
                '流水核对只按指令型备注或单笔出账达到 ¥2,000 的确定性规则触发。',
            ],
            'uncertainties': [
                '当前流水不是完整银行月报，无法代表所有现实支出。',
                '尚未记录的现金消费、其他账户和临时账单不会自动出现在结果中。',
                '预计工资和未来到账都不是到账保证。',
                '规则提醒不是欺诈、信用或投资风险的最终判断。',
            ],
            'decisionBoundary': 'TROPOS 负责发现、计算和解释；转账、取消续费、付款和画像变更仍由用户逐项确认。',
        },
        'outcomes': {'cancelledCount': cancelled_count, 'avoidedUpcomingOutflow': _yuan(avoided),
                     'receipts': cancelled_receipts},
        'assumptions': [
            '这是本机合成银行沙箱；预测基准日固定为 2026-10-20，第 0 天为今日，第 1–30 天为预测窗口。',
            '当前可见流水含合成历史样本与已记账操作，跨越多个日期且并非完整月报；不据此年化或判断前期趋势。',
            '出账构成包括还款、生活支出及账户调拨；目标储蓄与转账单独标识，不等同于消费总额。',
            f'预测沿用当前账单、有效订阅、未到账工资和每日 ¥{cash["daily"]:,.2f} 合成开销；预计工资不是到账保证。',
            '待确认计划未视为已付款，也不计入现金曲线；历史流水与备注不会生成可执行付款。',
            '订阅对比每次仅假设取消一项，并从该项第 1–30 天内的续费日起减少一次出账；今日余额不增加。',
            '取消续费不是退款；已取消数量记录当前状态，避免出账金额仅计算本窗口内一次续费，窗口外回执金额记为 0，不代表年度真实节省。',
            '流水待核对依据备注指令型关键词或单笔出账达到 ¥2,000 的可解释规则，不是欺诈识别模型或风险定论。',
            '所有待办仅供查看和核对；付款、取消续费与画像调整仍通过原有确认流程执行。',
        ],
    }
