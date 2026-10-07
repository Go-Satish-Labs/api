from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from .payment_provider import get_payment_provider

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/create-order", response_model=schemas.CreateOrderResponse)
def create_order(
    payload: schemas.CreateOrderRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if settings.PREMIUM_PRICE_PAISE < 100:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Configured payment amount must be at least 100 paise")

    try:
        provider = get_payment_provider()
        order = provider.create_order(settings.PREMIUM_PRICE_PAISE, receipt=f"user:{user.id}")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Razorpay is not configured on the backend") from exc
    except Exception as exc:
        provider_status = getattr(exc, "status_code", None)
        if provider_status == 401:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Razorpay authentication failed") from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unable to create Razorpay order") from exc

    sub = db.query(models.Subscription).filter(models.Subscription.user_id == user.id).first()
    if not sub:
        sub = models.Subscription(user_id=user.id)
        db.add(sub)
    sub.provider = provider.name
    sub.provider_order_id = order["order_id"]
    sub.status = "pending"
    db.commit()

    return schemas.CreateOrderResponse(
        provider=provider.name,
        order_id=order["order_id"],
        amount=order["amount"],
        currency=order["currency"],
        checkout_hint=order["checkout_hint"],
    )


@router.post("/confirm")
def confirm_payment(
    payload: schemas.ConfirmPaymentRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Client-initiated confirmation. This alone is NOT trusted for real
    money — verify_payment() checks the provider's signature (Razorpay) or,
    in mock mode, accepts it for local testing. The webhook below is the
    authoritative source for a real deployment."""
    provider = get_payment_provider()
    sub = db.query(models.Subscription).filter(models.Subscription.user_id == user.id).first()
    if not sub or sub.provider_order_id != payload.order_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown order")

    if not payload.order_id or not payload.payment_id or not payload.signature:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "order_id, payment_id, and signature are required")

    if not provider.verify_payment(payload.order_id, payload.payment_id, payload.signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment verification failed")

    sub.plan = "premium"
    sub.status = "active"
    sub.provider_payment_id = payload.payment_id
    sub.current_period_end = datetime.utcnow() + timedelta(days=30)
    sub.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Premium activated", "plan": sub.plan, "current_period_end": sub.current_period_end}


@router.post("/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """Authoritative entitlement update path (blueprint section 11: 'Payment
    provider webhook updates subscription status'). In mock mode this just
    logs; wire the real Razorpay webhook URL here once an account exists."""
    provider = get_payment_provider()
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not provider.verify_webhook(body, signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    payload = await request.json()
    order_id = (
        payload.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
        or payload.get("order_id")
    )
    if not order_id:
        return {"message": "No order_id in payload, ignored"}

    sub = db.query(models.Subscription).filter(models.Subscription.provider_order_id == order_id).first()
    if sub:
        sub.plan = "premium"
        sub.status = "active"
        sub.current_period_end = datetime.utcnow() + timedelta(days=30)
        db.commit()
    return {"message": "ok"}


@router.get("/subscription", response_model=schemas.SubscriptionOut)
def get_subscription(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.query(models.Subscription).filter(models.Subscription.user_id == user.id).first()
    if not sub:
        return schemas.SubscriptionOut(plan="free", status="active", provider="none")
    return schemas.SubscriptionOut(
        plan=sub.plan, status=sub.status, provider=sub.provider, current_period_end=sub.current_period_end
    )


@router.post("/cancel")
def cancel_subscription(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.query(models.Subscription).filter(models.Subscription.user_id == user.id).first()
    if sub:
        sub.status = "cancelled"
        db.commit()
    return {"message": "Subscription cancelled; premium access continues until period end"}
