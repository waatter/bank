"""一句话办完一串事：多步任务单、卡片管理与理财。

任务单（mission）把一句复合请求拆成有序的工具调用。每个工具有权限等级：

- L0 只读：规划时直接执行，不改账。
- L1 可逆：需要用户勾选授权；冻结与解冻互为撤回。
- L2 资金与合约：需要授权，并在执行时由守诺按累计影响逐项重新核验。
- L3 不可逆：除授权外，还要输入确认口令（卡号后四位）。

规划只生成草案。执行时在同一个数据库事务里按顺序重新核验每一步，某一步不通过即停止，
后续步骤标记为暂停；已完成的步骤保留回执。同一任务单重复确认只返回原结果。
"""
from __future__ import annotations

import json
import re
import secrets
import time
from decimal import Decimal

from .model import _money, _safety_guard, rule_intent
from .service import BankError, cents, now, uid, yuan

TIERS = {
    0: {'name': 'L0 只读', 'rule': '直接查询，不改账'},
    1: {'name': 'L1 可逆', 'rule': '需你授权，可随时撤回'},
    2: {'name': 'L2 资金', 'rule': '需你授权，执行前逐项重新核验'},
    3: {'name': 'L3 不可逆', 'rule': '需你授权并输入确认口令'},
}
TOOLS = {
    'cash_plan': (0, '查看现金计划'), 'bills_report': (0, '核对到期账单'),
    'subscriptions_report': (0, '查看订阅'), 'card_status': (0, '查看银行卡'),
    'wealth_catalog': (0, '查看理财产品'),
    'card_freeze': (1, '临时冻结银行卡'), 'card_unfreeze': (1, '解除冻结'),
    'transfer': (2, '转账'), 'cancel_subscription': (2, '取消订阅'),
    'wealth_subscribe': (2, '申购理财'), 'wealth_redeem': (2, '赎回理财'),
    'card_limit': (2, '申请调整额度'), 'card_report_lost': (3, '挂失银行卡'),
}

# 合成卡片：每位用户的卡号后四位与额度，均为演示数据。
CARDS = {
    'A': [{'id': 'debit', 'type': '借记卡', 'last4': '0826', 'limit': 50000},
          {'id': 'credit', 'type': '信用卡', 'last4': '3108', 'limit': 12000}],
    'B': [{'id': 'debit', 'type': '借记卡', 'last4': '6611', 'limit': 50000},
          {'id': 'credit', 'type': '信用卡', 'last4': '2203', 'limit': 15000},
          {'id': 'credit2', 'type': '信用卡', 'last4': '7719', 'limit': 8000}],
    'C': [{'id': 'debit', 'type': '借记卡', 'last4': '5520', 'limit': 50000},
          {'id': 'credit', 'type': '信用卡', 'last4': '9036', 'limit': 30000}],
}
# 合成理财产品。风险等级 R1–R4；锁定期内不能赎回。
PRODUCTS = [
    {'id': 'P1', 'name': '活期宝', 'risk': 1, 'lockDays': 0, 'minAmount': 1, 'yield': '近七日年化 1.6%（示例）', 'keywords': ['活期', '货币', '随存随取', '零钱']},
    {'id': 'P2', 'name': '稳健债基 90 天', 'risk': 2, 'lockDays': 90, 'minAmount': 1000, 'yield': '业绩比较基准 2.4%（示例）', 'keywords': ['稳健', '债', '90', '定开', '定期']},
    {'id': 'P3', 'name': '均衡混合基金', 'risk': 3, 'lockDays': 0, 'minAmount': 100, 'yield': '净值波动，不承诺收益', 'keywords': ['混合', '基金', '股票', '均衡']},
    {'id': 'P4', 'name': '科技成长基金', 'risk': 4, 'lockDays': 0, 'minAmount': 100, 'yield': '净值波动大，不承诺收益', 'keywords': ['成长', '科技', '激进']},
]
RISK_NAMES = {1: 'R1 谨慎', 2: 'R2 稳健', 3: 'R3 平衡', 4: 'R4 进取'}

_SPLIT = re.compile(r"[，,；;。！!\n]+|(?:然后|并且|同时|顺便|另外|以及|还有|接着|之后再)")
_REMAINING = re.compile(r"剩下的|剩余的|剩下|余下的|多出来的")
_AMOUNT = re.compile(r"(?:人民币|[¥￥])?\s*(\d+(?:\.\d+)?)\s*(万|千|k|K|w|W)?")
_CN_AMOUNT = re.compile(r"([零〇一二两三四五六七八九十百千万点]+)\s*(?:元|块)")
_UNITS = {'万': 10000, 'w': 10000, 'W': 10000, '千': 1000, 'k': 1000, 'K': 1000}


def parse_amount(text):
    """分句里的金额；期限、尾号等数字不算金额。无法精确到分时返回 None，由上层澄清。"""
    text = re.sub(r"(?<=\d)[,，](?=\d{3})", "", text)
    text = re.sub(r"尾号\s*\d+|\d+\s*(?:天|日|号|期|个月|张|项|个|次)", "", text)
    try:
        match = _AMOUNT.search(text)
        if match:
            value = Decimal(match[1]) * _UNITS.get(match[2] or '', 1)
        else:
            match = _CN_AMOUNT.search(text)
            if not match:
                return None
            value = _money(match[1] + '元')
        cents(str(value), True)
        return float(value)
    except (ValueError, ArithmeticError, BankError):
        return None


def risk_capacity(profile, shocked):
    """画像推断的风险承受上限；正式申购前仍应完成风险测评，这里只用于演示适当性拦截。"""
    level = {'grow': 3, 'accumulate': 2, 'protect': 1, 'recover': 1}.get(profile.get('goalType'), 1)
    if profile.get('incomeStability') != 'stable':
        level = min(level, 2)
    if shocked:
        level = min(level, 1)
    return level


class AgentOps:
    def __init__(self, bank):
        self.bank = bank
        with bank._db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS cards(user_id TEXT, id TEXT, content TEXT, PRIMARY KEY(user_id,id))')
            db.execute('CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL)')

    # ------------------------------------------------------------------ 卡片与理财状态
    def _cards(self, db, user_id):
        rows = [json.loads(r[0]) for r in db.execute('SELECT content FROM cards WHERE user_id=? ORDER BY rowid', (user_id,))]
        if rows:
            return rows
        user, _ = self.bank._user(db, user_id)
        for card in CARDS[user['persona']]:
            card = dict(card, status='active', pendingLimit=None)
            db.execute('INSERT OR IGNORE INTO cards VALUES(?,?,?)', (user_id, card['id'], json.dumps(card, ensure_ascii=False)))
        return [dict(c, status='active', pendingLimit=None) for c in CARDS[user['persona']]]

    @staticmethod
    def _save_card(db, user_id, card):
        db.execute('UPDATE cards SET content=? WHERE user_id=? AND id=?', (json.dumps(card, ensure_ascii=False), user_id, card['id']))

    def _holdings(self, db, user_id):
        out = []
        bought = [json.loads(r[0]) for r in db.execute("SELECT content FROM transactions WHERE user_id=? AND kind='理财申购' ORDER BY rowid DESC", (user_id,))]
        for product in PRODUCTS:
            row = db.execute('SELECT balance FROM accounts WHERE user_id=? AND kind=?', (user_id, 'wealth_' + product['id'])).fetchone()
            if row and row[0]:
                # 最近一次申购的锁定期覆盖全部持有份额（演示简化，偏保守）。
                last = next((tx for tx in bought if tx.get('productId') == product['id']), {})
                out.append({'productId': product['id'], 'name': product['name'], 'amount': yuan(row[0]),
                            'lockedUntil': last.get('lockedUntil')})
        return out

    def portfolio(self, user_id):
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            snap = self.bank._snapshot(db, user_id)
            capacity = risk_capacity(snap['profile'], snap['shocked'])
            return {'cards': self._cards(db, user_id), 'holdings': self._holdings(db, user_id),
                    'products': [dict(p, riskName=RISK_NAMES[p['risk']], suitable=p['risk'] <= capacity) for p in PRODUCTS],
                    'riskCapacity': {'level': capacity, 'name': RISK_NAMES[capacity],
                                     'basis': '由你确认的目标、收入稳定性与当前收入事件推断；正式申购前应完成风险测评。'},
                    'tiers': TIERS}

    # ------------------------------------------------------------------ 拆句与理解
    @staticmethod
    def split(message):
        clauses = []
        for clause in (c.strip() for c in _SPLIT.split(message)):
            if not clause:
                continue
            # “备注……”属于上一笔转账，不是新的一件事。
            if clauses and re.match(r"(?:备注|附言)", clause):
                clauses[-1] += '，' + clause
            else:
                clauses.append(clause)
        return clauses

    def _pick_card(self, cards, clause):
        tail = re.search(r"尾号\s*(\d{4})", clause)
        if tail:
            found = [c for c in cards if c['last4'] == tail[1]]
        elif '信用' in clause:
            found = [c for c in cards if c['type'] == '信用卡']
        elif re.search(r"借记|储蓄|工资卡|银行卡", clause):
            found = [c for c in cards if c['type'] == '借记卡']
        else:
            found = cards
        return found[0] if len(found) == 1 else None, found

    @staticmethod
    def _card_options(clause, found):
        return [{'label': f"{c['type']} 尾号 {c['last4']}", 'message': f"{clause}（尾号{c['last4']}）"} for c in found]

    def classify(self, db, user_id, clause, subs, cards):
        """把一个分句变成一个工具调用；理解不了或信息不足时返回 clarify，从不猜测。"""
        text = re.sub(r"\s+", "", clause)
        if '卡' in text and re.search(r"好像|可能|似乎|不确定|不知道", text) and re.search(r"丢|被盗|找不到", text) and not re.search(r"挂失", text):
            # 不确定是否丢失时不挂失（不可逆）；先查看卡片，冻结需用户明说。
            return ('card_status', {'uncertainLoss': True})
        if '卡' in text and re.search(r"挂失|丢了|丢失|被盗|找不到", text):
            card, found = self._pick_card(cards, text)
            return ('card_report_lost', {'card': card['id']}) if card else ('clarify', {'question': '你有多张卡，请说明要挂失的卡（如“尾号 3108 的信用卡”）。', 'options': self._card_options(clause, found)})
        if '卡' in text and re.search(r"解冻|解锁|恢复使用|重新启用", text):
            card, found = self._pick_card(cards, text)
            return ('card_unfreeze', {'card': card['id']}) if card else ('clarify', {'question': '请说明要解冻哪张卡。', 'options': self._card_options(clause, found)})
        if '卡' in text and re.search(r"冻结|锁住|锁卡|暂停使用|停用", text):
            card, found = self._pick_card(cards, text)
            return ('card_freeze', {'card': card['id']}) if card else ('clarify', {'question': '请说明要冻结哪张卡。', 'options': self._card_options(clause, found)})
        if re.search(r"额度|限额", text) and re.search(r"调|提|升|降|改|申请", text):
            card, found = self._pick_card([c for c in cards if c['type'] == '信用卡'], text if '卡' in text else '信用' + text)
            amount = parse_amount(text)
            if amount is None:
                return ('clarify', {'question': '请说明希望调整到的额度金额。'})
            return ('card_limit', {'card': card['id'], 'amount': amount}) if card else ('clarify', {'question': '你有多张信用卡，请说明尾号。', 'options': self._card_options(clause, found)})
        if '卡' in text and re.search(r"查|看|状态|有哪些|几张", text):
            return ('card_status', {})
        if re.search(r"赎回|取出|卖掉|卖出", text) and re.search(r"理财|基金|活期宝|债|产品", text):
            product = next((p for p in PRODUCTS if any(k in text for k in p['keywords']) or p['name'] in text), None)
            amount = self._amount_or_remaining(text)
            return ('wealth_redeem', {'product': product['id'] if product else None, 'amount': amount})
        if re.search(r"理财|基金|活期宝|定开|债基", text) and re.search(r"买|申购|存|放|投|配置", text):
            product = next((p for p in PRODUCTS if any(k in text for k in p['keywords']) or p['name'] in text), None)
            amount = self._amount_or_remaining(text)
            if amount is None:
                return ('clarify', {'question': '请说明申购金额，或说“剩下的”。'})
            return ('wealth_subscribe', {'product': product['id'] if product else None, 'amount': amount})
        if re.search(r"理财|基金", text) and re.search(r"看|推荐|有什么|哪些|适合", text):
            return ('wealth_catalog', {})
        if re.search(r"取消|退订|停掉|关掉|不要了", text) and re.search(r"会员|订阅|续费|自动扣", text):
            active = [s for s in subs if s['status'] == 'active']
            named = [s for s in active if s['name'] in text or s['name'].replace(' ', '') in text]
            if not named and re.search(r"没用|不常用|低使用|很少用|重复|多余", text):
                named = [s for s in active if s.get('flag') in ('低使用', '重复')]
            if not named:
                return ('clarify', {'question': '请说明要取消的订阅名称。', 'options': [{'label': s['name'], 'message': '取消' + s['name']} for s in active]})
            return ('cancel_subscription', {'subs': [s['id'] for s in named]})
        if re.search(r"目标|储蓄|存起来|攒", text) and re.search(r"存|转|放|攒", text):
            amount = self._amount_or_remaining(text)
            if amount is None:
                return ('clarify', {'question': '存入目标储蓄的金额是多少？也可以说“剩下的”。'})
            return ('transfer', {'to': 'goal', 'amount': amount, 'when': 'now', 'memo': ''})
        if _REMAINING.search(text) and re.search(r"存|转|放", text):
            return ('clarify', {'question': '“剩下的”要转到哪里？可以说“剩下的存进目标储蓄”。'})
        if re.search(r"转|汇|打给|付给", text):
            intent = rule_intent(clause)
            purpose = re.fullmatch(r"(.+?(?:元|块))\s*([\u4e00-\u9fa5]{1,8})", clause.strip())
            if intent['action'] != 'transfer' and purpose:
                # “转 2000 元房租”：金额后的短用途按备注处理，原句其余部分仍须完整解析。
                intent = rule_intent(purpose[1] + '，备注' + purpose[2])
            if intent['action'] != 'transfer':
                return ('clarify', {'question': intent.get('question', '请说明收款人和金额。')})
            with_payees = [json.loads(r[0]) for r in db.execute('SELECT content FROM counterparties WHERE user_id=?', (user_id,))]
            from .model import _unique_registered_payee
            payee = _unique_registered_payee(with_payees, intent['to'])
            if payee is None:
                same = [p for p in with_payees if p.get('name') == intent['to']]
                return ('clarify', {'question': f"「{intent['to']}」对应不止一位或未登记的收款人，请说明关系或尾号。", 'options': [{'label': f"{p['name']}（{p['alias']}）", 'message': clause.replace(intent['to'], f"{p['name']}（{p['alias']}）", 1)} for p in same]})
            return ('transfer', {'to': payee['id'], 'amount': intent['amount'], 'when': intent['when'], 'memo': intent.get('memo', '')})
        if re.search(r"订阅|会员|续费", text):
            return ('subscriptions_report', {})
        if re.search(r"账单|还款|房租|水电|到期", text):
            return ('bills_report', {})
        if re.search(r"计划|打理|安排|现金|余额|还剩", text):
            return ('cash_plan', {})
        return ('clarify', {'question': f"没能理解「{clause}」，请换个说法或拆开说。"})

    @staticmethod
    def _amount_or_remaining(text):
        if _REMAINING.search(text):
            return 'remaining'
        return parse_amount(text)

    @staticmethod
    def wants_mission(message, clauses):
        """多个分句，或涉及卡片与理财时走任务单；单笔转账等仍走原有链路。"""
        text = re.sub(r"\s+", "", message)
        if re.search(r"卡.*(挂失|冻结|解冻|锁|额度|限额|丢)|(挂失|冻结|解冻|额度|限额).*卡|理财|基金|活期宝|债基|赎回|申购|剩下的|目标储蓄|存进|存到|存入", text):
            return True
        if re.search(r"买|下单|购物|双十一|双11|分期|花呗", text):
            return False  # 购物仍走“先别买”的单一链路
        actionable = [c for c in clauses if re.search(r"转|汇|取消|退订|冻结|挂失|解冻|存|账单|订阅|会员", c)]
        return len(actionable) >= 2

    # ------------------------------------------------------------------ 规划
    def _ceiling(self, db, user_id, deduction, saved=0):
        """“剩下的”按当前账本与本单已计划扣款确定，与资金计划的建议上限同一规则；本轮储蓄与理财限额按整单累计。"""
        profile = self.bank._profile(db, user_id)
        _, st = self.bank._user(db, user_id)
        balance = self.bank._balance(db, user_id) - deduction
        cash = self.bank._cash(db, user_id, deduction)
        cap = 100000 if profile['incomeStability'] != 'stable' or st['shocked'] else 200000
        return max(0, min(cap - saved, balance - cents(profile['reserve']), cents(cash['free'])))

    def _fund_checks(self, db, user_id, deduction, amount, when='now'):
        profile = self.bank._profile(db, user_id)
        _, st = self.bank._user(db, user_id)
        balance = self.bank._balance(db, user_id) - deduction
        cash = self.bank._cash(db, user_id, deduction + amount)
        reserve = cents(profile['reserve'])
        return [
            {'name': '单笔限额', 'ok': amount <= 5_000_000, 'detail': '单笔最高 ¥50,000。'},
            {'name': '已到账余额', 'ok': balance >= amount, 'detail': f"计入本单前面步骤后可用 ¥{yuan(balance):,.2f}。"},
            {'name': '现金底线', 'ok': balance - amount >= reserve, 'detail': f"执行后保留 ¥{yuan(balance - amount):,.2f}，底线 ¥{yuan(reserve):,.2f}。"},
            {'name': '30 天现金路径', 'ok': cash['free'] >= 0, 'detail': f"连同本单前面步骤，预计最低 ¥{cash['min']:,.2f}。"},
            {'name': '工资到账条件', 'ok': when != 'salary' or st['salaryReceived'], 'detail': '发薪后才能执行的步骤不在本单立即执行。'},
        ]

    def _step(self, db, user_id, tool, args, deduction, snap, cards, saved=0, clause=''):
        tier, title = TOOLS.get(tool, (0, '需要澄清'))
        step = {'id': uid('step'), 'tool': tool, 'tier': tier, 'tierName': TIERS[tier]['name'] if tool != 'clarify' else '澄清',
                'title': title, 'args': args, 'checks': [], 'status': 'ready', 'impact': 0, 'clause': clause}
        if tool == 'clarify':
            step.update(title='需要你补充', status='needs_clarification', note=args['question'], options=args.get('options', []))
            return step
        if tier == 0:
            step['status'] = 'done'
            step['result'] = self._read(db, user_id, tool, snap, cards)
            if args.get('uncertainLoss'):
                step['note'] = '不确定卡是否丢失时，先冻结（可随时解冻）比挂失（不可撤回）更稳妥；本步只查看卡片状态。'
            return step
        if tool == 'transfer':
            amount = self._ceiling(db, user_id, deduction, saved) if args['amount'] == 'remaining' else cents(args['amount'], True)
            payee = json.loads(db.execute('SELECT content FROM counterparties WHERE user_id=? AND id=?', (user_id, args['to'])).fetchone()[0])
            step['title'] = '存入目标储蓄' if args['to'] == 'goal' else '转账'
            if args['amount'] == 'remaining':
                step['note'] = f"“剩下的”按本单前面步骤扣除后的建议上限计算为 ¥{yuan(amount):,.2f}（受现金底线、30 天路径与本轮限额约束）。"
            if amount <= 0:
                step.update(status='blocked', note='扣除前面步骤后，已没有可安全转出的剩余金额。', amount=0)
                return step
            step.update(amount=yuan(amount), payee=payee, title='存入目标储蓄' if args['to'] == 'goal' else f"转给{payee['name']}" + (f"（{payee['alias']}）" if payee.get('alias') and payee['alias'] != payee['name'] else ''),
                        checks=self._fund_checks(db, user_id, deduction, amount, args['when']), impact=amount)
        elif tool == 'cancel_subscription':
            names = [s['name'] for s in snap['subscriptions'] if s['id'] in args['subs']]
            fee = sum(s['fee'] for s in snap['subscriptions'] if s['id'] in args['subs'])
            step.update(title='取消订阅：' + '、'.join(names), checks=[{'name': '订阅状态', 'ok': True, 'detail': f"每月少支出 ¥{fee:,.2f}；今天余额不增加。"}])
        elif tool in ('wealth_subscribe', 'wealth_redeem'):
            product = next((p for p in PRODUCTS if p['id'] == args.get('product')), None)
            capacity = risk_capacity(snap['profile'], snap['shocked'])
            if tool == 'wealth_subscribe' and product is None:
                suitable = [p for p in PRODUCTS if p['risk'] <= capacity]
                product = suitable[0] if suitable else None
                step['note'] = f"未指定产品，按你的风险承受上限 {RISK_NAMES[capacity]} 选择了「{product['name']}」。" if product else None
            if product is None:
                held = self._holdings(db, user_id)
                if tool == 'wealth_redeem' and len(held) == 1:
                    product = next(p for p in PRODUCTS if p['id'] == held[0]['productId'])
                else:
                    step.update(tool='clarify', title='需要你补充', status='needs_clarification', note='请说明要操作的理财产品。', options=[{'label': p['name'], 'message': f"{step.get('clause', '')}（{p['name']}）"} for p in PRODUCTS])
                    return step
            if tool == 'wealth_subscribe':
                amount = self._ceiling(db, user_id, deduction, saved) if args['amount'] == 'remaining' else cents(args['amount'], True)
                if amount <= 0:
                    step.update(status='blocked', title=f"申购「{product['name']}」", note='扣除前面步骤后，没有可安全投入的剩余金额。', amount=0)
                    return step
                checks = self._fund_checks(db, user_id, deduction, amount)
                checks.append({'name': '适当性', 'ok': product['risk'] <= capacity,
                               'detail': f"产品 {RISK_NAMES[product['risk']]}，你的承受上限 {RISK_NAMES[capacity]}（画像推断）。"})
                checks.append({'name': '起购金额', 'ok': amount >= cents(product['minAmount']), 'detail': f"起购 ¥{product['minAmount']:,}。"})
                if product['lockDays']:
                    checks.append({'name': '锁定期', 'ok': True, 'detail': f"锁定 {product['lockDays']} 天，期间不能赎回；已从可用现金中扣除。"})
                step.update(title=f"申购「{product['name']}」", amount=yuan(amount), product=product, checks=checks, impact=amount)
            else:
                held = next((h for h in self._holdings(db, user_id) if h['productId'] == product['id']), None)
                held_cents = cents(held['amount']) if held else 0
                amount = held_cents if args['amount'] in (None, 'remaining') else cents(args['amount'], True)
                locked = bool(held and held.get('lockedUntil') and held['lockedUntil'] > time.time())
                step.update(title=f"赎回「{product['name']}」", amount=yuan(amount), product=product, impact=-amount, checks=[
                    {'name': '持有份额', 'ok': 0 < amount <= held_cents, 'detail': f"当前持有 ¥{yuan(held_cents):,.2f}。"},
                    {'name': '锁定期', 'ok': not locked, 'detail': '锁定期内不能赎回。' if locked else '不在锁定期内。'}])
        elif tool in ('card_freeze', 'card_unfreeze', 'card_report_lost', 'card_limit'):
            card = next(c for c in cards if c['id'] == args['card'])
            step['card'] = card
            step['title'] = f"{title}：{card['type']} 尾号 {card['last4']}"
            if tool == 'card_freeze':
                step['checks'] = [{'name': '卡片状态', 'ok': card['status'] == 'active', 'detail': '冻结后刷卡与线上支付暂停，可随时解冻。' if card['status'] == 'active' else f"当前状态：{card['status']}。"}]
            elif tool == 'card_unfreeze':
                step['checks'] = [{'name': '卡片状态', 'ok': card['status'] == 'frozen', 'detail': '恢复刷卡与线上支付。' if card['status'] == 'frozen' else f"当前状态：{card['status']}，无需解冻。"}]
            elif tool == 'card_report_lost':
                step['checks'] = [{'name': '卡片状态', 'ok': card['status'] in ('active', 'frozen'), 'detail': '挂失后原卡永久停用，补卡另行申请。'},
                                  {'name': '确认口令', 'ok': True, 'detail': '执行前需输入这张卡的后四位。'}]
                step['phraseHint'] = '输入卡号后四位'
            else:
                target = cents(args['amount'], True)
                step.update(title=f"申请把信用卡尾号 {card['last4']} 额度调到 ¥{yuan(target):,.0f}", amount=yuan(target))
                step['checks'] = [{'name': '申请性质', 'ok': card['status'] == 'active', 'detail': '只提交申请，银行审核后生效，不承诺批准。'},
                                  {'name': '额度范围', 'ok': 100000 <= target <= 10000000, 'detail': '可申请范围 ¥1,000–¥100,000。'}]
        if step['checks'] and not all(c['ok'] for c in step['checks']):
            step['status'] = 'blocked'
        return step

    def _read(self, db, user_id, tool, snap, cards):
        if tool == 'card_status':
            return {'cards': cards}
        if tool == 'wealth_catalog':
            cap = risk_capacity(snap['profile'], snap['shocked'])
            return {'products': [dict(p, riskName=RISK_NAMES[p['risk']], suitable=p['risk'] <= cap) for p in PRODUCTS], 'capacity': RISK_NAMES[cap]}
        if tool == 'subscriptions_report':
            return {'subscriptions': [s for s in snap['subscriptions'] if s['status'] == 'active']}
        if tool == 'bills_report':
            bills = sorted(snap['bills'], key=lambda b: b['day'])
            return {'bills': bills, 'total': sum(-b['amount'] for b in bills if b['amount'] < 0),
                    'note': '已登记账单按到期日计入现金路径，到期自动扣款；本单不会另行转出。'}
        cash = snap['cash']
        return {'min': cash['min'], 'minDay': cash['minDay'], 'free': cash['free'], 'balance': snap['balance']}

    def plan(self, user_id, message, mode='rules'):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
            raise BankError(400, 'INVALID_MESSAGE', '请输入 1 至 2000 字的请求。')
        clauses = self.split(message)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            user, _ = self.bank._user(db, user_id)
            snap = self.bank._snapshot(db, user_id)
            cards = self._cards(db, user_id)
            steps, deduction, saved = [], 0, 0
            guarded = _safety_guard(message)
            if guarded:
                # 注入与引用历史的整句拦截：一个分句可疑，整单都不生成可执行步骤。
                clauses = []
                steps.append({'id': uid('step'), 'tool': 'guard', 'tier': 0, 'tierName': '拦截', 'title': '整单已拦截',
                              'status': 'refused', 'note': guarded['question'], 'checks': [], 'args': {}, 'clause': message})
                self.bank._audit(db, user_id, 'ARGOS', '多步请求被拦截', '消息含跳过确认、伪造指令或引用不存在的历史；未生成任何可执行步骤。')
            card_ref = None
            for clause in clauses[:8]:
                text = re.sub(r"\s+", "", clause)
                if '卡' in text:
                    kind, tail = re.search(r"信用卡|借记卡|储蓄卡|工资卡|银行卡", text), re.search(r"尾号\s*(\d{4})", text)
                    card_ref = (kind[0] if kind else '卡') + (f"尾号{tail[1]}" if tail else '')
                elif card_ref and re.search(r"挂失|冻结|解冻|锁住|锁定|停用", text):
                    # “借记卡丢了，帮我挂失”：省略宾语时沿用上一句提到的卡。
                    clause = card_ref + clause
                tool, args = self.classify(db, user_id, clause, snap['subscriptions'], cards)
                if any(st['tool'] == tool and st['args'] == args for st in steps if tool != 'clarify'):
                    continue
                if tool == 'cancel_subscription':
                    # 已在前面步骤中取消的订阅不重复处理。
                    done = {s for st in steps if st['tool'] == 'cancel_subscription' for s in st['args']['subs']}
                    args = {'subs': [s for s in args['subs'] if s not in done]}
                    if not args['subs']:
                        continue
                step = self._step(db, user_id, tool, args, deduction, snap, cards, saved, clause)
                if step['status'] == 'ready':
                    deduction += step.get('impact', 0)
                    if step['tool'] == 'wealth_subscribe' or (step['tool'] == 'transfer' and step['args']['to'] == 'goal'):
                        saved += step['impact']
                steps.append(step)
            if len(clauses) > 8:
                steps.append({'id': uid('step'), 'tool': 'clarify', 'tier': 0, 'tierName': '澄清', 'title': '需要你补充',
                              'status': 'needs_clarification', 'note': '一次最多处理 8 件事，其余请分开说。', 'checks': [], 'args': {}})
            mission = {'id': uid('mission'), 'message': message, 'mode': mode, 'revision': user['revision'],
                       'createdAt': now(), 'expiresAt': time.time() + 600, 'token': secrets.token_urlsafe(24),
                       'status': 'planned', 'steps': steps,
                       'summary': {'actionable': sum(s['status'] == 'ready' for s in steps),
                                   'blocked': sum(s['status'] == 'blocked' for s in steps),
                                   'clarify': sum(s['status'] == 'needs_clarification' for s in steps),
                                   'outflow': yuan(sum(max(0, s.get('impact', 0)) for s in steps if s['status'] == 'ready'))}}
            db.execute('INSERT INTO missions VALUES(?,?,?)', (mission['id'], user_id, json.dumps(mission, ensure_ascii=False)))
            self.bank._audit(db, user_id, 'METIS', '生成多步任务单',
                             f"{len(steps)} 步：" + '；'.join(s['title'] for s in steps) + '。尚未执行任何写操作。')
            return {'mission': mission, 'snapshot': self.bank._snapshot(db, user_id)}

    # ------------------------------------------------------------------ 执行
    def confirm(self, user_id, mission_id, payload):
        if not isinstance(payload, dict) or payload.get('confirmation') is not True:
            raise BankError(403, 'CONFIRMATION_REQUIRED', '需要你明确授权本任务单。')
        approve, phrases = payload.get('approve'), payload.get('phrases') or {}
        if not isinstance(approve, list) or not all(isinstance(x, str) for x in approve) or not isinstance(phrases, dict):
            raise BankError(400, 'INVALID_MISSION_APPROVAL', '请勾选要授权的步骤。')
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            row = db.execute('SELECT content FROM missions WHERE user_id=? AND id=?', (user_id, mission_id)).fetchone()
            if not row:
                raise BankError(404, 'MISSION_NOT_FOUND', '任务单不存在。')
            mission = json.loads(row[0])
            if payload.get('token') != mission['token']:
                raise BankError(403, 'INVALID_APPROVAL', '确认凭证与任务单不匹配。')
            if mission['status'] == 'executed':
                return {'mission': mission, 'snapshot': self.bank._snapshot(db, user_id)}
            user, _ = self.bank._user(db, user_id)
            if mission['revision'] != user['revision']:
                raise BankError(409, 'STALE_APPROVAL', '账户在规划之后发生了变化，请重新生成任务单。')
            if mission['expiresAt'] < time.time():
                raise BankError(409, 'APPROVAL_EXPIRED', '任务单已过期，请重新生成。')
            ready = {s['id'] for s in mission['steps'] if s['status'] == 'ready'}
            if not set(approve) <= ready:
                raise BankError(400, 'INVALID_MISSION_APPROVAL', '只能授权可执行的步骤。')
            before = self.bank._summary(db, user_id)
            stopped, changed = None, False
            for step in mission['steps']:
                if step['status'] != 'ready':
                    continue
                if step['id'] not in approve:
                    step['status'] = 'skipped'
                    continue
                if stopped:
                    step['status'] = 'paused'
                    step['note'] = f"第 {stopped} 步未完成，本步暂停，未执行。"
                    continue
                ok, result = self._execute(db, user_id, step, phrases.get(step['id']))
                if ok:
                    step.update(status='done', result=result)
                    changed = True
                else:
                    step.update(status='failed', note=result)
                    stopped = mission['steps'].index(step) + 1
            mission['status'] = 'executed'
            mission['executedAt'] = now()
            db.execute('UPDATE missions SET content=? WHERE id=?', (json.dumps(mission, ensure_ascii=False), mission_id))
            db.execute('INSERT INTO authorizations VALUES(?,?,?,?,?,?)', (uid('auth'), user_id, user['revision'], 'mission',
                       json.dumps({'missionId': mission_id, 'approved': approve}, ensure_ascii=False), now()))
            if changed:
                done = [s['title'] for s in mission['steps'] if s['status'] == 'done' and s['tier'] > 0]
                self.bank._changed(db, user_id, before, '多步任务单已执行', '用户逐步授权的任务单',
                                   '完成：' + '；'.join(done) + ('；' + f'第 {stopped} 步未通过，后续暂停' if stopped else '') + '。')
            return {'mission': mission, 'snapshot': self.bank._snapshot(db, user_id)}

    def _execute(self, db, user_id, step, phrase):
        tool, key = step['tool'], 'mission:' + step['id']
        if tool in ('transfer', 'wealth_subscribe'):
            amount = cents(step['amount'], True)
            checks = self._fund_checks(db, user_id, 0, amount, step['args'].get('when', 'now'))
            if tool == 'wealth_subscribe':
                cap = risk_capacity(self.bank._profile(db, user_id), self.bank._user(db, user_id)[1]['shocked'])
                checks.append({'name': '适当性', 'ok': step['product']['risk'] <= cap, 'detail': '执行时重新核对风险承受上限。'})
            failed = [c for c in checks if not c['ok']]
            if failed:
                step['checks'] = checks
                return False, '执行前重新核验未通过：' + '、'.join(c['name'] for c in failed) + '。未扣款。'
            if tool == 'transfer':
                dest = 'goal' if step['args']['to'] == 'goal' else 'external'
                tx = self.bank._post(db, user_id, key, '目标储蓄' if dest == 'goal' else '转账', amount, step['payee']['name'], dest,
                                     {'missionStep': step['id'], 'to': step['args']['to'], 'memo': step['args'].get('memo', '')})
            else:
                product = step['product']
                account = user_id + ':wealth_' + product['id']
                db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?,0)', (account, user_id, 'wealth_' + product['id']))
                locked = time.time() + product['lockDays'] * 86400 if product['lockDays'] else None
                tx = self.bank._post(db, user_id, key, '理财申购', amount, product['name'], 'wealth_' + product['id'],
                                     {'missionStep': step['id'], 'productId': product['id'], 'lockedUntil': locked})
            return True, {'transaction': tx}
        if tool == 'wealth_redeem':
            product, amount = step['product'], cents(step['amount'], True)
            row = db.execute('SELECT balance FROM accounts WHERE user_id=? AND kind=?', (user_id, 'wealth_' + product['id'])).fetchone()
            if not row or row[0] < amount:
                return False, '持有份额不足，未赎回。'
            tx_id = uid('tx')
            tx = {'id': tx_id, 'time': now(), 'merchant': product['name'], 'cat': '理财赎回', 'amount': yuan(amount),
                  'receipt': uid('receipt'), 'status': 'settled', 'key': key, 'missionStep': step['id'], 'productId': product['id']}
            db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?)', (tx_id, user_id, key, '理财赎回', -amount, json.dumps(tx, ensure_ascii=False)))
            for account, delta in [(user_id + ':main', amount), (user_id + ':wealth_' + product['id'], -amount)]:
                db.execute('UPDATE accounts SET balance=balance+? WHERE id=?', (delta, account))
                db.execute('INSERT INTO entries(transaction_id,account_id,amount) VALUES(?,?,?)', (tx_id, account, delta))
            return True, {'transaction': tx}
        if tool == 'cancel_subscription':
            _, st = self.bank._user(db, user_id)
            receipts = []
            for sub in st['subs']:
                if sub['id'] in step['args']['subs'] and sub['status'] == 'active':
                    sub.update(status='cancelled', receipt={'id': uid('sub_receipt'), 'status': 'cancelled', 'subscriptionId': sub['id'], 'at': now()})
                    receipts.append(sub['receipt'])
            self.bank._save_state(db, user_id, st)
            return True, {'receipts': receipts}
        cards = self._cards(db, user_id)
        card = next(c for c in cards if c['id'] == step['args']['card'])
        if tool == 'card_freeze':
            if card['status'] != 'active':
                return False, f"卡片当前状态为 {card['status']}，未冻结。"
            card['status'] = 'frozen'
        elif tool == 'card_unfreeze':
            if card['status'] != 'frozen':
                return False, '卡片未处于冻结状态。'
            card['status'] = 'active'
        elif tool == 'card_report_lost':
            if phrase != card['last4']:
                return False, '确认口令（卡号后四位）不正确，未挂失。'
            card['status'] = 'lost'
        elif tool == 'card_limit':
            card['pendingLimit'] = step['amount']
        self._save_card(db, user_id, card)
        receipt = {'id': uid('card_receipt'), 'card': card['last4'], 'status': card['status'], 'at': now()}
        if tool == 'card_limit':
            receipt['status'] = 'limit_application_submitted'
        self.bank._audit(db, user_id, 'ARGOS', step['title'], f"回执 {receipt['id']}；按你的授权执行。")
        return True, {'receipt': receipt, 'card': card}

    # ------------------------------------------------------------------ 对话入口
    def respond(self, user_id, message):
        """/api/agent 的任务单分支：与单一意图同样只起草，执行仍需逐步授权。"""
        with self.bank._db() as db:
            consent = self.bank._profile(db, user_id).get('readConsent') is True
        if not consent:
            return {'mode': 'rules', 'intent': {'action': 'clarify', 'question': '请先建立画像并确认读取模拟账本。'},
                    'snapshot': self.bank.snapshot(user_id), 'reply': '请先完成画像并主动确认读取模拟账本，再让我拆解并草拟多步任务。未执行任何操作。',
                    'trace': [{'engine': 'ARGOS', 'detail': '未取得读取授权，未拆解任务。'}]}
        result = self.plan(user_id, message)
        mission, summary = result['mission'], result['mission']['summary']
        if any(step['tool'] == 'guard' for step in mission['steps']):
            return {'mode': 'mission', 'intent': {'action': 'clarify', 'question': mission['steps'][0]['note']}, 'mission': mission,
                    'snapshot': result['snapshot'], 'reply': '【多步任务单】整单已拦截：' + mission['steps'][0]['note'] + '未执行任何操作。',
                    'trace': [{'engine': 'ARGOS', 'detail': '整句命中跳过确认、伪造指令或历史引用规则；未生成可执行步骤。'}]}
        parts = [f"拆成 {len(mission['steps'])} 步"]
        if summary['actionable']:
            parts.append(f"{summary['actionable']} 步待你授权")
        if summary['blocked']:
            parts.append(f"{summary['blocked']} 步被守诺拦下")
        if summary['clarify']:
            parts.append(f"{summary['clarify']} 步需要你补充")
        reply = '【多步任务单】' + '，'.join(parts) + ('。只读步骤已直接查询；其余步骤尚未执行，勾选授权后按顺序逐项重新核验。' if summary['actionable'] else '。只读步骤已直接查询，没有需要授权的写操作。')
        trace = [{'engine': 'METIS', 'detail': f"按连接词与标点拆成 {len(mission['steps'])} 个分句，每句映射到一个已注册工具；无法唯一理解的分句改为澄清，不猜测。"},
                 {'engine': 'ARGOS', 'detail': '整句先过注入与历史引用拦截；资金步骤按本单累计影响核验余额、底线与 30 天路径。'},
                 {'engine': 'KAIROS', 'detail': '执行时严格按顺序；任一步未通过，后续步骤暂停，已完成步骤保留回执。'}]
        return {'mode': 'mission', 'intent': {'action': 'mission'}, 'mission': mission,
                'snapshot': result['snapshot'], 'reply': reply, 'trace': trace}
