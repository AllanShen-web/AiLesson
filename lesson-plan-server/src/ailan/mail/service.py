"""SMTP 发信与验证码缓存。

验证码只存在本进程的内存里：单实例部署够用，多实例要换成 Redis——
用户可能从 A 实例拿到码、提交到 B 实例，那种情况下这套校验必然失效。
"""

from __future__ import annotations

import logging
import secrets
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated

import aiosmtplib
from fastapi import Depends

from ailan.core.config import settings
from ailan.core.errors import UpstreamError

logger = logging.getLogger(__name__)

# 有效期 5 分钟，界面上的倒计时按这个数写死的
CODE_TTL_SECONDS = 5 * 60
SENDER_NAME = "客服布布"
SUBJECT_PREFIX = "【智慧教案】"
LOGO_PATH = Path("assets/logo.png")


class MailService:
    def __init__(self) -> None:
        # 收件邮箱 -> (验证码, 到期时刻)
        self._codes: dict[str, tuple[str, float]] = {}

    async def send_code(self, email: str, purpose: str) -> None:
        """发一封验证码邮件。`purpose` 会同时出现在标题和正文里，比如"注册验证"。"""
        code = self._new_code()
        self._codes[email] = (code, time.monotonic() + CODE_TTL_SECONDS)
        self._drop_expired()
        await self._deliver(email, purpose, code)

    def verify_code(self, email: str, code: str) -> bool:
        """校验并即刻作废：一个码只能用一次，输错也留着，避免被反复试探。"""
        cached = self._codes.get(email)
        if cached is None:
            return False
        expected, expires_at = cached
        if time.monotonic() > expires_at:
            self._codes.pop(email, None)
            return False
        if not secrets.compare_digest(expected, code or ""):
            return False
        self._codes.pop(email, None)
        return True

    @staticmethod
    def _new_code() -> str:
        # 六位数字，用 secrets 而不是 random：验证码是可预测的目标，不能用非加密随机源
        return f"{secrets.randbelow(900_000) + 100_000}"

    def _drop_expired(self) -> None:
        """顺手清掉过期的，免得这张表被人拿来灌内存。"""
        now = time.monotonic()
        for key in [key for key, (_, expires_at) in self._codes.items() if now > expires_at]:
            self._codes.pop(key, None)

    async def _deliver(self, email: str, purpose: str, code: str) -> None:
        message = EmailMessage()
        message["From"] = f"{SENDER_NAME} <{settings.smtp_from}>"
        message["To"] = email
        message["Subject"] = f"{SUBJECT_PREFIX}{purpose}验证码"
        message.set_content(build_code_email(purpose, code))

        if LOGO_PATH.exists():
            # cid 要和 HTML 里的 `src="cid:logo"` 对上，收发件客户端才会把它内联进正文
            message.add_attachment(
                LOGO_PATH.read_bytes(),
                maintype="image",
                subtype="png",
                filename=LOGO_PATH.name,
                cid="<logo>",
            )

        try:
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_pass,
                # 465 是隐式 TLS；换成 587 要把这里改成 start_tls=True
                use_tls=True,
            )
        except aiosmtplib.SMTPException as exc:
            # 具体是认证失败还是连不上只进日志，回给用户的是一句通用文案
            logger.error("验证码邮件发送失败 to=%s: %s", email, exc)
            raise UpstreamError("邮件发送失败，请稍后重试") from exc


def build_code_email(purpose: str, code: str) -> str:
    year = time.localtime().tm_year
    return CODE_EMAIL_HTML.format(code=code, purpose=purpose, year=year)


CODE_EMAIL_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fa;padding:40px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:linear-gradient(135deg,#0a1628,#1a2d4a);padding:32px 40px;text-align:center;">
            <img src="cid:logo" alt="Ailan" width="76" height="22" style="vertical-align:middle;display:inline-block;" /><span style="font-size:22px;font-weight:bold;color:#fff;letter-spacing:2px;vertical-align:middle;margin-left:2px;">智慧教案</span>
          </td>
        </tr>
        <tr>
          <td style="padding:40px;">
            <p style="font-size:15px;color:#333;margin:0 0 8px;">亲爱的用户，您好！</p>
            <p style="font-size:14px;color:#666;margin:0 0 24px;line-height:1.8;">
              感谢您使用<strong style="color:#1677ff;">Ailan智慧教案</strong>平台。您本次<strong style="color:#1677ff;">{purpose}</strong>的邮箱验证码为：
            </p>
            <div style="background:#f8fafc;border:1px dashed #d0d7de;border-radius:8px;padding:20px;text-align:center;margin:0 0 24px;">
              <span style="font-size:36px;font-weight:bold;color:#1677ff;letter-spacing:10px;font-family:monospace;">{code}</span>
            </div>
            <p style="font-size:13px;color:#999;margin:0 0 8px;line-height:1.8;">
              验证码将在 <strong style="color:#ff4d4f;">5 分钟</strong> 内有效，请勿将此验证码泄露给他人。
            </p>
            <p style="font-size:13px;color:#999;margin:0;line-height:1.8;">
              如果您并未请求此验证码，请忽略本邮件，您的账号不会产生任何变更。
            </p>
          </td>
        </tr>
        <tr>
          <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #eef0f3;">
            <p style="font-size:12px;color:#bbb;margin:0;">此邮件由系统自动发送，请勿直接回复</p>
            <p style="font-size:12px;color:#bbb;margin:4px 0 0;">&copy; {year} Ailan智慧教案 &middot; AI 智能教学方案生成平台</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


mail_service = MailService()


def get_mail_service() -> MailService:
    return mail_service


MailDep = Annotated[MailService, Depends(get_mail_service)]
