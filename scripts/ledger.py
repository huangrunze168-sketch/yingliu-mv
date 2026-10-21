#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger.py — 成本台账（JSONL 追加制，币与真钱分列）

用法:
  python ledger.py add <ledger.jsonl> <action> <shot_id> <backend> <coins> <cash_cny> <duration_sec> [note]
  python ledger.py summary <ledger.jsonl>          # 总览三数 + 分后端明细

action ∈ submit|retry|salvage|fail_refund|fit|concat
fail_refund 传负数即冲账；汇总时自动净值。
"""
import json
import sys
import os
import time
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

VALID_ACTIONS = {"submit", "retry", "salvage", "fail_refund", "fit", "concat"}
VALID_ACTIONS.update({"reserve", "settle", "release"})


def money(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("金额不是有效Decimal") from exc
    if not result.is_finite():
        raise ValueError("金额必须有限")
    return result


@contextmanager
def locked(path):
    lock = Path(str(path) + ".lock")
    deadline = time.monotonic() + 5
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError("台账锁被占用；先核查在途进程，禁止并发覆盖")
            time.sleep(.05)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink()


def record(path, entry, cash_limit):
    path = Path(path)
    required = ("event_id", "task_id", "action", "cash_cny", "coins", "duration_sec")
    if any(entry.get(field) in (None, "") for field in required):
        raise ValueError("台账须有event_id/task_id和完整金额字段")
    for field in ("cash_cny", "coins", "duration_sec"):
        entry[field] = str(money(entry[field]))
    amount, limit = money(entry["cash_cny"]), money(cash_limit)
    coin_amount = money(entry['coins'])
    if limit < 0 or money(entry["duration_sec"]) < 0 or entry["action"] not in VALID_ACTIONS:
        raise ValueError("预算、时长或action无效")
    if (amount < 0 or money(entry["coins"]) < 0) and entry["action"] != "fail_refund":
        raise ValueError("仅关联退款可使用负金额")
    with locked(path):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
        if any(row.get('cash_limit_cny') is None or money(row['cash_limit_cny']) != limit for row in rows):
            raise ValueError('现金封顶已锁定或旧台账待迁移，不能由单个任务自行抬高预算')
        for row in rows:
            if row.get("event_id") == entry["event_id"]:
                generated_fields = {"ts", "cash_limit_cny"}
                recorded_payload = {k: v for k, v in row.items() if k not in generated_fields}
                requested_payload = {k: v for k, v in entry.items() if k not in generated_fields}
                if recorded_payload != requested_payload:
                    raise ValueError("幂等event_id与既有事件冲突")
                return {"status": "idempotent", "entry": row}
        reservations, spent = {}, Decimal(0)
        task_spent, task_coins = {}, {}
        for row in rows:
            tid, value = row.get("task_id"), money(row["cash_cny"])
            if row["action"] == "reserve":
                reservations[tid] = value
            elif row["action"] == "release":
                reservations.pop(tid, None)
            else:
                reservations.pop(tid, None)
                spent += value
                task_spent[tid] = task_spent.get(tid, Decimal(0)) + value
                task_coins[tid] = task_coins.get(tid, Decimal(0)) + money(row['coins'])
        tid, action = entry["task_id"], entry["action"]
        if action == "reserve":
            if tid in reservations or tid in task_spent:
                raise ValueError("任务已预占或结算；重试须使用新的task_id")
            reservations[tid] = amount
        elif action == "release":
            if tid not in reservations or amount != 0 or coin_amount != 0:
                raise ValueError("释放须关联现存预占且金额为0")
            reservations.pop(tid)
        elif action == "fail_refund":
            reference = entry.get("refund_of")
            if (reference != tid or amount > 0 or coin_amount > 0 or (amount == 0 and coin_amount == 0)
                    or task_spent.get(tid, Decimal(0)) + amount < 0
                    or task_coins.get(tid, Decimal(0)) + coin_amount < 0):
                raise ValueError("退款必须关联已付款任务且不能超额")
            spent += amount
        else:
            if (amount > 0 or coin_amount > 0) and tid not in reservations:
                raise ValueError("付费事件必须先reserve预算")
            reservations.pop(tid, None)
            spent += amount
        if spent + sum(reservations.values()) > limit:
            raise ValueError("支出与在途预占超过现金封顶")
        # P2：批量付费记账必须先过剪辑预演+样片审片（不伪造批准，只硬依赖）
        if (entry.get("batch") is True or entry.get("submission_phase") == "batch") and action in {'reserve', 'submit', 'retry'}:
            project_dir = entry.get("project_dir")
            if not project_dir:
                raise ValueError("批量台账须提供 project_dir，以便核验审片批准")
            project = Path(project_dir)
            try:
                from .animatic_review import approved as animatic_approved
                from .director_review_gate import approved as director_approved
            except ImportError:
                from animatic_review import approved as animatic_approved
                from director_review_gate import approved as director_approved
            ok, errs = animatic_approved(project)
            if not ok:
                raise ValueError("批量记账前剪辑预演未批准: " + "; ".join(errs[:3]))
            dok, derrs = director_approved(project)
            if not dok:
                raise ValueError("批量记账前样片导演审片未批准: " + "; ".join(derrs[:3]))
        entry["ts"] = datetime.now().isoformat(timespec="seconds")
        entry['cash_limit_cny'] = str(limit)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return {"status": "recorded", "entry": entry}


def add(args):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("values", nargs=7)
    parser.add_argument("note", nargs="?", default="")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--cash-limit-cny", required=True)
    parser.add_argument("--refund-of")
    parsed = parser.parse_args(args)
    path, action, shot_id, backend, coins, cash, dur = parsed.values
    result = record(path, {"event_id": parsed.event_id, "task_id": parsed.task_id, "action": action,
                          "shot_id": shot_id, "backend": backend, "coins": coins, "cash_cny": cash,
                          "duration_sec": dur, "note": parsed.note, "refund_of": parsed.refund_of}, parsed.cash_limit_cny)
    print(json.dumps(result, ensure_ascii=False))


def summary(path):
    rows = []
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    by_backend = {}
    for r in rows:
        if r["action"] in {"reserve", "release"}:
            continue
        b = by_backend.setdefault(r.get("backend", "unknown"), {"coins": Decimal(0), "cash_cny": Decimal(0), "calls": 0})
        b["coins"] += money(r["coins"]); b["cash_cny"] += money(r["cash_cny"]); b["calls"] += 1
    out = {
        "overview": {   # 总览三数
            "final_video_sec": round(sum(float(r["duration_sec"]) for r in rows
                                         if r["action"] == "concat"), 3),
            "machine_sec": round(sum(float(r["duration_sec"]) for r in rows if r["action"] not in {"reserve", "release"}), 3),
            "cash_cny_net": str(sum((money(r["cash_cny"]) for r in rows if r["action"] not in {"reserve", "release"}), Decimal(0))),
            "coins_net": str(sum((money(r["coins"]) for r in rows if r["action"] not in {"reserve", "release"}), Decimal(0))),
        },
        "by_backend": {k: {"coins": str(v["coins"]), "cash_cny": str(v["cash_cny"]),
                           "calls": v["calls"]} for k, v in by_backend.items()},
        "entries": len(rows),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    if sys.argv[1] == "add":
        add(sys.argv[2:])
    elif sys.argv[1] == "summary":
        summary(sys.argv[2])
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
