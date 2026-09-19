import unittest
from datetime import datetime
from types import SimpleNamespace as N

from app.assistant import AssistantService


def _service(entries, demanded):
    deposit_repo = N(
        find_by_tenant_order_by_date_desc=lambda u: sorted(entries, key=lambda e: e.paid_date, reverse=True),
        total_for_tenant=lambda u: sum(e.amount for e in entries),
    )
    users = N(find_by_username=lambda u: N(username=u, full_name="Ravindra", demanded_deposit=demanded))
    return AssistantService(None, users, deposit_repo)


class DepositReplyTest(unittest.TestCase):
    def test_lists_payments_date_wise_with_totals(self):
        entries = [
            N(amount=1.0, source="razorpay", notes=None, paid_date=datetime(2026, 9, 17, 7, 17)),
            N(amount=100.0, source="manual", notes="cash", paid_date=datetime(2026, 9, 16, 7, 16)),
        ]
        svc = _service(entries, 20000.0)
        reply = svc.ask("Room1", "show my deposit history")
        self.assertIn("Demanded: Rs.20,000", reply)
        self.assertIn("Paid so far: Rs.101", reply)
        self.assertIn("Remaining: Rs.19,899", reply)
        lines = reply.splitlines()
        first = next(i for i, l in enumerate(lines) if "16 Sep 2026" in l)
        second = next(i for i, l in enumerate(lines) if "17 Sep 2026" in l)
        self.assertLess(first, second)
        self.assertIn("Recorded by owner (cash)", reply)
        self.assertIn("Paid online", reply)

    def test_no_payments_no_demand(self):
        reply = _service([], None).ask("Room1", "how much advance did I pay")
        self.assertIn("No deposit payments recorded yet.", reply)
        self.assertIn("no demanded amount", reply)

    def test_deposit_question_needs_no_llm_key(self):
        # Must answer even when no LLM is configured at all.
        reply = _service([], 5000.0).ask("Room1", "deposit balance?")
        self.assertIn("Remaining: Rs.5,000", reply)


if __name__ == "__main__":
    unittest.main()
