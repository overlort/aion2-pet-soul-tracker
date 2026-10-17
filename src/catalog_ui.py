"""Каталог петов (книжка): все 207 петов с прогрессом и тем, где фармить их души.

Поиск по имени пета, мобу и локации; фильтры по семейству, статусу и региону; сортировка.
Внизу — подробности выбранного пета: все мобы с уровнем, шансом и локацией, другие источники.
"""

from __future__ import annotations

from catalog import FAMILIES, REGIONS, STATUSES, SORTS, filter_rows, build_rows

BG = "#14161c"
PANEL = "#1e2129"
TRACK = "#2a2e38"
FG = "#e9e9ec"
MUTED = "#8b909a"
ACCENT = "#d9a441"
BLUE = "#6aa7e8"
GREEN = "#5cc27c"


class Catalog:
    def __init__(self, tk, root, tracker, pos, state: dict, save):
        from tkinter import ttk

        self.tk, self.ttk, self.t, self.st, self.save = tk, ttk, tracker, state, save
        self.dirty = True
        tracker.listeners.append(self._mark_dirty)
        cf = state.setdefault("catalog", {})
        self.family = tk.StringVar(value=cf.get("family", "Все"))
        self.status = tk.StringVar(value=cf.get("status", STATUSES[0]))
        self.region = tk.StringVar(value=cf.get("region", REGIONS[0]))
        self.sort = tk.StringVar(value=cf.get("sort", SORTS[0]))
        self.query = tk.StringVar(value="")

        w = self.win = tk.Toplevel(root)
        w.title("Aion 2 Soul Tracker — петы")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry(cf.get("geometry") or f"640x620+{pos[0]}+{pos[1]}")
        w.minsize(520, 420)
        w.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(w)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Cat.Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                        rowheight=22, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Cat.Treeview.Heading", background=BG, foreground=MUTED, relief="flat",
                        font=("Segoe UI", 8))
        style.map("Cat.Treeview", background=[("selected", TRACK)], foreground=[("selected", FG)])
        style.map("Cat.Treeview.Heading", background=[("active", PANEL)])
        style.configure("Cat.TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG,
                        arrowcolor=FG, bordercolor=TRACK, selectbackground=PANEL, selectforeground=FG)
        # у readonly-комбобокса в теме clam свои цвета поля (светло-серое) — переопределяем явно
        style.map("Cat.TCombobox",
                  fieldbackground=[("readonly", PANEL)], background=[("readonly", TRACK)],
                  foreground=[("readonly", FG)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", FG)], arrowcolor=[("readonly", FG)])
        w.option_add("*TCombobox*Listbox.background", PANEL)
        w.option_add("*TCombobox*Listbox.foreground", FG)
        w.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        w.option_add("*TCombobox*Listbox.selectForeground", "#16130c")

        # поиск
        top = tk.Frame(w, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="🔍", bg=BG, fg=MUTED, font=("Segoe UI Emoji", 10)).pack(side="left")
        e = tk.Entry(top, textvariable=self.query, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                     font=("Segoe UI", 10), highlightthickness=1, highlightbackground=TRACK, highlightcolor=ACCENT)
        e.pack(side="left", fill="x", expand=True, padx=(4, 0), ipady=3)
        e.focus_set()
        self.count = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.count.pack(side="right", padx=(8, 0))
        self.query.trace_add("write", lambda *a: self.refresh())

        # семейства
        fam = tk.Frame(w, bg=BG)
        fam.pack(fill="x", padx=10, pady=2)
        self.fam_buttons = {}
        for f in ("Все",) + FAMILIES:
            b = tk.Label(fam, text=f, bg=PANEL, fg=FG, font=("Segoe UI", 9), padx=9, pady=2, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda ev, f=f: self._set_family(f))
            self.fam_buttons[f] = b

        # статус / регион / сортировка
        opts = tk.Frame(w, bg=BG)
        opts.pack(fill="x", padx=10, pady=(2, 6))
        for label, var, values in (("Статус", self.status, STATUSES), ("Где", self.region, REGIONS),
                                   ("Сортировка", self.sort, SORTS)):
            tk.Label(opts, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(0, 3))
            cb = ttk.Combobox(opts, textvariable=var, values=values, state="readonly", width=16,
                              style="Cat.TCombobox", font=("Segoe UI", 9))
            cb.pack(side="left", padx=(0, 10))
            cb.bind("<<ComboboxSelected>>", lambda ev: (ev.widget.selection_clear(), self.refresh()))

        # таблица
        mid = tk.Frame(w, bg=BG)
        mid.pack(fill="both", expand=True, padx=10)
        cols = ("name", "fam", "lvl", "prog", "left", "where")
        tv = self.tv = ttk.Treeview(mid, columns=cols, show="headings", style="Cat.Treeview", selectmode="browse")
        for c, text, width, anchor in (("name", "Пет", 170, "w"), ("fam", "Семейство", 70, "w"),
                                       ("lvl", "Ур.", 64, "w"), ("prog", "Прогресс", 64, "center"),
                                       ("left", "Осталось", 60, "center"), ("where", "Где фармить", 220, "w")):
            tv.heading(c, text=text)
            tv.column(c, width=width, anchor=anchor, stretch=(c in ("name", "where")))
        tv.tag_configure("max", foreground=BLUE)
        tv.tag_configure("closed", foreground=MUTED)
        sb = ttk.Scrollbar(mid, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv.bind("<<TreeviewSelect>>", lambda ev: self._show_details())

        # подробности
        self.details = tk.Text(w, height=9, bg=PANEL, fg=FG, relief="flat", wrap="word", font=("Segoe UI", 9),
                               padx=10, pady=8, highlightthickness=0)
        self.details.pack(fill="x", padx=10, pady=(6, 10))
        self.details.tag_configure("h", font=("Segoe UI", 11, "bold"), foreground=FG)
        self.details.tag_configure("m", foreground=MUTED)
        self.details.tag_configure("a", foreground=ACCENT)
        self.details.configure(state="disabled")

        self.rows_by_iid = {}
        self._set_family(self.family.get(), refresh=False)
        self.refresh()
        self._poll()

    # ---------- данные ----------
    def _mark_dirty(self):
        self.dirty = True

    def _poll(self):
        if not self.alive():
            return
        if self.dirty:
            self.refresh(keep_selection=True)
        self.win.after(1000, self._poll)

    def _set_family(self, f, refresh=True):
        self.family.set(f)
        for name, b in self.fam_buttons.items():
            b.configure(bg=ACCENT if name == f else PANEL, fg="#16130c" if name == f else FG)
        if refresh:
            self.refresh()

    def refresh(self, keep_selection=False):
        self.dirty = False
        sel = self.tv.selection()
        sel_pet = self.rows_by_iid.get(sel[0], {}).get("pet") if sel else None
        rows = filter_rows(build_rows(self.t), self.query.get(), self.family.get(), self.status.get(),
                           self.region.get(), self.sort.get())
        self.tv.delete(*self.tv.get_children())
        self.rows_by_iid = {}
        for r in rows:
            tag = "max" if r["need"] is None else ("closed" if r["level"] == 0 and not r["progress"] else "")
            iid = self.tv.insert("", "end", values=(r["name"], r["type"], r["level_text"], r["prog_text"],
                                                    r["left_text"], r["where"]), tags=(tag,))
            self.rows_by_iid[iid] = r
            if keep_selection and r["pet"] == sel_pet:
                self.tv.selection_set(iid)
                self.tv.see(iid)
        self.count.configure(text=f"{len(rows)} из {len(self.t.names) or len(rows)}")
        self._remember()

    def _show_details(self):
        sel = self.tv.selection()
        r = self.rows_by_iid.get(sel[0]) if sel else None
        d = self.details
        d.configure(state="normal")
        d.delete("1.0", "end")
        if r:
            d.insert("end", f"{r['name']}", "h")
            d.insert("end", f"   {r['type']} · {r['level_text']} · {r['prog_text']}\n", "m")
            if r["mobs"]:
                more = f" (всего мобов: {r['total']})" if r["total"] > len(r["mobs"]) else ""
                d.insert("end", f"Души падают с{more}:\n", "m")
                for m in r["mobs"]:
                    d.insert("end", f"• {m['mob']}", "a")
                    d.insert("end", f"  {m['lvl']} · {m['pct']}\n   {m['loc']}\n")
            else:
                d.insert("end", "С мобов души не падают.\n", "m")
            if r["other"]:
                d.insert("end", "Другие источники: ", "m")
                d.insert("end", r["other"] + "\n")
        d.configure(state="disabled")

    # ---------- окно ----------
    def _remember(self):
        cf = self.st.setdefault("catalog", {})
        cf.update({"family": self.family.get(), "status": self.status.get(), "region": self.region.get(),
                   "sort": self.sort.get()})
        try:
            cf["geometry"] = self.win.geometry()
        except Exception:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def show(self):
        self.win.deiconify()
        self.win.lift()

    def close(self):
        self._remember()
        self.save()
        try:
            self.t.listeners.remove(self._mark_dirty)
        except ValueError:
            pass
        self.win.destroy()
