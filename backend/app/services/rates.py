from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from ..models import RateRule

class RateStrategy:
    """Strategy pattern: interchangeable rate calculation policy."""
    def calculate(self, fat: float, snf: float, rule: RateRule) -> Decimal:
        raise NotImplementedError

class FatSlabRateStrategy(RateStrategy):
    def calculate(self, fat: float, snf: float, rule: RateRule) -> Decimal:
        rate = Decimal(rule.base_rate) + Decimal(str(fat)) * Decimal(rule.per_fat_rate)
        rate += Decimal(str(max(0, snf - 8.0))) * Decimal(rule.snf_bonus)
        return rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class RateService:
    def __init__(self, db: Session, dairy_id: int, strategy: RateStrategy | None = None):
        self.db, self.dairy_id = db, dairy_id
        self.strategy = strategy or FatSlabRateStrategy()

    def rate_for(self, fat: float, snf: float, milk_type="cow"):
        rule = self.db.query(RateRule).filter(
            RateRule.dairy_id == self.dairy_id,
            RateRule.milk_type == milk_type,
            RateRule.fat_min <= fat, RateRule.fat_max >= fat
        ).first()
        if not rule:
            raise ValueError(f"No {milk_type} rate slab configured for fat {fat}")
        return self.strategy.calculate(fat, snf, rule)
