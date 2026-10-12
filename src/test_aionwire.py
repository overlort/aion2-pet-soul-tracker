"""Тесты декодера: python test_aionwire.py"""

import os

from aionwire import FrameDecoder, TcpStream, encode_frame, lz4_block_decompress


def _ext(n):
    out = bytearray()
    while n >= 255:
        out.append(255)
        n -= 255
    out.append(n)
    return bytes(out)


def lz4_compress(data: bytes) -> bytes:
    """Простой жадный LZ4-сжиматель, только для тестов."""
    out, table, i, anchor, n = bytearray(), {}, 0, 0, len(data)
    while i + 12 <= n:
        key = data[i:i + 4]
        j = table.get(key)
        table[key] = i
        if j is not None and i - j <= 65535:
            m = 4
            while i + m < n - 5 and data[j + m] == data[i + m]:
                m += 1
            lit = i - anchor
            ml = m - 4
            out.append((min(lit, 15) << 4) | min(ml, 15))
            if lit >= 15:
                out += _ext(lit - 15)
            out += data[anchor:i]
            out += (i - j).to_bytes(2, "little")
            if ml >= 15:
                out += _ext(ml - 15)
            i += m
            anchor = i
        else:
            i += 1
    lit = n - anchor
    out.append(min(lit, 15) << 4)
    if lit >= 15:
        out += _ext(lit - 15)
    out += data[anchor:]
    return bytes(out)


def test_lz4_roundtrip():
    samples = [b"", b"abc", b"a" * 1000, os.urandom(300), (b"hello world " * 50) + os.urandom(40) + b"xyzxyzxyzxyzxyzxyz" * 30]
    for d in samples:
        c = lz4_compress(d)
        assert lz4_block_decompress(c, len(d) + 10) == d
    assert len(lz4_compress(b"a" * 1000)) < 50


def hb():
    return encode_frame(0x0036, bytes(8))


def collect(dec, data, out):
    dec.feed(data, lambda op, body, d: out.append((op, body, d)))


def test_heartbeat_wire():
    assert hb()[:3] == b"\x0e\x00\x36"


def test_split_segments():
    s = hb() + encode_frame(0x0438, b"\x01\x02\x03\x04") + b"\x00" + hb()
    dec, got = FrameDecoder(True), []
    collect(dec, s[:7], got)
    collect(dec, s[7:], got)
    assert [g[0] for g in got] == [0x0036, 0x0438, 0x0036]
    assert got[1][1] == b"\x01\x02\x03\x04"


def test_lz4_bundle():
    inner = encode_frame(0x0438, b"\x09\x09\x09") + encode_frame(0x048D, b"\x07" * 30) + encode_frame(0x048D, b"\x07" * 30)
    body = len(inner).to_bytes(4, "little") + lz4_compress(inner)
    dec, got = FrameDecoder(True), []
    collect(dec, encode_frame(0xFFFF, body), got)
    assert [(g[0], g[2]) for g in got] == [(0x0438, 1), (0x048D, 1), (0x048D, 1)]
    assert got[2][1] == b"\x07" * 30


def test_resync_mid_stream():
    s = b"\x83\x44\x12\x99" + hb() + encode_frame(0x0438, b"\x05")
    dec, got = FrameDecoder(False), []
    collect(dec, s, got)
    assert [g[0] for g in got] == [0x0036, 0x0438]


def test_tcp_reorder_and_loss():
    frames = [hb(), encode_frame(0x4136, b"A" * 50), hb(), encode_frame(0x048D, b"B" * 20), hb(),
              encode_frame(0x2136, b"C" * 10), hb()]
    data = b"".join(frames)
    segs, seq, i = [], 1000, 0
    while i < len(data):
        segs.append((seq + i, data[i:i + 13]))
        i += 13
    got = []
    st = TcpStream(lambda op, body, d: got.append(op))
    st.segment(999, b"", syn=True)
    # перестановка
    order = list(range(len(segs)))
    order[2], order[3] = order[3], order[2]
    for k in order:
        st.segment(*segs[k])
    assert got == [0x0036, 0x4136, 0x0036, 0x048D, 0x0036, 0x2136, 0x0036], got

    # потеря: выкидываем сегмент, после переполнения буфера декодер находит heartbeat
    got.clear()
    st = TcpStream(lambda op, body, d: got.append(op))
    st.MAX_PENDING = 30
    st.segment(999, b"", syn=True)
    for k, sg in enumerate(segs):
        if k == 3:
            continue
        st.segment(*sg)
    assert got[0] == 0x0036 and got[-1] == 0x0036 and 0x2136 in got, got
    assert st.gaps == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
