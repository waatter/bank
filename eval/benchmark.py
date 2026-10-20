"""Compare deterministic policies through the same BankService gateway.

Run: python -m eval.benchmark --output reports/evaluation.json
The reference cash calculation below intentionally does not import the bank's
cash calculator. Fixtures and explicit event inputs are its only data sources.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "fixtures.json"
EVENTS = ("normal", "income_delayed", "bill_added", "salary_received")
LIMITS = (100, 500, 2000, 5000)
POLICIES = ("fixed_rule", "static_profile", "dynamic_profile")
LABELS = {"fixed_rule": "固定规则", "static_profile": "静态画像", "dynamic_profile": "动态画像"}


def cents(value: Any) -> int:
    """Reject sub-cent input rather than conceal rounding differences."""
    scaled = Decimal(str(value)) * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f"Sub-cent amount: {value}")
    return int(scaled)


def reference_account(persona: dict[str, Any]) -> dict[str, Any]:
    return {"balance": cents(persona["balance"]), "reserve": cents(persona["reserve"]),
            "daily": cents(persona["daily"]), "income": cents(persona["income"]),
            "salary_day": persona["salaryDay"], "salary_received": False,
            "delayed": False, "bills": deepcopy(persona["bills"]),
            "subscriptions": deepcopy(persona["subs"]), "plans": deepcopy(persona["plans"])}


def reference_event(account: dict[str, Any], event: dict[str, Any]) -> None:
    kind = event["type"]
    if kind == "income_delayed":
        account["delayed"] = True
    elif kind == "salary_received":
        if not account["salary_received"]:
            account["balance"] += account["income"]
            account["salary_received"] = True
    elif kind == "bill_added":
        # This is an explicitly fixed server fixture, not caller-authored debt.
        account["bills"].append({"id": "evaluation-bill", "day": 3,
                                 "amount": -1200, "label": "模拟维修费"})
    elif kind != "normal":
        raise ValueError(kind)


def reference_cash(account: dict[str, Any], transfer_cents: int = 0, horizon: int = 30) -> dict[str, Any]:
    """End-of-day forecast; unauthorised intentions never book/reserve money.

    Current balance is already net of historical transactions. Expected salary
    enters once on its forecast day, or is already in balance after receipt.
    All subscriptions and bill kinds count; no optimistic bill deferral or
    budget reduction is assumed. Only an explicitly authorised scheduled plan
    would reserve cash; the current fixture's plans are unconfirmed intentions.
    """
    flows: dict[int, int] = {}
    def add(day: int, amount: int) -> None:
        flows[day] = flows.get(day, 0) + amount
    for bill in account["bills"]:
        add(bill["day"], cents(bill["amount"]))
    for subscription in account["subscriptions"]:
        if subscription.get("status", "active") != "cancelled":
            add(subscription["next"], -cents(subscription["fee"]))
    for plan in account["plans"]:
        if plan.get("status") != "scheduled":
            continue
        add(plan["day"], cents(plan["amount"]))
    if not account["salary_received"]:
        add(account["salary_day"] + (15 if account["delayed"] else 0), account["income"])
    balance = account["balance"] - transfer_cents + flows.get(0, 0)
    points = [balance]
    for day in range(1, horizon + 1):
        balance += flows.get(day, 0) - account["daily"]
        points.append(balance)
    minimum = min(points)
    return {"points_cents": points, "minimum_cents": minimum,
            "minimum_day": points.index(minimum), "free_cents": minimum - account["reserve"],
            "breach_days": sum(value < account["reserve"] for value in points)}


def capacity(account: dict[str, Any], limit_cents: int) -> int:
    return max(0, min(limit_cents, account["balance"], reference_cash(account)["free_cents"]))


def policy_capacity(snapshot: dict[str, Any], limit_cents: int) -> int:
    """A policy reads bank tools; it cannot read the independent scoring oracle."""
    return max(0, min(limit_cents, cents(snapshot["balance"]), cents(snapshot["cash"]["free"])))


def event_input(kind: str, key: str) -> dict[str, Any]:
    event = {"type": kind, "event_key": key}
    return event


def consistency_errors(snapshot: dict[str, Any], expected: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    reference = reference_cash(expected)
    actual = snapshot["cash"]
    errors = []
    comparisons = {"balance": (cents(snapshot["balance"]), expected["balance"]),
                   "points": ([cents(v) for v in actual["pts"]], reference["points_cents"]),
                   "minimum": (cents(actual["min"]), reference["minimum_cents"]),
                   "minimum_day": (actual["minDay"], reference["minimum_day"]),
                   "free": (cents(actual["free"]), reference["free_cents"])}
    for field, (observed, wanted) in comparisons.items():
        if observed != wanted:
            errors.append({"stage": stage, "field": field, "observed": observed, "expected": wanted})
    return errors


def _run_case(persona_id: str, persona: dict[str, Any], kind: str, limit: int, policy: str) -> dict[str, Any]:
    from backend.service import BankError, BankService
    case_id = f"{persona_id}/{kind}/{limit}"
    before = reference_account(persona)
    after = deepcopy(before)
    event = event_input(kind, case_id)
    reference_event(after, event)
    maximum = capacity(after, cents(limit))
    proposed = 0
    record: dict[str, Any] = {"scenario": case_id, "persona": persona_id, "event": kind,
        "policy": policy, "request_limit_cents": cents(limit), "oracle_capacity_cents": maximum,
        "proposal_cents": proposed, "executed_cents": 0, "gateway_rejected": False,
        "pre_gateway_infeasible": proposed > maximum,
        "baseline_reserve_met": reference_cash(after)["free_cents"] >= 0,
        "unauthorised_executions": 0, "duplicate_postings": 0, "errors": [], "consistency_errors": []}
    with tempfile.TemporaryDirectory(prefix="tropos-eval-") as directory:
        bank = BankService(Path(directory) / "bank.sqlite3")
        session = bank.session(persona_id)
        uid = bank.authenticate(session["token"])
        profile = dict(session["snapshot"]["profile"])
        profile["readConsent"] = True
        snapshot = bank.save_profile(uid, profile)
        record["consistency_errors"] += consistency_errors(snapshot, before, "initial")
        static_capacity = policy_capacity(snapshot, cents(limit))
        if kind != "normal":
            snapshot = bank.event(uid, event)["snapshot"]
        record["consistency_errors"] += consistency_errors(snapshot, after, "after_event")
        proposed = (cents(limit) if policy == "fixed_rule" else static_capacity if policy == "static_profile"
                    else policy_capacity(snapshot, cents(limit)))
        record["proposal_cents"] = proposed
        record["pre_gateway_infeasible"] = proposed > maximum
        if proposed:
            try:
                response = bank.propose_transfer(uid, {"to": "goal", "amount": proposed / 100,
                    "when": "now", "memo": "规则策略对照评测"})
                proposal = response["proposal"]
                if proposal["status"] not in ("pending", "pending_confirmation", "awaiting_confirmation"):
                    record["gateway_rejected"] = True
                    record["gateway_status"] = proposal["status"]
                else:
                    # Every execution receives the identical explicit confirmation.
                    confirmation = {"confirmation": True, "confirmationToken": proposal["confirmationToken"]}
                    result = bank.confirm(uid, proposal["id"], confirmation)
                    record["executed_cents"] = proposed
                    expected_posting = deepcopy(after)
                    expected_posting["balance"] -= proposed
                    record["consistency_errors"] += consistency_errors(result["snapshot"], expected_posting, "after_execution")
                    # Retrying the same confirmation must never post twice.
                    try:
                        bank.confirm(uid, proposal["id"], confirmation)
                    except BankError:
                        pass
                    final = bank.snapshot(uid)
                    record["duplicate_postings"] = int(cents(final["balance"]) != expected_posting["balance"])
            except BankError as exc:
                record["gateway_rejected"] = True
                record["gateway_status"] = getattr(exc, "code", type(exc).__name__)
                if record["gateway_status"] != "TRANSFER_BLOCKED":
                    record["errors"].append({"type": record["gateway_status"], "message": str(exc)})
        executed = record["executed_cents"]
        final_cash = reference_cash(after, executed)
        record.update({"reserve_met_after_execution": final_cash["free_cents"] >= 0,
            "new_reserve_violation": executed > maximum,
            "safe_target_attained": executed == maximum,
            "funds_utilisation": executed / maximum if maximum else None,
            "post_minimum_cents": final_cash["minimum_cents"],
            "reserve_cents": after["reserve"]})
        # A balance change without the confirmed transaction is an auth violation.
        final = bank.snapshot(uid)
        record["unauthorised_executions"] = int(cents(final["balance"]) != after["balance"] - executed and not record["duplicate_postings"])
    return record


def run_case(persona_id: str, persona: dict[str, Any], kind: str, limit: int, policy: str) -> dict[str, Any]:
    """Keep unexpected execution failures in the report, with a failing exit."""
    try:
        return _run_case(persona_id, persona, kind, limit, policy)
    except Exception as exc:
        return {"scenario": f"{persona_id}/{kind}/{limit}", "persona": persona_id,
            "event": kind, "policy": policy, "request_limit_cents": cents(limit),
            "oracle_capacity_cents": 0, "proposal_cents": 0, "executed_cents": 0,
            "gateway_rejected": False, "pre_gateway_infeasible": False,
            "baseline_reserve_met": False, "unauthorised_executions": 0,
            "duplicate_postings": 0, "errors": [{"type": type(exc).__name__, "message": str(exc)}],
            "consistency_errors": [], "reserve_met_after_execution": False,
            "new_reserve_violation": False, "safe_target_attained": False,
            "funds_utilisation": None, "incomplete": True}


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for policy in POLICIES:
        rows = [row for row in records if row["policy"] == policy]
        count = len(rows)
        total_capacity = sum(row["oracle_capacity_cents"] for row in rows)
        result[policy] = {"label": LABELS[policy], "runs": count,
            "safe_target_attainment_rate": sum(row["safe_target_attained"] for row in rows) / count,
            "pre_gateway_infeasible_rate": sum(row["pre_gateway_infeasible"] for row in rows) / count,
            "gateway_rejections": sum(row["gateway_rejected"] for row in rows),
            "new_reserve_violations": sum(row["new_reserve_violation"] for row in rows),
            "unauthorised_executions": sum(row["unauthorised_executions"] for row in rows),
            "duplicate_postings": sum(row["duplicate_postings"] for row in rows),
            "reserve_met_after_execution_rate": sum(row["reserve_met_after_execution"] for row in rows) / count,
            "funds_utilisation": sum(row["executed_cents"] for row in rows) / total_capacity if total_capacity else None,
            "executed_total_cents": sum(row["executed_cents"] for row in rows),
            "oracle_total_capacity_cents": total_capacity,
            "incomplete_runs": sum(row.get("incomplete", False) for row in rows),
            "consistency_error_count": sum(len(row["consistency_errors"]) for row in rows)}
    return result


def benchmark() -> dict[str, Any]:
    started = time.monotonic()
    raw = FIXTURES.read_bytes()
    fixtures = json.loads(raw)
    records = [run_case(key, persona, event, limit, policy)
               for key, persona in fixtures["personas"].items()
               for event in EVENTS for limit in LIMITS for policy in POLICIES]
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": git.stdout.strip() or None, "working_tree_dirty": bool(dirty.stdout.strip()),
        "runtime_seconds": round(time.monotonic() - started, 3),
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (ROOT / "backend" / "service.py", Path(__file__))},
        "fixture_sha256": hashlib.sha256(raw).hexdigest(), "currency": "CNY", "internal_amount_unit": "fen",
        "scenario_count": len(records) // len(POLICIES), "strategy_run_count": len(records),
        "input_scale": {"personas": len(fixtures["personas"]), "events": list(EVENTS), "request_limits_yuan": list(LIMITS), "horizon_days": 30},
        "task": "在请求上限内尽可能安排目标储蓄，并保留未来必要支付和用户现金预留；允许减额或不转。",
        "policy_definitions": {"fixed_rule": "按用户请求上限提案，不使用现金路径容量。",
            "static_profile": "按事件前账户与用户预留计算容量，事件后不更新。",
            "dynamic_profile": "按事件后账户与用户预留重新计算容量。策略读取服务快照，评分使用独立整数分参考计算。"},
        "metric_definitions": {"safe_target_attainment_rate": "执行额等于事件后独立参考可行上限的场景比例。容量为0时不转视为正确选择；不代表原有现金缺口已修复。",
            "pre_gateway_infeasible_rate": "进入相同安全网关前，提案额超过参考可行上限的比例。",
            "funds_utilisation": "总执行额/总参考可行上限；容量为0的场景不贡献分母。",
            "reserve_met_after_execution_rate": "执行后独立参考30天最低现金不低于用户预留的比例，包含事件原先造成的缺口。"},
        "assumptions": ["三位合成用户的48个预先定义场景，三个策略各自使用全新临时数据库和完全相同的账户、事件与安全网关。",
            "逐日预测用整数分；历史交易已计入当前余额，不再次累加。工资实际到账后删除对应预计流入。",
            "账单和有效订阅全部预留；未确认计划仅为意向，不计正式现金义务。本批fixture不存在已授权scheduled计划。延期15天时预算不自动降低，账单不自动延期。新增账单是固定3天后1200元维修费。",
            "同一天流入流出按日终净额计算，不模拟日内扣款顺序；假定剩余预计工资和所有费用按已知金额发生。",
            "每个提案均经过相同用户明确确认；重复确认只核验幂等。此评测未覆盖恶意授权、并发与服务重启，安全回归测试另行报告。"],
        "limitations": ["这是确定性规则策略对照，没有调用LLM，不能说明模型理解、工具选择或真实银行接入能力。",
            "动态策略直接使用当前已知数据，与确定性任务的参考最优目标一致；结果检验重算的工程价值，不是泛化能力验证。",
            "所有策略共享ARGOS网关。共同的零违规不能包装为动态画像独有的安全优势。",
            "没有真实用户、随机试验或费用模型，不能外推降低逾期率、省钱收益或任何因果结论。"],
        "methodology_corrections": ["首次独立口径核对发现旧前端把未确认意向计作正式现金支出，而新账本不计。现统一为未确认仅展示；未来若增加正式scheduled任务，须另测其预留。",
            "首次核对发现收入延期会自动采用较低日常预算，混入用户未确认的消费下降假设。领域服务已修正为保持原预算，评测参考计算始终使用原预算。"],
        "summary": aggregate(records),
        "summary_by_event": {event: aggregate([row for row in records if row["event"] == event]) for event in EVENTS},
        "baseline_deficit_scenarios": sorted({row["scenario"] for row in records if not row["baseline_reserve_met"] and not row.get("incomplete")}),
        "failures": [row for row in records if row["errors"] or row["consistency_errors"] or row["new_reserve_violation"] or row["unauthorised_executions"] or row["duplicate_postings"]],
        "non_attainment_examples": [row for row in records if not row["safe_target_attained"]][:12],
        "records": records}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# TROPOS 本地规则策略对照", "", f"生成时间（UTC）：{report['generated_at']}。版本：`{report['git_revision']}`；工作区有未提交修改：{report['working_tree_dirty']}。",
        f"输入：{report['scenario_count']} 个场景，{report['strategy_run_count']} 次独立运行；3位合成用户 × 4种事件 × 4个转入上限。所有策略经过同一服务端安全网关。", "",
        "任务：" + report["task"], "", "| 策略 | 安全目标达成 | 网关前不可行 | 网关拒绝 | 可行资金安排比例 | 新增现金底线违规 | 未授权/重复记账 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for policy, summary in report["summary"].items():
        lines.append(f"| {summary['label']} | {summary['safe_target_attainment_rate']:.1%} | {summary['pre_gateway_infeasible_rate']:.1%} | {summary['gateway_rejections']} | {summary['funds_utilisation']:.1%} | {summary['new_reserve_violations']} | {summary['unauthorised_executions']}/{summary['duplicate_postings']} |")
    lines += ["", "安全目标达成要求执行额等于事件后参考可行上限。参考容量为0时不转属于正确选择，原有现金缺口仍需其他措施。",
        f"事件本身已使现金低于用户预留的场景：{len(report['baseline_deficit_scenarios'])} 个；" + "、".join(report["baseline_deficit_scenarios"]) + "。",
        "", "三策略共享安全网关，因此共同的零违规不能证明动态画像独有安全优势。动态策略按已知事件重算，与本任务参考最优目标一致；这只能检验重算与服务执行是否相符。", "", "## 策略与指标", ""]
    lines += [f"- {LABELS[key]}：{value}" for key, value in report["policy_definitions"].items()]
    lines += [""] + [f"- {key}：{value}" for key, value in report["metric_definitions"].items()]
    lines += ["", "## 按事件切片（目标达成率 / 网关拒绝数）", "", "| 事件 | 固定规则 | 静态画像 | 动态画像 |", "|---|---:|---:|---:|"]
    for event, summaries in report["summary_by_event"].items():
        values = [f"{summaries[policy]['safe_target_attainment_rate']:.1%} / {summaries[policy]['gateway_rejections']}" for policy in POLICIES]
        lines.append("| " + event + " | " + " | ".join(values) + " |")
    lines += ["", "## 未达成实例（最多12条）", "", "| 场景 | 策略 | 提案/执行/参考上限（元） | 网关结果 |", "|---|---|---:|---|"]
    for row in report["non_attainment_examples"]:
        lines.append(f"| {row['scenario']} | {LABELS[row['policy']]} | {row['proposal_cents']/100:.2f}/{row['executed_cents']/100:.2f}/{row['oracle_capacity_cents']/100:.2f} | {row.get('gateway_status','通过')} |")
    lines += ["", f"## 执行失败与参考口径不一致：{len(report['failures'])} 条", ""]
    if report["failures"]:
        lines += ["```json", json.dumps(report["failures"], ensure_ascii=False, indent=2), "```"]
    else:
        lines.append("本批次未发现新增现金底线违规、未授权执行、重复记账或参考口径不一致。所有服务快照逐日路径、最低余额、最低日和可用余量均与独立整数分计算核对。")
    lines += ["", "## 独立口径核对带来的修正", ""] + ["- " + text for text in report["methodology_corrections"]]
    lines += ["", "## 假设与适用边界", ""] + ["- " + text for text in report["assumptions"] + report["limitations"]]
    lines += ["", "## 复跑", "", "`python -m eval.benchmark --output reports/evaluation.json`", "", f"输入 SHA-256：`{report['fixture_sha256']}`。本批运行 {report['runtime_seconds']} 秒（本地测试用时，不代表实际产品响应速度）。逐场景原始记录和实际执行源码指纹保存在同目录 JSON 中。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "evaluation.json")
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    report = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = args.markdown or args.output.with_suffix(".md")
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"scenarios": report["scenario_count"], "runs": report["strategy_run_count"], "failures": len(report["failures"]), "output": str(args.output)}, ensure_ascii=False))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
