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


def _bill_service(bills):
    users = N(find_by_username=lambda u: N(username=u, full_name="Ravindra", demanded_deposit=None))
    bill_repo = N(find_by_tenant_name_order_by_month_desc=lambda u: bills)
    return AssistantService(bill_repo, users, N(total_for_tenant=lambda u: 0))


def _bill(month, kind, paid, rent=0, water=0, elec=0, misc=None):
    return N(month_year=month, bill_type=kind, paid=paid, rent=rent, water=water, electricity=elec, miscellaneous=misc)


class BillsReplyTest(unittest.TestCase):
    def test_lists_unpaid_with_total_due(self):
        bills = [
            _bill("2026-09", "RENT", False, rent=10000, water=300, misc=200),
            _bill("2026-09", "ELECTRICITY", False, elec=850),
            _bill("2026-08", "RENT", True, rent=10000, water=300),
        ]
        reply = _bill_service(bills).ask("Room1", "My bills")
        self.assertIn("2026-09 Rent: Rs.10,500 (rent Rs.10,000, water Rs.300, misc Rs.200)", reply)
        self.assertIn("2026-09 Electricity: Rs.850", reply)
        self.assertIn("Total due: Rs.11,350", reply)
        self.assertIn("Recently paid:", reply)
        self.assertIn("2026-08 Rent: Rs.10,300", reply)

    def test_all_paid(self):
        reply = _bill_service([_bill("2026-08", "RENT", True, rent=1000)]).ask("Room1", "what do I owe")
        self.assertIn("no unpaid bills", reply)

    def test_no_bills(self):
        self.assertEqual(_bill_service([]).ask("Room1", "my bills"), "You have no bills yet.")


class DepositRefundTest(unittest.TestCase):
    def test_refund_question_gets_fixed_reply_with_owner_contact(self):
        users = N(
            find_by_username=lambda u: N(username=u, full_name="Ravindra", demanded_deposit=20000.0),
            find_all=lambda: [N(role="ADMIN", username="mohan", full_name="Mohanbabu G", phone="9876543210", mail="o@x.com")],
        )
        svc = AssistantService(None, users, N(total_for_tenant=lambda u: 0, find_by_tenant_order_by_date_desc=lambda u: []))
        for q in ("withdraw my deposit", "refund my security deposit", "I want my deposit back"):
            reply = svc.ask("Room1", q)
            self.assertIn("refunds are not processed in this app", reply, q)
            self.assertIn("Mohanbabu G, phone 9876543210, email o@x.com", reply, q)


if __name__ == "__main__":
    unittest.main()
