"""Оверлей сниффера: маленькое окно поверх игры с кнопкой метки.

Игра должна быть в режиме «окно без рамки» (borderless), иначе окно поверх
полноэкранной игры не видно. Окно перетаскивается за заголовок.
"""

from __future__ import annotations

import json
import os
import queue
import re
import time

BG = "#15171d"
PANEL = "#1f222a"
FG = "#e8e8ea"
MUTED = "#8b909a"
ACCENT = "#d9a441"
ACCENT_HOT = "#f0bb55"
GREEN = "#5cc27c"
RED = "#e06060"


def bump_count(text: str) -> str:
    """'13/25' -> '14/25', '7' -> '8', всё остальное не трогаем."""
    m = re.fullmatch(r"\s*(\d+)\s*(?:/\s*(\d+))?\s*", text or "")
    if not m:
        return text
    n = int(m.group(1)) + 1
    return f"{n}/{m.group(2)}" if m.group(2) else str(n)


def make_note(pet: str, count: str, extra: str = "") -> str:
    parts = [p.strip() for p in (extra, pet, count) if p and p.strip()]
    return " ".join(parts) or "душа"


class Overlay:
    def __init__(self, session, on_close, state_path: str):
        import tkinter as tk  # импорт здесь: без tkinter сниффер работает в консоли

        self.tk = tk
        self.s = session
        self.on_close = on_close
        self.state_path = state_path
        self.events: queue.Queue = queue.Queue()
        session.listeners.append(self.events.put)

        root = self.root = tk.Tk()
        root.title("AION · души")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.9)
        except tk.TclError:
            pass
        root.configure(bg=BG, highlightthickness=1, highlightbackground="#2c303a")

        state = self._load_state()
        x, y = state.get("x", 60), state.get("y", 220)
        root.geometry(f"+{x}+{y}")

        # --- заголовок (за него тянем) ---
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x")
        title = tk.Label(head, text="⠿  AION · души", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        title.pack(side="left", padx=(8, 0), pady=3)
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=("Segoe UI", 10), cursor="hand2")
        close.pack(side="right", padx=8)
        close.bind("<Button-1>", lambda e: self.close())
        for w in (head, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # --- поля ---
        form = tk.Frame(root, bg=BG)
        form.pack(fill="x", padx=8)
        lbl = dict(bg=BG, fg=MUTED, font=("Segoe UI", 8))
        ent = dict(bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 10),
                   highlightthickness=1, highlightbackground="#2c303a", highlightcolor=ACCENT)
        tk.Label(form, text="Пет", **lbl).grid(row=0, column=0, sticky="w")
        tk.Label(form, text="Счёт", **lbl).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.pet = tk.Entry(form, width=16, **ent)
        self.pet.grid(row=1, column=0, sticky="we")
        self.count = tk.Entry(form, width=7, **ent)
        self.count.grid(row=1, column=1, sticky="we", padx=(6, 0))
        self.pet.insert(0, state.get("pet", ""))
        self.count.insert(0, state.get("count", ""))
        for e in (self.pet, self.count):
            e.bind("<Return>", lambda ev: self.soul())

        # --- главная кнопка ---
        self.btn = tk.Button(root, text="Душа выпала", command=self.soul, bg=ACCENT, fg="#16130c",
                             activebackground=ACCENT_HOT, activeforeground="#16130c", relief="flat",
                             bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2", pady=6)
        self.btn.pack(fill="x", padx=8, pady=(8, 4))

        # --- прочие события ---
        other = tk.Frame(root, bg=BG)
        other.pack(fill="x", padx=8)
        self.extra = tk.Entry(other, width=16, **ent)
        self.extra.pack(side="left", fill="x", expand=True)
        self.extra.insert(0, "")
        self.extra.bind("<Return>", lambda ev: self.other())
        tk.Button(other, text="Метка", command=self.other, bg=PANEL, fg=FG, activebackground="#2a2e38",
                  activeforeground=FG, relief="flat", bd=0, font=("Segoe UI", 9), cursor="hand2",
                  padx=10).pack(side="left", padx=(6, 0))
        tk.Label(root, text="напр.: открыл меню петов, уровень пета", bg=BG, fg=MUTED,
                 font=("Segoe UI", 7)).pack(anchor="w", padx=8)

        # --- статус и последние отчёты ---
        self.status = tk.Label(root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.status.pack(fill="x", padx=8, pady=(6, 0))
        self.log = tk.Label(root, text="", bg=BG, fg=FG, font=("Consolas", 8), anchor="w",
                            justify="left", wraplength=250)
        self.log.pack(fill="x", padx=8, pady=(2, 8))
        self.log_lines: list[str] = []

        root.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()

    # ---------- действия ----------
    def soul(self):
        n = self.s.marker(make_note(self.pet.get(), self.count.get()))
        new_count = bump_count(self.count.get())
        self.count.delete(0, "end")
        self.count.insert(0, new_count)
        self._flash(f"✓ метка #{n}")
        self._save_state()

    def other(self):
        text = self.extra.get().strip() or "событие"
        n = self.s.marker(make_note(self.pet.get(), "", extra=text))
        self.extra.delete(0, "end")
        self._flash(f"✓ метка #{n}")

    def _flash(self, text):
        self.btn.configure(text=text, bg=GREEN, activebackground=GREEN)
        self.root.after(900, lambda: self.btn.configure(text="Душа выпала", bg=ACCENT,
                                                        activebackground=ACCENT_HOT))

    # ---------- обновление ----------
    def _tick(self):
        try:
            while True:
                self._add_log(self.events.get_nowait())
        except queue.Empty:
            pass
        st = self.s.snapshot()
        idle = time.time() - st["last_raw"] if st["last_raw"] else None
        if not st.get("locked", True):
            dot, color, text = "●", RED, "ищу поток игры…"
        elif st["raw"] == 0:
            dot, color, text = "●", ACCENT, "поток найден, жду пакеты"
        elif idle is not None and idle > 10:
            dot, color, text = "●", ACCENT, f"тишина {idle:.0f} с"
        else:
            dot, color, text = "●", GREEN, "пишу"
        self.status.configure(
            text=f"{dot} {text} · пакетов {st['game']} · меток {st['markers']}", fg=color)
        self.root.after(500, self._tick)

    def _add_log(self, line: str):
        self.log_lines = (self.log_lines + [line])[-5:]
        self.log.configure(text="\n".join(self.log_lines))

    # ---------- перетаскивание и состояние ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"pet": self.pet.get(), "count": self.count.get(),
                           "x": self.root.winfo_x(), "y": self.root.winfo_y()}, f, ensure_ascii=False)
        except Exception:
            pass

    def close(self):
        self._save_state()
        self.root.destroy()
        self.on_close()

    def run(self):
        self.root.mainloop()
