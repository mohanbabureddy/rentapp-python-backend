import unittest
from unittest.mock import MagicMock

from app.models import DepositPayment, User
from app.services import DepositService


class DepositServiceTest(unittest.TestCase):
    def setUp(self):
        self.deposit_repo = MagicMock()
        self.user_repo = MagicMock()
        self.service = DepositService(self.deposit_repo, self.user_repo)
        self.user = User(id=3, username="Room1", password="x", role="TENANT")

    def test_get_summary_sums_history(self):
        self.user_repo.find_by_username.return_value = self.user
        self.deposit_repo.find_by_tenant_order_by_date_desc.return_value = [
            DepositPayment(id=2, tenant_username="Room1", amount=5000, source="manual"),
            DepositPayment(id=1, tenant_username="Room1", amount=20000, source="manual"),
        ]
        self.deposit_repo.total_for_tenant.return_value = 25000.0

        summary = self.service.get_summary("Room1")

        self.assertEqual(summary["totalAmountDeposited"], 25000.0)
        self.assertEqual(len(summary["history"]), 2)

    def test_get_summary_unknown_user_raises(self):
        self.user_repo.find_by_username.return_value = None
        with self.assertRaises(ValueError):
            self.service.get_summary("nobody")

    def test_set_move_in_date_parses_iso_date(self):
        self.user_repo.find_by_id.return_value = self.user
        self.service.set_move_in_date(3, "2026-01-15")
        self.assertEqual(str(self.user.move_in_date), "2026-01-15")
        self.user_repo.save.assert_called_once()

    def test_set_move_in_date_bad_format_raises(self):
        self.user_repo.find_by_id.return_value = self.user
        with self.assertRaises(ValueError):
            self.service.set_move_in_date(3, "15/01/2026")

    def test_add_manual_deposit_rejects_non_positive_amount(self):
        self.user_repo.find_by_id.return_value = self.user
        with self.assertRaises(ValueError):
            self.service.add_manual_deposit(3, 0)
        with self.assertRaises(ValueError):
            self.service.add_manual_deposit(3, -50)

    def test_add_manual_deposit_saves_entry(self):
        self.user_repo.find_by_username.return_value = self.user
        self.user_repo.find_by_id.return_value = self.user
        self.deposit_repo.find_by_tenant_order_by_date_desc.return_value = []
        self.deposit_repo.total_for_tenant.return_value = 1000.0

        self.service.add_manual_deposit(3, 1000, "cash")

        saved = self.deposit_repo.save.call_args[0][0]
        self.assertEqual(saved.amount, 1000)
        self.assertEqual(saved.source, "manual")
        self.assertEqual(saved.tenant_username, "Room1")

    def test_record_payment_saves_razorpay_entry(self):
        self.user_repo.find_by_username.return_value = self.user
        self.deposit_repo.find_by_tenant_order_by_date_desc.return_value = []
        self.deposit_repo.total_for_tenant.return_value = 2500.0

        self.service.record_payment("Room1", 2500, "pay_abc123")

        saved = self.deposit_repo.save.call_args[0][0]
        self.assertEqual(saved.source, "razorpay")
        self.assertEqual(saved.payment_id, "pay_abc123")


if __name__ == "__main__":
    unittest.main()
