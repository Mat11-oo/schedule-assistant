"""Deterministic interval allocation. Inputs are snapshots, not model output."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.planning.schemas import minute

ZONE = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def dt(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def bounds(start, end):
    return (datetime.combine(start, time.min, ZONE).astimezone(UTC),
            datetime.combine(end + timedelta(days=1), time.min, ZONE).astimezone(UTC))


def overlap(a, b, c, d):
    return a < d and c < b


def minutes(a, b):
    return max(0, int((b - a).total_seconds() // 60))


def day_minutes(start, end):
    result = {}
    while start < end:
        day = start.astimezone(ZONE).date()
        edge = datetime.combine(day + timedelta(days=1), time.min, ZONE).astimezone(UTC)
        stop = min(edge, end)
        result[day.isoformat()] = minutes(start, stop)
        start = stop
    return result


def subtract(intervals, start, end):
    result = []
    for a, b in intervals:
        if not overlap(a, b, start, end):
            result.append((a, b))
        else:
            if a < start:
                result.append((a, start))
            if end < b:
                result.append((end, b))
    return result


def working(policy, start, end, floor):
    free = []
    day = start.astimezone(ZONE).date()
    while day < end.astimezone(ZONE).date():
        midnight = datetime.combine(day, time.min, ZONE)
        for w in policy["weekly"]:
            if w["weekday"] == day.weekday():
                a = max((midnight + timedelta(minutes=minute(w["start"]))).astimezone(UTC), start, floor)
                b = min((midnight + timedelta(minutes=minute(w["end"]))).astimezone(UTC), end)
                if a < b:
                    free.append((a, b))
        day += timedelta(days=1)
    return sorted(free)


def conflicts(events, blocks, policy):
    """Show recorded conflicts without silently moving the underlying facts."""
    items = [{"id": e["id"], "kind": "event", "title": e["title"], "start_at": e["start_at"], "end_at": e["end_at"]} for e in events]
    items += [{"id": b["id"], "kind": "block", "title": b.get("title", "任务时间块"), "start_at": b["start_at"], "end_at": b["end_at"]} for b in blocks]
    items.sort(key=lambda x: dt(x["start_at"]))
    result = []
    gap = timedelta(minutes=policy["buffer_minutes"])
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if dt(b["start_at"]) >= dt(a["end_at"]) + gap:
                break
            result.append({"kind": "overlap" if overlap(dt(a["start_at"]), dt(a["end_at"]), dt(b["start_at"]), dt(b["end_at"])) else "buffer",
                           "first": a, "second": b})
            if len(result) >= 100:
                return result
    return result


def issues(snapshot, start, end):
    policy = snapshot["preferences"]
    tasks = {t["id"]: t for t in snapshot["tasks"]}
    blocks = [b for b in snapshot["blocks"] if overlap(dt(b["start_at"]), dt(b["end_at"]), start, end)]
    result, used = [], {}
    timings = {t["id"]: t for t in snapshot["timings"]}
    clock = datetime.now(UTC)
    work = working(policy, start, end, start)
    for block in blocks:
        task = tasks.get(block["task_id"])
        a, b = dt(block["start_at"]), dt(block["end_at"])
        if policy["configured"] and not any(x <= a and b <= y for x, y in work):
            result.append(f"「{block['title']}」有时间块超出当前可用时段；需要你决定是否调整。")
        if task and task["due_at"] and b > dt(task["due_at"]):
            result.append(f"「{task['title']}」有时间块晚于截止时间。")
        if b > clock:
            for dep in timings.get(block["task_id"], {}).get("dependency_ids", []):
                dependency = tasks.get(dep)
                if dependency and dependency["status"] == "done":
                    continue
                opt = timings.get(dep, {})
                required = opt.get("remaining_minutes")
                if required is None:
                    required = dependency.get("estimated_minutes") if dependency else None
                booked = [r for r in snapshot["blocks"] if r["task_id"] == dep and dt(r["end_at"]) > clock]
                if (required is None or not booked or sum(minutes(max(clock, dt(r["start_at"])), dt(r["end_at"])) for r in booked) < required
                        or max(dt(r["end_at"]) for r in booked) + timedelta(minutes=policy["buffer_minutes"]) > a):
                    result.append(f"「{block['title']}」的前置任务尚未完整安排在它之前，请核对剩余耗时或重新排期。")
                    break
        for day, count in day_minutes(max(a, start), min(b, end)).items():
            used[day] = used.get(day, 0) + count
    for day, count in used.items():
        if count > policy["daily_limit_minutes"]:
            result.append(f"{day} 已安排任务 {count} 分钟，超过当前每日上限 {policy['daily_limit_minutes']} 分钟。")
    return list(dict.fromkeys(result))[:100]


def generate(snapshot, request, now):
    policy, tasks = snapshot["preferences"], {t["id"]: t for t in snapshot["tasks"]}
    if not policy["configured"]:
        raise ValueError("请先确认保存可用时段与每日容量，再生成排期")
    start, end = bounds(request.start_date, request.end_date)
    if end <= now or start > now + timedelta(days=366):
        raise ValueError("请选择从今天起一年内的安排范围")
    floor = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    selected = set(request.task_ids if request.task_ids is not None else [t["id"] for t in tasks.values() if t["status"] in {"todo", "doing"}])
    if selected - tasks.keys():
        raise ValueError("所选任务已变化，请刷新任务列表")
    removed = [b for b in snapshot["blocks"] if request.replace_unlocked and b["task_id"] in selected
               and not b["locked"] and dt(b["start_at"]) >= max(floor, start) and dt(b["end_at"]) <= end]
    removed_ids = {b["id"] for b in removed}
    kept = [b for b in snapshot["blocks"] if b["id"] not in removed_ids]
    free = working(policy, start, end, floor)
    buffer = timedelta(minutes=policy["buffer_minutes"])
    for item in snapshot["events"] + kept:
        free = subtract(free, dt(item["start_at"]) - buffer, dt(item["end_at"]) + buffer)
    free_before = list(free)
    used = {}
    for b in kept:
        for day, count in day_minutes(dt(b["start_at"]), dt(b["end_at"])).items():
            used[day] = used.get(day, 0) + count
    existing_used = dict(used)
    timing = {t["id"]: t for t in snapshot["timings"]}
    additions, unallocated, completed, visiting = [], [], {}, set()
    coverage = "仅核对本应用内手动固定安排与已确认时间块；未读取外部日历。"
    warnings = [coverage, "排入时间块不表示任务已开始或完成；已过去的时间不会自动扣减剩余工作量。"] + issues(snapshot, start, end)
    def fail(task, reason, remaining=None):
        unallocated.append({"task_id": task["id"], "title": task["title"], "reason": reason,
                            "remaining_minutes": remaining,
                            "options": ["调整截止时间", "核对或缩小剩余工作量", "调整优先级或可用时段"]})
        completed[task["id"]] = None
        return None

    def place(key):
        nonlocal free
        if key in completed:
            return completed[key]
        task = tasks[key]
        if task["status"] == "done":
            completed[key] = floor
            return floor
        if task["status"] not in {"todo", "doing"}:
            return fail(task, "任务处于等待或取消状态，不自动安排")
        if key in visiting:
            return fail(task, "前置关系形成循环，请先调整前置任务")
        opts = timing.get(key, {})
        duration = opts.get("remaining_minutes")
        if duration is None:
            duration = task["estimated_minutes"]
        if duration is None:
            return fail(task, "耗时尚未估算，请先填写预计或剩余耗时")
        if duration == 0:
            return fail(task, "剩余耗时为 0，请明确标记完成或更新耗时")
        owned = sorted((b for b in kept if b["task_id"] == key and dt(b["end_at"]) > floor), key=lambda b: b["end_at"])
        reserved = sum(minutes(max(floor, dt(b["start_at"])), dt(b["end_at"])) for b in owned)
        due = min(end, dt(task["due_at"])) if task["due_at"] else end
        for other, setting in timing.items():
            if key in setting.get("dependency_ids", []):
                for block in kept:
                    if block["task_id"] == other and dt(block["end_at"]) > floor:
                        due = min(due, dt(block["start_at"]) - buffer)
        # Off-range reservations remain untouched; do not assume late bookings
        # satisfy an earlier deadline or that their work has actually happened.
        if task["due_at"] and any(dt(b["end_at"]) > dt(task["due_at"]) for b in owned):
            warnings.append(f"「{task['title']}」存在晚于截止时间的已确认时间块，请单独改期。")
        if key not in selected and reserved < duration:
            completed[key] = None
            return None
        if due <= floor and reserved < duration:
            return fail(task, "截止时间已过或早于可安排范围，请先调整日期", duration - reserved)
        if len(visiting) >= 100:
            return fail(task, "前置任务层级超过 100 层，请简化依赖关系")
        visiting.add(key)
        earliest = max(floor, start)
        dependency_end = None
        for dep in opts.get("dependency_ids", []):
            if dep not in tasks:
                visiting.discard(key)
                return fail(task, "前置任务已不可用，请核对前置关系", duration - reserved)
            if tasks[dep]["status"] == "done":
                continue
            finish = place(dep)
            if finish is None:
                visiting.discard(key)
                return fail(task, "前置任务尚未完成且无法在本轮完整排入", duration - reserved)
            earliest = max(earliest, finish + buffer)
            dependency_end = max(dependency_end or floor, finish + buffer)
        visiting.discard(key)
        if dependency_end and any(dt(b["start_at"]) < dependency_end for b in owned):
            return fail(task, "已有时间块早于前置任务结束，请先改期或重排", max(0, duration - reserved))
        if reserved >= duration:
            completed[key] = max(dt(b["end_at"]) for b in owned)
            return completed[key]
        remaining = duration - reserved
        minimum = min(opts.get("min_block_minutes", 30), remaining)
        split = opts.get("splittable", False)
        if not split and owned:
            return fail(task, "已有时间块未覆盖全部工作量；请重排原时间块，或明确允许拆分", remaining)
        while remaining:
            allocated = False
            for x, y in free:
                x, y = max(x, earliest), min(y, due)
                if x >= y:
                    continue
                day = x.astimezone(ZONE).date().isoformat()
                capacity = max(0, policy["daily_limit_minutes"] - used.get(day, 0))
                length = min(minutes(x, y), capacity, remaining)
                if not split and length < remaining:
                    continue
                if split and 0 < remaining - length < minimum:
                    length -= minimum - (remaining - length)
                if length < minimum:
                    continue
                stop = x + timedelta(minutes=length)
                additions.append({"task_id": key, "title": task["title"], "start_at": x.isoformat(), "end_at": stop.isoformat(),
                                  "minutes": length, "locked": False,
                                  "reason": "先满足前置顺序；同等可排任务按截止时间、优先级与创建时间排序"})
                free = subtract(free, x - buffer, stop + buffer)
                used[day] = used.get(day, 0) + length
                remaining -= length
                allocated = True
                break
            if not allocated:
                break
        if remaining:
            return fail(task, "可用时间、单段长度或每日容量不足", remaining)
        owned_end = [dt(b["end_at"]) for b in owned] + [dt(b["end_at"]) for b in additions if b["task_id"] == key]
        completed[key] = max(owned_end)
        return completed[key]

    priority = {"high": 0, "normal": 1, "low": 2}
    for t in sorted((tasks[k] for k in selected), key=lambda t: (t["due_at"] or "9999", priority[t["priority"]], t["created_at"], t["id"])):
        place(t["id"])
    days = []
    day = request.start_date
    while day <= request.end_date:
        key = day.isoformat()
        available = sum(minutes(a, b) for a, b in free_before if a.astimezone(ZONE).date() == day)
        days.append({"date": key, "free_minutes_before": available, "already_booked_minutes": existing_used.get(key, 0),
                     "added_minutes": used.get(key, 0) - existing_used.get(key, 0),
                     "daily_limit_minutes": policy["daily_limit_minutes"]})
        day += timedelta(days=1)
    return {"kind": "automatic", "additions": additions, "removed": removed,
            "kept": [b for b in kept if overlap(dt(b["start_at"]), dt(b["end_at"]), start, end)],
            "unallocated": unallocated, "days": days, "warnings": list(dict.fromkeys(warnings)), "coverage": coverage,
            "conflicts": conflicts(snapshot["events"], [b for b in kept if overlap(dt(b["start_at"]), dt(b["end_at"]), start, end)], policy)}
