"""Логика каталога петов (без окна): строки с прогрессом и тем, где фармить, фильтры и сортировка.

Данные «где фармить» — data/pet_farm.json (собрано с metabot.gg):
    {"1066": {"t": 13, "r": [[mob, "100%", 40, loc], ...], "o": "..."}}  (normalize_farm разворачивает)
    loc = "Зоны · Регион · N spawns"; пусто — моб только в подземельях/инстансах.
"""

from __future__ import annotations

FAMILIES = ("Fera", "Natura", "Varian", "Cogni", "Special")
STATUSES = ("Все", "Не открыт", "Ур. 1", "Ур. 2", "Не максимум", "Максимум")
REGIONS = ("Любой регион", "Асмодиане (Altgard, Morheim)", "Элийцы (Verteron, Eltnen)", "Бездна (Reshanta)",
           "Подземелья", "Не с мобов")
SORTS = ("Меньше осталось", "Имя", "Уровень ↓", "Уровень ↑")

ASMO = ("altgard", "morheim", "beluslan", "brusthonin")
ELYOS = ("verteron", "eltnen", "heiron", "theobomos")

NEED = {0: 5, 1: 25, 2: 75}


def region_of(loc: str) -> str:
    """'Zones · Altgard · 33 spawns' -> 'asmo' | 'elyos' | 'abyss' | 'dungeon' | 'other'."""
    if not loc:
        return "dungeon"
    low = loc.lower()
    if "reshanta" in low or "abyss" in low:
        return "abyss"
    if any(r in low for r in ASMO):
        return "asmo"
    if any(r in low for r in ELYOS):
        return "elyos"
    return "other"


def region_name(loc: str) -> str:
    """Короткая подпись региона: 'Altgard', 'Chaotic Middle Reshanta', 'подземелье'."""
    if not loc:
        return "подземелье/инстанс"
    parts = [p.strip() for p in loc.split("·")]
    if len(parts) >= 3:
        return parts[-2]
    return parts[-1] if len(parts) == 2 else parts[0]


def zone_name(loc: str) -> str:
    return loc.split("·")[0].strip() if loc else ""


def short_other(text: str) -> str:
    """Из описания источников оставляем суть: 'Inside: ...', 'Other sources: ...'."""
    if not text:
        return ""
    keep = [p.strip() for p in text.split("|") if p.strip().startswith(("Inside:", "Other sources:"))]
    out = "; ".join(keep) or text.strip()
    return out.replace("Inside:", "сундуки:").replace("Other sources:", "ещё:")


def normalize_farm(f) -> dict:
    """Компактный формат {"t", "r": [[mob, pct, lvl, loc]], "o"} -> {"total", "rows": [{...}], "other"}."""
    if not f:
        return {"total": 0, "rows": [], "other": ""}
    if "rows" in f:
        return f
    rows = [{"mob": m, "pct": p, "lvl": f"Lv. {lv}" if isinstance(lv, int) else str(lv), "loc": loc or ""}
            for m, p, lv, loc in f.get("r", [])]
    return {"total": f.get("t") or len(rows), "rows": rows, "other": f.get("o", "")}


def build_rows(tracker) -> list[dict]:
    farm = getattr(tracker, "farm", {}) or {}
    pets = set(tracker.names) | set(tracker.levels) | set(tracker.progress)
    rows = []
    for pet in pets:
        i = tracker.info(pet)
        f = normalize_farm(farm.get(pet) or farm.get(str(pet)))
        mobs = f.get("rows") or []
        best = mobs[0] if mobs else None
        if best:
            reg = region_name(best["loc"])
            zone = zone_name(best["loc"])
            where = f"{best['mob']} · {zone} · {reg}" if zone else f"{best['mob']} · {reg}"
        else:
            where = "не с мобов (сундуки и др.)"
        lvl = i["level"]
        rows.append({
            **i,
            "mobs": mobs, "total": f.get("total") or len(mobs), "other": short_other(f.get("other", "")),
            "where": where,
            "regions": {region_of(m["loc"]) for m in mobs},
            "level_text": "не открыт" if lvl == 0 else ("макс." if i["need"] is None else f"ур. {lvl}"),
            "prog_text": "—" if i["need"] is None else f"{i['progress']}/{i['need']}",
            "left_text": "—" if i["left"] is None else str(i["left"]),
            "search": " ".join([i["name"], *(m["mob"] + " " + m["loc"] for m in mobs)]).lower(),
        })
    return rows


def _status_ok(r, status: str) -> bool:
    lvl, mx = r["level"], r["need"] is None
    return {
        "Все": True, "Не открыт": lvl == 0, "Ур. 1": lvl == 1 and not mx, "Ур. 2": lvl == 2 and not mx,
        "Не максимум": not mx, "Максимум": mx,
    }.get(status, True)


def _region_ok(r, region: str) -> bool:
    regs = r["regions"]
    if region.startswith("Асмодиане"):
        return "asmo" in regs
    if region.startswith("Элийцы"):
        return "elyos" in regs
    if region.startswith("Бездна"):
        return "abyss" in regs
    if region == "Подземелья":
        return "dungeon" in regs
    if region == "Не с мобов":
        return not r["mobs"]
    return True


def filter_rows(rows, query="", family="Все", status="Все", region=REGIONS[0], sort=SORTS[0]) -> list[dict]:
    q = (query or "").strip().lower()
    out = [r for r in rows
           if (not q or all(w in r["search"] for w in q.split()))
           and (family in ("", "Все") or r["type"] == family)
           and _status_ok(r, status) and _region_ok(r, region)]
    if sort == "Имя":
        out.sort(key=lambda r: r["name"].lower())
    elif sort == "Уровень ↓":
        out.sort(key=lambda r: (-(r["level"] if r["need"] is not None else 9), r["name"].lower()))
    elif sort == "Уровень ↑":
        out.sort(key=lambda r: ((r["level"] if r["need"] is not None else 9), r["name"].lower()))
    else:  # меньше осталось: сначала те, кому почти хватает; максимум — в конец
        out.sort(key=lambda r: (r["left"] is None, r["left"] if r["left"] is not None else 0, r["name"].lower()))
    return out
