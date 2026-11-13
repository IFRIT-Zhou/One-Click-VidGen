"""Spend alerts via the existing verified SSL SMTP configuration."""
from email.message import EmailMessage
from email.utils import formataddr
import smtplib
import ssl

from .config import settings
from .pool_balance_alerts import DeliveryResult


def configured():
    return bool(settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_sender)


def send(recipient, text, delivery_id):
    message = EmailMessage()
    message["Subject"] = "One-Click VidGen 上游累计消费每20美元提醒"
    message["From"] = formataddr(("One-Click VidGen", settings.smtp_sender))
    message["To"] = recipient
    message["Message-ID"] = f"<{delivery_id}@oneclickvidgen.com>"
    message.set_content(text + "\n\n请核对平台消费记录，按需充值。\n此邮件不包含 API 密钥；账号短编号用于区分监测记录。\n")
    smtp = None
    submitting = False
    try:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ssl.create_default_context())
        smtp.login(settings.smtp_username, settings.smtp_password)
        submitting = True
        refused = smtp.send_message(message)
        return DeliveryResult("failed" if refused else "accepted")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        return DeliveryResult("failed")
    except Exception:
        return DeliveryResult("unknown" if submitting else "failed")
    finally:
        # A QUIT failure after successful DATA must not cause a duplicate email.
        if smtp:
            try:
                smtp.close()
            except Exception:
                pass
