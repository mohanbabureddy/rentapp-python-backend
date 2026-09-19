import unittest
from types import SimpleNamespace as N

from app.services import ComplaintService


class _Repo:
    def __init__(self, complaint):
        self.complaint = complaint

    def find_by_id(self, _id):
        return self.complaint

    def save(self, c):
        return c


class WithdrawComplaintTest(unittest.TestCase):
    def test_open_complaint_is_closed_with_note(self):
        c = N(id=1, tenant_name="Room1", status="OPEN", closed_date=None, resolution_comment=None)
        out = ComplaintService(_Repo(c)).withdraw_complaint(1)
        self.assertEqual(out.status, "CLOSED")
        self.assertEqual(out.resolution_comment, "Withdrawn by tenant")
        self.assertIsNotNone(out.closed_date)

    def test_already_closed_is_rejected(self):
        c = N(id=1, tenant_name="Room1", status="CLOSED", closed_date=None, resolution_comment="fixed")
        with self.assertRaises(ValueError):
            ComplaintService(_Repo(c)).withdraw_complaint(1)

    def test_missing_complaint(self):
        with self.assertRaises(ValueError):
            ComplaintService(_Repo(None)).withdraw_complaint(99)


if __name__ == "__main__":
    unittest.main()
