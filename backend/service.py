"""Authoritative synthetic bank ledger. Money is integer cents inside SQLite.

Fixtures are opening account snapshots, not transactions to replay. Historical fixture
transactions are display-only; every operation made through this service has balanced
ledger entries. Sessions are local demo sessions, not production identity verification.
"""
import contextlib
import contextvars
import datetime as dt
from decimal import Decimal, InvalidOperation
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import struct
import time
import urllib.parse
import uuid

from .mailer import AuthMailer, masked_email, normalize_email
from .profiling import assess_profile, validate_assessment


_ACTIVE_DATABASES = contextvars.ContextVar('tropos_active_databases', default={})


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
    def __init__(self, db_path, fixtures_path=None, mailer=None):
        self.db_path = str(db_path)
        self.fixtures = json.loads(Path(fixtures_path or Path(__file__).with_name('fixtures.json')).read_text(encoding='utf-8'))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.auth_master_key = self._load_auth_master_key()
        self.mailer = mailer or AuthMailer(Path(self.db_path).parent)
        with self._db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, persona TEXT NOT NULL, name TEXT, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, account_type TEXT NOT NULL DEFAULT 'demo');
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
                CREATE TABLE IF NOT EXISTS shopping_provider_bindings(provider TEXT NOT NULL,user_id TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(provider,user_id));
                CREATE TABLE IF NOT EXISTS shopping_official_previews(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shopping_official_orders(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,request_key TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(user_id,request_key));
            ''')
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
            if "account_type" not in user_columns:
                # Earlier releases made persona unique, so A/B/C could only represent
                # three shared demo identities. Rebuild the table without that global
                # uniqueness while preserving every existing synthetic ledger.
                db.executescript('''
                    ALTER TABLE users RENAME TO users_persona_only;
                    CREATE TABLE users(
                        id TEXT PRIMARY KEY, persona TEXT NOT NULL, name TEXT,
                        revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                        account_type TEXT NOT NULL DEFAULT 'demo'
                    );
                    INSERT INTO users(id,persona,name,revision,state,account_type)
                    SELECT id,persona,name,revision,state,'demo' FROM users_persona_only;
                    DROP TABLE users_persona_only;
                ''')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS auth_accounts(
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS auth_challenges(
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at TEXT,created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_challenges_user_kind ON auth_challenges(user_id,kind,created);
                CREATE TABLE IF NOT EXISTS mfa_settings(
                    user_id TEXT PRIMARY KEY,version TEXT NOT NULL,pending_expires REAL NOT NULL,
                    enabled_at TEXT,last_counter INTEGER NOT NULL DEFAULT -1
                );
                CREATE TABLE IF NOT EXISTS mfa_recovery_codes(
                    user_id TEXT NOT NULL,code_hash TEXT NOT NULL,used_at TEXT,
                    PRIMARY KEY(user_id,code_hash)
                );
                CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
            ''')
            auth_columns = {row["name"] for row in db.execute("PRAGMA table_info(auth_accounts)").fetchall()}
            if "email" not in auth_columns:
                db.execute("ALTER TABLE auth_accounts ADD COLUMN email TEXT COLLATE NOCASE")
            if "email_verified_at" not in auth_columns:
                db.execute("ALTER TABLE auth_accounts ADD COLUMN email_verified_at TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS auth_accounts_email ON auth_accounts(email) WHERE email IS NOT NULL")
            binding_columns = db.execute("PRAGMA table_info(shopping_provider_bindings)").fetchall()
            binding_pk = [row["name"] for row in sorted(
                (row for row in binding_columns if row["pk"]), key=lambda row: row["pk"])]
            if binding_pk == ["provider"]:
                # 3.0 allowed only one machine-wide Meituan binding. Preserve it
                # while upgrading to one independent binding per authenticated user.
                db.executescript('''
                    ALTER TABLE shopping_provider_bindings RENAME TO shopping_provider_bindings_single_account;
                    CREATE TABLE shopping_provider_bindings(
                        provider TEXT NOT NULL,user_id TEXT NOT NULL,content TEXT NOT NULL,
                        PRIMARY KEY(provider,user_id)
                    );
                    INSERT INTO shopping_provider_bindings(provider,user_id,content)
                    SELECT provider,user_id,content FROM shopping_provider_bindings_single_account;
                    DROP TABLE shopping_provider_bindings_single_account;
                ''')

    def _load_auth_master_key(self):
        configured = os.environ.get('TROPOS_AUTH_MASTER_KEY', '').strip()
        if configured:
            try:
                value = base64.urlsafe_b64decode(configured + '=' * (-len(configured) % 4))
            except Exception as error:
                raise ValueError('TROPOS_AUTH_MASTER_KEY must be URL-safe base64') from error
            if len(value) < 32:
                raise ValueError('TROPOS_AUTH_MASTER_KEY must decode to at least 32 bytes')
            return value
        path = Path(self.db_path).with_name(Path(self.db_path).name + '.auth.key')
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            value = secrets.token_bytes(32)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return path.read_bytes()
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(value)
        if len(value) != 32:
            raise ValueError('authentication master key file is invalid')
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value

    @contextlib.contextmanager
    def _db(self, write=False):
        key = str(Path(self.db_path).resolve())
        active = _ACTIVE_DATABASES.get()
        if key in active:
            db, writable = active[key]
            if write and not writable:
                raise RuntimeError('Cannot promote a read transaction into a write transaction')
            yield db
            return
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        token = _ACTIVE_DATABASES.set(dict(active, **{key: (db, write)}))
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            _ACTIVE_DATABASES.reset(token)
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

    def _seed_account(self, db, persona, *, user_id, name, account_type):
        p = self.fixtures['personas'][persona]
        state = {'shocked': False, 'replan': False, 'salaryReceived': False, 'extraBills': [],
                 'subs': [dict(s, status='active') for s in p['subs']]}
        profile = {'goalType': p['tone'], 'goalName': p['goal']['name'], 'target': p['goal']['target'],
                   'reserve': p['reserve'], 'incomeStability': 'uncertain' if persona == 'A' else 'stable',
                   'style': persona, 'automation': 'confirm', 'readConsent': False}
        db.execute('INSERT INTO users(id,persona,name,revision,state,account_type) VALUES(?,?,?,0,?,?)',
                   (user_id, persona, name, json.dumps(state), account_type))
        db.execute('INSERT INTO profiles VALUES(?,0,?)', (user_id, json.dumps(profile)))
        for kind, balance in [('main', cents(p['balance'])), ('goal', cents(p['goal']['saved'])), ('external', 0), ('income', 0)]:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (user_id + ':' + kind, user_id, kind, balance))
        for payee in self.fixtures['payees']:
            db.execute('INSERT INTO counterparties VALUES(?,?,?)', (user_id, payee['id'], json.dumps(payee)))
        for plan in p['plans']:
            pl = dict(plan, status='waiting_condition' if plan.get('dep') else 'pending_confirmation')
            db.execute('INSERT INTO plans VALUES(?,?,?)', (user_id, plan['id'], json.dumps(pl)))
        self._audit(db, user_id, 'SYSTEM', '独立沙箱账本已建立', '合成账户初始化；尚未取得读取画像和执行授权。')
        return user_id

    def _seed(self, db, persona):
        p = self.fixtures['personas'][persona]
        return self._seed_account(db, persona, user_id='demo_' + persona,
                                  name=p['name'], account_type='demo')

    @staticmethod
    def _username(value):
        if not isinstance(value, str):
            raise BankError(400, 'INVALID_USERNAME', '用户名须为 3–32 位字母、数字或下划线。')
        value = value.strip().casefold()
        if not re.fullmatch(r'[a-z0-9_]{3,32}', value):
            raise BankError(400, 'INVALID_USERNAME', '用户名须为 3–32 位字母、数字或下划线。')
        return value

    @staticmethod
    def _password(value):
        if not isinstance(value, str) or not 10 <= len(value) <= 128 or len(value.encode('utf-8')) > 256:
            raise BankError(400, 'INVALID_PASSWORD', '密码长度须为 10–128 个字符。')
        if any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f for char in value):
            raise BankError(400, 'INVALID_PASSWORD', '密码不能包含控制字符。')
        return value

    @staticmethod
    def _password_digest(password, salt):
        # stdlib has no Argon2id. OWASP's scrypt fallback profile uses 16 MiB
        # with p=5; each account gets an independent 128-bit salt.
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2 ** 14,
                              r=8, p=5, maxmem=64 * 1024 * 1024, dklen=32).hex()

    def _new_session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 86400))
        return token

    def _challenge(self, db, user_id, kind, target, ttl=900):
        token = secrets.token_urlsafe(32)
        created = time.time()
        db.execute('UPDATE auth_challenges SET consumed_at=? WHERE user_id=? AND kind=? AND consumed_at IS NULL',
                   (now(), user_id, kind))
        db.execute('DELETE FROM auth_challenges WHERE expires<?', (created - 86400,))
        db.execute('INSERT INTO auth_challenges(id,user_id,kind,target,token_hash,expires,created) VALUES(?,?,?,?,?,?,?)',
                   (uid('challenge'), user_id, kind, target,
                    hashlib.sha256(token.encode()).hexdigest(), created + ttl, created))
        return token

    @staticmethod
    def _challenge_row(db, token, kind):
        if not isinstance(token, str) or not 20 <= len(token) <= 200 or any(char.isspace() for char in token):
            return None
        return db.execute('SELECT * FROM auth_challenges WHERE token_hash=? AND kind=? AND consumed_at IS NULL AND expires>?',
                          (hashlib.sha256(token.encode()).hexdigest(), kind, time.time())).fetchone()

    def _deliver(self, operation, recipient, token):
        try:
            return operation(recipient, token)
        except Exception:
            # The challenge remains valid so an authenticated user can retry after
            # SMTP is repaired. Never place the token in an HTTP error or log.
            return 'delivery-failed'

    def _mfa_secret(self, user_id, version):
        return hmac.new(self.auth_master_key,
                        ('tropos-totp\0' + user_id + '\0' + version).encode(), hashlib.sha256).digest()[:20]

    @staticmethod
    def _totp(secret, counter):
        digest = hmac.new(secret, struct.pack('>Q', counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0f
        value = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
        return f'{value:06d}'

    def _recovery_hash(self, code):
        normalized = re.sub(r'[^A-Z2-7]', '', str(code).upper())
        return hmac.new(self.auth_master_key, ('tropos-recovery\0' + normalized).encode(), hashlib.sha256).hexdigest()

    def _verify_mfa(self, db, user_id, code, *, allow_pending=False):
        setting = db.execute('SELECT * FROM mfa_settings WHERE user_id=?', (user_id,)).fetchone()
        if not setting or (not allow_pending and not setting['enabled_at']):
            return False
        text = str(code).strip() if isinstance(code, (str, int)) else ''
        secret = self._mfa_secret(user_id, setting['version'])
        if re.fullmatch(r'\d{6}', text):
            current = int(time.time() // 30)
            for counter in range(current - 1, current + 2):
                if counter > setting['last_counter'] and hmac.compare_digest(self._totp(secret, counter), text):
                    db.execute('UPDATE mfa_settings SET last_counter=? WHERE user_id=?', (counter, user_id))
                    return True
            return False
        code_hash = self._recovery_hash(text)
        recovery = db.execute('SELECT used_at FROM mfa_recovery_codes WHERE user_id=? AND code_hash=?',
                              (user_id, code_hash)).fetchone()
        if recovery and not recovery['used_at']:
            db.execute('UPDATE mfa_recovery_codes SET used_at=? WHERE user_id=? AND code_hash=?',
                       (now(), user_id, code_hash))
            return True
        return False

    def register(self, payload):
        if not isinstance(payload, dict):
            raise BankError(400, 'INVALID_REGISTRATION', '注册信息格式不正确。')
        username = self._username(payload.get('username'))
        password = self._password(payload.get('password'))
        try:
            email = normalize_email(payload.get('email'))
        except ValueError:
            raise BankError(400, 'INVALID_EMAIL', '请填写有效的邮箱地址。') from None
        persona = payload.get('templatePersona', 'C')
        if persona not in self.fixtures['personas']:
            raise BankError(400, 'INVALID_PERSONA', '请选择 A、B 或 C 作为初始财务样例。')
        display_name = payload.get('displayName', username).strip() if isinstance(payload.get('displayName', username), str) else ''
        if not 1 <= len(display_name) <= 30 or any(ord(char) < 32 for char in display_name):
            raise BankError(400, 'INVALID_DISPLAY_NAME', '显示名称须为 1–30 个可见字符。')
        salt = secrets.token_bytes(16)
        digest = self._password_digest(password, salt)
        with self._db(True) as db:
            if db.execute('SELECT 1 FROM auth_accounts WHERE username=?', (username,)).fetchone():
                raise BankError(409, 'USERNAME_TAKEN', '该用户名已被使用。')
            if db.execute('SELECT 1 FROM auth_accounts WHERE email=?', (email,)).fetchone():
                raise BankError(409, 'EMAIL_TAKEN', '该邮箱已绑定其他账户。')
            user_id = uid('usr')
            self._seed_account(db, persona, user_id=user_id, name=display_name, account_type='member')
            db.execute('INSERT INTO auth_accounts(user_id,username,password_hash,password_salt,created_at,email) VALUES(?,?,?,?,?,?)',
                       (user_id, username, digest, salt.hex(), now(), email))
            verification = self._challenge(db, user_id, 'verify_email', email)
            token = self._new_session(db, user_id)
            self._audit(db, user_id, 'USER', '账户注册成功', '已建立独立用户 ID 与独立合成账本。')
            result = {'token': token, 'snapshot': self._snapshot(db, user_id)}
        result['mailDelivery'] = self._deliver(self.mailer.verification, email, verification)
        return result

    def login(self, payload):
        if not isinstance(payload, dict):
            raise BankError(400, 'INVALID_LOGIN', '登录信息格式不正确。')
        username = self._username(payload.get('username'))
        password = self._password(payload.get('password'))
        failure = None
        result = None
        with self._db(True) as db:
            row = db.execute('SELECT * FROM auth_accounts WHERE username=?', (username,)).fetchone()
            # Run the same expensive KDF for unknown accounts to reduce timing leakage.
            salt = bytes.fromhex(row['password_salt']) if row else bytes(16)
            candidate = self._password_digest(password, salt)
            valid = bool(row and hmac.compare_digest(candidate, row['password_hash']))
            if row and row['locked_until'] > time.time():
                # Keep the public response indistinguishable from a bad username or
                # password so the throttle cannot be used for account enumeration.
                failure = BankError(401, 'INVALID_CREDENTIALS', '用户名或密码不正确，或登录尝试过多，请稍后再试。')
            elif not valid:
                if row:
                    attempts = row['failed_attempts'] + 1
                    locked_until = time.time() + 900 if attempts >= 5 else 0
                    db.execute('UPDATE auth_accounts SET failed_attempts=?,locked_until=? WHERE user_id=?',
                               (0 if locked_until else attempts, locked_until, row['user_id']))
                failure = BankError(401, 'INVALID_CREDENTIALS', '用户名或密码不正确，或登录尝试过多，请稍后再试。')
            else:
                db.execute('UPDATE auth_accounts SET failed_attempts=0,locked_until=0 WHERE user_id=?', (row['user_id'],))
                mfa = db.execute('SELECT enabled_at FROM mfa_settings WHERE user_id=?', (row['user_id'],)).fetchone()
                if mfa and mfa['enabled_at']:
                    challenge = self._challenge(db, row['user_id'], 'login_mfa', '', ttl=300)
                    result = {'mfaRequired': True, 'challengeToken': challenge, 'expiresIn': 300}
                else:
                    token = self._new_session(db, row['user_id'])
                    self._audit(db, row['user_id'], 'USER', '账户已登录', '已创建新的限时会话；未记录明文口令。')
                    result = {'token': token, 'snapshot': self._snapshot(db, row['user_id'])}
        if failure:
            raise failure
        return result

    def resend_email_verification(self, user_id):
        with self._db(True) as db:
            account = db.execute('SELECT email,email_verified_at FROM auth_accounts WHERE user_id=?', (user_id,)).fetchone()
            if not account or not account['email']:
                raise BankError(400, 'EMAIL_REQUIRED', '当前账户没有可验证邮箱。')
            if account['email_verified_at']:
                return {'ok': True, 'alreadyVerified': True, 'snapshot': self._snapshot(db, user_id)}
            recent = db.execute("SELECT created FROM auth_challenges WHERE user_id=? AND kind='verify_email' ORDER BY created DESC LIMIT 1", (user_id,)).fetchone()
            if recent and recent['created'] > time.time() - 60:
                raise BankError(429, 'EMAIL_RATE_LIMITED', '发送过于频繁，请一分钟后再试。')
            token = self._challenge(db, user_id, 'verify_email', account['email'])
            snapshot = self._snapshot(db, user_id)
        delivery = self._deliver(self.mailer.verification, account['email'], token)
        return {'ok': True, 'mailDelivery': delivery, 'snapshot': snapshot}

    def bind_email(self, user_id, email, password, code=None):
        try:
            email = normalize_email(email)
        except ValueError:
            raise BankError(400, 'INVALID_EMAIL', '请填写有效的邮箱地址。') from None
        password = self._password(password)
        with self._db(True) as db:
            account = db.execute('SELECT password_hash,password_salt FROM auth_accounts WHERE user_id=?', (user_id,)).fetchone()
            valid_password = bool(account and hmac.compare_digest(
                self._password_digest(password, bytes.fromhex(account['password_salt'])), account['password_hash']))
            mfa = db.execute('SELECT enabled_at FROM mfa_settings WHERE user_id=?', (user_id,)).fetchone()
            valid_mfa = not (mfa and mfa['enabled_at']) or self._verify_mfa(db, user_id, code)
            if not valid_password or not valid_mfa:
                raise BankError(401, 'INVALID_CREDENTIALS', '密码或 MFA 验证码不正确。')
            owner = db.execute('SELECT user_id FROM auth_accounts WHERE email=?', (email,)).fetchone()
            if owner and owner['user_id'] != user_id:
                raise BankError(409, 'EMAIL_TAKEN', '该邮箱已绑定其他账户。')
            db.execute('UPDATE auth_accounts SET email=?,email_verified_at=NULL WHERE user_id=?', (email, user_id))
            db.execute("UPDATE auth_challenges SET consumed_at=? WHERE user_id=? AND kind IN ('verify_email','password_reset') AND consumed_at IS NULL",
                       (now(), user_id))
            token = self._challenge(db, user_id, 'verify_email', email)
            self._audit(db, user_id, 'USER', '账户邮箱已更新', '新邮箱须重新验证后才能用于找回密码。')
            snapshot = self._snapshot(db, user_id)
        delivery = self._deliver(self.mailer.verification, email, token)
        return {'ok': True, 'mailDelivery': delivery, 'snapshot': snapshot}

    def verify_email(self, token):
        with self._db(True) as db:
            challenge = self._challenge_row(db, token, 'verify_email')
            if not challenge:
                raise BankError(400, 'INVALID_OR_EXPIRED_TOKEN', '验证链接无效或已经过期。')
            account = db.execute('SELECT email FROM auth_accounts WHERE user_id=?', (challenge['user_id'],)).fetchone()
            if not account or not hmac.compare_digest(account['email'] or '', challenge['target']):
                raise BankError(400, 'INVALID_OR_EXPIRED_TOKEN', '验证链接无效或已经过期。')
            verified_at = now()
            db.execute('UPDATE auth_challenges SET consumed_at=? WHERE id=?', (verified_at, challenge['id']))
            db.execute('UPDATE auth_accounts SET email_verified_at=? WHERE user_id=?', (verified_at, challenge['user_id']))
            self._audit(db, challenge['user_id'], 'USER', '邮箱验证完成', '邮箱已用于账户恢复和安全通知。')
            return {'ok': True}

    def request_password_reset(self, identifier):
        value = identifier.strip().casefold() if isinstance(identifier, str) else ''
        recipient = token = None
        with self._db(True) as db:
            account = db.execute('SELECT user_id,email,email_verified_at FROM auth_accounts WHERE username=? OR email=?',
                                 (value, value)).fetchone() if value else None
            if account and account['email'] and account['email_verified_at']:
                recent = db.execute("SELECT created FROM auth_challenges WHERE user_id=? AND kind='password_reset' ORDER BY created DESC LIMIT 1", (account['user_id'],)).fetchone()
                hourly = db.execute("SELECT COUNT(*) FROM auth_challenges WHERE user_id=? AND kind='password_reset' AND created>?", (account['user_id'], time.time() - 3600)).fetchone()[0]
                if (not recent or recent['created'] <= time.time() - 60) and hourly < 5:
                    token = self._challenge(db, account['user_id'], 'password_reset', account['email'])
                    recipient = account['email']
        if recipient and token:
            self._deliver(self.mailer.password_reset, recipient, token)
        return {'ok': True, 'message': '如果账户存在且邮箱已验证，重置邮件将很快送达。'}

    def reset_password(self, token, password):
        password = self._password(password)
        salt = secrets.token_bytes(16)
        digest = self._password_digest(password, salt)
        with self._db(True) as db:
            challenge = self._challenge_row(db, token, 'password_reset')
            if not challenge:
                raise BankError(400, 'INVALID_OR_EXPIRED_TOKEN', '重置链接无效或已经过期。')
            changed_at = now()
            db.execute('UPDATE auth_challenges SET consumed_at=? WHERE id=?', (changed_at, challenge['id']))
            db.execute('UPDATE auth_accounts SET password_hash=?,password_salt=?,failed_attempts=0,locked_until=0 WHERE user_id=?',
                       (digest, salt.hex(), challenge['user_id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (challenge['user_id'],))
            self._audit(db, challenge['user_id'], 'USER', '密码已重置', '所有已有登录会话已撤销；MFA 设置保持不变。')
        return {'ok': True}

    def setup_mfa(self, user_id):
        with self._db(True) as db:
            account = db.execute('SELECT username,email_verified_at FROM auth_accounts WHERE user_id=?', (user_id,)).fetchone()
            if not account or not account['email_verified_at']:
                raise BankError(403, 'EMAIL_VERIFICATION_REQUIRED', '请先验证邮箱，再开启 MFA。')
            existing = db.execute('SELECT enabled_at FROM mfa_settings WHERE user_id=?', (user_id,)).fetchone()
            if existing and existing['enabled_at']:
                raise BankError(409, 'MFA_ALREADY_ENABLED', 'MFA 已经开启。')
            version = secrets.token_urlsafe(18)
            expires = time.time() + 600
            db.execute('INSERT INTO mfa_settings(user_id,version,pending_expires,enabled_at,last_counter) VALUES(?,?,?,NULL,-1) '
                       'ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,pending_expires=excluded.pending_expires,enabled_at=NULL,last_counter=-1',
                       (user_id, version, expires))
            secret = base64.b32encode(self._mfa_secret(user_id, version)).decode().rstrip('=')
            label = 'TROPOS:' + account['username']
            uri = 'otpauth://totp/' + urllib.parse.quote(label) + '?' + urllib.parse.urlencode(
                {'secret': secret, 'issuer': 'TROPOS', 'algorithm': 'SHA1', 'digits': '6', 'period': '30'})
            return {'secret': secret, 'uri': uri, 'expiresIn': 600}

    def enable_mfa(self, user_id, code):
        with self._db(True) as db:
            setting = db.execute('SELECT * FROM mfa_settings WHERE user_id=?', (user_id,)).fetchone()
            if not setting or setting['enabled_at'] or setting['pending_expires'] < time.time():
                raise BankError(400, 'MFA_SETUP_EXPIRED', 'MFA 设置已过期，请重新开始。')
            if not self._verify_mfa(db, user_id, code, allow_pending=True):
                raise BankError(400, 'INVALID_MFA_CODE', '动态验证码不正确。')
            enabled_at = now()
            db.execute('UPDATE mfa_settings SET enabled_at=?,pending_expires=0 WHERE user_id=?', (enabled_at, user_id))
            db.execute('DELETE FROM mfa_recovery_codes WHERE user_id=?', (user_id,))
            codes = []
            for _ in range(10):
                raw = base64.b32encode(secrets.token_bytes(8)).decode().rstrip('=')[:12]
                code_text = raw[:4] + '-' + raw[4:8] + '-' + raw[8:]
                codes.append(code_text)
                db.execute('INSERT INTO mfa_recovery_codes(user_id,code_hash) VALUES(?,?)',
                           (user_id, self._recovery_hash(code_text)))
            self._audit(db, user_id, 'USER', 'MFA 已开启', '后续登录需要动态验证码或一次性恢复码。')
            return {'ok': True, 'recoveryCodes': codes, 'snapshot': self._snapshot(db, user_id)}

    def complete_mfa_login(self, challenge_token, code):
        failure = None
        result = None
        with self._db(True) as db:
            challenge = self._challenge_row(db, challenge_token, 'login_mfa')
            if not challenge:
                raise BankError(401, 'INVALID_MFA_CHALLENGE', '登录验证已过期，请重新输入密码。')
            if challenge['attempts'] >= 5:
                raise BankError(401, 'INVALID_MFA_CHALLENGE', '登录验证已过期，请重新输入密码。')
            if not self._verify_mfa(db, challenge['user_id'], code):
                db.execute('UPDATE auth_challenges SET attempts=attempts+1 WHERE id=?', (challenge['id'],))
                failure = BankError(401, 'INVALID_MFA_CODE', '动态验证码或恢复码不正确。')
            else:
                completed_at = now()
                db.execute('UPDATE auth_challenges SET consumed_at=? WHERE id=?', (completed_at, challenge['id']))
                token = self._new_session(db, challenge['user_id'])
                self._audit(db, challenge['user_id'], 'USER', 'MFA 登录成功', '密码与第二因素均已通过校验。')
                result = {'token': token, 'snapshot': self._snapshot(db, challenge['user_id'])}
        if failure:
            raise failure
        return result

    def disable_mfa(self, user_id, password, code):
        password = self._password(password)
        with self._db(True) as db:
            account = db.execute('SELECT password_hash,password_salt FROM auth_accounts WHERE user_id=?', (user_id,)).fetchone()
            valid_password = bool(account and hmac.compare_digest(
                self._password_digest(password, bytes.fromhex(account['password_salt'])), account['password_hash']))
            if not valid_password or not self._verify_mfa(db, user_id, code):
                raise BankError(401, 'INVALID_CREDENTIALS', '密码或 MFA 验证码不正确。')
            db.execute('DELETE FROM mfa_recovery_codes WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM mfa_settings WHERE user_id=?', (user_id,))
            self._audit(db, user_id, 'USER', 'MFA 已关闭', '后续登录只校验账户密码。')
            return {'ok': True, 'snapshot': self._snapshot(db, user_id)}

    def session(self, persona):
        if persona not in self.fixtures['personas']:
            raise BankError(400, 'INVALID_PERSONA', '请选择有效的演示账户。')
        with self._db(True) as db:
            row = db.execute("SELECT id FROM users WHERE persona=? AND account_type='demo'", (persona,)).fetchone()
            user_id = row[0] if row else self._seed(db, persona)
            token = self._new_session(db, user_id)
            return {'token': token, 'snapshot': self._snapshot(db, user_id)}

    def authenticate(self, token):
        if not isinstance(token, str):
            raise BankError(401, 'UNAUTHORIZED', '请先进入演示账户。')
        with self._db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise BankError(401, 'UNAUTHORIZED', '会话已失效，请重新登录。')
            return row[0]

    def logout(self, token):
        if not isinstance(token, str) or not token:
            return
        with self._db(True) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',
                       (hashlib.sha256(token.encode()).hexdigest(),))

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
        profile = self._profile(db, user_id)
        balance = yuan(self._balance(db, user_id))
        bills = p['bills'] + st['extraBills']
        # Reuse facts from this transaction; the pure assessor never reads a snapshot.
        risk_profile = assess_profile(
            profile=profile, balance=balance, income=p['income'],
            debt=sum(-bill['amount'] for bill in bills if bill['kind'] in ['还款', '分期', '贷款']),
            daily=cash['daily'], min=cash['min'], free=cash['free'],
            shocked=st['shocked'], salaryReceived=st['salaryReceived'])
        tx = [json.loads(row[0]) for row in db.execute('SELECT content FROM transactions WHERE user_id=? ORDER BY rowid DESC', (user_id,))]
        history = [dict(t, id='fixture_' + str(i), status='historical', receipt=None) for i, t in enumerate(p['tx'])]
        auth = db.execute('SELECT username,email,email_verified_at FROM auth_accounts WHERE user_id=?', (user_id,)).fetchone()
        mfa = db.execute('SELECT enabled_at FROM mfa_settings WHERE user_id=?', (user_id,)).fetchone()
        public_user = {k: user[k] for k in ['id','persona','name','revision']}
        public_user.update(accountType=user['account_type'], username=auth['username'] if auth else None,
                           email=masked_email(auth['email']) if auth else None,
                           emailVerified=bool(auth and auth['email_verified_at']),
                           mfaEnabled=bool(mfa and mfa['enabled_at']))
        return {'user': public_user, 'profile': profile,
                'balance': balance, 'goalFunded': yuan(self._balance(db, user_id, 'goal')),
                'shocked': st['shocked'], 'replan': st['replan'], 'salaryReceived': st['salaryReceived'],
                'bills': bills, 'plans': [json.loads(r[0]) for r in db.execute('SELECT content FROM plans WHERE user_id=?', (user_id,))],
                'subscriptions': st['subs'], 'transactions': tx + history, 'cash': cash,
                'policy': self._policy(db, user_id, cash),
                'riskProfile': risk_profile,
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
        if 'assessment' in profile:
            try:
                clean['assessment'] = validate_assessment(profile['assessment'])
            except ValueError as error:
                raise BankError(400, 'INVALID_PROFILE', str(error)) from error
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
            recovered = [self._present_proposal(db, user_id, json.loads(row[0]), user, consent, at) for row in rows]
            return {'proposals': recovered, 'snapshot': self._snapshot(db, user_id)}

    def proposal(self, user_id, proposal_id):
        """Recover one owned proposal, including those older than the recent list."""
        with self._db() as db:
            user, _ = self._user(db, user_id)
            consent = self._profile(db, user_id).get('readConsent') is True
            proposal = self._present_proposal(db, user_id, self._proposal(db, user_id, proposal_id), user, consent, time.time())
            return {'proposal': proposal, 'snapshot': self._snapshot(db, user_id)}

    def _present_proposal(self, db, user_id, proposal, user, consent, at):
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
        return proposal

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
        with self._db(True) as db:
            return self._cancel_subscription(db, user_id, sub_id, payload)

    def _cancel_subscription(self, db, user_id, sub_id, payload):
        """Shared mutation so a reviewed chat operation and receipt commit atomically."""
        self._confirm(payload)
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
            for table in ('life_scenes', 'life_state', 'life_plan_receipts', 'shopping_searches',
                          'shopping_checkouts', 'shopping_orders', 'subscription_proposals',
                          'subscription_proposal_keys', 'conversations', 'conversation_requests',
                          'cards', 'missions', 'household'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    db.execute('DELETE FROM ' + table + ' WHERE user_id=?', (user_id,))
            for table in ['profiles','authorizations','accounts','counterparties','transactions','idempotency','proposals','plans','events','audit','profile_updates']:
                db.execute('DELETE FROM ' + table + ' WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
            self._seed_account(db, user['persona'], user_id=user_id, name=user['name'],
                               account_type=user['account_type'])
            self._audit(db, user_id, 'USER', '当前模拟账户已重置', '仅重置本人合成演示账本。')
            return self._snapshot(db, user_id)
