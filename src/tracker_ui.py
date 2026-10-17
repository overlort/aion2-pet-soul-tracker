"""Оверлей Aion 2 Soul Tracker.

Основное окно: последний пет крупно, «Недавние» и «Почти готовы» (сворачиваются).
Справа от окна — парящие кнопки, они всегда кликабельны (отдельное окно):
    🔒/🔓  замок: закрыт — оверлей пропускает клики в игру; открыт — можно двигать и сворачивать
    👁     глаз: спрятать/показать оверлей целиком (остаются только замок и глаз)
    ⚙      настройки: прозрачность, какие блоки показывать

Прозрачность и пропуск кликов делаются средствами Windows (WS_EX_LAYERED + SetLayeredWindowAttributes,
WS_EX_TRANSPARENT), как в экономическом приложении, а не через -alpha у Tk: так они не сбивают друг друга.
"""

from __future__ import annotations

import datetime as dt
import json
import os

TITLE = "Aion 2 Soul Tracker"

BG = "#14161c"
PANEL = "#1e2129"
TRACK = "#2a2e38"
BORDER = "#2c303a"
FG = "#e9e9ec"
MUTED = "#8b909a"
ACCENT = "#d9a441"
GREEN = "#5cc27c"
RED = "#e06060"
BLUE = "#6aa7e8"

RECENT_MAX = 5
CLOSEST_MAX = 3
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.25, 1.0, 0.85

DEFAULTS = {
    "locked": False, "hidden": False, "opacity": OPACITY_DEFAULT,
    "show_card": True, "show_recent": True, "show_closest": True,
    "recent_open": True, "closest_open": False,
}


def fmt_progress(i: dict) -> str:
    if i["need"] is None:
        return "макс. уровень"
    return f"{i['progress']} / {i['need']}"


def level_label(lvl: int) -> str:
    return "не открыт" if lvl == 0 else f"ур. {lvl}"


def clamp_opacity(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = OPACITY_DEFAULT
    return max(OPACITY_MIN, min(OPACITY_MAX, v))


# ---------- Windows ----------
def hwnd_of(win) -> int:
    """Настоящее окно верхнего уровня Windows для окна Tk (GA_ROOT, а не владелец)."""
    import ctypes
    GA_ROOT = 2
    return ctypes.windll.user32.GetAncestor(win.winfo_id(), GA_ROOT) or win.winfo_id()


def apply_style(win, opacity: float, click_through: bool):
    """Прозрачность всего окна и клики сквозь него."""
    if os.name != "nt":
        try:
            win.attributes("-alpha", opacity)
        except Exception:
            pass
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = hwnd_of(win)
        GWL_EXSTYLE, LAYERED, TRANSPARENT, LWA_ALPHA = -20, 0x80000, 0x20, 0x2
        st = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | LAYERED
        st = (st | TRANSPARENT) if click_through else (st & ~TRANSPARENT)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
        u.SetLayeredWindowAttributes(hwnd, 0, int(clamp_opacity(opacity) * 255), LWA_ALPHA)
    except Exception:
        pass


def bring_to_top(win):
    """Поднять окно над всеми «поверх всех» без кражи фокуса."""
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


# ---------- раздел со сворачиванием ----------
class Section:
    """Раздел со сворачиваемым заголовком: «▾ Недавние (3)»."""

    def __init__(self, ui, parent, title: str, key: str):
        tk = ui.tk
        self.ui, self.title, self.key = ui, title, key
        self.frame = tk.Frame(parent, bg=BG)
        self.head = tk.Label(self.frame, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w", cursor="hand2")
        self.head.pack(fill="x")
        self.head.bind("<Button-1>", lambda e: self.toggle())
        self.box = tk.Frame(self.frame, bg=BG)
        self.rows = []
        self.visible = False
        self.enabled = True

    @property
    def opened(self) -> bool:
        return bool(self.ui.st[self.key])

    def toggle(self):
        if self.ui.st["locked"]:
            return
        self.ui.st[self.key] = not self.opened
        self.ui.save_ui()
        self.render(self.rows)

    def render(self, rows):
        tk = self.ui.tk
        self.rows = rows
        show = self.enabled and bool(rows)
        if not show:
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


# ---------- оверлей ----------
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
        self.st = dict(DEFAULTS)
        self.st.update(self._load_ui())
        self.st["opacity"] = clamp_opacity(self.st.get("opacity"))
        self.settings_win = None
        self._icons_pos = None

        root = self.root = tk.Tk()
        root.title(TITLE)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        root.geometry(f"+{self.st.get('x', 60)}+{self.st.get('y', 200)}")

        # заголовок: точка статуса, название, ✕
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

        # главная карточка
        self.card = tk.Frame(root, bg=PANEL)
        self.name = tk.Label(self.card, text="Ждём первую душу…", bg=PANEL, fg=FG,
                             font=("Segoe UI", 13, "bold"), anchor="w")
        self.name.pack(fill="x", padx=10, pady=(8, 0))
        self.line = tk.Label(self.card, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.line.pack(fill="x", padx=10)
        self.bar = tk.Canvas(self.card, height=10, width=250, bg=PANEL, highlightthickness=0)
        self.bar.pack(fill="x", padx=10, pady=(6, 4))
        self.left = tk.Label(self.card, text="", bg=PANEL, fg=FG, font=("Segoe UI", 10), anchor="w")
        self.left.pack(fill="x", padx=10, pady=(0, 8))

        # разделы
        self.sections = tk.Frame(root, bg=BG)
        self.recent = Section(self, self.sections, "Недавние", "recent_open")
        self.closest = Section(self, self.sections, "Почти готовы", "closest_open")

        self.status = tk.Label(root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w",
                               justify="left", wraplength=260)
        self._layout()

        # парящие кнопки: отдельное окно, кликабельно всегда
        icons = self.icons = tk.Toplevel(root)
        icons.overrideredirect(True)
        icons.attributes("-topmost", True)
        icons.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        self.btn_lock = self._icon_button("", lambda: self.set_locked(not self.st["locked"]))
        self.btn_eye = self._icon_button("👁", lambda: self.set_hidden(not self.st["hidden"]))
        self.btn_gear = self._icon_button("⚙", self.open_settings)
        self._layout_icons()

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.update_idletasks()
        self._place_icons(force=True)
        root.after(150, self._restyle)
        if self.st["hidden"]:
            root.after(200, lambda: self.set_hidden(True, save=False))
        self._tick()

    # ---------- кнопки ----------
    def _icon_button(self, text, command):
        b = self.tk.Label(self.icons, text=text, bg=BG, fg=MUTED, font=("Segoe UI Emoji", 10),
                          cursor="hand2", width=2)
        b.bind("<Button-1>", lambda e: command())
        return b

    def _layout_icons(self):
        for b in (self.btn_lock, self.btn_eye, self.btn_gear):
            b.pack_forget()
        locked, hidden = self.st["locked"], self.st["hidden"]
        self.btn_lock.configure(text="🔒" if locked else "🔓", fg=ACCENT if locked else MUTED)
        self.btn_eye.configure(fg=MUTED if hidden else FG)
        self.btn_lock.pack(pady=(1, 0))
        self.btn_eye.pack()
        if not hidden:
            self.btn_gear.pack(pady=(0, 1))

    def _place_icons(self, force=False):
        try:
            if not self.st["hidden"] or self._icons_pos is None:
                self._icons_pos = (self.root.winfo_x() + self.root.winfo_width() + 2, self.root.winfo_y())
            if self._icons_pos is None:
                self._icons_pos = (self.st.get("x", 60) + 280, self.st.get("y", 200))
            if force or self._icons_pos != getattr(self, "_icons_placed", None):
                self.icons.geometry(f"+{self._icons_pos[0]}+{self._icons_pos[1]}")
                self._icons_placed = self._icons_pos
            bring_to_top(self.icons)
        except Exception:
            pass

    def _restyle(self):
        apply_style(self.root, self.st["opacity"], self.st["locked"])

    def set_locked(self, locked: bool, save: bool = True):
        self.st["locked"] = locked
        self._restyle()
        self._layout_icons()
        bring_to_top(self.icons)
        if save:
            self.save_ui()

    def set_hidden(self, hidden: bool, save: bool = True):
        self.st["hidden"] = hidden
        if hidden:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.after(50, self._restyle)    # после показа окна Windows может сбросить стиль
            self.dirty = True
        self._layout_icons()
        self._place_icons(force=True)
        if save:
            self.save_ui()

    # ---------- настройки ----------
    def open_settings(self):
        tk = self.tk
        if self.settings_win is not None:
            try:
                self.settings_win.deiconify()
                self.settings_win.lift()
                return
            except Exception:
                self.settings_win = None
        w = self.settings_win = tk.Toplevel(self.root)
        w.title(f"{TITLE} — настройки")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        x, y = self._icons_pos or (100, 100)
        w.geometry(f"+{x + 40}+{y}")

        lbl = dict(bg=BG, fg=FG, font=("Segoe UI", 9))
        tk.Label(w, text="Прозрачность", **lbl).pack(anchor="w", padx=14, pady=(12, 0))
        row = tk.Frame(w, bg=BG)
        row.pack(fill="x", padx=14)
        pct = tk.Label(row, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), width=5)
        pct.pack(side="right")

        def on_opacity(v):
            self.st["opacity"] = clamp_opacity(int(float(v)) / 100)
            pct.configure(text=f"{int(float(v))}%")
            self._restyle()

        scale = tk.Scale(row, from_=int(OPACITY_MIN * 100), to=int(OPACITY_MAX * 100), orient="horizontal",
                         showvalue=False, resolution=5, length=200, command=on_opacity,
                         bg=BG, fg=FG, troughcolor=TRACK, activebackground=ACCENT,
                         highlightthickness=0, bd=0, sliderrelief="flat")
        scale.set(int(round(self.st["opacity"] * 100)))
        scale.pack(side="left", fill="x", expand=True)
        pct.configure(text=f"{int(round(self.st['opacity'] * 100))}%")

        tk.Label(w, text="Показывать", **lbl).pack(anchor="w", padx=14, pady=(10, 2))
        self._checks = {}
        for key, text in (("show_card", "Главную карточку (последний пет)"),
                          ("show_recent", "«Недавние»"),
                          ("show_closest", "«Почти готовы»")):
            var = tk.BooleanVar(value=bool(self.st[key]))
            self._checks[key] = var
            tk.Checkbutton(w, text=text, variable=var, command=lambda k=key: self._toggle_block(k),
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 9), anchor="w", highlightthickness=0, bd=0
                           ).pack(fill="x", padx=12)

        def done():
            self.save_ui()
            w.destroy()
            self.settings_win = None
        tk.Button(w, text="Готово", command=done, bg=PANEL, fg=FG, activebackground=TRACK, activeforeground=FG,
                  relief="flat", bd=0, padx=16, pady=3, font=("Segoe UI", 9), cursor="hand2"
                  ).pack(anchor="e", padx=14, pady=12)
        w.protocol("WM_DELETE_WINDOW", done)

    def _toggle_block(self, key):
        self.st[key] = bool(self._checks[key].get())
        self._layout()
        self.dirty = True
        self.save_ui()

    def _layout(self):
        """Порядок блоков: карточка, разделы, строка статуса; скрытые — не показываем."""
        for w in (self.card, self.sections, self.status):
            w.pack_forget()
        if self.st["show_card"]:
            self.card.pack(fill="x", padx=8, pady=(2, 6))
        self.sections.pack(fill="x")
        self.status.pack(fill="x", padx=8, pady=(2, 6))
        self.recent.enabled = bool(self.st["show_recent"])
        self.closest.enabled = bool(self.st["show_closest"])

    # ---------- отрисовка ----------
    def _mark_dirty(self):
        self.dirty = True

    def _tick(self):
        found = self.s.snapshot().get("locked")
        self.dot.configure(fg=GREEN if found else RED)
        if self.dirty and not self.st["hidden"]:
            self.dirty = False
            self._render()
        self._render_status(found)
        self._place_icons()
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
        # без главной карточки последний пет тоже попадает в «Недавние»
        skip = pet if self.st["show_card"] else None
        self.recent.render([r for r in self.t.recent(RECENT_MAX + 1) if r["pet"] != skip][:RECENT_MAX])
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

    # ---------- перетаскивание и состояние ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        if self.st["locked"]:
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
        self._place_icons()

    def _load_ui(self) -> dict:
        try:
            with open(self.ui_state_path, encoding="utf-8") as f:
                data = json.load(f)
            if "opacity" not in data and "alpha" in data:     # v1 хранил прозрачность как alpha
                data["opacity"] = data["alpha"]
            return data
        except Exception:
            return {}

    def save_ui(self):
        try:
            os.makedirs(os.path.dirname(self.ui_state_path), exist_ok=True)
            if not self.st["hidden"]:
                self.st.update({"x": self.root.winfo_x(), "y": self.root.winfo_y()})
            with open(self.ui_state_path, "w", encoding="utf-8") as f:
                json.dump(self.st, f)
        except Exception:
            pass

    def close(self):
        self.save_ui()
        self.root.destroy()
        self.on_close()

    def run(self):
        self.root.mainloop()
