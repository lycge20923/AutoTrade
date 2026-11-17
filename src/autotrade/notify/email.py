"""用 Gmail SMTP 寄信。"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from autotrade.config import Settings


class EmailError(Exception):
    """寄信失敗，訊息會直接顯示給使用者。"""


@dataclass
class EmailSender:
    host: str
    port: int
    username: str
    password: str
    smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP_SSL

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = self.username
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")

        try:
            with self.smtp_factory(
                self.host, self.port, context=ssl.create_default_context(), timeout=30
            ) as smtp:
                smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(
                "Gmail 登入失敗：請確認 .env 裡的 GMAIL_ADDRESS 和 GMAIL_APP_PASSWORD。"
                "密碼要用「應用程式密碼」，不是 Gmail 登入密碼。"
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"寄信失敗：{exc}") from exc


def sender_from_settings(settings: Settings) -> EmailSender:
    s = settings.secrets
    if not s.gmail_address or not s.gmail_app_password:
        raise EmailError("尚未設定寄信帳號：請在 .env 填入 GMAIL_ADDRESS 和 GMAIL_APP_PASSWORD。")
    cfg = settings.app.notify.email
    return EmailSender(
        host=cfg.smtp_host,
        port=cfg.smtp_port,
        username=s.gmail_address,
        # Google 顯示的應用程式密碼中間有空格，實際上不含空格
        password=s.gmail_app_password.get_secret_value().replace(" ", ""),
    )
