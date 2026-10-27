"""家庭与日常：亲情付、小荷包、预算与记一笔。

这些能力与转账、卡片、理财共用同一张任务单和同一套权限分级：
- 亲情付：家人在额度和允许类目内发起代付请求，由你逐笔批准（L2，动钱）；拒绝、暂停、恢复为 L1；
  调整月额度属于授权变更（L2）；你也可以请父母为你代付，对方同意后款项才到账。
- 小荷包：命名的子账户，存入、取回走复式账本（L2）；新建荷包只改配置（L1）。
- 预算与记一笔：设定分类月预算、记录现金或其他渠道的支出（L1，可删除），不改银行余额。
数据均为合成演示数据。
"""
from __future__ import annotations

import json
import re
import time

from .service import BankError, cents, now, uid, yuan

HOUSEHOLD_TOOLS = {
    'family_status': (0, '查看亲情付'), 'pocket_status': (0, '查看小荷包'),
    'budget_status': (0, '查看预算'), 'spending_report': (0, '本月消费分析'),
    'family_decline': (1, '拒绝代付请求'), 'family_pause': (1, '暂停亲情付'), 'family_resume': (1, '恢复亲情付'),
    'family_ask': (1, '请家人代付'), 'pocket_create': (1, '新建小荷包'), 'budget_set': (1, '设定月预算'),
    'note_add': (1, '记一笔'),
    'family_approve': (2, '批准代付'), 'family_limit': (2, '调整亲情付额度'),
    'pocket_deposit': (2, '存入小荷包'), 'pocket_withdraw': (2, '从小荷包取回'),
}

# 合成家庭关系。members：你为家人开通的亲情付；incoming：家人为你开通的；requests：等你批准的代付。
FAMILY = {
    'A': {'members': [{'id': 'f_brother', 'name': '晓帆', 'relation': '弟弟', 'monthlyCap': 800, 'used': 420,
                       'categories': ['餐饮', '学习', '交通'], 'status': 'active'}],
          'incoming': [{'id': 'in_mom', 'name': '妈妈', 'relation': '妈妈', 'monthlyCap': 1000, 'used': 300,
                        'note': '妈妈为你开通的亲情付，可用于房租与生活缴费'}],
          'requests': [{'id': 'rq_a1', 'member': 'f_brother', 'amount': 300, 'merchant': '大学书店', 'category': '学习', 'memo': '下学期教材'}]},
    'B': {'members': [{'id': 'f_mom', 'name': '妈妈', 'relation': '妈妈', 'monthlyCap': 500, 'used': 120,
                       'categories': ['日常', '医疗', '餐饮'], 'status': 'active'}],
          'incoming': [],
          'requests': [{'id': 'rq_b1', 'member': 'f_mom', 'amount': 199, 'merchant': '养生直播间', 'category': '保健品',
                        'memo': '直播间限时特价', 'risk': '直播间保健品是针对老人的高发诈骗与冲动消费类目'}]},
    'C': {'members': [{'id': 'f_daughter', 'name': '小满', 'relation': '女儿', 'monthlyCap': 600, 'used': 180,
                       'categories': ['学习', '餐饮', '文具'], 'status': 'active'},
                      {'id': 'f_dad', 'name': '爸爸', 'relation': '爸爸', 'monthlyCap': 2000, 'used': 400,
                       'categories': ['医疗', '日常'], 'status': 'active'}],
          'incoming': [],
          'requests': [{'id': 'rq_c1', 'member': 'f_daughter', 'amount': 268, 'merchant': '游戏充值中心', 'category': '游戏', 'memo': '皮肤礼包'},
                       {'id': 'rq_c2', 'member': 'f_dad', 'amount': 860, 'merchant': '社区药房', 'category': '医疗', 'memo': '降压药三个月用量'}]},
}
POCKETS = {'A': [{'id': 'emergency', 'name': '应急小荷包', 'target': 3000}],
           'B': [{'id': 'repay', 'name': '还款小荷包', 'target': 3000}],
           'C': [{'id': 'edu', 'name': '小满教育金', 'target': 20000}]}
BUDGETS = {'A': {'餐饮': 800, '数码': 300}, 'B': {'美妆': 300, '餐饮': 1200}, 'C': {'餐饮': 2000, '娱乐': 500}}
CATEGORY_WORDS = {
    '餐饮': ['外卖', '午饭', '晚饭', '早饭', '早餐', '午餐', '晚餐', '咖啡', '奶茶', '吃饭', '聚餐', '餐饮', '饭'],
    '交通': ['打车', '地铁', '公交', '出行', '加油', '停车', '交通'],
    '日常': ['超市', '便利店', '买菜', '日用', '日常'],
    '数码': ['数码', '手机', '耳机', '配件', '电脑'],
    '美妆': ['美妆', '护肤', '口红', '化妆'],
    '学习': ['教材', '课程', '买书', '学习', '培训'],
    '医疗': ['药', '医院', '医疗', '看病'],
    '娱乐': ['电影', '游戏', '娱乐', '演出', 'KTV'],
}
# 记账口径：只统计消费类出账；转账、还款、住房、储蓄与理财不是“花掉的钱”。
NOT_SPENDING = {'储蓄', '应急金', '收入', '转账', '还款', '住房', '贷款', '分期', '目标储蓄', '理财申购', '理财赎回', '小荷包', '亲情付到账', '退款'}


def category_of(text):
    return next((cat for cat, words in CATEGORY_WORDS.items() if any(w in text for w in words)), None)


class Household:
    def __init__(self, bank):
        self.bank = bank
        with bank._db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS household(user_id TEXT PRIMARY KEY, content TEXT NOT NULL)')

    # ------------------------------------------------------------------ 状态
    def state(self, db, user_id):
        row = db.execute('SELECT content FROM household WHERE user_id=?', (user_id,)).fetchone()
        if row:
            return json.loads(row[0])
        persona = self.bank._user(db, user_id)[0]['persona']
        family = json.loads(json.dumps(FAMILY[persona]))
        for request in family['requests']:
            request.update(status='pending', at=now())
        family['asks'] = []
        state = {'family': family, 'pockets': [dict(p) for p in POCKETS[persona]],
                 'budgets': dict(BUDGETS[persona]), 'notes': []}
        db.execute('INSERT INTO household VALUES(?,?)', (user_id, json.dumps(state, ensure_ascii=False)))
        return state

    @staticmethod
    def save(db, user_id, state):
        db.execute('UPDATE household SET content=? WHERE user_id=?', (json.dumps(state, ensure_ascii=False), user_id))

    def pocket_balance(self, db, user_id, pocket_id):
        row = db.execute('SELECT balance FROM accounts WHERE user_id=? AND kind=?', (user_id, 'pocket_' + pocket_id)).fetchone()
        return row[0] if row else 0

    def spending(self, db, user_id, state, snapshot):
        """本月（合成账本基准月 10 月）按分类的消费；手记一笔计入，转账与还款不计。"""
        totals = {}
        for tx in snapshot['transactions']:
            cat, amount = tx.get('cat'), tx.get('amount', 0)
            month_ok = tx.get('status') != 'historical' or str(tx.get('time', '')).startswith('10/')
            if amount < 0 and cat not in NOT_SPENDING and month_ok:
                cat = '餐饮' if cat == '餐饮' else cat
                totals[cat] = totals.get(cat, 0) + cents(-amount)
        for note in state['notes']:
            totals[note['category']] = totals.get(note['category'], 0) + cents(note['amount'])
        rows = sorted(({'category': k, 'spent': yuan(v), 'budget': state['budgets'].get(k)} for k, v in totals.items()), key=lambda r: -r['spent'])
        for cat, budget in state['budgets'].items():
            if cat not in totals:
                rows.append({'category': cat, 'spent': 0, 'budget': budget})
        for row in rows:
            if row['budget']:
                row['ratio'] = round(row['spent'] / row['budget'], 3)
        return {'rows': rows, 'total': yuan(sum(totals.values())),
                'basis': '合成账本 10 月可见消费流水与手记支出；转账、还款、房租、储蓄与理财不计入。'}

    def overview(self, user_id):
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            state = self.state(db, user_id)
            snapshot = self.bank._snapshot(db, user_id)
            pockets = [dict(p, balance=yuan(self.pocket_balance(db, user_id, p['id']))) for p in state['pockets']]
            return {'family': state['family'], 'pockets': pockets, 'budgets': state['budgets'],
                    'notes': state['notes'][-20:][::-1], 'spending': self.spending(db, user_id, state, snapshot)}

    # ------------------------------------------------------------------ 理解
    def _member(self, family, text):
        found = [m for m in family['members'] if m['name'] in text or m['relation'] in text]
        return found

    def classify(self, text, state, amount_or_remaining, parse_amount):
        family = state['family']
        decide = re.search(r"同意|批准|通过|准了|拒绝|驳回|不同意|不批", text)
        members = self._member(family, text)
        pending = [r for r in family['requests'] if r['status'] == 'pending']
        if decide and (members or re.search(r"亲情|代付|请求|申请", text)):
            named = re.search(r"妈妈|爸爸|父母|弟弟|妹妹|哥哥|姐姐|女儿|儿子|爷爷|奶奶|外公|外婆|老婆|老公|孩子", text)
            if named and not members:
                # 说的是没开通亲情付的家人，绝不能落到别人的请求上。
                return ('clarify', {'question': f"没有找到「{named[0]}」的待批准代付请求，未处理任何请求。"})
            if members:
                pending = [r for r in pending if r['member'] in {m['id'] for m in members}]
            if len(pending) != 1:
                names = {m['id']: m for m in family['members']}
                return ('clarify', {'question': '请说明是哪一笔代付请求。' if pending else '当前没有对应的待批准代付请求。',
                                    'options': [{'label': f"{names[r['member']]['relation']} · {r['merchant']} ¥{r['amount']:,}",
                                                 'message': f"{decide[0]}{names[r['member']]['relation']}的{r['merchant']}代付"} for r in pending]})
            tool = 'family_decline' if re.search(r"拒绝|驳回|不同意|不批", text) else 'family_approve'
            return (tool, {'request': pending[0]['id']})
        if re.search(r"(让|请|找|叫)(妈妈|爸爸|父母|家里|老妈|老爸).{0,8}(帮我付|代付|帮我出|帮忙付|帮我交)", text):
            amount = parse_amount(text)
            inc = next((i for i in family['incoming'] if i['relation'] in text or i['name'] in text), None)
            if not family['incoming']:
                return ('clarify', {'question': '目前没有家人为你开通亲情付，无法发起代付请求。'})
            if inc is None or amount is None:
                return ('clarify', {'question': '请说明请哪位家人代付、金额是多少。'})
            purpose = re.sub(r".*(帮我付|代付|帮我出|帮忙付|帮我交)", "", text) or '代付'
            purpose = re.sub(r"[\d.,，]+\s*(元|块)?", "", purpose).strip() or '代付'
            return ('family_ask', {'incoming': inc['id'], 'amount': amount, 'purpose': purpose[:20]})
        if re.search(r"亲情|亲属卡|代付", text) or (members and re.search(r"额度|暂停|停用|恢复|开通", text)):
            if not members and re.search(r"额度|暂停|停用|恢复", text):
                return ('clarify', {'question': '请说明是哪位家人的亲情付。',
                                    'options': [{'label': m['relation'], 'message': f"{text}（{m['relation']}）"} for m in family['members']]})
            if re.search(r"暂停|停用|冻结|关闭", text):
                return ('family_pause', {'member': members[0]['id']}) if len(members) == 1 else ('clarify', {'question': '请说明暂停哪位家人的亲情付。'})
            if re.search(r"恢复|重新开|启用", text):
                return ('family_resume', {'member': members[0]['id']}) if len(members) == 1 else ('clarify', {'question': '请说明恢复哪位家人的亲情付。'})
            if re.search(r"额度|上限|限额", text) and re.search(r"调|改|提|降|设|到", text):
                amount = parse_amount(text)
                if len(members) != 1 or amount is None:
                    return ('clarify', {'question': '请说明哪位家人、每月额度调到多少。'})
                return ('family_limit', {'member': members[0]['id'], 'amount': amount})
            return ('family_status', {})
        pocket_names = [p for p in state['pockets'] if p['name'] in text]
        if re.search(r"荷包|小金库|存钱罐", text) or pocket_names:
            if re.search(r"新建|建一个|开一个|建个|开个|创建|新开", text):
                head = re.sub(r"新建|创建|新开|建一个|开一个|建个|开个|一个|叫做|叫|名为|名字是|[「」“”\"]", "", text.split('荷包')[0])
                head = head.removesuffix('小')
                name = (re.search(r"([一-龥A-Za-z0-9]{1,8})$", head) or [None, '新'])[1]
                return ('pocket_create', {'name': name + '小荷包', 'target': parse_amount(text) or 0})
            pocket = pocket_names[0] if len(pocket_names) == 1 else (state['pockets'][0] if len(state['pockets']) == 1 else None)
            if re.search(r"取出|取回|拿出|转回|提出", text):
                if pocket is None:
                    return ('clarify', {'question': '请说明从哪个小荷包取回。'})
                amount = amount_or_remaining(text)
                return ('pocket_withdraw', {'pocket': pocket['id'], 'amount': None if amount == 'remaining' else amount})
            if re.search(r"存|放|转|攒", text):
                if pocket is None:
                    return ('clarify', {'question': '请说明存进哪个小荷包。', 'options': [{'label': p['name'], 'message': f"{text}（{p['name']}）"} for p in state['pockets']]})
                amount = amount_or_remaining(text)
                if amount is None:
                    return ('clarify', {'question': '存入多少？也可以说“剩下的”。'})
                return ('pocket_deposit', {'pocket': pocket['id'], 'amount': amount})
            return ('pocket_status', {})
        if re.search(r"预算", text) or re.search(r"(每月|每个月|一个月|这个月).{0,8}(不超过|控制在|最多花)", text):
            category = category_of(text)
            amount = parse_amount(text)
            if re.search(r"设|定|不超过|控制在|最多|调|改", text) and amount is not None:
                if not category:
                    return ('clarify', {'question': '这笔预算属于哪个分类？如餐饮、交通、日常、数码、美妆、学习、医疗、娱乐。'})
                return ('budget_set', {'category': category, 'amount': amount})
            return ('budget_status', {})
        if re.search(r"记一笔|记账|记下|记个账|记上", text) or re.match(r"(今天)?(午饭|晚饭|早饭|早餐|午餐|晚餐|打车|咖啡|奶茶|买菜|地铁|停车|加油|电影)(花了|用了)?\d", text):
            amount = parse_amount(text)
            category = category_of(text)
            if amount is None:
                return ('clarify', {'question': '这一笔花了多少钱？'})
            label = re.sub(r"记一笔|记账|记下|记个账|记上|花了|用了|[\d.,，]+\s*(元|块)?|今天|帮我|[:：]", "", text).strip() or (category or '支出')
            return ('note_add', {'amount': amount, 'category': category or '其他', 'label': label[:20]})
        if re.search(r"花在哪|花了多少|消费分析|月报|支出分析|记账本|这个月花", text):
            return ('spending_report', {})
        return None

    # ------------------------------------------------------------------ 规划
    def step(self, ops, db, user_id, step, deduction, saved):
        state = self.state(db, user_id)
        tool, args, family = step['tool'], step['args'], state['family']
        members = {m['id']: m for m in family['members']}
        if step['tier'] == 0:
            step['status'] = 'done'
            step['result'] = self.read(db, user_id, tool, state)
            return
        if tool in ('family_approve', 'family_decline'):
            request = next(r for r in family['requests'] if r['id'] == args['request'])
            member = members[request['member']]
            step['title'] = f"{'批准' if tool == 'family_approve' else '拒绝'}{member['relation']}{member['name'] if member['name'] != member['relation'] else ''}的代付：{request['merchant']}"
            step['request'] = dict(request, memberName=member['name'], relation=member['relation'])
            if tool == 'family_decline':
                step['checks'] = [{'name': '请求状态', 'ok': request['status'] == 'pending', 'detail': '拒绝不扣款，家人会收到通知。'}]
                return
            amount = cents(request['amount'], True)
            left = cents(member['monthlyCap']) - cents(member['used'])
            checks = [{'name': '本月剩余额度', 'ok': amount <= left, 'detail': f"本月额度 ¥{member['monthlyCap']:,}，已用 ¥{member['used']:,}，剩余 ¥{yuan(left):,.2f}。"},
                      {'name': '允许类目', 'ok': request['category'] in member['categories'],
                       'detail': f"「{request['category']}」{'在' if request['category'] in member['categories'] else '不在'}你为{member['relation']}设定的类目（{'、'.join(member['categories'])}）内。"},
                      {'name': '亲情付状态', 'ok': member['status'] == 'active', 'detail': '已暂停的亲情付不能代付。' if member['status'] != 'active' else '正常。'}]
            if request.get('risk'):
                checks.append({'name': '商户风险', 'ok': False, 'detail': request['risk'] + '；建议先和家人通个电话。'})
            checks += ops._fund_checks(db, user_id, deduction, amount)
            step.update(amount=yuan(amount), checks=checks, impact=amount)
        elif tool in ('family_pause', 'family_resume', 'family_limit'):
            member = members[args['member']]
            step['title'] = f"{HOUSEHOLD_TOOLS[tool][1]}：{member['relation']}{'' if member['name'] == member['relation'] else member['name']}"
            if tool == 'family_pause':
                step['checks'] = [{'name': '当前状态', 'ok': member['status'] == 'active', 'detail': '暂停后家人无法发起新的代付，可随时恢复。'}]
            elif tool == 'family_resume':
                step['checks'] = [{'name': '当前状态', 'ok': member['status'] == 'paused', 'detail': '恢复后家人可在额度与类目内发起代付。' if member['status'] == 'paused' else '亲情付未暂停，无需恢复。'}]
            else:
                target = cents(args['amount'], True)
                step.update(amount=yuan(target), title=step['title'] + f" 每月 ¥{yuan(target):,.0f}")
                step['checks'] = [{'name': '额度范围', 'ok': 0 < target <= 500000, 'detail': '亲情付每月额度不超过 ¥5,000（演示规则）。'},
                                  {'name': '授权变更', 'ok': True, 'detail': f"原额度 ¥{member['monthlyCap']:,}；调整只影响以后的代付，每一笔仍需你批准。"}]
        elif tool == 'family_ask':
            inc = next(i for i in family['incoming'] if i['id'] == args['incoming'])
            amount = cents(args['amount'], True)
            left = cents(inc['monthlyCap']) - cents(inc['used'])
            step.update(amount=yuan(amount), title=f"请{inc['relation']}代付：{args['purpose']}",
                        checks=[{'name': '对方为你设的剩余额度', 'ok': amount <= left, 'detail': f"本月剩余 ¥{yuan(left):,.2f}。"},
                                {'name': '到账方式', 'ok': True, 'detail': f"只发出请求；{inc['relation']}同意后款项才会到账，可随时撤回。"}])
        elif tool == 'pocket_create':
            exists = any(p['name'] == args['name'] for p in state['pockets'])
            step['title'] = f"新建「{args['name']}」" + (f"，目标 ¥{args['target']:,.0f}" if args.get('target') else '')
            step['checks'] = [{'name': '名称', 'ok': not exists and len(state['pockets']) < 8, 'detail': '已有同名荷包。' if exists else '最多 8 个小荷包。'}]
        elif tool in ('pocket_deposit', 'pocket_withdraw'):
            pocket = next(p for p in state['pockets'] if p['id'] == args['pocket'])
            held = self.pocket_balance(db, user_id, pocket['id'])
            if tool == 'pocket_deposit':
                amount = ops._ceiling(db, user_id, deduction, saved) if args['amount'] == 'remaining' else cents(args['amount'], True)
                step['title'] = f"存入「{pocket['name']}」"
                if args['amount'] == 'remaining':
                    step['note'] = f"“剩下的”按本单前面步骤扣除后的建议上限计算为 ¥{yuan(amount):,.2f}。"
                if amount <= 0:
                    step.update(status='blocked', amount=0, note='扣除前面步骤后，没有可安全存入的剩余金额。')
                    return
                step.update(amount=yuan(amount), impact=amount, pocket=pocket, checks=ops._fund_checks(db, user_id, deduction, amount))
            else:
                amount = held if args.get('amount') is None else cents(args['amount'], True)
                step.update(title=f"从「{pocket['name']}」取回", amount=yuan(amount), impact=-amount, pocket=pocket,
                            checks=[{'name': '荷包余额', 'ok': 0 < amount <= held, 'detail': f"荷包里有 ¥{yuan(held):,.2f}。"}])
        elif tool == 'budget_set':
            old = state['budgets'].get(args['category'])
            step.update(title=f"设定「{args['category']}」每月预算 ¥{args['amount']:,.0f}",
                        checks=[{'name': '预算性质', 'ok': True, 'detail': (f"原预算 ¥{old:,}；" if old else '') + '预算只用于提醒和“先别买”，不会拦截你的支付。'}])
        elif tool == 'note_add':
            step.update(title=f"记一笔：{args['label']}（{args['category']}）", amount=args['amount'],
                        checks=[{'name': '记账口径', 'ok': True, 'detail': '记的是现金或其他渠道的支出，不改银行余额；可随时删除。'}])

    def read(self, db, user_id, tool, state):
        if tool == 'family_status':
            return {'family': state['family']}
        if tool == 'pocket_status':
            return {'pockets': [dict(p, balance=yuan(self.pocket_balance(db, user_id, p['id']))) for p in state['pockets']]}
        snapshot = self.bank._snapshot(db, user_id)
        return {'spending': self.spending(db, user_id, state, snapshot), 'budgets': state['budgets']}

    # ------------------------------------------------------------------ 执行
    def execute(self, ops, db, user_id, step, key):
        state = self.state(db, user_id)
        tool, args, family = step['tool'], step['args'], state['family']
        members = {m['id']: m for m in family['members']}
        receipt = {'id': uid('house_receipt'), 'at': now()}
        if tool in ('family_approve', 'family_decline'):
            request = next(r for r in family['requests'] if r['id'] == args['request'])
            if request['status'] != 'pending':
                return False, '这笔代付请求已处理，未重复执行。'
            if tool == 'family_decline':
                request.update(status='declined', receipt=receipt)
                self.save(db, user_id, state)
                return True, {'receipt': dict(receipt, status='declined')}
            member = members[request['member']]
            amount = cents(request['amount'], True)
            checks = [c for c in step['checks'] if c['name'] in ('允许类目', '商户风险')]
            left = cents(member['monthlyCap']) - cents(member['used'])
            checks.append({'name': '本月剩余额度', 'ok': amount <= left and member['status'] == 'active', 'detail': '执行时重新核对额度与状态。'})
            checks += ops._fund_checks(db, user_id, 0, amount)
            failed = [c['name'] for c in checks if not c['ok']]
            if failed:
                return False, '执行前重新核验未通过：' + '、'.join(failed) + '。未扣款。'
            tx = self.bank._post(db, user_id, key, '亲情付', amount, request['merchant'], 'external',
                                 {'missionStep': step['id'], 'familyRequest': request['id'], 'member': member['name']})
            member['used'] = yuan(cents(member['used']) + amount)
            request.update(status='approved', transactionId=tx['id'], receipt=dict(receipt, transaction=tx['id']))
            self.save(db, user_id, state)
            return True, {'transaction': tx}
        if tool in ('family_pause', 'family_resume', 'family_limit'):
            member = members[args['member']]
            if tool == 'family_pause':
                if member['status'] != 'active':
                    return False, '亲情付已是暂停状态。'
                member['status'] = 'paused'
            elif tool == 'family_resume':
                if member['status'] != 'paused':
                    return False, '亲情付未暂停。'
                member['status'] = 'active'
            else:
                member['monthlyCap'] = step['amount']
            self.save(db, user_id, state)
            return True, {'receipt': dict(receipt, status=member['status'], monthlyCap=member['monthlyCap'])}
        if tool == 'family_ask':
            inc = next(i for i in family['incoming'] if i['id'] == args['incoming'])
            ask = {'id': uid('ask'), 'incoming': inc['id'], 'relation': inc['relation'], 'amount': step['amount'],
                   'purpose': args['purpose'], 'status': 'waiting', 'at': now()}
            family.setdefault('asks', []).append(ask)
            self.save(db, user_id, state)
            return True, {'receipt': dict(receipt, status='request_sent', ask=ask['id'])}
        if tool == 'pocket_create':
            if any(p['name'] == args['name'] for p in state['pockets']):
                return False, '已有同名小荷包。'
            pocket = {'id': uid('pk')[:16], 'name': args['name'], 'target': args.get('target') or 0}
            state['pockets'].append(pocket)
            self.save(db, user_id, state)
            return True, {'receipt': dict(receipt, status='created', pocket=pocket['id'])}
        if tool in ('pocket_deposit', 'pocket_withdraw'):
            pocket_id = args['pocket']
            account = user_id + ':pocket_' + pocket_id
            db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?,0)', (account, user_id, 'pocket_' + pocket_id))
            amount = cents(step['amount'], True)
            if tool == 'pocket_deposit':
                failed = [c['name'] for c in ops._fund_checks(db, user_id, 0, amount) if not c['ok']]
                if failed:
                    return False, '执行前重新核验未通过：' + '、'.join(failed) + '。未存入。'
                tx = self.bank._post(db, user_id, key, '小荷包', amount, step['pocket']['name'], 'pocket_' + pocket_id, {'missionStep': step['id']})
            else:
                if self.pocket_balance(db, user_id, pocket_id) < amount:
                    return False, '荷包余额不足，未取回。'
                tx = self.bank._post(db, user_id, key, '小荷包', -amount, step['pocket']['name'], 'pocket_' + pocket_id, {'missionStep': step['id'], 'direction': 'withdraw'})
            return True, {'transaction': tx}
        if tool == 'budget_set':
            state['budgets'][args['category']] = args['amount']
            self.save(db, user_id, state)
            return True, {'receipt': dict(receipt, status='budget_set')}
        if tool == 'note_add':
            note = {'id': uid('note'), 'amount': args['amount'], 'category': args['category'], 'label': args['label'], 'at': now()}
            state['notes'].append(note)
            self.save(db, user_id, state)
            return True, {'receipt': dict(receipt, status='noted', note=note['id'])}
        raise BankError(400, 'UNKNOWN_TOOL', '不支持的工具。')

    # ------------------------------------------------------------------ 演示：家人回应你的代付请求
    def simulate_ask(self, user_id, ask_id, payload):
        decision = payload.get('decision') if isinstance(payload, dict) else None
        if decision not in ('approve', 'decline'):
            raise BankError(400, 'INVALID_DECISION', '请选择模拟同意或拒绝。')
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            state = self.state(db, user_id)
            family = state['family']
            ask = next((a for a in family.get('asks', []) if a['id'] == ask_id), None)
            if ask is None:
                raise BankError(404, 'ASK_NOT_FOUND', '没有这笔代付请求。')
            if ask['status'] != 'waiting':
                return {'ask': ask, 'snapshot': self.bank._snapshot(db, user_id)}
            if decision == 'decline':
                ask['status'] = 'declined'
            else:
                inc = next(i for i in family['incoming'] if i['id'] == ask['incoming'])
                amount = cents(ask['amount'], True)
                if cents(inc['monthlyCap']) - cents(inc['used']) < amount:
                    raise BankError(409, 'FAMILY_CAP_EXCEEDED', '超过家人为你设定的本月额度。')
                before = self.bank._summary(db, user_id)
                tx = self.bank._post(db, user_id, 'ask:' + ask_id, '亲情付到账', -amount, f"{inc['relation']}的亲情付", 'income', {'ask': ask_id})
                inc['used'] = yuan(cents(inc['used']) + amount)
                ask.update(status='paid', transactionId=tx['id'])
                self.bank._changed(db, user_id, before, f"{inc['relation']}的亲情付已到账", '模拟家人同意', f"{ask['purpose']} ¥{ask['amount']:,.2f}，记入已到账余额。")
            self.save(db, user_id, state)
            return {'ask': ask, 'snapshot': self.bank._snapshot(db, user_id)}
