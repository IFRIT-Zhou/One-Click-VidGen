"""Create referral, registration verification, and expiring reward tables."""
from app.bonus_credits import BonusCreditGrant, BonusCreditReservation
from app.db import Base, engine
from app.referral_program import (
    PromotionClaim,
    ReferralAttribution,
    ReferralProfile,
    RegistrationEmailCode,
)


if __name__ == "__main__":
    Base.metadata.create_all(engine, tables=[
        BonusCreditGrant.__table__, BonusCreditReservation.__table__,
        ReferralProfile.__table__, ReferralAttribution.__table__,
        RegistrationEmailCode.__table__, PromotionClaim.__table__,
    ])
    print("Referral program schema is ready.")
