"""Тесты каталога: данные pet_farm.json, регионы, фильтры, поиск, сортировка."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from catalog import region_of, normalize_farm, build_rows, filter_rows

ROOT = os.path.join(os.path.dirname(__file__), "..", "data")


class FakeTracker:
    def __init__(self):
        names = json.load(open(os.path.join(ROOT, "pet_names.json"), encoding="utf-8"))
        types = json.load(open(os.path.join(ROOT, "pet_types.json"), encoding="utf-8"))
        self.names = {int(k): v for k, v in names.items()}
        self.types = {int(k): v for k, v in types.items()}
        self.farm = {int(k): v for k, v in json.load(open(os.path.join(ROOT, "pet_farm.json"), encoding="utf-8")).items()}
        self.levels = {1095: 2, 1096: 3}
        self.progress = {1095: 74}

    def info(self, pet):
        need = {0: 5, 1: 25, 2: 75}
        lvl = self.levels.get(pet, 0)
        n = need.get(lvl)
        p = self.progress.get(pet, 0)
        return {"pet": pet, "name": self.names.get(pet, str(pet)), "type": self.types.get(pet, ""),
                "level": lvl, "progress": p, "need": n, "left": None if n is None else n - p}


def test_farm_file():
    t = FakeTracker()
    assert len(t.farm) == 207
    assert set(t.farm) == set(t.names)


def test_regions():
    assert region_of("Purifying Forest · Altgard · 60") == "asmo"
    assert region_of("Nephra Snowfield · Morheim") == "asmo"
    assert region_of("Kraka Footpath · Verteron · 10") == "elyos"
    assert region_of("Contaminated Marsh · Chaotic Middle Reshanta · 22") == "abyss"
    assert region_of("") == "dungeon"
    assert region_of("Destroyed Abyss Gate · Verteron · 19") == "elyos"


def test_normalize():
    f = normalize_farm({"t": 3, "r": [["Mob", "100%", 26, "Z · Altgard · 1"]], "o": "x"})
    assert f["total"] == 3 and f["rows"][0]["lvl"] == "Lv. 26" and f["other"] == "x"
    assert normalize_farm(None)["rows"] == []


def test_filters_and_sort():
    rows = build_rows(FakeTracker())
    assert len(rows) == 207
    assert filter_rows(rows, sort="Меньше осталось")[0]["pet"] == 1095  # осталось 1
    assert all(r["need"] is None for r in filter_rows(rows, status="Максимум"))
    assert filter_rows(rows, status="Максимум")[0]["pet"] == 1096
    fera = filter_rows(rows, family="Fera")
    assert fera and all(r["type"] == "Fera" for r in fera)
    hit = filter_rows(rows, query="whirlwind spirit")
    assert [r["pet"] for r in hit] == [1095]
    elyos = filter_rows(rows, region="Элийцы (Verteron, Eltnen)")
    assert elyos and all("elyos" in r["regions"] for r in elyos)
    assert all(not r["mobs"] for r in filter_rows(rows, region="Не с мобов"))
    dung = filter_rows(rows, region="Подземелья")
    assert dung and all("подземелье" in r["where"] for r in dung)
    abyss = filter_rows(rows, region="Бездна (Reshanta)")
    assert abyss and all("Reshanta" in r["where"] for r in abyss)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all catalog tests passed")
