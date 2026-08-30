"""Deterministic paper-trading gate and accounting (no execution adapter)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN


ZERO = Decimal("0")


@dataclass(frozen=True)
class ResearchReadiness:
    matured_prediction_count: int
    baseline_comparison_completed: bool
    leakage_checks_passed: bool
    cost_adjusted_advantage: float
    paired_ci95_low: float
    calibration_ece: float | None


@dataclass(frozen=True)
class PaperGateDecision:
    allowed: bool
    reasons: tuple[str, ...]


def paper_trading_gate(
    readiness: ResearchReadiness,
    *,
    min_matured_predictions: int = 1_000,
    max_calibration_ece: float = 0.1,
) -> PaperGateDecision:
    reasons: list[str] = []
    if readiness.matured_prediction_count < min_matured_predictions:
        reasons.append("insufficient_prediction_history")
    if not readiness.baseline_comparison_completed:
        reasons.append("baseline_comparison_incomplete")
    if not readiness.leakage_checks_passed:
        reasons.append("data_leakage_not_cleared")
    if readiness.cost_adjusted_advantage <= 0 or readiness.paired_ci95_low <= 0:
        reasons.append("cost_adjusted_advantage_not_statistically_positive")
    if (
        readiness.calibration_ece is None
        or readiness.calibration_ece > max_calibration_ece
    ):
        reasons.append("calibration_not_ready")
    return PaperGateDecision(allowed=not reasons, reasons=tuple(reasons))


@dataclass(frozen=True)
class PaperRiskPolicy:
    allowed_subjects: frozenset[str]
    maximum_position_fraction: Decimal = Decimal("0.02")
    maximum_daily_loss_fraction: Decimal = Decimal("0.01")
    maximum_slippage_bps: Decimal = Decimal("50")
    fee_bps: Decimal = Decimal("5")
    emergency_stop: bool = False


@dataclass(frozen=True)
class PaperSignal:
    artifact_id: str
    subject: str
    direction: str
    confidence: Decimal
    observed_price: Decimal
    requested_slippage_bps: Decimal


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    artifact_id: str
    subject: str
    side: str
    quantity: Decimal
    fill_price: Decimal
    notional: Decimal
    fee: Decimal
    realized_pnl: Decimal


@dataclass
class PaperPortfolio:
    initial_cash: Decimal
    cash: Decimal | None = None
    positions: dict[str, Decimal] = field(default_factory=dict)
    average_prices: dict[str, Decimal] = field(default_factory=dict)
    daily_realized_pnl: Decimal = ZERO
    fills: list[PaperFill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.cash is None:
            self.cash = self.initial_cash


class PaperTradeEngine:
    """All sizing, slippage, fees, and PnL are deterministic Decimal math."""

    def __init__(
        self,
        portfolio: PaperPortfolio,
        policy: PaperRiskPolicy,
        gate: PaperGateDecision,
    ):
        self.portfolio = portfolio
        self.policy = policy
        self.gate = gate

    def process(self, signal: PaperSignal) -> PaperFill:
        if not self.gate.allowed:
            raise RuntimeError("paper trading gate is closed: " + ",".join(self.gate.reasons))
        if self.policy.emergency_stop:
            raise RuntimeError("paper emergency stop is active")
        if any(fill.artifact_id == signal.artifact_id for fill in self.portfolio.fills):
            raise ValueError("duplicate prediction artifact")
        if signal.subject not in self.policy.allowed_subjects:
            raise ValueError("subject is not allowlisted")
        if signal.direction not in {"UP", "DOWN"}:
            raise ValueError("paper engine only accepts UP or DOWN directional signals")
        if not ZERO <= signal.confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        if signal.observed_price <= 0:
            raise ValueError("observed_price must be positive")
        if signal.requested_slippage_bps > self.policy.maximum_slippage_bps:
            raise ValueError("slippage limit exceeded")
        loss_limit = self.portfolio.initial_cash * self.policy.maximum_daily_loss_fraction
        if self.portfolio.daily_realized_pnl <= -loss_limit:
            raise RuntimeError("maximum daily paper loss reached")

        equity = self._equity({signal.subject: signal.observed_price})
        max_notional = equity * self.policy.maximum_position_fraction
        confidence_scale = max(ZERO, (signal.confidence - Decimal("0.5")) * Decimal("2"))
        target_notional = (max_notional * confidence_scale).quantize(
            Decimal("0.000001"), rounding=ROUND_DOWN
        )
        if target_notional <= 0:
            raise ValueError("signal confidence does not justify a paper position")
        direction_sign = Decimal("1") if signal.direction == "UP" else Decimal("-1")
        preliminary_target = (
            direction_sign * target_notional / signal.observed_price
        ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        current_quantity = self.portfolio.positions.get(signal.subject, ZERO)
        preliminary_delta = preliminary_target - current_quantity
        if preliminary_delta == 0:
            raise ValueError("paper position already matches deterministic target")
        side = "BUY" if preliminary_delta > 0 else "SELL"
        slip = signal.requested_slippage_bps / Decimal("10000")
        fill_price = signal.observed_price * (Decimal("1") + slip if side == "BUY" else Decimal("1") - slip)
        target_quantity = (direction_sign * target_notional / fill_price).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )
        quantity = target_quantity - current_quantity
        if quantity == 0:
            raise ValueError("paper position already matches deterministic target")
        notional = abs(quantity * fill_price)
        fee = notional * self.policy.fee_bps / Decimal("10000")
        projected_realized = self._realized_before_fee(
            signal.subject, quantity, fill_price
        ) - fee
        if self.portfolio.daily_realized_pnl + projected_realized < -loss_limit:
            raise RuntimeError("paper fill would exceed maximum daily loss")
        assert self.portfolio.cash is not None
        projected_cash = self.portfolio.cash - quantity * fill_price - fee
        if projected_cash < 0:
            raise RuntimeError("insufficient paper cash balance")
        realized = self._apply_position(signal.subject, quantity, fill_price, fee)
        fill = PaperFill(
            fill_id=f"paper-{len(self.portfolio.fills) + 1:08d}",
            artifact_id=signal.artifact_id,
            subject=signal.subject,
            side=side,
            quantity=quantity,
            fill_price=fill_price,
            notional=notional,
            fee=fee,
            realized_pnl=realized,
        )
        self.portfolio.fills.append(fill)
        return fill

    def _realized_before_fee(
        self,
        subject: str,
        quantity_delta: Decimal,
        fill_price: Decimal,
    ) -> Decimal:
        old_quantity = self.portfolio.positions.get(subject, ZERO)
        old_average = self.portfolio.average_prices.get(subject, ZERO)
        if not old_quantity or old_quantity * quantity_delta >= 0:
            return ZERO
        closing = min(abs(old_quantity), abs(quantity_delta))
        direction = Decimal("1") if old_quantity > 0 else Decimal("-1")
        return closing * (fill_price - old_average) * direction

    def _apply_position(
        self,
        subject: str,
        quantity_delta: Decimal,
        fill_price: Decimal,
        fee: Decimal,
    ) -> Decimal:
        old_quantity = self.portfolio.positions.get(subject, ZERO)
        old_average = self.portfolio.average_prices.get(subject, ZERO)
        new_quantity = old_quantity + quantity_delta
        realized = self._realized_before_fee(subject, quantity_delta, fill_price)
        if new_quantity == 0:
            new_average = ZERO
        elif old_quantity == 0 or old_quantity * quantity_delta > 0:
            new_average = (
                abs(old_quantity) * old_average + abs(quantity_delta) * fill_price
            ) / abs(new_quantity)
        elif old_quantity * new_quantity < 0:
            new_average = fill_price
        else:
            new_average = old_average
        assert self.portfolio.cash is not None
        self.portfolio.cash -= quantity_delta * fill_price + fee
        self.portfolio.positions[subject] = new_quantity
        self.portfolio.average_prices[subject] = new_average
        self.portfolio.daily_realized_pnl += realized - fee
        return realized - fee

    def _equity(self, prices: dict[str, Decimal]) -> Decimal:
        assert self.portfolio.cash is not None
        value = self.portfolio.cash
        for subject, quantity in self.portfolio.positions.items():
            if subject not in prices:
                continue
            value += quantity * prices[subject]
        return value


class RealExecutionBoundary:
    """Explicit Phase-5 seam.  No live execution implementation is present."""

    enabled = False

    def submit(self, *_args, **_kwargs):
        raise RuntimeError(
            "real execution is disabled; a separately reviewed deterministic risk engine "
            "and execution adapter are required"
        )
