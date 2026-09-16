import os
import unittest
from unittest.mock import patch

from app.models import TenantBill
from app.services import EmailService


class EmailServiceTest(unittest.TestCase):
    def setUp(self):
        os.environ["RESEND_API_KEY"] = "test-api-key"
        os.environ["MAIL_SENDER"] = "noreply@example.com"

    @patch("app.services.requests.post")
    def test_notify_bill_generated_sends_email(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        bill = TenantBill(tenant_name="tenant1", month_year="2026-08", rent=1000, water=50, electricity=60)

        EmailService().notify_bill_generated(bill, "tenant@example.com", "2026-08")

        self.assertTrue(mock_post.called)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        self.assertEqual(kwargs["json"]["to"], ["tenant@example.com"])


if __name__ == "__main__":
    unittest.main()
