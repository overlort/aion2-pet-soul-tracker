"""Анализ записи сниффера: какие пакеты приходят рядом с метками.

    python analyze.py <папка_сессии>                 кандидаты: опкоды, которые липнут к меткам
    python analyze.py <папка> --op 048d              все пакеты этого опкода с расстоянием до метки
    python analyze.py <папка> --value 13             где в пакетах встречается число 13 (varint/u8/u16/u32)
    python analyze.py <папка> --before 8 --after 2   окно вокруг метки в секундах

Метку ставят чуть позже события (реакция), поэтому окно по умолчанию смещено назад.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from aionwire import NOISY, op_name, read_varint  # noqa: E402


def load(session_dir: str):
    events, markers = [], []
    with open(os.path.join(session_dir, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "marker" in r:
                markers.append(r)
            else:
                r["op"] = int(r["op"], 16)
                events.append(r)
    events.sort(key=lambda r: r["t"])
    markers.sort(key=lambda r: r["t"])
    return events, markers


def nearest_marker(markers, t):
    if not markers:
        return None, None
    ts = [m["t"] for m in markers]
    i = bisect.bisect_left(ts, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(markers):
            d = t - markers[j]["t"]
            if best is None or abs(d) < abs(best[1]):
                best = (markers[j], d)
    return best


def in_window(d, before, after):
    return d is not None and -before <= d <= after


def candidates(events, markers, before, after):
    total = collections.Counter()
    near = collections.Counter()
    hit_markers = collections.defaultdict(set)
    for e in events:
        total[e["op"]] += 1
        m, d = nearest_marker(markers, e["t"])
        if in_window(d, before, after):
            near[e["op"]] += 1
            hit_markers[e["op"]].add(m["marker"])
    rows = []
    for op, n in near.items():
        cover = len(hit_markers[op]) / len(markers)
        precision = n / total[op]
        rows.append((cover * precision, cover, precision, op, n, total[op]))
    rows.sort(reverse=True)
    print(f"Меток: {len(markers)}, пакетов: {len(events)}, окно: -{before}s..+{after}s\n")
    print("  оценка  меток%  точн%  опкод           рядом/всего")
    for score, cover, prec, op, n, tot in rows[:25]:
        flag = "" if op not in NOISY else "  (боевой)"
        print(f"  {score:5.2f}  {cover*100:5.0f}%  {prec*100:5.0f}%  {op_name(op):>12} ({op:04x})  {n}/{tot}{flag}")
    print("\nОценка высокая = опкод приходит почти у каждой метки и редко в другое время.")


def dump_op(events, markers, op, before, after, only_near):
    for e in events:
        if e["op"] != op:
            continue
        m, d = nearest_marker(markers, e["t"])
        near = in_window(d, before, after)
        if only_near and not near:
            continue
        tag = f"метка #{m['marker']} {m['note']!r} {d:+.1f}s" if m and near else ""
        print(f"{e['t']:.3f} len={e['len']:<5} {tag}\n    {bytes.fromhex(e['hex']).hex(' ')}")


def find_value(events, markers, value, before, after):
    print(f"Ищу число {value} рядом с метками...\n")
    for e in events:
        m, d = nearest_marker(markers, e["t"])
        if not in_window(d, before, after) or e["op"] in NOISY:
            continue
        b = bytes.fromhex(e["hex"])
        hits = []
        for o in range(len(b)):
            r = read_varint(b, o)
            if r and r[0] == value and (o == 0 or b[o - 1] < 0x80):
                hits.append(f"varint@{o}")
            if o + 2 <= len(b) and int.from_bytes(b[o:o + 2], "little") == value and value > 255:
                hits.append(f"u16@{o}")
            if o + 4 <= len(b) and int.from_bytes(b[o:o + 4], "little") == value:
                hits.append(f"u32@{o}")
        if hits:
            print(f"метка #{m['marker']} {m['note']!r} {d:+.1f}s  {op_name(e['op'])} len={e['len']}  {', '.join(hits[:6])}")
            print(f"    {b[:96].hex(' ')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--before", type=float, default=8)
    ap.add_argument("--after", type=float, default=2)
    ap.add_argument("--op", help="опкод в hex, например 048d")
    ap.add_argument("--all", action="store_true", help="с --op: показать и пакеты вдали от меток")
    ap.add_argument("--value", type=int)
    a = ap.parse_args()

    events, markers = load(a.session)
    if a.op:
        dump_op(events, markers, int(a.op, 16), a.before, a.after, not a.all)
    elif a.value is not None:
        find_value(events, markers, a.value, a.before, a.after)
    elif not markers:
        cnt = collections.Counter(e["op"] for e in events)
        print("Меток нет. Опкоды в записи:")
        for op, n in cnt.most_common():
            print(f"  {op_name(op):>12} ({op:04x})  {n}")
    else:
        candidates(events, markers, a.before, a.after)


if __name__ == "__main__":
    main()
