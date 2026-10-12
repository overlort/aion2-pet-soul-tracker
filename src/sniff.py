"""Сниффер-исследователь AION 2.

Пассивно читает трафик игры через Npcap (ничего не отправляет и не меняет),
раскладывает его на игровые пакеты и пишет всё в папку captures/<дата_время>/:

    traffic.pcap   - сырой трафик игрового потока (можно открыть в Wireshark)
    events.jsonl   - каждый игровой пакет: время, опкод, тело в hex; плюс метки
    diag.txt       - диагностика: адаптеры, найденный поток игры

Как находится игра: слушаем весь TCP на всех адаптерах (включая loopback и
VPN-адаптеры), ищем поток, где сервер шлёт heartbeat `0E 00 36` ~19 раз в секунду.
С пинг-редьюсером (GearUP, ExitLag) или VPN предпочитаем loopback/виртуальный адаптер:
именно оттуда игра получает данные. Так же делают открытые DPS-метры.

Метки ставятся кнопкой в оверлее (окно поверх игры) или текстом + Enter в консоли.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import threading
import time

from aionwire import HEARTBEATS, NOISY, HEARTBEAT, TcpStream, op_name
import netdiag

try:
    from scapy.all import AsyncSniffer, IP, IPv6, TCP, PcapWriter, conf
except ImportError:
    print("Не установлен scapy. Запустите start_sniffer.bat, он всё поставит.")
    sys.exit(1)

WINDOW = 3.0             # секунд до и после метки
SIG_THRESHOLD = 12       # heartbeat-пакетов ...
SIG_WINDOW = 3.0         # ... за столько секунд = это поток игры
LOOPBACK_GRACE = 2.5     # сколько ждать, не появится ли тот же поток на loopback/VPN
RELOCK_AFTER = 45.0      # тишина в захваченном потоке -> ищем поток заново (переподключение)

VIRTUAL_HINTS = ("loopback", "wintun", "tap-windows", "tap adapter", "tunnel", "vpn", "gearup",
                 "exitlag", "wireguard", "openvpn", "virtual adapter")


def app_dir() -> str:
    """Папка программы: рядом с exe, а при запуске из исходников - корень проекта."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def tcp_payload(ip, tcp) -> bytes:
    """Данные TCP без Ethernet-заполнителя (scapy иногда приклеивает его к payload)."""
    raw = bytes(tcp)[int(tcp.dataofs) * 4:]
    if ip.version == 4:
        plen = int(ip.len or 0) - int(ip.ihl) * 4 - int(tcp.dataofs) * 4
    else:
        plen = int(ip.plen or 0) - int(tcp.dataofs) * 4
    if 0 <= plen <= len(raw) and (ip.version != 4 or ip.len):
        return raw[:plen]
    return raw  # склеенные сетевой картой пакеты: длина в заголовке бывает неверной


def has_signature(payload: bytes) -> bool:
    return any(sig in payload for sig in HEARTBEATS)


def is_virtual(desc: str) -> bool:
    d = desc.lower()
    return any(h in d for h in VIRTUAL_HINTS)


class Session:
    def __init__(self, out_dir, verbose: bool, log, dev_desc: dict, record: bool = True):
        self.verbose = verbose
        self.log = log
        self.dev_desc = dev_desc
        self.dir = out_dir
        self.record = record
        self.packet_hooks = []   # f(opcode, body) для каждого игрового пакета (трекер)
        self.events = self.pcap = None
        if record:
            os.makedirs(out_dir, exist_ok=True)
            self.events = open(os.path.join(out_dir, "events.jsonl"), "a", encoding="utf-8")
            self.pcap = PcapWriter(os.path.join(out_dir, "traffic.pcap"), linktype=101, sync=True)
        self.lock = threading.RLock()
        self.streams: dict[tuple, TcpStream] = {}
        self.op_counts = collections.Counter()
        self.recent = collections.deque()
        self.raw_packets = 0
        self.markers = 0
        self.started = time.time()
        self.warmup_until = self.started + 20
        self.cur_time = time.time()
        self.last_raw = 0.0
        self.listeners = []  # колбэки для строк-отчётов (оверлей)

        # поиск потока игры
        self.locked = None            # (dev, server_port) или (None, port) при ручном порте
        self.locked_at = 0.0
        self.lock_event = threading.Event()
        self.sig_hits: dict[tuple, list] = {}
        self.first_candidate = 0.0
        self.dev_tcp = collections.Counter()   # все TCP-пакеты по адаптерам (до захвата)
        self.dev_sig = collections.Counter()   # из них с heartbeat-сигнатурой

    def desc(self, dev) -> str:
        return self.dev_desc.get(dev, dev or "любой адаптер")

    # ---------- приём сырых пакетов ----------
    def on_raw(self, pkt, dev):
        ip = pkt.getlayer(IP) or pkt.getlayer(IPv6)
        if ip is None or not pkt.haslayer(TCP):
            return
        tcp = pkt[TCP]
        locked = self.locked
        if locked is None:
            self._detect(ip, tcp, dev)
            return
        ldev, port = locked
        if (ldev is not None and dev != ldev) or port not in (tcp.sport, tcp.dport):
            return
        with self.lock:
            self.raw_packets += 1
            self.last_raw = time.time()
            self.cur_time = float(pkt.time)
            if self.pcap is not None:
                try:
                    self.pcap.write(ip)
                except Exception:
                    pass
            if tcp.sport != port:
                return  # клиент -> сервер зашифрован, только в pcap
            key = (ip.src, tcp.sport, ip.dst, tcp.dport)
            stream = self.streams.get(key)
            syn = bool(int(tcp.flags) & 0x02)
            payload = tcp_payload(ip, tcp)
            if stream is None or syn:
                stream = TcpStream(self.on_game_packet)
                self.streams[key] = stream
                print(f"[поток] {ip.src}:{tcp.sport} -> {ip.dst}:{tcp.dport}" + (" (с начала)" if syn else " (с середины)"))
            stream.segment(int(tcp.seq), payload, syn=syn)

    def _detect(self, ip, tcp, dev):
        payload = tcp_payload(ip, tcp)
        with self.lock:
            self.dev_tcp[dev] += 1
            if not payload or not has_signature(payload) or self.locked is not None:
                return
            self.dev_sig[dev] += 1
            now = time.time()
            key = (dev, ip.src, tcp.sport, ip.dst, tcp.dport)
            hit = self.sig_hits.get(key)
            if hit is None or now - hit[1] > SIG_WINDOW:
                hit = [0, now]
            hit[0] += 1
            hit[1] = now
            self.sig_hits[key] = hit
            if not self.first_candidate:
                self.first_candidate = now
            if hit[0] < SIG_THRESHOLD:
                return
            virtual = is_virtual(self.desc(dev))
            if not virtual and now - self.first_candidate < LOOPBACK_GRACE:
                return  # даём шанс тому же потоку проявиться на loopback/VPN
            self.locked = (dev, tcp.sport)
            self.locked_at = now
            self.log(f"НАШЁЛ ПОТОК ИГРЫ: {ip.src}:{tcp.sport} -> {ip.dst}:{tcp.dport} "
                     f"на адаптере «{self.desc(dev)}»")
            self.lock_event.set()

    def unlock(self):
        with self.lock:
            self.locked = None
            self.sig_hits.clear()
            self.first_candidate = 0.0
            self.dev_tcp.clear()
            self.dev_sig.clear()
            self.streams.clear()

    # ---------- игровые пакеты ----------
    def on_game_packet(self, op: int, body: bytes, depth: int):
        t = self.cur_time
        self.op_counts[op] += 1
        for hook in self.packet_hooks:
            try:
                hook(op, body)
            except Exception as e:
                print(f"ошибка обработчика {op:04x}: {e}")
        if op == HEARTBEAT or self.events is None:
            return
        rec = {"t": round(t, 3), "op": f"{op:04x}", "len": len(body), "d": depth, "hex": body.hex()}
        self.events.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if op not in NOISY:
            self.recent.append((t, op, len(body), body[:24].hex(" ")))
            while self.recent and self.recent[0][0] < t - 60:
                self.recent.popleft()
            first = self.op_counts[op] == 1
            if self.verbose or (first and t > self.warmup_until):
                tag = "НОВЫЙ " if first else ""
                print(f"  {tag}{op_name(op):>12}  len={len(body):<5} {body[:24].hex(' ')}")

    # ---------- метки ----------
    def marker(self, note: str) -> int:
        with self.lock:
            self.markers += 1
            n = self.markers
            t = time.time()
            rec = {"t": round(t, 3), "marker": n, "note": note}
            if self.events is not None:
                self.events.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self.events.flush()
        print(f"\n>>> МЕТКА #{n} {note!r} — жду {WINDOW:.0f} с и покажу, что пришло рядом")
        timer = threading.Timer(WINDOW, self._report, args=(n, t))
        timer.daemon = True
        timer.start()
        return n

    def _report(self, n: int, t: float):
        with self.lock:
            near = [r for r in self.recent if t - WINDOW <= r[0] <= t + WINDOW]
        print(f"--- рядом с меткой #{n}: {len(near)} пакетов (без боевого спама) ---")
        for rt, op, ln, head in near:
            print(f"  {rt - t:+5.1f}s {op_name(op):>12} len={ln:<5} {head}")
        print("---\n")
        ops = collections.Counter(op_name(op) for _, op, _, _ in near)
        short = ", ".join(f"{name}×{c}" if c > 1 else name for name, c in ops.most_common(4)) or "ничего"
        self._notify(f"#{n}: {short}")

    def _notify(self, line: str):
        for cb in self.listeners:
            try:
                cb(line)
            except Exception:
                pass

    def snapshot(self) -> dict:
        with self.lock:
            return {"raw": self.raw_packets, "game": sum(self.op_counts.values()),
                    "markers": self.markers, "last_raw": self.last_raw,
                    "locked": self.locked is not None}

    def status(self):
        with self.lock:
            game = sum(self.op_counts.values())
            top = ", ".join(f"{op_name(o)}:{c}" for o, c in self.op_counts.most_common(6))
            gaps = sum(s.gaps for s in self.streams.values())
            state = "поток найден" if self.locked else "ищу поток игры"
        print(f"[{dt.datetime.now():%H:%M:%S}] {state} | сырых={self.raw_packets} игровых={game} "
              f"опкодов={len(self.op_counts)} потерь={gaps} меток={self.markers} | {top}")

    def search_report(self):
        with self.lock:
            rows = [(self.desc(d), c, self.dev_sig[d]) for d, c in self.dev_tcp.most_common()]
            best = sorted(self.sig_hits.items(), key=lambda kv: -kv[1][0])[:5]
        self.log("Пока не нашёл поток игры. TCP-пакеты по адаптерам (всего / с сигнатурой игры):")
        if not rows:
            self.log("  ни одного TCP-пакета — Npcap ничего не видит (установка Npcap? права администратора?)")
        for d, c, s in rows:
            self.log(f"  {d:<50} {c:>7} / {s}")
        for (dev, src, sp, dst, dp), (cnt, _) in best:
            self.log(f"  кандидат: {src}:{sp} -> {dst}:{dp} на «{self.desc(dev)}», сигнатур подряд: {cnt}")

    def close(self):
        with self.lock:
            if self.events is None:
                return
            self.events.close()
            self.pcap.close()
            summary = {f"{o:04x}": c for o, c in self.op_counts.most_common()}
            with open(os.path.join(self.dir, "opcodes.json"), "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=1)


def usable_ifaces():
    out = []
    for iface in list(conf.ifaces.values()):
        try:
            if hasattr(iface, "is_valid") and not iface.is_valid():
                continue
        except Exception:
            pass
        out.append(iface)
    return out


def dev_id(iface) -> str:
    return getattr(iface, "network_name", None) or getattr(iface, "name", None) or str(iface)


def dev_label(iface) -> str:
    return getattr(iface, "description", None) or getattr(iface, "name", None) or str(iface)


def start_sniffers(session: Session, ifaces, bpf: str):
    sniffers = []
    for iface in ifaces:
        dev = dev_id(iface)
        try:
            s = AsyncSniffer(iface=iface, filter=bpf, store=False,
                             prn=lambda p, d=dev: session.on_raw(p, d))
            s.start()
            sniffers.append(s)
        except Exception:
            pass
    return sniffers


def stop_sniffers(sniffers):
    for s in sniffers:
        try:
            s.stop()
        except Exception:
            pass


class Capture:
    """Управляет снифферами: ищет поток игры, сужает захват, ищет заново при тишине."""

    def __init__(self, session: Session, ifaces, log, port=None):
        self.session = session
        self.ifaces = ifaces
        self.by_dev = {dev_id(i): i for i in ifaces}
        self.log = log
        self.port = port
        self.sniffers = []
        self.stopped = threading.Event()
        self.swap = threading.Lock()

    def start(self) -> bool:
        if self.port:
            self.session.locked = (None, self.port)
            self.session.locked_at = time.time()
            bpf = f"tcp port {self.port}"
            self.log(f"Порт задан вручную: {self.port}")
        else:
            bpf = "tcp"
            self.log("Ищу поток игры по сигнатуре на всех адаптерах...")
        self.sniffers = start_sniffers(self.session, self.ifaces, bpf)
        self.log(f"Слушаю {len(self.sniffers)} адаптеров.")
        if not self.sniffers:
            self.log("Не удалось открыть ни один адаптер. Установлен ли Npcap? Запущено ли от администратора?")
            return False
        if not self.port:
            threading.Thread(target=self._watcher, daemon=True).start()
        threading.Thread(target=self._monitor, daemon=True).start()
        return True

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.swap:
            stop_sniffers(self.sniffers)

    def _narrow(self):
        dev, port = self.session.locked
        target = [self.by_dev[dev]] if dev in self.by_dev else self.ifaces
        with self.swap:
            if self.stopped.is_set():
                return
            old = self.sniffers
            self.sniffers = start_sniffers(self.session, target, f"tcp port {port}")
            stop_sniffers(old)
        self.log(f"Дальше слушаю только порт {port} на «{self.session.desc(dev)}».")

    def _widen(self):
        with self.swap:
            if self.stopped.is_set():
                return
            self.session.unlock()
            self.session.lock_event.clear()
            old = self.sniffers
            self.sniffers = start_sniffers(self.session, self.ifaces, "tcp")
            stop_sniffers(old)

    def _watcher(self):
        while not self.stopped.is_set():
            if self.session.lock_event.wait(1) and not self.stopped.is_set():
                self.session.lock_event.clear()
                self._narrow()

    def _monitor(self):
        reported = time.time()
        while not self.stopped.wait(5):
            s = self.session
            now = time.time()
            if s.locked is None:
                if now - reported > 30:
                    reported = now
                    s.search_report()
            elif not self.port and now - max(s.last_raw, s.locked_at) > RELOCK_AFTER:
                self.log(f"Поток игры молчит {RELOCK_AFTER:.0f} с (переподключение?) — ищу заново.")
                self._widen()


def describe_environment(log, ifaces):
    log(f"Сетевых адаптеров: {len(ifaces)}")
    for i in ifaces:
        tag = "  (loopback/VPN)" if is_virtual(dev_label(i)) else ""
        log(f"  {dev_label(i)}{tag}")
    red = netdiag.reducers()
    if red:
        log("Запущены пинг-редьюсер/VPN: " + ", ".join(red) + " — ищу поток и на loopback/VPN-адаптерах.")
    conns = netdiag.game_connections()
    if conns:
        log("Соединения процесса игры (для справки):")
        for name, pid, local, rip, rport in conns:
            log(f"  {name} (pid {pid})  {local} -> {rip}:{rport}")
    else:
        log("Процесс игры не найден по имени (ищу 'aion') — это не страшно, поток ищется по содержимому.")


def npcap_missing() -> bool:
    if os.name != "nt":
        return False
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return not os.path.exists(os.path.join(root, "System32", "Npcap", "wpcap.dll"))


def make_logger(path):
    f = open(path, "a", encoding="utf-8")
    lock = threading.Lock()

    def log(msg=""):
        print(msg)
        with lock:
            if not f.closed:
                f.write(f"[{dt.datetime.now():%H:%M:%S}] {msg}\n")
                f.flush()

    def close():
        with lock:
            f.close()
    log.close = close
    return log


def main():
    if npcap_missing():
        print("Npcap не установлен. Скачайте и установите: https://npcap.com/#download")
        print("При установке отметьте «WinPcap API-compatible Mode». Потом запустите программу снова.")
        input("Enter — закрыть")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="порт сервера (обычно не нужен: поток ищется сам)")
    ap.add_argument("--out", default=os.path.join(app_dir(), "captures"))
    ap.add_argument("-v", "--verbose", action="store_true", help="печатать все не-боевые пакеты")
    ap.add_argument("--no-overlay", action="store_true", help="без окна, только консоль")
    args = ap.parse_args()

    out = os.path.abspath(os.path.join(args.out, dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")))
    os.makedirs(out, exist_ok=True)
    log = make_logger(os.path.join(out, "diag.txt"))
    log(f"Пишу в {out}")
    ifaces = usable_ifaces()
    describe_environment(log, ifaces)

    session = Session(out, args.verbose, log, {dev_id(i): dev_label(i) for i in ifaces})
    capture = Capture(session, ifaces, log, args.port)
    if not capture.start():
        return

    stopped = threading.Event()

    def reader():
        try:
            for line in sys.stdin:
                session.marker(line.strip() or "Enter")
        except Exception:
            pass
    threading.Thread(target=reader, daemon=True).start()

    def stop():
        if stopped.is_set():
            return
        stopped.set()
        capture.stop()
        session.close()
        log.close()
        print(f"\nГотово. Записано в {out}")

    def status_loop():
        while not stopped.wait(10):
            session.status()
    threading.Thread(target=status_loop, daemon=True).start()

    overlay = None
    if not args.no_overlay:
        try:
            from overlay import Overlay
            state = os.path.join(os.path.dirname(out), "overlay_state.json")
            overlay = Overlay(session, stop, state)
        except Exception as e:
            print(f"Оверлей не запустился ({e}), работаю в консоли.")

    if overlay is not None:
        print("Оверлей открыт: кнопка «Душа выпала» ставит метку. Закрыть — крестик в оверлее.")
        print("Метку можно поставить и тут: текст + Enter.\n")
        try:
            overlay.run()
        except KeyboardInterrupt:
            pass
        stop()
    else:
        print("Текст + Enter здесь — метка с заметкой (например: папис 13/25). Ctrl+C — стоп.\n")
        try:
            while not stopped.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        stop()


if __name__ == "__main__":
    main()
