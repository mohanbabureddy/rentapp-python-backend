import os
import unittest
from unittest.mock import MagicMock, patch

import razorpay

from app.models import TenantBill
from app.payments import PaymentService


class PaymentServiceTest(unittest.TestCase):
    def setUp(self):
        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_fake"
        os.environ["RAZORPAY_KEY_SECRET"] = "fake_secret"
        self.repo = MagicMock()

    def _bill(self, **overrides):
        defaults = dict(id=1, tenant_name="Room1", rent=500.0, water=50.0, electricity=60.0, miscellaneous=None, paid=False)
        defaults.update(overrides)
        return TenantBill(**defaults)

    # -- create_order --------------------------------------------------

    @patch("app.payments.razorpay.Client")
    def test_create_order_not_configured_raises(self, mock_client_cls):
        del os.environ["RAZORPAY_KEY_ID"]
        service = PaymentService(self.repo)
        with self.assertRaises(RuntimeError):
            service.create_order(1)

    @patch("app.payments.razorpay.Client")
    def test_create_order_bill_not_found_raises(self, mock_client_cls):
        self.repo.find_by_id.return_value = None
        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.create_order(1)

    @patch("app.payments.razorpay.Client")
    def test_create_order_already_paid_raises(self, mock_client_cls):
        self.repo.find_by_id.return_value = self._bill(paid=True)
        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.create_order(1)

    @patch("app.payments.razorpay.Client")
    def test_create_order_zero_amount_raises(self, mock_client_cls):
        self.repo.find_by_id.return_value = self._bill(rent=0, water=0, electricity=0, miscellaneous=0)
        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.create_order(1)

    @patch("app.payments.razorpay.Client")
    def test_create_order_includes_miscellaneous_and_auto_capture(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.order.create.return_value = {"id": "order_abc"}
        self.repo.find_by_id.return_value = self._bill(rent=500.0, water=50.0, electricity=60.0, miscellaneous=100.0)

        service = PaymentService(self.repo)
        result = service.create_order(1)

        call_args = mock_client.order.create.call_args[0][0]
        self.assertEqual(call_args["amount"], 71000)  # (500+50+60+100) * 100
        self.assertEqual(call_args["payment_capture"], 1)
        self.assertEqual(result["orderId"], "order_abc")
        self.assertEqual(result["amount"], 71000)

    # -- verify_payment --------------------------------------------------

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_missing_params_raises(self, mock_client_cls):
        self.repo.find_by_id.return_value = self._bill()
        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.verify_payment(1, "", "pay_1", "sig")

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_bad_signature_raises(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("bad")
        self.repo.find_by_id.return_value = self._bill()

        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.verify_payment(1, "order_abc", "pay_1", "sig")

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_bill_mismatch_raises(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.order.fetch.return_value = {"amount": 61000, "notes": {"billId": "999"}}
        mock_client.payment.fetch.return_value = {"status": "captured"}
        self.repo.find_by_id.return_value = self._bill()

        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.verify_payment(1, "order_abc", "pay_1", "sig")

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_amount_mismatch_raises(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.order.fetch.return_value = {"amount": 1, "notes": {"billId": "1"}}
        mock_client.payment.fetch.return_value = {"status": "captured"}
        self.repo.find_by_id.return_value = self._bill()

        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.verify_payment(1, "order_abc", "pay_1", "sig")

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_not_captured_raises(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.order.fetch.return_value = {"amount": 61000, "notes": {"billId": "1"}}
        mock_client.payment.fetch.return_value = {"status": "authorized"}
        self.repo.find_by_id.return_value = self._bill()

        service = PaymentService(self.repo)
        with self.assertRaises(ValueError):
            service.verify_payment(1, "order_abc", "pay_1", "sig")

    @patch("app.payments.razorpay.Client")
    def test_verify_payment_success(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.order.fetch.return_value = {"amount": 61000, "notes": {"billId": "1"}}
        mock_client.payment.fetch.return_value = {"status": "captured"}
        self.repo.find_by_id.return_value = self._bill()

        service = PaymentService(self.repo)
        service.verify_payment(1, "order_abc", "pay_1", "sig")  # should not raise
        mock_client.utility.verify_payment_signature.assert_called_once()


if __name__ == "__main__":
    unittest.main()
