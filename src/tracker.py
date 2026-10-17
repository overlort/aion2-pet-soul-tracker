"""Aion 2 Soul Tracker — трекер душ петов: оверлей поверх игры с прогрессом петов.

Читает трафик игры пассивно (как DPS-метры), ничего не отправляет и не трогает игру.
Точные числа берутся из списка петов, который сервер присылает при входе в мир,
дальше каждая душа добавляется на лету. Состояние хранится в tracker_data/ рядом с программой.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

import sniff
from pets import PetTracker, load_names


def resource(*parts) -> str:
    """Файл, вшитый в exe (PyInstaller), или из папки проекта."""
    base = getattr(sys, "_MEIPASS", None) or sniff.app_dir()
    return os.path.join(base, *parts)


def message(text: str, error: bool = True):
    """Окошко Windows (в exe нет консоли), а при запуске из консоли — ещё и текст."""
    print(text)
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk()
        r.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)("Aion 2 Soul Tracker", text)
        r.destroy()
    except Exception:
        pass


def npcap_help():
    message("Для работы трекеру нужен бесплатный драйвер Npcap (его же используют DPS-метры).\n\n"
            "Сейчас откроется страница загрузки: скачайте «Npcap installer», установите\n"
            "и запустите трекер снова.")
    try:
        import webbrowser
        webbrowser.open("https://npcap.com/#download")
    except Exception:
        pass


def main():
    if sniff.npcap_missing():
        npcap_help()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="порт сервера (обычно не нужен)")
    ap.add_argument("--data", default=os.path.join(sniff.app_dir(), "tracker_data"))
    args = ap.parse_args()

    os.makedirs(args.data, exist_ok=True)
    diag = os.path.join(args.data, "diag.txt")
    try:
        os.replace(diag, diag + ".prev")
    except OSError:
        pass
    log = sniff.make_logger(diag)
    log("Трекер душ петов запущен.")
    ifaces = sniff.usable_ifaces()
    sniff.describe_environment(log, ifaces)

    names = load_names(resource("data", "pet_names.json"))
    types = load_names(resource("data", "pet_types.json"))
    tracker = PetTracker(os.path.join(args.data, "pets_state.json"), names, types)
    session = sniff.Session(None, False, log, {sniff.dev_id(i): sniff.dev_label(i) for i in ifaces}, record=False)
    session.packet_hooks.append(tracker.on_packet)

    def log_events():
        last = 0.0
        tick = threading.Event()
        while not tick.wait(1):
            with tracker.lock:
                new = [e for e in tracker.events if e[0] > last]
            if new:
                last = new[-1][0]
            for _, msg in new:
                log(msg)
    threading.Thread(target=log_events, daemon=True).start()

    capture = sniff.Capture(session, ifaces, log, args.port)
    if not capture.start():
        message("Не удалось начать захват трафика.\n\nПроверьте, что установлен Npcap и трекер "
                "запущен от администратора. Подробности: tracker_data\\diag.txt")
        return

    stopped = threading.Event()

    def stop():
        if stopped.is_set():
            return
        stopped.set()
        capture.stop()
        tracker._save()
        log("Трекер остановлен.")
        log.close()

    try:
        from tracker_ui import TrackerOverlay
        ui = TrackerOverlay(tracker, session, stop, os.path.join(args.data, "ui_state.json"))
    except Exception as e:
        log(f"Оверлей не запустился ({e}). Работаю в консоли, Ctrl+C — выход.")
        if getattr(sys, "frozen", False):
            message(f"Оверлей не запустился: {e}")
            stop()
            return
        try:
            while not stopped.wait(1):
                pass
        except KeyboardInterrupt:
            stop()
        return

    print("Оверлей открыт. Закрыть — крестик в оверлее.")
    try:
        ui.run()
    except KeyboardInterrupt:
        pass
    stop()


if __name__ == "__main__":
    main()
