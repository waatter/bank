"""Persistent, user-owned dialogue tasks. Text may prepare/revoke, never execute.

The model still receives only a new user message. Explicit follow-ups are resolved
against typed server context; no historical prose is promoted into instructions.
"""
import copy
import hashlib
import json
import re
import threading

from .insights import build_insights
from .model import agent_response, deterministic_plan, rule_intent, _money, _TRANSFER_LANGUAGE
from .service import BankError, cents, now, uid


IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
UNSAFE = re.compile(r'(?:不用|无需|不必|跳过|绕过|自动|直接).{0,12}确认|(?:替|代|帮)(?:我|用户).{0,6}确认|忽略.{0,20}(?:规则|指令)|SYSTEM\s*[:：]|\b(?:execute|confirm)\b|(?:execute|confirm)[_-](?:subscription|transfer)', re.I)


class ConversationService:
    def __init__(self, bank, planner, subscriptions):
        self.bank, self.planner, self.subscriptions = bank, planner, subscriptions
        self._locks, self._lock_guard = {}, threading.Lock()
        with bank._db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,version INTEGER NOT NULL,content TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(user_id)')
            db.execute('CREATE TABLE IF NOT EXISTS conversation_requests(user_id TEXT,request_key TEXT,fingerprint TEXT,conversation_id TEXT,response TEXT,PRIMARY KEY(user_id,request_key))')

    def _lock(self, user_id, key):
        with self._lock_guard:
            return self._locks.setdefault((user_id, key), threading.RLock())

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise BankError(400, 'INVALID_CONVERSATION', '办理记录标识不正确，请重新读取。')
        return value

    def _load(self, db, user_id, conversation_id):
        row = db.execute('SELECT version,content FROM conversations WHERE user_id=? AND id=?', (user_id, conversation_id)).fetchone()
        if not row:
            raise BankError(404, 'CONVERSATION_NOT_FOUND', '没有找到当前账户的这段办理记录。')
        return row['version'], json.loads(row['content'])

    @staticmethod
    def _stored(result):
        result = copy.deepcopy({key: value for key, value in result.items() if key not in {'snapshot', 'conversation'}})
        # A history record never stores a reusable approval token or a stale card.
        for field in ('proposal', 'subscriptionProposal'):
            if result.get(field):
                result[field] = {'id': result[field]['id']}
        return result

    def _refresh(self, user_id, stored):
        result = copy.deepcopy(stored)
        snapshot = self.bank.snapshot(user_id)
        result['snapshot'] = snapshot
        for field, fetch in [('proposal', self.bank.proposal), ('subscriptionProposal', self.subscriptions.get)]:
            if result.get(field):
                try:
                    current = fetch(user_id, result[field]['id'])
                    result[field] = current['proposal']
                    result['snapshot'] = current['snapshot']
                    if field == 'subscriptionProposal' and current['proposal']['status'] == 'executed':
                        result['plan'] = deterministic_plan(current['snapshot'])
                except BankError as error:
                    if error.status != 404:
                        raise
                    result.pop(field, None)
                    result['taskState'] = {'status': 'unavailable', 'label': '原事项不可用', 'detail': '未恢复可执行操作，请重新说明事项。'}
        snapshot = result['snapshot']
        if snapshot['profile'].get('readConsent'):
            if result.get('insights') or result.get('subscriptionOptions') is not None or result.get('subscriptionFocus'):
                insights = build_insights(snapshot)
                result['insights'] = insights
                if 'subscriptionOptions' in result:
                    result['subscriptionOptions'] = insights['subscriptions']
                if result.get('subscriptionFocus'):
                    selected = result['subscriptionFocus']['id']
                    result['subscriptionFocus'] = next((s for s in insights['subscriptions'] if s['id'] == selected), None)
            if result.get('plan'):
                result['plan'] = deterministic_plan(snapshot)
        else:
            for field in ('plan', 'insights', 'subscriptionOptions', 'subscriptionFocus', 'billDetail'):
                result.pop(field, None)
        return result

    def get(self, user_id, conversation_id):
        conversation_id = self._identifier(conversation_id)
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            version, content = self._load(db, user_id, conversation_id)
        return {'conversation': {'id': conversation_id, 'version': version},
                'messages': content['messages'],
                'currentResult': self._refresh(user_id, content.get('result', self._response('请继续说明想办理的事项。')))}

    def turn(self, user_id, message, conversation_id=None, request_key=None, version=None):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
            raise BankError(400, 'INVALID_MESSAGE', '请输入 1 至 2000 字的请求。')
        if conversation_id is not None:
            self._identifier(conversation_id)
        request_key = self._identifier(request_key) if request_key is not None else uid('turn')
        if version is not None and (type(version) is not int or version < 0):
            raise BankError(400, 'INVALID_CONVERSATION_VERSION', '办理记录版本不正确。')
        fingerprint = hashlib.sha256(json.dumps([conversation_id, message], ensure_ascii=False).encode()).hexdigest()
        with self._lock(user_id, conversation_id or request_key):
            with self.bank._db() as db:
                self.bank._user(db, user_id)
                replay = db.execute('SELECT * FROM conversation_requests WHERE user_id=? AND request_key=?', (user_id, request_key)).fetchone()
                if replay:
                    if replay['fingerprint'] != fingerprint:
                        raise BankError(409, 'TURN_KEY_REUSED', '这个消息凭证已用于其他内容，请先核对原办理记录。')
                    current_version, _ = self._load(db, user_id, replay['conversation_id'])
                    stored = json.loads(replay['response'])
                else:
                    if conversation_id:
                        current_version, content = self._load(db, user_id, conversation_id)
                    else:
                        current_version, content = 0, {'context': {}, 'messages': []}
                    if version is not None and current_version != version:
                        raise BankError(409, 'CONVERSATION_CHANGED', '这段对话已有新的办理步骤，请先恢复记录再继续。', {'version': current_version})
            if replay:
                result = self._refresh(user_id, stored)
                result.update(conversation={'id': replay['conversation_id'], 'version': current_version}, replayed=True)
                return result
            context = content['context']
            # Any provider request happens before acquiring the atomic write lock.
            # Typed context handlers remain rules even when a model is configured.
            compact = re.sub(r'\s+', '', message)
            contextual = (UNSAFE.search(message) or re.search(r'订阅|会员|续费|取消|回执|改成|改为|更正|确认', message)
                          or (context.get('topic') == 'transfer' and not _TRANSFER_LANGUAGE.search(message)))
            if not contextual and self.bank.snapshot(user_id)['profile'].get('readConsent'):
                planned = self.planner.understand(message)
            else:
                planned = {'mode': 'rules', 'intent': rule_intent(message), 'trace': [
                    {'engine': 'METIS', 'detail': '由当前消息与明确的服务端办理对象进行规则处理。'}]}
            context['_turnGuard'] = {'id': conversation_id, 'version': current_version,
                                     'fingerprint': fingerprint, 'messages': content['messages'], 'rawMessage': message}
            context['_planned'] = planned
            return self._route(user_id, message.strip(), context, request_key)

    @staticmethod
    def _response(reply, action='clarify', status=None, label=None, **extra):
        result = {'mode': 'rules', 'intent': {'action': action}, 'reply': reply,
                  'trace': [{'engine': 'METIS', 'detail': '本次按服务端保存的明确办理对象处理上下文；未调用模型，不执行金融操作。'}]}
        if status:
            result['taskState'] = {'status': status, 'label': label or status, 'detail': reply}
        result.update(extra)
        return result

    def _payees(self, user_id):
        with self.bank._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT content FROM counterparties WHERE user_id=?', (user_id,))]

    @staticmethod
    def _names(payee):
        names = {payee['id'], payee['name'], payee['alias'], payee['name'] + '（' + payee['alias'] + '）'}
        if payee['name'] == '妈妈':
            names.update({'我妈', '我妈妈'})
        return names

    @staticmethod
    def _followup_amount(text, correction=False):
        """Consume a whole target clause; never search for the first number.

        Fee explanations and rejected/old amounts may accompany a target, but
        they are not targets. Multiple affirmative targets require clarification.
        The shared monetary parser remains the authority on precision and units.
        """
        targets = []
        clauses = re.split(r'[，。；;！？!?]|(?<!\d),|,(?!\d)', text)
        for clause in filter(None, clauses):
            target = re.fullmatch(r'(?:把(?:转账)?金额)?(?:改成|改为|更正为|最终按)(.+)', clause)
            if not target and correction:
                target = re.fullmatch(r'是(.+)', clause)
            if not target and not correction:
                target = re.fullmatch(r'(?:转账)?金额(?:就|是|为)?(.+)', clause)
            if target:
                targets.append(_money(target[1]))
                continue
            if re.fullmatch(r'(?:手续费|费用)(?:是|为)?[^改更最终]+', clause):
                continue
            if re.fullmatch(r'(?:不是|原来)(.+)', clause):
                _money(re.sub(r'^(?:不是|原来)', '', clause))
                continue
            if re.fullmatch(r'(?:其他|其它)(?:照刚说的|不变)|(?:收款人|备注|收款人和备注)(?:都)?(?:别改|不变)|刚(?:看|查).{0,12}(?:聊天|记录)', clause):
                continue
            if not correction:
                targets.append(_money(clause))
            else:
                raise ValueError('unscoped_correction')
        if len(targets) != 1:
            raise ValueError('ambiguous_amount')
        cents(targets[0], True)
        return targets[0]

    def _pending_payee(self, text, choices):
        """Resolve affirmative evidence only within the saved pending choices."""
        evidence, excluded = [], set()
        for clause in filter(None, re.split(r'[，,。；;！？!?]|而是', text)):
            negative = re.match(r'^(?:不是|别选|不要选|不选|也不是|排除)', clause)
            body = clause[negative.end():] if negative else clause
            # Relationship paraphrases describe registered aliases, not names
            # or an arbitrary new beneficiary supplied by a model.
            body = re.sub(r'(?:一起|一块)(?:上班|工作)', '同事', body)
            signals = []
            tail = re.search(r'(?:收款)?(?:卡)?尾号([0-9]{4})', body)
            if tail:
                signals.append({p['id'] for p in choices if p.get('acct', '').endswith(tail[1])})
                body = body[:tail.start()] + body[tail.end():]
            tokens = sorted({token for p in choices for token in (p['id'], p['name'], p['alias']) if token}, key=len, reverse=True)
            if tokens:
                pattern = '|'.join(re.escape(token) for token in tokens)
                for match in re.finditer(pattern, body):
                    signals.append({p['id'] for p in choices if match[0] in self._names(p)})
                body = re.sub(pattern, '', body)
            body = re.sub(r'收款人|选择|那个|这个|那位|这位|是|选|跟|和|我|的|吧', '', body)
            if body or not signals:
                return None
            candidates = set.intersection(*signals)
            if negative:
                excluded.update(candidates)
            else:
                evidence.append(candidates)
        if not evidence:
            return None
        selected = set.intersection(*evidence) - excluded
        return next((p for p in choices if p['id'] in selected), None) if len(selected) == 1 else None

    def _draft_transfer(self, user_id, payload, context, replace=False):
        with self.bank._db(True) as db:
            previous = None
            if replace:
                previous = self.bank._proposal(db, user_id, context['transferId'])
                existing = db.execute('SELECT 1 FROM transactions WHERE user_id=? AND idem_key=?', (user_id, previous['id'])).fetchone()
                if existing or previous['status'] == 'settled':
                    raise BankError(409, 'ALREADY_SETTLED', '原转账已经记账，不能改成新金额。请先查询原回执；未新建转账。')
                if previous['status'] == 'revoked':
                    raise BankError(409, 'REVOKED', '原草案已撤销，请重新完整说明新转账。')
            drafted = self.bank._propose_transfer(db, user_id, payload)
            if previous:
                previous['status'] = 'revoked'
                db.execute('UPDATE proposals SET content=? WHERE user_id=? AND id=?', (json.dumps(previous), user_id, previous['id']))
                self.bank._audit(db, user_id, 'ARGOS', '对话更正已使旧草案撤销', previous['id'] + ' → ' + drafted['proposal']['id'])
        context.update(transferId=drafted['proposal']['id'], activeKind='transfer', topic='transfer')
        context.pop('pendingTransfer', None)
        reply = ('金额已更正，旧草案已撤销；请核对新草案后单独确认。' if replace else '已准备转账草案，请核对后单独确认。') + '尚未扣款。'
        return self._response(reply, 'transfer', **drafted,
                              **({'supersededProposalId': previous['id']} if previous else {}))

    def _transfer_followup(self, user_id, text, context):
        if context.get('topic') != 'transfer':
            return None
        compact = re.sub(r'\s+', '', text).rstrip('。！!')
        correction = re.search(r'改成|改为|更正为|最终按|(?:^|[，,])是.+(?:不是|别改)', compact)
        if correction and context.get('activeKind') == 'transfer' and context.get('transferId'):
            try:
                amount = self._followup_amount(compact, correction=True)
            except (ValueError, BankError, ArithmeticError):
                return self._response('请给出大于零且精确到分的新金额，例如“改成 200 元”。原草案未修改，未扣款。')
            with self.bank._db() as db:
                old = self.bank._proposal(db, user_id, context['transferId'])
            payload = {key: old[key] for key in ('to', 'when', 'memo')}
            payload['amount'] = str(amount)
            return self._draft_transfer(user_id, payload, context, replace=True)
        pending = context.get('pendingTransfer')
        if pending:
            # A new transfer sentence must pass the original complete-request
            # safety gates instead of being interpreted as a relationship answer.
            if _TRANSFER_LANGUAGE.search(text):
                return None
            if pending.get('amount') is None:
                try:
                    amount = self._followup_amount(compact)
                except (ValueError, BankError, ArithmeticError):
                    return None
                return self._draft_transfer(user_id, dict(pending, amount=str(amount)), context)
            choices = [p for p in self._payees(user_id) if p['id'] in context.get('payeeChoices', [])]
            memo = re.search(r'(?:备注|附言)(?:为|是)?[:：]?(.+)$', compact)
            choice_text = compact[:memo.start()].rstrip('，,') if memo else compact
            chosen = self._pending_payee(choice_text, choices)
            if chosen:
                payload = {key: pending[key] for key in ('amount', 'when', 'memo')}
                payload['to'] = chosen['id']
                if memo:
                    payload['memo'] = memo[1]
                return self._draft_transfer(user_id, payload, context)
        return None

    def _route(self, user_id, text, context, request_key):
        guard, planned = context.pop('_turnGuard'), context.pop('_planned')
        with self.bank._db(True) as db:
            # Recheck after the database write lock, before creating any resource.
            # This works across service instances and process restarts.
            replay = db.execute('SELECT * FROM conversation_requests WHERE user_id=? AND request_key=?',
                                (user_id, request_key)).fetchone()
            if replay:
                if replay['fingerprint'] != guard['fingerprint']:
                    raise BankError(409, 'TURN_KEY_REUSED', '该消息凭证已用于其他内容。')
                version, _ = self._load(db, user_id, replay['conversation_id'])
                result = self._refresh(user_id, json.loads(replay['response']))
                result.update(conversation={'id': replay['conversation_id'], 'version': version}, replayed=True)
                return result
            if guard['id']:
                version, _ = self._load(db, user_id, guard['id'])
                if version != guard['version']:
                    raise BankError(409, 'CONVERSATION_CHANGED', '这段对话已有新的办理步骤，请先恢复记录再继续。', {'version': version})
            before = copy.deepcopy(context)
            db.execute('SAVEPOINT conversation_action')
            try:
                result = self._dispatch(user_id, text, context, request_key, planned)
            except BankError as error:
                db.execute('ROLLBACK TO conversation_action')
                context.clear()
                context.update(before)
                if error.status >= 500:
                    raise
                result = self._response(error.message, status='needs_review', label='请核对后继续')
                result['errorCode'] = error.code
            db.execute('RELEASE conversation_action')
            result.setdefault('snapshot', self.bank.snapshot(user_id))
            stored = self._stored(result)
            messages = guard['messages'] + [{'role': 'user', 'content': guard['rawMessage'], 'at': now()},
                                            {'role': 'assistant', 'content': result['reply'], 'at': now()}]
            content = {'context': context, 'messages': messages[-80:], 'result': stored}
            conversation_id = guard['id'] or uid('conversation')
            version = guard['version'] + 1
            db.execute('INSERT INTO conversations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,content=excluded.content',
                       (conversation_id, user_id, version, json.dumps(content, ensure_ascii=False)))
            db.execute('INSERT INTO conversation_requests VALUES(?,?,?,?,?)',
                       (user_id, request_key, guard['fingerprint'], conversation_id, json.dumps(stored, ensure_ascii=False)))
            result['conversation'] = {'id': conversation_id, 'version': version}
            return result

    def _dispatch(self, user_id, text, context, request_key, planned):
        class ResolvedPlanner:
            @staticmethod
            def understand(_message):
                return copy.deepcopy(planned)
        planner = ResolvedPlanner()
        snapshot = self.bank.snapshot(user_id)
        if snapshot['profile'].get('readConsent') is not True:
            return self._response('请先建立画像并主动同意读取模拟账本，再开始办理。')
        compact = re.sub(r'\s+', '', text).rstrip('。！!')
        if UNSAFE.search(text):
            return self._response('每个变更操作都需要你单独核对并确认。请说明具体事项，当前没有执行或代确认。')
        # Follow-ups are deliberately narrow and typed. General prose cannot
        # synthesize an executable action from the earlier conversation.
        followup = self._transfer_followup(user_id, text, context)
        if followup:
            return followup
        if re.fullmatch(r'(?:取消|撤销|撤回)(?:刚才的?|这笔|上一笔)?(?:转账|转账草案|草案|方案)', compact):
            if not context.get('transferId'):
                return self._response('这段对话没有明确的转账草案，请提供原方案或到转账页核对。')
            result = self.bank.revoke(user_id, context['transferId'])
            context['activeKind'] = None
            return self._response('原转账草案已撤销，未执行扣款。', 'transfer', **result)
        if re.search(r'回执|到账了吗|执行了吗|成功了吗|结果如何|办理结果', compact):
            kind = 'subscription' if re.search(r'订阅|续费|取消', compact) else context.get('activeKind')
            if kind == 'subscription' and context.get('subscriptionId'):
                result = self.subscriptions.get(user_id, context['subscriptionId'])
                proposal = result['proposal']
                return self._response('已查到原取消回执。今天余额不增加，下面按最新账本重新安排。' if proposal['status'] == 'executed' else '这项取消尚未完成，请核对当前状态；没有重复发起取消。',
                                      'subscriptions', subscriptionProposal=proposal, snapshot=result['snapshot'],
                                      **({'plan': deterministic_plan(result['snapshot'])} if proposal['status'] == 'executed' else {}))
            if context.get('transferId'):
                result = self.bank.proposal(user_id, context['transferId'])
                settled = result['proposal']['status'] == 'executed'
                return self._response('原转账已记账，以下是原回执；没有新建或重复扣款。' if settled else '原转账还未记账，请核对当前草案状态；没有重新转账。', 'transfer', **result)
            return self._response('这段对话没有可唯一定位的办理凭证，请提供原方案或明确要查哪项。')
        if re.fullmatch(r'(?:好|好的|可以|确认|确认取消|确认转账|就这样|执行|继续办理|帮我确认)', compact):
            if context.get('activeKind') == 'subscription' and context.get('subscriptionId'):
                result = self.subscriptions.get(user_id, context['subscriptionId'])
                return self._response('请核对下面这一项并点击单独确认；这条消息本身没有取消续费。', 'subscriptions', subscriptionProposal=result['proposal'], snapshot=result['snapshot'])
            if context.get('activeKind') == 'transfer' and context.get('transferId'):
                result = self.bank.proposal(user_id, context['transferId'])
                return self._response('请在下面核对收款人和金额，再点击单独确认；没有因这条消息执行转账。', 'transfer', **result)
            return self._response('请明确要继续哪一项。当前没有唯一的待确认操作。')
        if re.fullmatch(r'(?:算了|先不办了|保留它|保留订阅|不取消了|先不取消|撤销取消方案)', compact):
            if context.get('activeKind') == 'subscription' and context.get('subscriptionId'):
                result = self.subscriptions.revoke(user_id, context['subscriptionId'])
                context['activeKind'] = None
                return self._response('取消方案已撤销，订阅仍保留；没有变更已完成的历史回执。', 'subscriptions', subscriptionProposal=result['proposal'], snapshot=result['snapshot'])
            return self._response('没有执行新的变更。如要撤销草案，请明确是哪一项。')
        # Transfer language wins over subscription words inside notes or mixed
        # commands. Such words can never trigger a cancellation side effect.
        if _TRANSFER_LANGUAGE.search(text):
            context.pop('pendingTransfer', None)
            context.pop('payeeChoices', None)
            result = agent_response(self.bank, user_id, text, planner)
            context.update(topic='transfer', activeKind=None)
            if result.get('proposal'):
                context.update(transferId=result['proposal']['id'], activeKind='transfer')
                context.pop('pendingTransfer', None)
                return result
            original = rule_intent(text)
            if original['action'] == 'transfer':
                matches = [p for p in self._payees(user_id) if original['to'] in self._names(p)]
                if len(matches) > 1:
                    context['pendingTransfer'] = {key: original[key] for key in ('amount', 'when', 'memo')}
                    context['payeeChoices'] = [p['id'] for p in matches]
                    result['transferOptions'] = matches
                    result['reply'] = '有同名收款人，请选择明确关系；金额已记为 ¥' + f"{original['amount']:,.2f}" + '，未生成可执行草案。'
                    return result
            memo = re.search(r'(?:备注|附言)(?:为|是)?[:：]?(.+)$', compact)
            request = compact[:memo.start()].rstrip('，,') if memo else compact
            partial = re.fullmatch(r'(?:请|帮我|麻烦|现在|今天|我想|想|先)*(?:给|向)(.{1,30}?)(?:转账|转)(?:一下|点钱|些钱|吧)?', request)
            if partial:
                matches = [p for p in self._payees(user_id) if partial[1] in self._names(p)]
                if len(matches) == 1:
                    context['pendingTransfer'] = {'to': matches[0]['id'], 'amount': None, 'when': 'now', 'memo': memo[1] if memo else ''}
                    result['reply'] = '要给' + matches[0]['name'] + '转多少元？请填写准确金额，之后仍要单独确认。'
            return result
        # A complete read-only plan may mention bills or earlier calculations.
        # Its intent still comes through the original planner safety precheck.
        if planned['intent']['action'] == 'plan' or (
                planned['intent']['action'] == 'bills'
                and re.search(r'计划|安排|重新排|重算|再算|预算', compact)):
            # Both outcomes are read-only and have passed the original safety
            # precheck. An explicit plan takes priority over its bill inputs.
            planned = dict(planned, intent={'action': 'plan'})
            result = agent_response(self.bank, user_id, text, planner)
            context.update(topic='plan', activeKind=None)
            context.pop('pendingTransfer', None)
            context.pop('payeeChoices', None)
            return result
        insights = build_insights(snapshot)
        subscriptions = insights['subscriptions']
        sub_text = re.search(r'订阅|会员|续费|代扣|取消', text)
        cancel_request = re.search(r'取消|停止|停掉|关闭|关掉|终止|停了|停一下', compact)
        mentions_subscription = sub_text or any(
            sub['name'] in text or re.search(r'(?<![A-Za-z0-9_-])' + re.escape(sub['id']) + r'(?![A-Za-z0-9_-])', text)
            for sub in subscriptions)
        if mentions_subscription and cancel_request:
            # Reported speech and denials are data, never the user's own draft
            # instruction. Keep this before resolving IDs or quoted names.
            denied = re.search(r'(?:不是|并非|(?:不|没|未)(?:有)?(?:说过|说|表示|同意|要求|授权|让你|打算|计划|需要|想|考虑|要){0,3}|别|不要|不能|不可|不用|不必|无需|无须)(?:再|替我|帮我|给我|去)?(?:取消|停(?:止|掉|了|一下)?|关闭|关掉|终止)|保留', compact)
            reported = re.search(r'客服|聊天记录|短信|邮件|原文|引用|转述|诱导|诈骗|辨别|〈|<|tool\s*=', compact, re.I)
            if denied or reported:
                context.update(topic='subscriptions', activeKind=None)
                context.pop('pendingTransfer', None)
                context.pop('payeeChoices', None)
                return self._response('没有准备或执行取消。引用内容不能作为办理指令；请明确说明你本人要查询或办理的事项。')
        quoted = re.findall(r'[「“"]([^」”"]+)[」”"]', text)
        if quoted and sub_text:
            selected = [sub for sub in subscriptions if sub['id'] in quoted or sub['name'] in quoted]
            if not selected:
                context.update(topic='subscriptions', activeKind=None)
                context.pop('selectedSubscription', None)
                return self._response('没有找到该编号或名称的订阅，请从当前账户列表明确选择。未准备或执行取消。',
                                      'subscriptions', subscriptionOptions=subscriptions)
        else:
            selected = [sub for sub in subscriptions if sub['name'] in text or re.search(
                r'(?<![A-Za-z0-9_-])' + re.escape(sub['id']) + r'(?![A-Za-z0-9_-])', text)]
        if not selected and context.get('topic') == 'subscriptions':
            choices = [s for s in subscriptions if s['id'] in context.get('subscriptionChoices', [])]
            short = re.sub(r'^(?:选|选择|是)', '', compact).strip('「」“”')
            selected = [s for s in choices if short == s['name']]
            if not selected and re.search(r'它|这项|这个|那项|那个', compact) and context.get('selectedSubscription'):
                selected = [s for s in subscriptions if s['id'] == context['selectedSubscription']]
        if sub_text or selected:
            if re.search(r'别取消|不要取消|不取消|不能取消|不想取消|不用取消|无需取消|不必取消|保留', compact):
                return self._response('没有取消订阅。需要保留或撤销某个待确认方案时，请明确该项。', 'subscriptions')
            if cancel_request and re.search(r'全部|所有|都(?:取消|停|关|终止)|全(?:取消|停|关)|一起(?:取消|停|关)|一并(?:取消|停|关)|其他订阅|其它订阅', compact):
                context.update(topic='subscriptions', activeKind=None, subscriptionChoices=[s['id'] for s in subscriptions])
                context.pop('selectedSubscription', None)
                return self._response('批量请求需要逐项核对，请先明确一项订阅。当前未准备任何取消方案。',
                                      'subscriptions', subscriptionOptions=subscriptions)
            if len(selected) != 1:
                context.update(topic='subscriptions', activeKind=None, subscriptionChoices=[s['id'] for s in subscriptions])
                context.pop('selectedSubscription', None)
                return self._response('请先选择一项订阅比较影响，再决定是否准备取消；当前没有批量取消。' if cancel_request else f'当前登记 {len(subscriptions)} 项订阅，可逐项查看续费与取消影响。',
                                      'clarify' if cancel_request else 'subscriptions', subscriptionOptions=subscriptions, insights=insights)
            sub = selected[0]
            context.update(topic='subscriptions', selectedSubscription=sub['id'], subscriptionChoices=[sub['id']])
            compare = re.search(r'如果|假如|假设|比较|影响|看看|会怎样|能省|多少钱|要不要', compact)
            if sub['status'] == 'cancelled':
                context['activeKind'] = None
                return self._response('这项订阅已有取消回执，不再重复取消。今天余额不会增加。', 'subscriptions', subscriptionFocus=sub, insights=insights)
            if cancel_request and not compare:
                drafted = self.subscriptions.propose(user_id, sub['id'], request_key=request_key)
                context.update(subscriptionId=drafted['proposal']['id'], activeKind='subscription')
                return self._response('已准备「' + sub['name'] + '」的取消方案。先核对少支出与剩余缺口，再点击单独确认；尚未取消。',
                                      'subscriptions', subscriptionProposal=drafted['proposal'], snapshot=drafted['snapshot'])
            context['activeKind'] = None
            return self._response(f'单独取消「{sub["name"]}」可在本预测窗口少支出 ¥{sub["impact"]["avoidedOutflow"]:,.2f}，最低余额改善 ¥{sub["impact"]["minImprovement"]:,.2f}，仍有缺口 ¥{sub["impact"]["remainingGap"]:,.2f}。现在只比较，没有取消或退款。',
                                  'subscriptions', subscriptionFocus=sub, insights=insights)
        matching = [bill for bill in snapshot['bills'] if bill['label'] in text or (
            len(stem := re.sub(r'(?:账单|尾款|分期)$', '', bill['label'])) >= 2
            and stem in text and re.search(r'查|哪天|多少|到期|账单|尾款|还款', compact))]
        if matching or re.search(r'账单|还款|房租|水电|支出|流水|最大|最高|哪笔', compact):
            context.update(topic='bills', activeKind=None)
            context.pop('pendingTransfer', None)
            context.pop('payeeChoices', None)
            if not matching and re.search(r'还款', compact):
                matching = [bill for bill in snapshot['bills'] if bill['kind'] in {'还款','分期','贷款'}]
            if not matching and re.search(r'水电', compact):
                matching = [bill for bill in snapshot['bills'] if '水电' in bill['label']]
            if matching:
                context['billIds'] = [bill['id'] for bill in matching]
                return self._response('以下是已登记账单的金额和到期日；尚未支付。它们已纳入 30 天现金路径。', 'bills', insights=insights,
                                      billDetail={'title': '核对这几笔待付账单', 'items': matching,
                                                  'summary': f'当前最低预计余额 ¥{snapshot["cash"]["min"]:,.2f}，现金底线缺口 ¥{insights["liquidity"]["gap"]:,.2f}。'})
        result = agent_response(self.bank, user_id, text, planner)
        context['topic'] = result['intent']['action']
        context['activeKind'] = None
        return result
