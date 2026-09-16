import unittest
from unittest.mock import MagicMock

from app.services import OTPCooldownError, OTPService


class OTPServiceTest(unittest.TestCase):
    def setUp(self):
        OTPService.otp_storage = {}
        self.email_service = MagicMock()
        self.service = OTPService(self.email_service)

    def test_generate_otp_sends_email(self):
        self.service.generate_otp("tenant@example.com")
        self.email_service.send_otp_email.assert_called_once()

    def test_second_request_within_cooldown_raises(self):
        self.service.generate_otp("tenant@example.com")
        with self.assertRaises(OTPCooldownError):
            self.service.generate_otp("tenant@example.com")

    def test_different_email_not_throttled(self):
        self.service.generate_otp("tenant@example.com")
        self.service.generate_otp("other@example.com")  # should not raise
        self.assertEqual(self.email_service.send_otp_email.call_count, 2)

    def test_verify_correct_otp_succeeds(self):
        otp = self.service.generate_otp("tenant@example.com")
        self.assertTrue(self.service.verify_otp("tenant@example.com", otp))

    def test_verify_wrong_otp_fails(self):
        self.service.generate_otp("tenant@example.com")
        self.assertFalse(self.service.verify_otp("tenant@example.com", "0000"))


if __name__ == "__main__":
    unittest.main()
