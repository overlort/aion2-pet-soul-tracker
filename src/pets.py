"""Прогресс петов AION 2 по игровым пакетам.

Пакеты (сервер -> клиент, найдены по записям, см. docs/FINDINGS.md):

    00 90  при входе в мир, полный список:
           u32 ?, varint N, N x (u32 пет, u32 пет, u32 уровень),
                  varint M, M x (u32 пет, u32 прогресс)
           Пет без записи прогресса = 0. Пет без записи уровня = ещё не открыт (уровень 0).
    0D 90  душа засчитана: u8 01, u32 пет, u32 +сколько
    09 90  пет открыт: varint игрок, u32 пет, u32 уровень (1), ...
    14 90  уровень пета повышен (автоматически): 00 00, u32 пет, u32 уровень, ...
    0E 90  души потрачены на уровень: u8 01, u32 пет, u32 сколько (5 / 25 / 75)
    11 90  душа подобрана, но её пет уже на макс. уровне (номера пета нет)

Душ до следующего уровня: не открыт -> 5, ур.1 -> 25, ур.2 -> 75, ур.3 = максимум.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time

from aionwire import read_varint

OP_PET_LIST = 0x0090
OP_SOUL = 0x0D90
OP_LEVEL = 0x0990
OP_LEVEL_UP = 0x1490
OP_SPENT = 0x0E90
OP_SOUL_MAXED = 0x1190

NEED = {0: 5, 1: 25, 2: 75}
LEVEL_AFTER = {5: 1, 25: 2, 75: 3}   # сколько душ потрачено -> какой уровень получен
MAX_LEVEL = 3


def parse_pet_list(body: bytes):
    """-> (levels {пет: уровень}, progress {пет: прогресс}) или None, если формат не тот."""
    try:
        o = 4
        r = read_varint(body, o)
        if r is None:
            return None
        n, o = r
        if n > 2000 or o + n * 12 > len(body):
            return None
        levels = {}
        for _ in range(n):
            a, b, lvl = struct.unpack_from("<III", body, o)
            o += 12
            if a != b or not (1 <= lvl <= MAX_LEVEL) or not (1000 <= a <= 9999):
                return None
            levels[a] = lvl
        r = read_varint(body, o)
        if r is None:
            return None
        m, o = r
        if m > 2000 or o + m * 8 > len(body):
            return None
        progress = {}
        for _ in range(m):
            a, p = struct.unpack_from("<II", body, o)
            o += 8
            if not (1000 <= a <= 9999) or p > 1000:
                return None
            progress[a] = p
        return levels, progress
    except struct.error:
        return None


def parse_soul(body: bytes):
    """0D 90 -> (пет, прибавка) или None."""
    if len(body) < 9 or body[0] != 0x01:
        return None
    pet, amount = struct.unpack_from("<II", body, 1)
    if not (1000 <= pet <= 9999) or not (0 < amount < 1000):
        return None
    return pet, amount


def parse_level_up(body: bytes):
    """14 90 -> (пет, уровень) или None."""
    if len(body) < 10:
        return None
    pet, lvl = struct.unpack_from("<II", body, 2)
    if not (1000 <= pet <= 9999) or not (1 <= lvl <= MAX_LEVEL):
        return None
    return pet, lvl


def parse_spent(body: bytes):
    """0E 90 -> (пет, потрачено душ) или None. Формат как у 0D 90."""
    return parse_soul(body)


def parse_level(body: bytes):
    """09 90 -> (пет, уровень) или None."""
    r = read_varint(body, 0)
    if r is None:
        return None
    _, o = r
    if o + 8 > len(body):
        return None
    pet, lvl = struct.unpack_from("<II", body, o)
    if not (1000 <= pet <= 9999) or not (1 <= lvl <= MAX_LEVEL):
        return None
    return pet, lvl


class PetTracker:
    """Состояние петов + сохранение на диск. Потокобезопасно."""

    def __init__(self, state_path: str, names: dict | None = None, types: dict | None = None):
        self.state_path = state_path
        self.lock = threading.RLock()
        self.levels: dict[int, int] = {}
        self.progress: dict[int, int] = {}
        self.synced_at = 0.0          # когда пришёл полный список (вход в мир)
        self.session: dict[int, int] = {}   # душ за этот запуск
        self.last_pet: int | None = None
        self.last_time = 0.0
        self.maxed_souls = 0
        self.maxed_last = 0.0
        self.events: list[tuple[float, str]] = []
        self.listeners = []
        self.names = dict(names or {})
        self.types = dict(types or {})
        self._load()

    # ---------- имена ----------
    def name(self, pet: int) -> str:
        return self.names.get(pet) or f"Пет #{pet}"

    # ---------- расчёты ----------
    def info(self, pet: int) -> dict:
        with self.lock:
            lvl = self.levels.get(pet, 0)
            prog = self.progress.get(pet, 0)
            need = NEED.get(lvl)
            return {
                "pet": pet, "name": self.name(pet), "type": self.types.get(pet, ""), "level": lvl, "progress": prog, "need": need,
                "left": None if need is None else max(0, need - prog),
                "ready": need is not None and prog >= need,
                "session": self.session.get(pet, 0),
                "known": bool(self.synced_at) or pet in self.levels or pet in self.progress,
            }

    def recent(self, n: int = 4) -> list[dict]:
        with self.lock:
            pets = sorted(self.session, key=lambda p: -self._touched.get(p, 0))[:n]
        return [self.info(p) for p in pets]

    def closest(self, n: int = 3) -> list[dict]:
        """Петы, которым меньше всего осталось до следующего уровня."""
        with self.lock:
            pets = set(self.levels) | set(self.progress)
        rows = [self.info(p) for p in pets]
        rows = [r for r in rows if r["left"] is not None and r["left"] > 0]
        rows.sort(key=lambda r: (r["left"], -r["level"]))
        return rows[:n]

    def summary(self) -> dict:
        with self.lock:
            lv = list(self.levels.values())
            return {"open": len(lv), "lvl1": lv.count(1), "lvl2": lv.count(2), "lvl3": lv.count(3),
                    "synced": self.synced_at, "session_total": sum(self.session.values()),
                    "maxed_souls": self.maxed_souls, "maxed_last": self.maxed_last}

    # ---------- пакеты ----------
    _touched: dict

    def on_packet(self, op: int, body: bytes):
        if op == OP_PET_LIST:
            parsed = parse_pet_list(body)
            if parsed:
                with self.lock:
                    self.levels, self.progress = parsed
                    self.synced_at = time.time()
                self._log(f"Получен список петов: открыто {len(parsed[0])}")
                self._save()
                self._changed()
        elif op == OP_SOUL:
            parsed = parse_soul(body)
            if parsed:
                pet, amount = parsed
                with self.lock:
                    self.progress[pet] = self.progress.get(pet, 0) + amount
                    self.session[pet] = self.session.get(pet, 0) + amount
                    self._touched[pet] = time.time()
                    self.last_pet, self.last_time = pet, time.time()
                i = self.info(pet)
                need = "макс" if i["need"] is None else f"{i['progress']}/{i['need']}"
                self._log(f"+{amount} {i['name']}: {need}")
                self._save()
                self._changed()
        elif op in (OP_LEVEL, OP_LEVEL_UP):
            parsed = parse_level(body) if op == OP_LEVEL else parse_level_up(body)
            if parsed:
                self._set_level(*parsed)
        elif op == OP_SPENT:
            parsed = parse_spent(body)
            if parsed:
                pet, spent = parsed
                with self.lock:
                    self.progress[pet] = max(0, self.progress.get(pet, 0) - spent)
                lvl = LEVEL_AFTER.get(spent)
                if lvl:
                    self._set_level(pet, lvl)
                else:
                    self._log(f"{self.name(pet)}: потрачено {spent} душ")
                    self._save()
                    self._changed()
        elif op == OP_SOUL_MAXED:
            with self.lock:
                self.maxed_souls += 1
                self.maxed_last = time.time()
            self._log(f"душа пета с макс. уровнем (11 90: {body.hex(' ')})")
            self._changed()

    def _set_level(self, pet: int, lvl: int):
        with self.lock:
            raised = lvl > self.levels.get(pet, 0)
            if raised:
                self.levels[pet] = lvl
                self.progress[pet] = 0
            self._touched[pet] = time.time()
            self.last_pet, self.last_time = pet, time.time()
        if raised:
            self._log(f"{self.name(pet)}: уровень {lvl}!")
        self._save()
        self._changed()

    # ---------- служебное ----------
    def _log(self, msg: str):
        with self.lock:
            self.events.append((time.time(), msg))
            self.events = self.events[-50:]

    def _changed(self):
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:
                pass

    def _load(self):
        self._touched = {}
        data = _read_json(self.state_path) or {}
        self.levels = {int(k): v for k, v in data.get("levels", {}).items()}
        self.progress = {int(k): v for k, v in data.get("progress", {}).items()}
        self.saved_synced_at = data.get("synced_at", 0)

    def _save(self):
        with self.lock:
            data = {"levels": self.levels, "progress": self.progress,
                    "synced_at": self.synced_at or self.saved_synced_at, "saved_at": time.time()}
        _write_json(self.state_path, data)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def load_names(path: str) -> dict:
    data = _read_json(path) or {}
    return {int(k): v for k, v in data.items()}
