"""Оверлей трекера душ: последний пет крупно, «Недавние» и «Почти готовы» сворачиваются.

Замочек: закрыт — окно прозрачно для мыши (клики уходят в игру), открыт — окно можно
двигать и сворачивать разделы. Сам замочек — отдельная маленькая кнопка справа от окна,
поэтому на неё можно нажать и когда основное окно «прозрачное».
"""

from __future__ import annotations

import datetime as dt
import json
import os

TITLE = "Анубис=140 мора"

BG = "#14161c"
PANEL = "#1e2129"
TRACK = "#2a2e38"
FG = "#e9e9ec"
MUTED = "#8b909a"
ACCENT = "#d9a441"
GREEN = "#5cc27c"
RED = "#e06060"
BLUE = "#6aa7e8"

RECENT_MAX = 5
CLOSEST_MAX = 3
LOCK_W = 26


def fmt_progress(i: dict) -> str:
    if i["need"] is None:
        return "макс. уровень"
    return f"{i['progress']} / {i['need']}"


def level_label(lvl: int) -> str:
    return "не открыт" if lvl == 0 else f"ур. {lvl}"


def hwnd_of(win) -> int:
    """Настоящее окно верхнего уровня Windows для окна Tk (GA_ROOT, а не владелец)."""
    import ctypes
    GA_ROOT = 2
    return ctypes.windll.user32.GetAncestor(win.winfo_id(), GA_ROOT) or win.winfo_id()


def bring_to_top(win):
    """Windows: поднять окно над всеми «поверх всех» без кражи фокуса."""
    if os.name != "nt":
        win.lift()
        return
    try:
        import ctypes
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = -1, 0x1, 0x2, 0x10, 0x40
        ctypes.windll.user32.SetWindowPos(hwnd_of(win), HWND_TOPMOST, 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    except Exception:
        win.lift()


def set_click_through(root, enabled: bool):
    """Windows: окно пропускает клики мыши насквозь (WS_EX_TRANSPARENT)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = hwnd_of(root)
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


class Section:
    """Раздел со сворачиваемым заголовком: «▾ Недавние»."""

    def __init__(self, ui, parent, title: str, key: str, opened: bool):
        tk = ui.tk
        self.ui, self.title, self.key, self.opened = ui, title, key, opened
        self.frame = tk.Frame(parent, bg=BG)
        self.head = tk.Label(self.frame, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w", cursor="hand2")
        self.head.pack(fill="x")
        self.head.bind("<Button-1>", lambda e: self.toggle())
        self.box = tk.Frame(self.frame, bg=BG)
        self.rows = []
        self.visible = False

    def toggle(self):
        if self.ui.locked:
            return
        self.opened = not self.opened
        self.ui.ui_state[self.key] = self.opened
        self.ui.save_ui()
        self.render(self.rows)

    def render(self, rows):
        tk = self.ui.tk
        self.rows = rows
        if not rows:
            if self.visible:
                self.frame.pack_forget()
                self.visible = False
            return
        if not self.visible:
            self.frame.pack(fill="x", padx=8, pady=(0, 2))
            self.visible = True
        arrow = "▾" if self.opened else "▸"
        self.head.configure(text=f"{arrow} {self.title} ({len(rows)})")
        for w in self.box.winfo_children():
            w.destroy()
        if not self.opened:
            self.box.pack_forget()
            return
        self.box.pack(fill="x")
        for r in rows:
            f = tk.Frame(self.box, bg=BG)
            f.pack(fill="x", pady=1)
            tk.Label(f, text=r["name"], bg=BG, fg=FG, font=("Segoe UI", 9), anchor="w").pack(side="left")
            right = "макс." if r["need"] is None else f"{level_label(r['level'])} · {r['progress']}/{r['need']}"
            tk.Label(f, text=right, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")


class TrackerOverlay:
    def __init__(self, tracker, session, on_close, ui_state_path: str):
        import tkinter as tk

        self.tk = tk
        self.t = tracker
        self.s = session
        self.on_close = on_close
        self.ui_state_path = ui_state_path
        self.dirty = True
        tracker.listeners.append(self._mark_dirty)
        self.ui_state = self._load_ui()
        self.locked = bool(self.ui_state.get("locked", False))

        root = self.root = tk.Tk()
        root.title(TITLE)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self.ui_state.get("alpha", 0.9))
        except tk.TclError:
            pass
        root.configure(bg=BG, highlightthickness=1, highlightbackground="#2c303a")
        root.geometry(f"+{self.ui_state.get('x', 60)}+{self.ui_state.get('y', 200)}")

        # заголовок: точка статуса, название, [место под замок], ✕
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x")
        self.dot = tk.Label(head, text="●", bg=BG, fg=RED, font=("Segoe UI", 9))
        self.dot.pack(side="left", padx=(8, 3), pady=3)
        title = tk.Label(head, text=TITLE, bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=("Segoe UI", 10), cursor="hand2")
        close.pack(side="right", padx=(2, 8))
        close.bind("<Button-1>", lambda e: self.close())
        for w in (head, title, self.dot):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.head = head

        # главная карточка
        card = tk.Frame(root, bg=PANEL)
        card.pack(fill="x", padx=8, pady=(2, 6))
        self.name = tk.Label(card, text="Ждём первую душу…", bg=PANEL, fg=FG,
                             font=("Segoe UI", 13, "bold"), anchor="w")
        self.name.pack(fill="x", padx=10, pady=(8, 0))
        self.line = tk.Label(card, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.line.pack(fill="x", padx=10)
        self.bar = tk.Canvas(card, height=10, width=250, bg=PANEL, highlightthickness=0)
        self.bar.pack(fill="x", padx=10, pady=(6, 4))
        self.left = tk.Label(card, text="", bg=PANEL, fg=FG, font=("Segoe UI", 10), anchor="w")
        self.left.pack(fill="x", padx=10, pady=(0, 8))

        # разделы
        self.sections = tk.Frame(root, bg=BG)
        self.sections.pack(fill="x")
        self.recent = Section(self, self.sections, "Недавние", "recent_open", self.ui_state.get("recent_open", True))
        self.closest = Section(self, self.sections, "Почти готовы", "closest_open",
                               self.ui_state.get("closest_open", False))

        self.status = tk.Label(root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w",
                               justify="left", wraplength=260)
        self.status.pack(fill="x", padx=8, pady=(2, 6))

        # замочек — отдельное окно, кликабельно всегда
        lock = self.lock_win = tk.Toplevel(root)
        lock.overrideredirect(True)
        lock.attributes("-topmost", True)
        lock.configure(bg=BG, highlightthickness=1, highlightbackground="#2c303a")
        self.lock_btn = tk.Label(lock, text="", bg=BG, fg=MUTED, font=("Segoe UI Emoji", 10), cursor="hand2",
                                 width=2)
        self.lock_btn.pack()
        self.lock_btn.bind("<Button-1>", lambda e: self.set_locked(not self.locked))

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.update_idletasks()
        self._place_lock()
        root.after(200, lambda: self.set_locked(self.locked, save=False))
        self._tick()

    # ---------- замок ----------
    def set_locked(self, locked: bool, save: bool = True):
        self.locked = locked
        set_click_through(self.root, locked)
        bring_to_top(self.lock_win)
        self.lock_btn.configure(text="🔒" if locked else "🔓", fg=ACCENT if locked else MUTED)
        if save:
            self.ui_state["locked"] = locked
            self.save_ui()

    def _place_lock(self):
        try:
            x = self.root.winfo_x() + self.root.winfo_width() + 2    # снаружи, справа от заголовка
            y = self.root.winfo_y()
            if (x, y) != getattr(self, "_lock_pos", None):
                self.lock_win.geometry(f"+{x}+{y}")
                self._lock_pos = (x, y)
            bring_to_top(self.lock_win)
        except Exception:
            pass

    # ---------- отрисовка ----------
    def _mark_dirty(self):
        self.dirty = True

    def _tick(self):
        found = self.s.snapshot().get("locked")
        self.dot.configure(fg=GREEN if found else RED)
        if self.dirty:
            self.dirty = False
            self._render()
        self._render_status(found)
        self._place_lock()
        self.root.after(300, self._tick)

    def _render(self):
        pet = self.t.last_pet
        if pet is None:
            sm = self.t.summary()
            self.name.configure(text="Ждём первую душу…")
            self.line.configure(text=(f"Открыто петов: {sm['open']} (ур.1 {sm['lvl1']} · ур.2 {sm['lvl2']} · "
                                      f"ур.3 {sm['lvl3']})" if sm["open"] else "Прогресс петов ещё не получен"))
            self._draw_bar(0, 1, MUTED)
            self.left.configure(text="")
        else:
            i = self.t.info(pet)
            self.name.configure(text=i["name"])
            kind = f"{i['type']} · " if i["type"] else ""
            self.line.configure(text=f"{kind}{level_label(i['level'])} · {fmt_progress(i)}")
            if i["need"] is None:
                self._draw_bar(1, 1, BLUE)
                self.left.configure(text="Максимальный уровень", fg=BLUE)
            else:
                self._draw_bar(i["progress"], i["need"], ACCENT)
                self.left.configure(text=f"Осталось {i['left']}", fg=FG)
        self.recent.render([r for r in self.t.recent(RECENT_MAX + 1) if r["pet"] != pet][:RECENT_MAX])
        self.closest.render(self.t.closest(CLOSEST_MAX))

    def _draw_bar(self, value, total, color):
        c = self.bar
        c.delete("all")
        w = max(c.winfo_width(), 250)
        c.create_rectangle(0, 0, w, 10, fill=TRACK, width=0)
        frac = 0 if not total else max(0.0, min(1.0, value / total))
        if frac > 0:
            c.create_rectangle(0, 0, int(w * frac), 10, fill=color, width=0)

    def _render_status(self, found):
        sm = self.t.summary()
        color = MUTED
        if not found:
            text, color = "ищу поток игры… (вы в мире?)", RED
        elif sm["synced"]:
            text = f"синхронизировано {dt.datetime.fromtimestamp(sm['synced']):%H:%M}"
        elif self.t.saved_synced_at:
            text = (f"данные от {dt.datetime.fromtimestamp(self.t.saved_synced_at):%d.%m %H:%M} — "
                    f"перезайдите на персонажа для сверки")
            color = ACCENT
        else:
            text, color = "перезайдите на персонажа — так трекер получит прогресс петов", ACCENT
        if self.status.cget("text") != text:
            self.status.configure(text=text, fg=color)

    # ---------- действия ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        if self.locked:
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
        self._place_lock()

    def _load_ui(self) -> dict:
        try:
            with open(self.ui_state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_ui(self):
        try:
            os.makedirs(os.path.dirname(self.ui_state_path), exist_ok=True)
            self.ui_state.update({"x": self.root.winfo_x(), "y": self.root.winfo_y()})
            with open(self.ui_state_path, "w", encoding="utf-8") as f:
                json.dump(self.ui_state, f)
        except Exception:
            pass

    def close(self):
        self.save_ui()
        self.root.destroy()
        self.on_close()

    def run(self):
        self.root.mainloop()
