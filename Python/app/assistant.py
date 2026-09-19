import logging
import os
from typing import List, Optional

import anthropic
import requests

from app.models import TenantBill, User
from app.repositories import DepositRepository, TenantBillRepository, UserRepository

logger = logging.getLogger("app.assistant")

MODEL = "claude-opus-5"


class AssistantService:
    """Answers a tenant's free-form questions (e.g. "who do I pay rent to") using
    only that tenant's own bill data -- never another tenant's, and never anything
    fabricated (bank/UPI details the app doesn't actually have on file)."""

    def __init__(self, bill_repo: TenantBillRepository, user_repo: UserRepository, deposit_repo: DepositRepository):
        self.bill_repo = bill_repo
        self.deposit_repo = deposit_repo
        self.user_repo = user_repo
        self._provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        self._ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self._ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def _ask_ollama(self, system_prompt: str, message: str) -> str:
        resp = requests.post(
            f"{self._ollama_url}/api/chat",
            json={
                "model": self._ollama_model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")

    def _admin_contact(self) -> Optional[User]:
        for user in self.user_repo.find_all():
            if user.role == "ADMIN":
                return user
        return None

    def _build_system_prompt(self, tenant: User, bills: List[TenantBill]) -> str:
        admin = self._admin_contact()
        recent = sorted(bills, key=lambda b: b.month_year or "", reverse=True)[:12]
        bill_lines = []
        for b in recent:
            total = (b.rent or 0) + (b.water or 0) + (b.electricity or 0) + (b.miscellaneous or 0)
            status = "PAID" if b.paid else "UNPAID"
            if b.bill_type == "ELECTRICITY":
                bill_lines.append(
                    f"- {b.month_year} ELECTRICITY bill: amount=Rs.{b.electricity or 0}, status={status}"
                )
            else:
                bill_lines.append(
                    f"- {b.month_year} RENT bill: rent=Rs.{b.rent or 0}, water=Rs.{b.water or 0}, "
                    f"miscellaneous=Rs.{b.miscellaneous or 0}, total=Rs.{total}, status={status}"
                )
        bills_block = "\n".join(bill_lines) if bill_lines else "No bills on record."

        admin_block = (
            f"Property manager / admin contact: username={admin.username}, "
            f"email={admin.mail or 'not on file'}, phone={admin.phone or 'not on file'}."
            if admin else "No admin contact is on file."
        )

        demanded = tenant.demanded_deposit
        paid_deposit = self.deposit_repo.total_for_tenant(tenant.username)
        if demanded is None:
            deposit_block = f"Security deposit: paid so far Rs.{paid_deposit}; no demanded amount is set."
        else:
            deposit_block = (
                f"Security deposit: demanded Rs.{demanded}, paid so far Rs.{paid_deposit}, "
                f"remaining Rs.{max(demanded - paid_deposit, 0)}. The tenant can pay deposit "
                "in instalments using 'Pay Deposit' at the top of the My Bills page."
            )

        return (
            "You are a helpful assistant inside a rent management app, answering only "
            f"for the tenant '{tenant.username}'. Use ONLY the data below -- never invent "
            "bank account numbers, UPI IDs, or any payment detail that isn't given here. "
            "Rent and electricity are separate bills, each paid entirely inside this app via the 'Pay' button on the tenant's "
            "Bills page (a Razorpay checkout popup); there is no separate bank transfer or "
            "UPI payment to make. If asked something this data doesn't cover, say so honestly "
            "instead of guessing. Keep answers short and direct.\n\n"
            f"{admin_block}\n\n{deposit_block}\n\n"
            f"Tenant's bill history (most recent first):\n{bills_block}"
        )

    def ask(self, username: str, message: str) -> str:
        if self._provider != "ollama" and self._client is None:
            raise RuntimeError("The assistant isn't configured yet (missing ANTHROPIC_API_KEY in .env).")
        if not message or not message.strip():
            raise ValueError("Message required")

        tenant = self.user_repo.find_by_username(username)
        if tenant is None:
            raise ValueError("Tenant not found")
        bills = self.bill_repo.find_by_tenant_name_order_by_month_desc(username)
        system_prompt = self._build_system_prompt(tenant, bills)

        logger.info("Assistant question from '%s': %s", username, message[:200])
        try:
            if self._provider == "ollama":
                answer = self._ask_ollama(system_prompt, message.strip())
            else:
                response = self._client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=system_prompt,
                    output_config={"effort": "low"},
                    messages=[{"role": "user", "content": message.strip()}],
                )
                answer = next((block.text for block in response.content if block.type == "text"), "")
        except (anthropic.APIError, requests.RequestException):
            logger.exception("Assistant API call failed for '%s'.", username)
            raise RuntimeError("Assistant is temporarily unavailable. Please try again shortly.")

        logger.info("Assistant answered '%s' (%d chars).", username, len(answer))
        return answer
