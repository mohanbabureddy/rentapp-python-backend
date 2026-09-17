import unittest
from unittest.mock import MagicMock

from app.models import TenantBill
from app.services import TenantBillService


class TenantBillServicePaidGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock()
        self.user_repo = MagicMock()
        self.service = TenantBillService(self.repo, self.user_repo, email_service=None)

    def _bill(self, **overrides):
        defaults = dict(id=1, tenant_name="Room1", month_year="2026-09", bill_type="RENT", rent=500.0, water=50.0, electricity=60.0, miscellaneous=None, paid=False)
        defaults.update(overrides)
        return TenantBill(**defaults)

    def test_update_unpaid_bill_succeeds(self):
        self.repo.find_by_id.return_value = self._bill(paid=False)
        updated = self._bill(rent=600.0)
        self.service.update_bill(1, updated)
        self.repo.save.assert_called_once()

    def test_update_paid_bill_rejected(self):
        self.repo.find_by_id.return_value = self._bill(paid=True)
        updated = self._bill(rent=600.0)
        with self.assertRaises(PermissionError):
            self.service.update_bill(1, updated)
        self.repo.save.assert_not_called()

    def test_delete_unpaid_bill_succeeds(self):
        self.repo.find_by_id.return_value = self._bill(paid=False)
        self.service.delete_bill(1)
        self.repo.delete_by_id.assert_called_once_with(1)

    def test_add_bill_passes_bill_type_to_uniqueness_check(self):
        self.repo.find_by_tenant_name_and_month.return_value = None
        electricity_bill = self._bill(bill_type="ELECTRICITY", rent=None, water=None, electricity=800.0)

        self.service.add_bill(electricity_bill)

        self.repo.find_by_tenant_name_and_month.assert_called_once_with("Room1", "2026-09", "ELECTRICITY")

    def test_add_bill_rejects_duplicate_same_type(self):
        self.repo.find_by_tenant_name_and_month.return_value = self._bill()
        with self.assertRaises(ValueError):
            self.service.add_bill(self._bill())

    def test_delete_paid_bill_rejected(self):
        self.repo.find_by_id.return_value = self._bill(paid=True)
        with self.assertRaises(PermissionError):
            self.service.delete_bill(1)
        self.repo.delete_by_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()
