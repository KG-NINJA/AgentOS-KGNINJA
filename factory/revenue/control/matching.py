"""Match observed engineering demand to explicit executor capabilities.

No model reputation, fabricated assignment probability or revenue inference.
Commerce buyer matching remains the existing HyperXosist matcher.
"""
from .contracts import ControlError, fields, integer, require, text
from ..sources import instant


def match(c, actor, demand):
    from .contracts import role
    role(actor, "agent_operator")
    fields(demand, ("languages", "max_cash_microusd", "max_work_minutes", "max_human_minutes", "max_results"))
    require(type(demand["languages"]) is list and 1 <= len(demand["languages"]) <= 10, "CAPABILITIES_REQUIRED", 400)
    for language in demand["languages"]:
        text(language, 100)
    for key in ("max_cash_microusd", "max_work_minutes", "max_human_minutes"):
        integer(demand[key])
    require(type(demand["max_results"]) is int and 1 <= demand["max_results"] <= 5, "INVALID_RESULT_LIMIT", 400)
    candidates, rejected = [], []
    for row in c.get(actor, "opportunities"):
        o = row["observation"]
        try:
            c._eligible(o, "engineering")
            require(set(o["work_scope"]["languages"]) <= set(demand["languages"]), "EXECUTOR_CAPABILITY_MISMATCH")
            e = o["estimates"]
            require(e["cost_basis_ref"] and all(e[k] is not None for k in ("compute_cost_upper_microusd", "other_cash_cost_upper_microusd", "runner_minutes_upper", "human_minutes_upper")), "COST_UNKNOWN")
            cash = e["compute_cost_upper_microusd"] + e["other_cash_cost_upper_microusd"]
            require(cash <= min(demand["max_cash_microusd"], c.policy["cash_cap_microusd"])
                    and e["runner_minutes_upper"] <= min(demand["max_work_minutes"], c.policy["work_cap_minutes"])
                    and e["human_minutes_upper"] <= min(demand["max_human_minutes"], c.policy["human_cap_minutes"]), "BUDGET_MISMATCH")
            require(0 <= c.clock() - instant(o["source"]["observed_at"]) <= 86400, "OBSERVATION_TOO_OLD")
            candidates.append({"opportunity_id": row["id"], "observation_sha256": row["observation_sha256"], "cash_cap_microusd": cash,
                               "work_minutes": e["runner_minutes_upper"], "human_minutes": e["human_minutes_upper"], "reward": o["reward"],
                               "requires_fresh_source_and_owner_approval": True})
        except ControlError as exc:
            rejected.append({"opportunity_id": row["id"], "reason": exc.code})
    candidates.sort(key=lambda x: (x["cash_cap_microusd"], x["human_minutes"], x["work_minutes"], x["opportunity_id"]))
    from .engine import stamp
    return {"matches": candidates[:demand["max_results"]], "rejected": rejected, "ranking": "lowest_bounded_cost_then_time", "evaluated_at": stamp(c.clock()), "policy_sha256": c.fingerprint,
            "commitment_authorized": False, "real_revenue_created": False}
