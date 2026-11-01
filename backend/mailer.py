"""Transactional authentication mail with SMTP and a private local outbox fallback."""
from __future__ import annotations

from email.message import EmailMessage
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import time
import urllib.parse
import uuid


EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError("invalid email")
    value = value.strip().casefold()
    if len(value) > 254 or not EMAIL.fullmatch(value) or ".." in value:
        raise ValueError("invalid email")
    return value


def masked_email(value):
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return visible + "***@" + domain


class AuthMailer:
    def __init__(self, data_directory):
        self.data_directory = Path(data_directory)
        self.outbox = self.data_directory / "mail-outbox"
        self.host = os.environ.get("TROPOS_SMTP_HOST", "").strip()
        self.port = int(os.environ.get("TROPOS_SMTP_PORT", "465" if os.environ.get("TROPOS_SMTP_SSL") == "1" else "587"))
        self.username = os.environ.get("TROPOS_SMTP_USERNAME", "").strip()
        self.password = os.environ.get("TROPOS_SMTP_PASSWORD", "")
        self.sender = os.environ.get("TROPOS_SMTP_FROM", self.username).strip()
        self.use_ssl = os.environ.get("TROPOS_SMTP_SSL", "").strip() == "1"
        self.starttls = os.environ.get("TROPOS_SMTP_STARTTLS", "1").strip() != "0"
        address = os.environ.get("TROPOS_PUBLIC_URL", os.environ.get("TROPOS_SITE_ADDRESS", "http://127.0.0.1:8765")).strip().rstrip("/")
        if address and "://" not in address:
            address = "https://" + address
        parsed = urllib.parse.urlsplit(address)
        self.public_url = address if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path else "http://127.0.0.1:8765"

    @property
    def mode(self):
        return "smtp" if self.host and self.sender else "development-outbox"

    def verification(self, recipient, token):
        link = self.public_url + "/demo.html?verify=" + urllib.parse.quote(token)
        return self._send(recipient, "验证你的 TROPOS 邮箱", "请在 15 分钟内完成邮箱验证：\n\n" + link + "\n\n如果不是你本人操作，请忽略此邮件。", link, "verify")

    def password_reset(self, recipient, token):
        link = self.public_url + "/demo.html?reset=" + urllib.parse.quote(token)
        return self._send(recipient, "重置你的 TROPOS 密码", "请在 15 分钟内重置密码：\n\n" + link + "\n\n链接只能使用一次。如果不是你本人操作，请忽略此邮件。", link, "password-reset")

    def _send(self, recipient, subject, body, link, kind):
        recipient = normalize_email(recipient)
        if self.mode == "development-outbox":
            self.outbox.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.outbox, 0o700)
            except OSError:
                pass
            target = self.outbox / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex + ".json")
            target.write_text(json.dumps({"to": recipient, "subject": subject, "body": body,
                                          "link": link, "kind": kind}, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            return self.mode
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message.set_content(body)
        context = ssl.create_default_context()
        client_context = smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context) if self.use_ssl else smtplib.SMTP(self.host, self.port, timeout=10)
        with client_context as client:
            if not self.use_ssl and self.starttls:
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)
        return self.mode
