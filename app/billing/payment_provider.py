"""
Payment provider abstraction.

The blueprint calls for Razorpay, but a real integration needs a Razorpay
account (KYC, live/test API keys) which this build does not have. Rather than
blocking the whole billing flow, we ship a `MockPaymentProvider` that
reproduces Razorpay's order -> checkout -> webhook shape exactly:

    create_order()  ~= razorpay_client.order.create()
    verify_payment() ~= razorpay.utility.verify_payment_signature()

Both providers implement the same tiny interface, so switching
PAYMENT_PROVIDER=razorpay in .env (plus real keys) is the only change needed
once a Razorpay account exists — no route or frontend code changes.
"""
import hashlib
import hmac
import time
import uuid
from abc import ABC, abstractmethod

from ..config import settings


class PaymentProvider(ABC):
    @abstractmethod
    def create_order(self, amount_usd: int, receipt: str) -> dict:
        ...

    @abstractmethod
    def verify_payment(self, order_id: str, payment_id: str, signature: str | None) -> bool:
        ...

    @abstractmethod
    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        ...


class MockPaymentProvider(PaymentProvider):
    """Free, no external account required. Simulates a successful checkout
    immediately so the entitlement flow (order -> confirm -> webhook ->
    premium unlock) can be built and tested end-to-end today. Swap for
    RazorpayProvider when a real account is available — no other code changes."""

    name = "mock"

    def create_order(self, amount_usd: int, receipt: str) -> dict:
        order_id = f"mock_order_{uuid.uuid4().hex[:12]}"
        return {
            "order_id": order_id,
            "amount": amount_usd * 100,  # store in "cents" like Razorpay's paise convention
            "currency": "USD",
            "checkout_hint": (
                "MOCK MODE: no real payment gateway is connected. Call "
                "POST /billing/confirm with this order_id and any payment_id "
                "to simulate a successful payment and unlock premium."
            ),
        }

    def verify_payment(self, order_id: str, payment_id: str, signature: str | None) -> bool:
        # Mock mode: any non-empty order_id/payment_id is accepted as "paid".
        return bool(order_id and payment_id)

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        return True


class RazorpayProvider(PaymentProvider):
    """Real Razorpay integration. Requires `pip install razorpay` and a real
    RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET in .env.
    Not active until PAYMENT_PROVIDER=razorpay is set with those keys present."""

    name = "razorpay"

    def __init__(self):
        import razorpay  # imported lazily so the mock path never needs this dependency
        self.client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    def create_order(self, amount_usd: int, receipt: str) -> dict:
        order = self.client.order.create({
            "amount": amount_usd * 100,
            "currency": "USD",
            "receipt": receipt,
            "payment_capture": 1,
        })
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "checkout_hint": "Use RAZORPAY_KEY_ID with Razorpay Checkout.js on the frontend to complete payment.",
        }

    def verify_payment(self, order_id: str, payment_id: str, signature: str | None) -> bool:
        try:
            self.client.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
            return True
        except Exception:  # noqa: BLE001
            return False

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        if not signature or not settings.RAZORPAY_WEBHOOK_SECRET:
            return False
        expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def get_payment_provider() -> PaymentProvider:
    if settings.PAYMENT_PROVIDER == "razorpay" and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        return RazorpayProvider()
    return MockPaymentProvider()
