"""Authoritative synthetic bank ledger. Money is integer cents inside SQLite.

Fixtures are opening account snapshots, not transactions to replay. Historical fixture
transactions are display-only; every operation made through this service has balanced
ledger entries. Sessions are local demo sessions, not production identity verification.
"""
import contextlib
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


class BankError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = details or {}


def cents(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise BankError(400, 'INVALID_AMOUNT', '请填写有效金额。')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
            raise InvalidOperation
        n = int(amount * 100)
        if abs(n) > 10_000_000_000 or (positive and n <= 0):
            raise InvalidOperation
        return n
    except (ValueError, InvalidOperation, OverflowError):
        raise BankError(400, 'INVALID_AMOUNT', '金额须为有效数字，最多两位小数，且在演示限额内。')


def yuan(value):
    return value / 100


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex


class BankService:
    def __init__(self, db_path, fixtures_path=None):
        self.db_path = str(db_path)
        self.fixtures = json.loads(Path(fixtures_path or Path(__file__).with_name('fixtures.json')).read_text(encoding='utf-8'))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, persona TEXT UNIQUE, name TEXT, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles(user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, profile TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS authorizations(id TEXT PRIMARY KEY,user_id TEXT,version INTEGER,kind TEXT,content TEXT,at TEXT);
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,user_id TEXT,kind TEXT,balance INTEGER NOT NULL,UNIQUE(user_id,kind));
                CREATE TABLE IF NOT EXISTS counterparties(user_id TEXT,id TEXT,content TEXT,PRIMARY KEY(user_id,id));
                CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,user_id TEXT,idem_key TEXT,kind TEXT,amount INTEGER,content TEXT,UNIQUE(user_id,idem_key));
                CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY,transaction_id TEXT,account_id TEXT,amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency(user_id TEXT,key TEXT,operation TEXT,resource TEXT,fingerprint TEXT,PRIMARY KEY(user_id,key));
                CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,user_id TEXT,content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(user_id TEXT,id TEXT,content TEXT,PRIMARY KEY(user_id,id));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,user_id TEXT,event_key TEXT,type TEXT,content TEXT,UNIQUE(user_id,event_key));
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id TEXT,content TEXT);
                CREATE TABLE IF NOT EXISTS profile_updates(id INTEGER PRIMARY KEY,user_id TEXT,content TEXT);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS shopping_searches(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shopping_checkouts(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shopping_orders(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_key TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(user_id,request_key));
                CREATE TABLE IF NOT EXISTS shopping_provider_bindings(provider TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shopping_official_previews(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shopping_official_orders(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_key TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(user_id,request_key));
            ''')

    @contextlib.contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _user(self, db, user_id):
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            raise BankError(404, 'USER_NOT_FOUND', '模拟账户不存在。')
        return dict(row), json.loads(row['state'])

    def _profile(self, db, user_id):
        row = db.execute('SELECT profile FROM profiles WHERE user_id=?', (user_id,)).fetchone()
        if not row:
            raise BankError(404, 'USER_NOT_FOUND', '模拟账户不存在。')
        return json.loads(row[0])

    def _consent(self, db, user_id):
        if self._profile(db, user_id).get('readConsent') is not True:
            raise BankError(403, 'CONSENT_REQUIRED', '请先主动确认读取模拟账本并建立画像。')

    def _confirm(self, payload):
        if not isinstance(payload, dict) or payload.get('confirmation') is not True:
            raise BankError(403, 'CONFIRMATION_REQUIRED', '需要你明确确认本次操作。')

    def _seed(self, db, persona):
        p = self.fixtures['personas'][persona]
        user_id = 'demo_' + persona
        state = {'shocked': False, 'replan': False, 'salaryReceived': False, 'extraBills': [],
                 'subs': [dict(s, status='active') for s in p['subs']]}
        profile = {'goalType': p['tone'], 'goalName': p['goal']['name'], 'target': p['goal']['target'],
                   'reserve': p['reserve'], 'incomeStability': 'uncertain' if persona == 'A' else 'stable',
                   'style': persona, 'automation': 'confirm', 'readConsent': False}
        db.execute('INSERT INTO users VALUES(?,?,?,0,?)', (user_id, persona, p['name'], json.dumps(state)))
        db.execute('INSERT INTO profiles VALUES(?,0,?)', (user_id, json.dumps(profile)))
        for kind, balance in [('main', cents(p['balance'])), ('goal', cents(p['goal']['saved'])), ('external', 0), ('income', 0)]:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (user_id + ':' + kind, user_id, kind, balance))
        for payee in self.fixtures['payees']:
            db.execute('INSERT INTO counterparties VALUES(?,?,?)', (user_id, payee['id'], json.dumps(payee)))
        for plan in p['plans']:
            pl = dict(plan, status='waiting_condition' if plan.get('dep') else 'pending_confirmation')
            db.execute('INSERT INTO plans VALUES(?,?,?)', (user_id, plan['id'], json.dumps(pl)))
        self._audit(db, user_id, 'SYSTEM', '模拟账本已建立', '演示账户初始化；尚未取得读取画像和执行授权。')
        return user_id

    def session(self, persona):
        if persona not in self.fixtures['personas']:
            raise BankError(400, 'INVALID_PERSONA', '请选择有效的演示账户。')
        with self._db(True) as db:
            row = db.execute('SELECT id FROM users WHERE persona=?', (persona,)).fetchone()
            user_id = row[0] if row else self._seed(db, persona)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 86400))
            return {'token': token, 'snapshot': self._snapshot(db, user_id)}

    def authenticate(self, token):
        if not isinstance(token, str):
            raise BankError(401, 'UNAUTHORIZED', '请先进入演示账户。')
        with self._db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise BankError(401, 'UNAUTHORIZED', '会话已失效，请重新进入演示账户。')
            return row[0]

    def _balance(self, db, user_id, kind='main'):
        return db.execute('SELECT balance FROM accounts WHERE user_id=? AND kind=?', (user_id, kind)).fetchone()[0]

    def _cash(self, db, user_id, deduction=0):
        user, st = self._user(db, user_id)
        p, profile = self.fixtures['personas'][user['persona']], self._profile(db, user_id)
        flows = list(p['bills']) + st['extraBills']
        flows += [{'day': s['next'], 'amount': -s['fee'], 'label': s['name'] + '续费', 'type': 'subscription'} for s in st['subs'] if s['status'] == 'active']
        salary_day = p['salaryDay'] + (15 if st['shocked'] else 0)
        if not st['salaryReceived']:
            flows += [{'day': salary_day, 'amount': p['income'], 'label': '预计工资（尚未到账）', 'type': 'salary'}]
        # Unconfirmed plans are suggestions, never obligations or executed payments.
        daily = cents(p['daily'])  # Proposed budget reductions never become facts without consent.
        bal = self._balance(db, user_id) - deduction
        points = [bal]
        for day in range(1, 31):
            bal -= daily
            bal += sum(cents(x['amount']) for x in flows if x['day'] == day)
            points.append(bal)
        reserve = cents(profile['reserve'])
        minimum = min(points)
        breaches = [i for i, n in enumerate(points) if n < reserve]
        return {'pts': [yuan(v) for v in points], 'min': yuan(minimum), 'minDay': points.index(minimum),
                'free': yuan(minimum - reserve), 'breach': breaches[0] if breaches else -1,
                'breachDays': len(breaches), 'salaryDay': salary_day, 'daily': yuan(daily), 'events': flows}

    def _policy(self, db, user_id, cash):
        user, st = self._user(db, user_id)
        p, f = self.fixtures['personas'][user['persona']], self._profile(db, user_id)
        labels = {'protect':'现金流守护', 'recover':'负债减压', 'accumulate':'财富积累', 'grow':'资产增长'}
        scores = dict(protect=20, recover=5, accumulate=15, grow=5)
        reasons = {key: [] for key in scores}
        def add(key, value, reason):
            scores[key] += value
            reasons[key].append(reason)
        add(f['goalType'], 40, '你确认的当前目标')
        if f['incomeStability'] != 'stable':
            add('protect', 25 if f['incomeStability'] == 'uncertain' else 15, '你声明的收入存在波动')
        if cash['free'] < 0:
            add('protect', 80, '30 天预测最低余额低于你设定的预留')
        elif cash['free'] < f['reserve'] * .2:
            add('protect', 30, '安全线以上的余量较小')
        else:
            add('accumulate', 15, '必要支付和现金预留后仍有余量')
        debt = sum(-x['amount'] for x in p['bills'] + st['extraBills'] if x['kind'] in ['还款','分期','贷款'])
        if p['income'] > 0 and debt / p['income'] >= .6:
            add('recover', 40, '未来 30 天还款占预计月收入较高')
        if st['shocked']:
            add('protect', 50, '收入延期，相关任务需要暂停复核')
        rows = sorted([{'key': k, 'name': labels[k], 'value': int(v / sum(scores.values()) * 100), 'reasons': reasons[k] or ['保留为兼顾目标']} for k, v in scores.items()], key=lambda x: -x['value'])
        rows[0]['value'] += 100 - sum(x['value'] for x in rows)
        return {'version': 'ETHOS-R1', 'priorities': rows}

    def _snapshot(self, db, user_id):
        user, st = self._user(db, user_id)
        p = self.fixtures['personas'][user['persona']]
        cash = self._cash(db, user_id)
        tx = [json.loads(row[0]) for row in db.execute('SELECT content FROM transactions WHERE user_id=? ORDER BY rowid DESC', (user_id,))]
        history = [dict(t, id='fixture_' + str(i), status='historical', receipt=None) for i, t in enumerate(p['tx'])]
        return {'user': {k: user[k] for k in ['id','persona','name','revision']}, 'profile': self._profile(db, user_id),
                'balance': yuan(self._balance(db, user_id)), 'goalFunded': yuan(self._balance(db, user_id, 'goal')),
                'shocked': st['shocked'], 'replan': st['replan'], 'salaryReceived': st['salaryReceived'],
                'bills': p['bills'] + st['extraBills'], 'plans': [json.loads(r[0]) for r in db.execute('SELECT content FROM plans WHERE user_id=?', (user_id,))],
                'subscriptions': st['subs'], 'transactions': tx + history, 'cash': cash,
                'policy': self._policy(db, user_id, cash),
                'audit': [json.loads(r[0]) for r in db.execute('SELECT content FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 200', (user_id,))],
                'profileUpdates': [json.loads(r[0]) for r in db.execute('SELECT content FROM profile_updates WHERE user_id=? ORDER BY id DESC LIMIT 100', (user_id,))]}

    def snapshot(self, user_id):
        with self._db() as db:
            return self._snapshot(db, user_id)

    def insights(self, user_id):
        """Read consent and the full insight basis in one consistent transaction."""
        from .insights import build_insights
        with self._db() as db:
            self._consent(db, user_id)
            snapshot = self._snapshot(db, user_id)
            result = build_insights(snapshot)
            result['snapshot'] = snapshot
            return result

    def _audit(self, db, user_id, engine, title, detail):
        db.execute('INSERT INTO audit(user_id,content) VALUES(?,?)', (user_id, json.dumps({'engine': engine, 'title': title, 'detail': detail, 'at': now()}, ensure_ascii=False)))

    def _summary(self, db, user_id):
        cash = self._cash(db, user_id)
        mode = self._policy(db, user_id, cash)['priorities'][0]['name']
        return f"{mode}；30 天最低 ¥{cash['min']:,.2f}；余量 ¥{cash['free']:,.2f}"

    def _changed(self, db, user_id, before, title, source, reason):
        db.execute('UPDATE users SET revision=revision+1 WHERE id=?', (user_id,))
        db.execute('INSERT INTO profile_updates(user_id,content) VALUES(?,?)', (user_id, json.dumps({'title': title, 'source': source, 'at': now(), 'before': before, 'after': self._summary(db, user_id), 'reason': reason}, ensure_ascii=False)))
        self._audit(db, user_id, 'ETHOS', title, reason)

    def _save_state(self, db, user_id, state):
        db.execute('UPDATE users SET state=? WHERE id=?', (json.dumps(state, ensure_ascii=False), user_id))

    def save_profile(self, user_id, profile):
        if not isinstance(profile, dict):
            raise BankError(400, 'INVALID_PROFILE', '画像格式无效。')
        if profile.get('readConsent') is not True:
            raise BankError(403, 'CONSENT_REQUIRED', '请主动同意读取当前模拟账本。')
        if profile.get('goalType') not in ['protect','recover','accumulate','grow'] or profile.get('incomeStability') not in ['stable','mixed','uncertain'] or profile.get('style') not in ['A','B','C'] or profile.get('automation') not in ['advice','confirm']:
            raise BankError(400, 'INVALID_PROFILE', '请确认目标、收入和服务偏好。')
        if not isinstance(profile.get('goalName'), str) or not 1 <= len(profile['goalName'].strip()) <= 50:
            raise BankError(400, 'INVALID_PROFILE', '目标名称需要 1–50 个字。')
        target, reserve = cents(profile.get('target'), True), cents(profile.get('reserve'))
        if reserve < 0:
            raise BankError(400, 'INVALID_PROFILE', '现金预留不能为负数。')
        clean = {k: profile[k] for k in ['goalType','goalName','incomeStability','style','automation','readConsent']}
        clean.update(goalName=clean['goalName'].strip(), target=yuan(target), reserve=yuan(reserve))
        with self._db(True) as db:
            self._user(db, user_id)
            before = self._summary(db, user_id)
            db.execute('UPDATE profiles SET version=version+1,profile=? WHERE user_id=?', (json.dumps(clean), user_id))
            version = db.execute('SELECT version FROM profiles WHERE user_id=?', (user_id,)).fetchone()[0]
            db.execute('INSERT INTO authorizations VALUES(?,?,?,?,?,?)', (uid('auth'), user_id, version, 'read_profile', json.dumps(clean), now()))
            self._changed(db, user_id, before, '用户确认画像', '用户主动选择', '长期目标与现金底线由你确认；旧转账方案须重新核验。')
            return self._snapshot(db, user_id)

    def _checks(self, db, user_id, amount, when):
        f = self._profile(db, user_id)
        bal = self._balance(db, user_id)
        cash = self._cash(db, user_id, amount)
        _, st = self._user(db, user_id)
        return [
            {'name': '单笔限额', 'ok': amount <= 5_000_000, 'detail': '单笔最高 ¥50,000。'},
            {'name': '已到账余额', 'ok': bal >= amount, 'detail': '预计工资不作为当前余额。'},
            {'name': '当前现金底线', 'ok': bal - amount >= cents(f['reserve']), 'detail': '转出后仍须保留你设定的现金。'},
            {'name': '30 天现金路径', 'ok': cash['free'] >= 0, 'detail': f"执行后预计最低 ¥{cash['min']:,.2f}，已计必要账单和订阅。"},
            {'name': '工资到账条件', 'ok': when != 'salary' or st['salaryReceived'], 'detail': '发薪后计划仅在到账后恢复待确认。'}]

    def propose_transfer(self, user_id, payload):
        with self._db(True) as db:
            return self._propose_transfer(db, user_id, payload)

    def _propose_transfer(self, db, user_id, payload, life_version=None):
        """Create a draft inside the caller transaction; never authorise or pay."""
        if not isinstance(payload, dict):
            raise BankError(400, 'INVALID_TRANSFER', '转账方案格式无效。')
        amount = cents(payload.get('amount'), True)
        when, to = payload.get('when', 'now'), payload.get('to')
        if when not in ['now','salary'] or not isinstance(to, str):
            raise BankError(400, 'INVALID_TRANSFER', '请选择收款账户和执行条件。')
        memo = payload.get('memo', '')
        if not isinstance(memo, str) or len(memo) > 200:
            raise BankError(400, 'INVALID_TRANSFER', '备注最多 200 字。')
        self._consent(db, user_id)
        user, _ = self._user(db, user_id)
        payee = db.execute('SELECT content FROM counterparties WHERE user_id=? AND id=?', (user_id, to)).fetchone()
        if not payee:
            raise BankError(400, 'UNKNOWN_PAYEE', '请选择明确的已登记收款账户；同名联系人须消歧。')
        checks = self._checks(db, user_id, amount, when)
        status = 'pending_confirmation' if all(c['ok'] for c in checks) else ('waiting_condition' if when == 'salary' and not checks[-1]['ok'] else 'blocked')
        proposal = {'id': uid('proposal'), 'to': to, 'payee': json.loads(payee[0]), 'amount': yuan(amount), 'when': when,
                    'memo': memo, 'revision': user['revision'], 'status': status, 'expiresAt': time.time() + 600,
                    'confirmationToken': secrets.token_urlsafe(24)}
        if life_version is not None:
            proposal['lifeVersion'] = life_version
        db.execute('INSERT INTO proposals VALUES(?,?,?)', (proposal['id'], user_id, json.dumps(proposal)))
        self._audit(db, user_id, 'ARGOS', '转账方案待你确认' if status == 'pending_confirmation' else '转账方案暂缓', f"收款账户 {to}；金额 ¥{yuan(amount):,.2f}；未执行扣款。")
        return {'proposal': proposal, 'checks': checks, 'snapshot': self._snapshot(db, user_id)}


    def _proposal(self, db, user_id, proposal_id):
        row = db.execute('SELECT content FROM proposals WHERE user_id=? AND id=?', (user_id, proposal_id)).fetchone()
        if not row:
            raise BankError(404, 'PROPOSAL_NOT_FOUND', '转账方案不存在。')
        return json.loads(row[0])

    @staticmethod
    def _life_approval_current(db, user_id, proposal):
        if 'lifeVersion' not in proposal:
            return True
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='life_state'").fetchone():
            return False
        state = db.execute('SELECT version FROM life_state WHERE user_id=?', (user_id,)).fetchone()
        version = state[0] if state else 0
        if version != proposal['lifeVersion']:
            return False
        return not any(json.loads(row[0]).get('status') == 'draft' for row in
                       db.execute('SELECT content FROM life_scenes WHERE user_id=?', (user_id,)))

    def proposals(self, user_id, limit=50):
        """Recover this user's recent proposals without changing approvals or the ledger.

        Status is a read-time view of the same revision, expiry and cash gates used
        by confirm(). Stored approval tokens are returned only while actionable.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise BankError(400, 'INVALID_LIMIT', '方案读取数量须为 1 至 50。')
        with self._db() as db:
            user, _ = self._user(db, user_id)
            consent = self._profile(db, user_id).get('readConsent') is True
            at = time.time()
            rows = db.execute('SELECT content FROM proposals WHERE user_id=? ORDER BY rowid DESC LIMIT ?',
                              (user_id, limit)).fetchall()
            recovered = []
            for row in rows:
                proposal = json.loads(row[0])
                token = proposal.pop('confirmationToken', None)
                proposal.update(canConfirm=False, invalidReason=None, checks=[])
                transaction = db.execute('SELECT content FROM transactions WHERE user_id=? AND idem_key=?',
                                         (user_id, proposal['id'])).fetchone()
                # A committed receipt remains authoritative even after expiry or
                # later account changes. Recovery never turns it into a new action.
                if transaction:
                    tx = json.loads(transaction[0])
                    proposal.update(status='executed', transactionId=tx['id'], transaction=tx)
                elif proposal['status'] == 'revoked':
                    proposal['invalidReason'] = 'REVOKED'
                elif not consent:
                    proposal.update(status='blocked', invalidReason='CONSENT_REQUIRED')
                elif proposal['revision'] != user['revision']:
                    proposal.update(status='stale', invalidReason='STALE_APPROVAL')
                elif not self._life_approval_current(db, user_id, proposal):
                    proposal.update(status='stale', invalidReason='STALE_LIFE_PLAN')
                elif proposal['expiresAt'] < at:
                    proposal.update(status='expired', invalidReason='APPROVAL_EXPIRED')
                elif proposal['status'] == 'settled':
                    proposal.update(status='blocked', invalidReason='TRANSACTION_NOT_FOUND')
                else:
                    checks = self._checks(db, user_id, cents(proposal['amount'], True), proposal['when'])
                    proposal['checks'] = checks
                    if all(check['ok'] for check in checks) and token:
                        proposal.update(status='pending_confirmation', canConfirm=True, confirmationToken=token)
                    else:
                        status = 'waiting_condition' if proposal['when'] == 'salary' and not checks[-1]['ok'] else 'blocked'
                        proposal.update(status=status, invalidReason='TRANSFER_BLOCKED' if token else 'INVALID_APPROVAL')
                recovered.append(proposal)
            return {'proposals': recovered, 'snapshot': self._snapshot(db, user_id)}

    def _post(self, db, user_id, key, kind, amount, merchant, destination, extra=None):
        tx_id = uid('tx')
        tx = {'id': tx_id, 'time': now(), 'merchant': merchant, 'cat': kind, 'amount': yuan(-amount),
              'receipt': uid('receipt'), 'status': 'settled', 'key': key}
        if extra:
            tx.update(extra)
        db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?)', (tx_id, user_id, key, kind, amount, json.dumps(tx, ensure_ascii=False)))
        for account, delta in [(user_id + ':main', -amount), (user_id + ':' + destination, amount)]:
            db.execute('UPDATE accounts SET balance=balance+? WHERE id=?', (delta, account))
            db.execute('INSERT INTO entries(transaction_id,account_id,amount) VALUES(?,?,?)', (tx_id, account, delta))
        return tx

    def confirm(self, user_id, proposal_id, payload):
        self._confirm(payload)
        with self._db(True) as db:
            self._consent(db, user_id)
            proposal = self._proposal(db, user_id, proposal_id)
            if payload.get('confirmationToken') != proposal['confirmationToken']:
                raise BankError(403, 'INVALID_APPROVAL', '确认凭证与本次方案不匹配。')
            # Proposal identity is canonical idempotency identity, even with changed caller keys.
            existing = db.execute('SELECT content FROM transactions WHERE user_id=? AND idem_key=?', (user_id, proposal_id)).fetchone()
            if existing:
                return {'transaction': json.loads(existing[0]), 'snapshot': self._snapshot(db, user_id)}
            key = payload.get('key', proposal_id)
            if not isinstance(key, str) or not 1 <= len(key) <= 200:
                raise BankError(400, 'INVALID_KEY', '请提供有效请求凭证。')
            used = db.execute('SELECT resource FROM idempotency WHERE user_id=? AND key=?', (user_id, key)).fetchone()
            if used and used[0] != proposal_id:
                raise BankError(409, 'KEY_REUSED', '此请求凭证已用于另一笔操作。')
            user, _ = self._user(db, user_id)
            if proposal['status'] == 'revoked':
                raise BankError(409, 'REVOKED', '此方案已撤销。')
            if proposal['revision'] != user['revision']:
                raise BankError(409, 'STALE_APPROVAL', '账户或画像已变化，请重新生成方案并确认。')
            if not self._life_approval_current(db, user_id, proposal):
                raise BankError(409, 'STALE_LIFE_PLAN', '生活安排已变化，请重新比较后生成目标草案。')
            if proposal['expiresAt'] < time.time():
                raise BankError(409, 'APPROVAL_EXPIRED', '方案已过期，请重新核验。')
            checks = self._checks(db, user_id, cents(proposal['amount'], True), proposal['when'])
            if not all(c['ok'] for c in checks):
                raise BankError(409, 'TRANSFER_BLOCKED', '当前条件未满足，未执行扣款。', {'checks': checks})
            before = self._summary(db, user_id)
            tx = self._post(db, user_id, proposal_id, '目标储蓄' if proposal['to'] == 'goal' else '转账', cents(proposal['amount']), proposal['payee']['name'], 'goal' if proposal['to'] == 'goal' else 'external', {'proposalId': proposal_id, 'to': proposal['to'], 'memo': proposal['memo']})
            db.execute('INSERT OR IGNORE INTO idempotency VALUES(?,?,?,?,?)', (user_id, key, 'transfer', proposal_id, proposal_id))
            proposal['status'] = 'settled'
            proposal['transactionId'] = tx['id']
            db.execute('UPDATE proposals SET content=? WHERE id=?', (json.dumps(proposal), proposal_id))
            db.execute('INSERT INTO authorizations VALUES(?,?,?,?,?,?)', (uid('auth'), user_id, user['revision'], 'transfer', json.dumps({'proposalId': proposal_id, 'content': proposal}), now()))
            self._changed(db, user_id, before, '已确认的转账完成', '银行沙箱回执', '按本次明确授权执行，已重新核验最新余额、现金底线和账单路径。')
            return {'transaction': tx, 'snapshot': self._snapshot(db, user_id)}

    def revoke(self, user_id, proposal_id):
        with self._db(True) as db:
            p = self._proposal(db, user_id, proposal_id)
            if p['status'] == 'settled':
                raise BankError(409, 'ALREADY_SETTLED', '已到账交易不能撤销为未执行状态。')
            p['status'] = 'revoked'
            db.execute('UPDATE proposals SET content=? WHERE id=?', (json.dumps(p), proposal_id))
            self._audit(db, user_id, 'USER', '转账方案已撤销', proposal_id)
            return {'proposal': p, 'snapshot': self._snapshot(db, user_id)}

    def transaction(self, user_id, key):
        with self._db() as db:
            self._user(db, user_id)
            row = db.execute('SELECT content FROM transactions WHERE user_id=? AND (id=? OR idem_key=?)', (user_id, key, key)).fetchone()
            if not row:
                idem = db.execute('SELECT resource FROM idempotency WHERE user_id=? AND key=? AND operation=?', (user_id, key, 'transfer')).fetchone()
                if idem:
                    row = db.execute('SELECT content FROM transactions WHERE user_id=? AND idem_key=?', (user_id, idem[0])).fetchone()
            if not row:
                raise BankError(404, 'TRANSACTION_NOT_FOUND', '没有找到该请求的交易回执，请勿盲目重复支付。')
            return {'transaction': json.loads(row[0]), 'snapshot': self._snapshot(db, user_id)}

    def cancel_subscription(self, user_id, sub_id, payload):
        self._confirm(payload)
        with self._db(True) as db:
            self._consent(db, user_id)
            user, st = self._user(db, user_id)
            sub = next((s for s in st['subs'] if s['id'] == sub_id), None)
            if not sub:
                raise BankError(404, 'SUBSCRIPTION_NOT_FOUND', '订阅不存在。')
            if sub['status'] == 'cancelled':
                return {'receipt': sub['receipt'], 'snapshot': self._snapshot(db, user_id)}
            before = self._summary(db, user_id)
            sub.update(status='cancelled', receipt={'id': uid('sub_receipt'), 'status': 'cancelled', 'subscriptionId': sub_id, 'at': now()})
            self._save_state(db, user_id, st)
            self._changed(db, user_id, before, '订阅已取消', '银行沙箱回执', sub['name'] + '未来续费已移出预测，当前余额未增加。')
            db.execute('INSERT INTO authorizations VALUES(?,?,?,?,?,?)', (uid('auth'), user_id, user['revision'], 'cancel_subscription', json.dumps({'subscriptionId': sub_id, 'confirmation': True}), now()))
            return {'receipt': sub['receipt'], 'snapshot': self._snapshot(db, user_id)}

    def event(self, user_id, payload):
        if not isinstance(payload, dict) or payload.get('type') not in ['income_delayed','bill_added','salary_received']:
            raise BankError(400, 'INVALID_EVENT', '请选择支持的演示事件。')
        kind, key = payload['type'], payload.get('event_key')
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise BankError(400, 'INVALID_KEY', '演示事件需要唯一请求凭证。')
        with self._db(True) as db:
            self._consent(db, user_id)
            previous = db.execute('SELECT type,content FROM events WHERE user_id=? AND event_key=?', (user_id, key)).fetchone()
            if previous:
                if previous['type'] != kind:
                    raise BankError(409, 'KEY_REUSED', '事件凭证已用于其他事件。')
                return {'event': json.loads(previous['content']), 'snapshot': self._snapshot(db, user_id)}
            user, st = self._user(db, user_id)
            p = self.fixtures['personas'][user['persona']]
            before = self._summary(db, user_id)
            ev = {'id': uid('event'), 'type': kind, 'event_key': key, 'at': now(), 'source': 'synthetic_fixture'}
            if kind == 'salary_received' and st['salaryReceived']:
                prior = db.execute("SELECT content FROM events WHERE user_id=? AND type='salary_received' ORDER BY rowid LIMIT 1", (user_id,)).fetchone()
                return {'event': json.loads(prior[0]), 'snapshot': self._snapshot(db, user_id)}
            if kind == 'income_delayed':
                if st['salaryReceived']:
                    raise BankError(409, 'INCOME_ALREADY_RECEIVED', '本期工资已到账，不能再次标记延期。')
                st.update(shocked=True, replan=False)
                title, reason = '预计工资延期', '预计到账日延后 15 天；工资依赖计划继续等待；降低日常预算仅为建议，预测仍用原支出。'
            elif kind == 'salary_received':
                self._post(db, user_id, 'salary:current-cycle', '收入', -cents(p['income']), '本期工资', 'income')
                st.update(salaryReceived=True, shocked=False, replan=False)
                for row in db.execute('SELECT id,content FROM plans WHERE user_id=?', (user_id,)).fetchall():
                    plan = json.loads(row['content'])
                    if plan.get('dep'):
                        plan['status'] = 'pending_confirmation'
                        db.execute('UPDATE plans SET content=? WHERE user_id=? AND id=?', (json.dumps(plan), user_id, row['id']))
                title, reason = '本期工资已到账', '仅记入一次真实沙箱账款，移除本笔预计收入；相关计划恢复待确认，未自动扣款。'
            else:
                st['extraBills'].append({'id': ev['id'], 'day': 3, 'label': '模拟维修费', 'amount': -1200, 'kind': '生活缴费', 'icon': 'receipt'})
                title, reason = '新增维修账单', '合成事件增加一笔 3 天后 ¥1,200 的账单，当前余额未扣减。'
            self._save_state(db, user_id, st)
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)', (ev['id'], user_id, key, kind, json.dumps(ev)))
            self._changed(db, user_id, before, title, '模拟账本事件', reason)
            return {'event': ev, 'snapshot': self._snapshot(db, user_id)}

    def reset(self, user_id):
        """Only call behind explicit HTTP confirmation. Reset this synthetic user only."""
        with self._db(True) as db:
            user, _ = self._user(db, user_id)
            # Keep bearer sessions so the same browser can inspect its reset account.
            ids = [r[0] for r in db.execute('SELECT id FROM transactions WHERE user_id=?', (user_id,))]
            for tx in ids:
                db.execute('DELETE FROM entries WHERE transaction_id=?', (tx,))
            # Optional v2 planning tables share this reset transaction.
            for table in ('life_scenes', 'life_state', 'life_plan_receipts', 'shopping_searches', 'shopping_checkouts', 'shopping_orders', 'cards', 'missions', 'household'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    db.execute('DELETE FROM ' + table + ' WHERE user_id=?', (user_id,))
            for table in ['profiles','authorizations','accounts','counterparties','transactions','idempotency','proposals','plans','events','audit','profile_updates']:
                db.execute('DELETE FROM ' + table + ' WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
            self._seed(db, user['persona'])
            self._audit(db, user_id, 'USER', '当前模拟账户已重置', '仅重置本人合成演示账本。')
            return self._snapshot(db, user_id)
