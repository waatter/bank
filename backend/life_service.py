"""User-scoped hypothetical plans and explicitly committed future obligations."""
import hashlib
import json
import re
from pathlib import Path

from .life_planner import analyze as analyze_plan, validate_scene, validate_settings
from .service import BankError, cents, now, uid, yuan


DEFAULT_SETTINGS = {
    'illiquidAssets': 0, 'delayDays': 15, 'expenseShock': 1200,
    'shockDay': 3, 'goalAmount': 500,
}


class LifeService:
    def __init__(self, bank_service):
        self.bank = bank_service
        with self.bank._db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS life_state(user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, settings TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS life_scenes(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS life_scenes_user ON life_scenes(user_id)')
            db.execute('CREATE TABLE IF NOT EXISTS life_plan_receipts(user_id TEXT NOT NULL, request_key TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(user_id,request_key))')

    def _state(self, db, user_id):
        row = db.execute('SELECT version,settings FROM life_state WHERE user_id=?', (user_id,)).fetchone()
        if row:
            return row['version'], json.loads(row['settings'])
        return 0, dict(DEFAULT_SETTINGS)

    def _scenes(self, db, user_id):
        return [json.loads(row['content']) for row in db.execute('SELECT content FROM life_scenes WHERE user_id=? ORDER BY rowid', (user_id,))]

    def _catalogue(self):
        path = Path(__file__).with_name('life_scenarios.json')
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

    def _get(self, db, user_id):
        version, settings = self._state(db, user_id)
        return {'version': version, 'settings': settings, 'scenes': self._scenes(db, user_id),
                'catalogue': self._catalogue(), 'snapshot': self.bank._snapshot(db, user_id)}

    def get(self, user_id):
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            return self._get(db, user_id)

    @staticmethod
    def _payload(payload):
        if not isinstance(payload, dict):
            raise BankError(400, 'INVALID_LIFE_PAYLOAD', '生活规划请求格式无效。')
        return payload

    @staticmethod
    def _version(payload, current):
        version = payload.get('version')
        if type(version) is not int or version != current:
            raise BankError(409, 'LIFE_VERSION_CONFLICT', '生活规划已更新，请刷新后重试。', {'version': current})

    def _scene(self, db, user_id, scene_id):
        row = db.execute('SELECT content FROM life_scenes WHERE user_id=? AND id=?', (user_id, scene_id)).fetchone()
        if not row:
            raise BankError(404, 'LIFE_SCENE_NOT_FOUND', '生活场景不存在。')
        return json.loads(row['content'])

    @staticmethod
    def _save_scene(db, user_id, scene):
        db.execute('UPDATE life_scenes SET content=? WHERE user_id=? AND id=?',
                   (json.dumps(scene, ensure_ascii=False), user_id, scene['id']))

    def _advance(self, db, user_id, version, settings, title, detail):
        db.execute('INSERT INTO life_state(user_id,version,settings) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,settings=excluded.settings',
                   (user_id, version + 1, json.dumps(settings, ensure_ascii=False)))
        self.bank._audit(db, user_id, 'TROPOS', title, detail)

    @staticmethod
    def _validate(validator, value, code):
        try:
            return validator(value)
        except ValueError as exc:
            raise BankError(400, code, str(exc)) from exc

    def save_settings(self, user_id, payload):
        payload = self._payload(payload)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            version, _ = self._state(db, user_id)
            self._version(payload, version)
            settings = self._validate(validate_settings, payload.get('settings'), 'INVALID_LIFE_SETTINGS')
            self._advance(db, user_id, version, settings, '生活规划假设已更新', '你修改了生活规划的压力测试假设；尚未承诺任何支出。')
            return self._get(db, user_id)

    def add_scene(self, user_id, payload):
        payload = self._payload(payload)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            version, settings = self._state(db, user_id)
            self._version(payload, version)
            scene = self._validate(validate_scene, payload.get('scene'), 'INVALID_LIFE_SCENE')
            if sum(scene['status'] != 'cancelled' for scene in self._scenes(db, user_id)) >= 10:
                raise BankError(409, 'LIFE_SCENE_LIMIT', '最多保留 10 个未取消的生活场景。')
            scene.update(id=uid('scene'), status='draft', createdAt=now())
            db.execute('INSERT INTO life_scenes(id,user_id,content) VALUES(?,?,?)',
                       (scene['id'], user_id, json.dumps(scene, ensure_ascii=False)))
            self._advance(db, user_id, version, settings, '生活场景已加入假设', f"{scene['title']}；尚未承诺支出。")
            return self._get(db, user_id)

    def cancel_scene(self, user_id, scene_id, payload):
        payload = self._payload(payload)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            self.bank._confirm(payload)
            scene = self._scene(db, user_id, scene_id)
            if scene['status'] == 'committed':
                raise BankError(409, 'LIFE_SCENE_COMMITTED', '已承诺场景属于真实未来义务，需要另行变更账单凭证。')
            if scene['status'] == 'cancelled':
                return self._get(db, user_id)
            version, settings = self._state(db, user_id)
            self._version(payload, version)
            scene.update(status='cancelled', cancelledAt=now())
            self._save_scene(db, user_id, scene)
            self._advance(db, user_id, version, settings, '生活场景假设已取消', scene['title'])
            return self._get(db, user_id)

    def commit_scene(self, user_id, scene_id, payload):
        payload = self._payload(payload)
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            self.bank._confirm(payload)
            scene = self._scene(db, user_id, scene_id)
            # A network retry returns the already saved outcome even with old versions.
            if scene['status'] == 'committed':
                return self._get(db, user_id)
            if scene['status'] == 'cancelled':
                raise BankError(409, 'LIFE_SCENE_CANCELLED', '已取消的场景不能承诺。')
            version, settings = self._state(db, user_id)
            self._version(payload, version)
            user, account_state = self.bank._user(db, user_id)
            revision = payload.get('accountRevision')
            if type(revision) is not int or revision != user['revision']:
                raise BankError(409, 'STALE_APPROVAL', '账户事实已更新，请重新查看现金路径并确认。', {'accountRevision': user['revision']})
            day = scene['preferredDay']
            if type(day) is not int or not 1 <= day <= 30:
                raise BankError(400, 'INVALID_LIFE_SCENE', '承诺日期须为未来第 1–30 天。')
            before = self.bank._summary(db, user_id)
            bill_id = 'life_' + scene['id']
            account_state['extraBills'].append({'id': bill_id, 'day': day, 'label': scene['title'],
                                                'amount': -scene['amount'], 'kind': '生活场景',
                                                'icon': 'receipt', 'source': 'life_scene'})
            self.bank._save_state(db, user_id, account_state)
            scene.update(status='committed', billId=bill_id, committedAt=now(),
                         committedAmount=scene['amount'], committedDay=day, committedMode='original')
            self._save_scene(db, user_id, scene)
            self.bank._changed(db, user_id, before, '生活场景已承诺', '用户主动确认生活场景',
                               f"{scene['title']}已计入第 {day} 天未来义务；当前余额未扣款，旧转账方案须重新核验。")
            self._advance(db, user_id, version, settings, '生活场景已成为未来义务', f"{scene['title']}；账单凭证 {bill_id}。")
            return self._get(db, user_id)

    def commit_plan(self, user_id, payload):
        payload = self._payload(payload)
        required = {'version', 'accountRevision', 'policyId', 'confirmation', 'requestKey'}
        if set(payload) != required or payload.get('policyId') not in ('original', 'buffer', 'balanced'):
            raise BankError(400, 'INVALID_LIFE_PLAN', '请选择明确的生活比较方案，并提供当前版本。')
        request_key = payload.get('requestKey')
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{7,127}', request_key):
            raise BankError(400, 'INVALID_LIFE_PLAN', '请提供有效的方案请求标识。')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            self.bank._confirm(payload)
            saved = db.execute('SELECT content FROM life_plan_receipts WHERE user_id=? AND request_key=?', (user_id, request_key)).fetchone()
            if saved:
                record = json.loads(saved['content'])
                if record['fingerprint'] != fingerprint:
                    raise BankError(409, 'KEY_REUSED', '此请求标识已用于不同方案，请刷新后重新确认。')
                return {**self._get(db, user_id), 'receipt': record['receipt']}
            version, settings = self._state(db, user_id)
            self._version(payload, version)
            snapshot = self.bank._snapshot(db, user_id)
            revision = payload.get('accountRevision')
            if type(revision) is not int or revision != snapshot['user']['revision']:
                raise BankError(409, 'STALE_APPROVAL', '账户事实已更新，请重新比较并确认生活方案。')
            scenes = self._scenes(db, user_id)
            drafts = {scene['id']: scene for scene in scenes if scene['status'] == 'draft'}
            if not drafts:
                raise BankError(409, 'LIFE_PLAN_EMPTY', '没有待采用的生活场景，请先添加场景。')
            analysis = analyze_plan(snapshot, scenes, settings)
            allocations = next(policy['allocations'] for policy in analysis['policies'] if policy['id'] == payload['policyId'])
            if len(allocations) != len(drafts) or {item['sceneId'] for item in allocations} != set(drafts):
                raise BankError(409, 'INVALID_LIFE_PLAN', '比较方案与当前生活场景不一致，请重新比较。')
            # All amounts and dates below come from the current server-side planner.
            receipt_id, at = uid('lifeplan'), now()
            user, account_state = self.bank._user(db, user_id)
            before = self.bank._summary(db, user_id)
            for allocation in allocations:
                scene = drafts[allocation['sceneId']]
                amount, day, mode = allocation['amount'], allocation['day'], allocation['mode']
                if type(day) is not int or not scene['earliestDay'] <= day <= scene['latestDay']:
                    raise BankError(409, 'INVALID_LIFE_PLAN', '方案支付日超出你允许的范围。')
                amount_cents = cents(amount)
                if amount_cents < 0:
                    raise BankError(409, 'INVALID_LIFE_PLAN', '方案金额无效。')
                if amount_cents == 0:
                    if scene['allowSkip'] is not True or scene['importance'] == 'essential' or mode != 'skipped':
                        raise BankError(409, 'INVALID_LIFE_PLAN', '你未允许放弃这项支出。')
                    scene.update(status='cancelled', cancelledAt=at, cancelledReason='用户采用生活比较方案并选择本次不支出',
                                 planReceiptId=receipt_id, committedAmount=0, committedDay=day, committedMode=mode)
                else:
                    expected = scene['alternativeAmount'] if mode == 'alternative' else scene['amount']
                    if mode not in ('original', 'delayed', 'alternative') or (mode == 'alternative' and not scene['allowAlternative']) or cents(expected) != amount_cents:
                        raise BankError(409, 'INVALID_LIFE_PLAN', '方案超出你允许的金额或替代安排。')
                    bill_id = 'life_' + scene['id']
                    account_state['extraBills'].append({'id': bill_id, 'day': day, 'label': scene['title'],
                                                        'amount': -yuan(amount_cents), 'kind': '生活场景',
                                                        'icon': 'receipt', 'source': 'life_scene', 'planReceiptId': receipt_id})
                    scene.update(status='committed', billId=bill_id, committedAt=at, planReceiptId=receipt_id,
                                 committedAmount=yuan(amount_cents), committedDay=day, committedMode=mode)
                self._save_scene(db, user_id, scene)
            self.bank._save_state(db, user_id, account_state)
            self.bank._changed(db, user_id, before, '生活比较方案已采用', '用户主动确认比较方案',
                               '已按所选方案的金额和日期登记未来义务；允许放弃的场景已取消，当前余额未扣款，旧转账方案须重新核验。')
            self._advance(db, user_id, version, settings, '生活比较方案已采用', f"方案 {payload['policyId']}；凭证 {receipt_id}。")
            receipt = {'id': receipt_id, 'requestKey': request_key, 'policyId': payload['policyId'],
                       'accountRevisionBefore': user['revision'], 'lifeVersionBefore': version,
                       'allocations': allocations, 'at': at}
            db.execute('INSERT INTO life_plan_receipts(user_id,request_key,content) VALUES(?,?,?)',
                       (user_id, request_key, json.dumps({'fingerprint': fingerprint, 'receipt': receipt}, ensure_ascii=False)))
            return {**self._get(db, user_id), 'receipt': receipt}

    def propose_goal(self, user_id, payload):
        """Create a goal proposal from the same current bank/life transaction.

        Contract: exactly {version, accountRevision, amount}; amount is a finite
        positive JSON number in yuan with cent precision. This only produces the
        ordinary bank confirmation proposal and never transfers money.
        """
        payload = self._payload(payload)
        if set(payload) != {'version', 'accountRevision', 'amount'}:
            raise BankError(400, 'INVALID_LIFE_GOAL', '目标草案需要当前生活版本、账户版本与金额。')
        with self.bank._db(True) as db:
            self.bank._consent(db, user_id)
            version, settings = self._state(db, user_id)
            self._version(payload, version)
            snapshot = self.bank._snapshot(db, user_id)
            revision = payload.get('accountRevision')
            if type(revision) is not int or revision != snapshot['user']['revision']:
                raise BankError(409, 'STALE_APPROVAL', '账户事实已更新，请重新比较目标储蓄金额。')
            scenes = self._scenes(db, user_id)
            if any(scene['status'] == 'draft' for scene in scenes):
                raise BankError(409, 'LIFE_DRAFTS_PENDING', '请先采用或取消所有待比较生活场景，再生成目标转账草案。')
            amount = payload['amount']
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise BankError(400, 'INVALID_AMOUNT', '目标转账金额须为有效数字。')
            amount_cents = cents(amount, positive=True)
            analysis = analyze_plan(snapshot, scenes, settings)
            ceiling = next(policy['metrics']['goalAmount'] for policy in analysis['policies'] if policy['id'] == 'balanced')
            if amount_cents > cents(ceiling):
                raise BankError(409, 'LIFE_GOAL_LIMIT', '目标草案金额超出当前生活压力情景允许的范围，请重新比较。', {'maximum': ceiling})
            return self.bank._propose_transfer(db, user_id, {'to': 'goal', 'amount': yuan(amount_cents),
                                                            'when': 'now', 'memo': '生活规划2.0 · 目标储蓄'},
                                               life_version=version)

    def analyze(self, user_id):
        with self.bank._db() as db:
            self.bank._consent(db, user_id)
            version, settings = self._state(db, user_id)
            snapshot = self.bank._snapshot(db, user_id)
            result = analyze_plan(snapshot, self._scenes(db, user_id), settings)
            return {**result, 'lifeVersion': version, 'accountRevision': snapshot['user']['revision']}
