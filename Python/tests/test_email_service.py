import os
import unittest
from unittest.mock import patch

from app.models import TenantBill
from app.services import EmailService


class EmailServiceTest(unittest.TestCase):
    def setUp(self):
        os.environ["MAIL_SERVER"] = "127.0.0.1"
        os.environ["MAIL_PORT"] = "2525"
        os.environ["MAIL_USE_TLS"] = "false"
        os.environ["MAIL_USERNAME"] = ""
        os.environ["MAIL_PASSWORD"] = ""
        os.environ["MAIL_SENDER"] = "noreply@example.com"

    @patch("app.services.smtplib.SMTP")
    def test_notify_bill_generated_sends_email(self, mock_smtp):
        bill = TenantBill(tenant_name="tenant1", month_year="2026-08", rent=1000, water=50, electricity=60)

        EmailService().notify_bill_generated(bill, "tenant@example.com", "2026-08", None)

        self.assertTrue(mock_smtp.called)
        self.assertTrue(mock_smtp.return_value.__enter__.called)


if __name__ == "__main__":
    unittest.main()
