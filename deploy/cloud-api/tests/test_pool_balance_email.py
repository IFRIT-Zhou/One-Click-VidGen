import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
import smtplib
import unittest
from unittest.mock import patch, MagicMock
from app.pool_balance_email import send


class EmailTests(unittest.TestCase):
    def test_smtp_acceptance_and_no_secret_body(self):
        with patch("app.pool_balance_email.smtplib.SMTP_SSL") as factory:
            factory.return_value.send_message.return_value = {}
            result = send("942700596@qq.com", "余额低于30，需要充值。", "bal_test")
            self.assertEqual(result.status, "accepted")
            message = factory.return_value.send_message.call_args.args[0]
            self.assertEqual(message["To"], "942700596@qq.com")
            self.assertEqual(message["Message-ID"], "<bal_test@oneclickvidgen.com>")

    def test_pre_send_failure_can_retry(self):
        with patch("app.pool_balance_email.smtplib.SMTP_SSL", side_effect=TimeoutError):
            self.assertEqual(send("942700596@qq.com", "余额低", "id").status, "failed")

    def test_timeout_after_submit_is_unknown(self):
        with patch("app.pool_balance_email.smtplib.SMTP_SSL") as factory:
            factory.return_value.send_message.side_effect = TimeoutError
            self.assertEqual(send("942700596@qq.com", "余额低", "id").status, "unknown")

    def test_explicit_rejection_is_failed(self):
        with patch("app.pool_balance_email.smtplib.SMTP_SSL") as factory:
            factory.return_value.send_message.side_effect = smtplib.SMTPDataError(550, b"rejected")
            self.assertEqual(send("942700596@qq.com", "余额低", "id").status, "failed")

    def test_close_failure_does_not_change_acceptance(self):
        with patch("app.pool_balance_email.smtplib.SMTP_SSL") as factory:
            factory.return_value.send_message.return_value = {}
            factory.return_value.close.side_effect = TimeoutError
            self.assertEqual(send("942700596@qq.com", "余额低", "id").status, "accepted")
