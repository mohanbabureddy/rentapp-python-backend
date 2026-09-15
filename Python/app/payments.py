import logging
import os
from typing import Any, Dict

import razorpay

from app.models import TenantBill
from app.repositories import TenantBillRepository

logger = logging.getLogger("app.payments")


class PaymentService:
    """Server-side half of the Razorpay flow. Without this, markPaid had to trust
    whatever the browser told it -- anyone could mark any bill paid with no payment
    at all. Now a bill can only be marked paid after we've created its Razorpay order
    ourselves (so the amount can't be tampered with client-side), verified the
    HMAC signature Razorpay returns, and confirmed with Razorpay's own API that the
    order was actually issued for this bill and the payment actually captured."""

    def __init__(self, bill_repo: TenantBillRepository):
        self.bill_repo = bill_repo
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        self._client = razorpay.Client(auth=(key_id, key_secret)) if key_id and key_secret else None
        self._key_id = key_id

    @staticmethod
    def _bill_amount_paise(bill: TenantBill) -> int:
        total = (bill.rent or 0) + (bill.water or 0) + (bill.electricity or 0)
        return round(total * 100)

    def create_order(self, bill_id: int) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Payments aren't configured (missing RAZORPAY_KEY_ID/SECRET in .env).")
        bill = self.bill_repo.find_by_id(bill_id)
        if bill is None:
            raise ValueError("Bill not found")
        if bill.paid:
            raise ValueError("Bill is already paid")
        amount = self._bill_amount_paise(bill)
        if amount <= 0:
            raise ValueError("Bill amount is zero; nothing to pay")

        order = self._client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"bill-{bill_id}",
            "notes": {"billId": str(bill_id), "tenantName": bill.tenant_name or ""},
        })
        logger.info("Created Razorpay order %s for bill %s (tenant=%s, amount=%s paise).",
                    order["id"], bill_id, bill.tenant_name, amount)
        return {"orderId": order["id"], "amount": amount, "currency": "INR", "keyId": self._key_id}

    def verify_payment(self, bill_id: int, order_id: str, payment_id: str, signature: str) -> None:
        if self._client is None:
            raise RuntimeError("Payments aren't configured (missing RAZORPAY_KEY_ID/SECRET in .env).")
        if not order_id or not payment_id or not signature:
            raise ValueError("orderId, paymentId, and signature are all required")

        bill = self.bill_repo.find_by_id(bill_id)
        if bill is None:
            raise ValueError("Bill not found")

        try:
            self._client.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
        except razorpay.errors.SignatureVerificationError:
            logger.warning("Razorpay signature verification FAILED for bill %s (order=%s, payment=%s).",
                            bill_id, order_id, payment_id)
            raise ValueError("Payment signature verification failed")

        # The signature alone only proves this order/payment pair is authentic --
        # it doesn't prove the order was for THIS bill, or that Razorpay's servers
        # actually have a matching, captured payment on file. Without these checks, a
        # signature from a real (but different, e.g. much cheaper) paid order -- or
        # even a correctly-HMAC'd but entirely fabricated order/payment id pair --
        # could be used to mark an unrelated or unpaid bill as paid.
        try:
            order = self._client.order.fetch(order_id)
            payment = self._client.payment.fetch(payment_id)
        except (razorpay.errors.BadRequestError, razorpay.errors.ServerError, razorpay.errors.GatewayError) as exc:
            logger.warning("Razorpay lookup failed for bill %s (order=%s, payment=%s): %s", bill_id, order_id, payment_id, exc)
            raise ValueError("Could not verify this payment with Razorpay")

        if str((order.get("notes") or {}).get("billId")) != str(bill_id):
            logger.warning("Razorpay order %s was not issued for bill %s (notes=%s).", order_id, bill_id, order.get("notes"))
            raise ValueError("Payment does not match this bill")

        expected_amount = self._bill_amount_paise(bill)
        if int(order.get("amount", -1)) != expected_amount:
            logger.warning("Razorpay order %s amount %s does not match bill %s expected amount %s.",
                            order_id, order.get("amount"), bill_id, expected_amount)
            raise ValueError("Payment amount does not match bill amount")

        if payment.get("status") != "captured":
            logger.warning("Razorpay payment %s for bill %s is not captured (status=%s).", payment_id, bill_id, payment.get("status"))
            raise ValueError("Payment was not captured")

        logger.info("Verified Razorpay payment %s (order=%s) for bill %s.", payment_id, order_id, bill_id)
