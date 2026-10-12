"""Тесты модели петов на настоящих пакетах из записей: python test_pets.py"""

import json
import os
import tempfile

from pets import PetTracker, load_names, parse_level, parse_pet_list, parse_soul
TYPES = load_names(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pet_types.json"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKETS = json.load(open(os.path.join(ROOT, "testdata", "pet_packets.json"), encoding="utf-8"))
NAMES = load_names(os.path.join(ROOT, "data", "pet_names.json"))


def packets(file_suffix):
    return [(int(r["op"], 16), bytes.fromhex(r["hex"])) for r in PACKETS if r["file"].endswith(file_suffix)]


def tracker():
    d = tempfile.mkdtemp()
    return PetTracker(os.path.join(d, "state.json"), NAMES, TYPES), d


def test_pet_list_from_login():
    body = next(b for op, b in packets("17-53-18") if op == 0x0090)
    levels, progress = parse_pet_list(body)
    assert len(levels) == 175 and len(progress) == 143
    assert levels[1066] == 2 and progress[1066] == 7          # Папис 2 ур., 7/75 — как в игре
    assert levels[1067] if 1067 in levels else True
    assert max(p for k, p in progress.items() if levels.get(k) == 1) < 25
    assert max(p for k, p in progress.items() if levels.get(k) == 2) < 75
    assert all(p < 5 for k, p in progress.items() if k not in levels)


def test_papis_session_counts_six():
    t, _ = tracker()
    t.levels[1066], t.progress[1066] = 2, 1                    # было 1/75
    for op, body in packets("17-47-27"):
        t.on_packet(op, body)
    i = t.info(1066)
    assert (i["name"], i["level"], i["progress"], i["need"], i["left"], i["session"]) == \
        ("Papis", 2, 7, 75, 68, 6), i


def test_registration_then_soul():
    t, _ = tracker()
    for op, body in packets("17-35-48"):
        t.on_packet(op, body)
    a = t.info(0x042B)                                          # 5 душ -> открыт, ещё +1
    assert (a["level"], a["progress"], a["need"]) == (1, 1, 25), a
    assert t.maxed_souls == 4                                 # души петов на макс. уровне
    assert t.info(0x0487)["session"] == 1 and t.info(0x04A9)["session"] == 1


def test_auto_level_up_giant_sparkie():
    t, _ = tracker()
    t.levels[1034], t.progress[1034] = 1, 24                   # Giant Sparkie 24/25
    for op, body in packets("18-05-58"):
        t.on_packet(op, body)
    i = t.info(1034)
    assert (i["name"], i["level"], i["progress"], i["need"], i["session"]) == ("Giant Sparkie", 2, 0, 75, 1), i


def test_level_three_is_max():
    t, _ = tracker()
    t.levels[1147], t.progress[1147] = 2, 68                   # Пет #1147 68/75, 7 душ -> ур. 3
    for op, body in packets("18-09-58"):
        t.on_packet(op, body)
    i = t.info(1147)
    assert (i["level"], i["progress"], i["need"], i["left"], i["session"]) == (3, 0, None, None, 7), i
    assert (i["name"], i["type"]) == ("Dratona", "Cogni")
    assert all(r["pet"] != 1147 for r in t.closest(5))
    assert t.maxed_souls == 1                                  # следующая душа того же пета -> 11 90


def test_spent_before_level_packet():
    t, _ = tracker()
    t.levels[1034], t.progress[1034] = 1, 24
    pk = dict((op, b) for op, b in packets("18-05-58"))
    for op in (0x0D90, 0x0E90, 0x1490):                        # обратный порядок тоже ок
        t.on_packet(op, pk[op])
    assert (t.levels[1034], t.progress[1034]) == (2, 0)


def test_persistence():
    t, d = tracker()
    for op, body in packets("17-53-18"):
        t.on_packet(op, body)
    t2 = PetTracker(os.path.join(d, "state.json"), NAMES, TYPES)
    assert t2.info(1066)["progress"] == 7 and t2.levels[1066] == 2
    assert t2.name(1042) == "Ursus" and t2.name(9999) == "Пет #9999"


def test_all_my_pets_have_names():
    body = next(b for op, b in packets("17-53-18") if op == 0x0090)
    levels, progress = parse_pet_list(body)
    assert all(p in NAMES and p in TYPES for p in set(levels) | set(progress))
    assert len(NAMES) == 207


def test_parsers_reject_garbage():
    assert parse_pet_list(b"\x00" * 3) is None
    assert parse_pet_list(bytes(range(200))) is None
    assert parse_soul(b"\x02" + bytes(8)) is None
    assert parse_level(b"\xff\xff\xff") is None


def test_closest():
    t, _ = tracker()
    for op, body in packets("17-53-18"):
        t.on_packet(op, body)
    rows = t.closest(3)
    assert len(rows) == 3 and rows[0]["left"] <= rows[1]["left"] <= rows[2]["left"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
