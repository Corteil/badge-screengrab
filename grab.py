#!/usr/bin/env python3
"""Grab the Tildagon screen over USB serial.

Uses display.get_framebuffer() (240x240 RGB565, byte-swapped) via the raw REPL,
RLE-compressed on the badge because USB serial output is slow.

  grab.py                    single screenshot -> grab_<timestamp>.png
  grab.py -o shot.png        single screenshot to a named file
  grab.py --gif out.gif -n 50 -d 0.2   record 50 frames as an animated GIF
  grab.py --live             live view window (needs tkinter)
"""
import argparse
import base64
import sys
import time

import numpy as np
import serial
from PIL import Image

W = H = 240
# USB serial out of the badge is slow (~10 KB/s), so send only what changed
# since the previous grab, RLE-coded on-device. Tokens (top two bits of h):
#   00  literal: (h & 63) + 1 changed pixels follow (2 bytes each)
#   01  repeat:  one pixel (2 bytes) repeated (h & 63) + 1 times
#   11  skip:    ((h & 63) << 8 | next byte) + 1 pixels unchanged
SETUP = """
import display, binascii, micropython

@micropython.viper
def px(b: ptr8, i: int) -> int:
    return b[2 * i] | (b[2 * i + 1] << 8)

@micropython.viper
def rle(src: ptr8, prev: ptr8, n: int, dst: ptr8) -> int:
    i = 0
    o = 0
    while i < n:
        p = px(src, i)
        if p == px(prev, i):
            j = i + 1
            while j < n and j - i < 16384 and px(src, j) == px(prev, j):
                j += 1
            dst[o] = 0xC0 | ((j - i - 1) >> 8)
            dst[o + 1] = (j - i - 1) & 255
            o += 2
            i = j
            continue
        j = i + 1
        while j < n and j - i < 64 and px(src, j) == p:
            j += 1
        if j - i >= 2:
            dst[o] = 0x40 | (j - i - 1)
            dst[o + 1] = src[2 * i]
            dst[o + 2] = src[2 * i + 1]
            o += 3
            i = j
            continue
        k = i + 1
        while k < n and k - i < 64 and px(src, k) != px(prev, k):
            if k + 1 < n and px(src, k) == px(src, k + 1):
                break
            k += 1
        dst[o] = k - i - 1
        o += 1
        for m in range(2 * i, 2 * k):
            dst[o] = src[m]
            o += 1
        i = k
    return o

_buf = bytearray(130000)
_prev = bytearray(115200)
"""
SNIPPET = """
_fb = display.get_framebuffer()
_n = rle(_fb, _prev, 57600, _buf)
_prev[:] = _fb
print(binascii.b2a_base64(memoryview(_buf)[:_n]).decode().strip())
"""


class Badge:
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=2)
        self.prev = bytes(W * H * 2)  # matches the badge's initial _prev
        self._enter_raw()
        self._exec(SETUP)

    def _read_until(self, marker, timeout=10):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            buf += self.s.read(self.s.in_waiting or 1)
            if buf.endswith(marker) or marker in buf[-len(marker) - 4:]:
                return buf
        raise TimeoutError(f"waiting for {marker!r}, got {buf[-80:]!r}")

    def _enter_raw(self):
        self.s.write(b"\r\x03\x03")  # interrupt whatever is running
        time.sleep(0.1)
        self.s.reset_input_buffer()
        self.s.write(b"\x01")  # raw REPL
        self._read_until(b"raw REPL; CTRL-B to exit\r\n>")

    def _exec(self, code, timeout=15):
        self.s.write(code.encode().replace(b"\r\n", b"\n"))
        self.s.write(b"\x04")
        self._read_until(b"OK")
        out = self._read_until(b"\x04\x04>", timeout=timeout)
        stdout, err = out[:-3].split(b"\x04", 1) if b"\x04" in out[:-3] else (out[:-3], b"")
        if err.strip():
            raise RuntimeError(err.decode(errors="replace"))
        return stdout.strip()

    def grab(self):
        self.prev = unrle(base64.b64decode(self._exec(SNIPPET)), self.prev)
        return decode(self.prev)

    def close(self):
        try:
            self.s.write(b"\x02")  # back to friendly REPL
        finally:
            self.s.close()


def unrle(data, prev):
    out = bytearray()
    i = 0
    while i < len(data):
        h = data[i]
        kind, n = h >> 6, (h & 63) + 1
        if kind == 0:
            out += data[i + 1:i + 1 + 2 * n]
            i += 1 + 2 * n
        elif kind == 1:
            out += data[i + 1:i + 3] * n
            i += 3
        else:
            n = ((h & 63) << 8 | data[i + 1]) + 1
            out += prev[len(out):len(out) + 2 * n]
            i += 2
    return bytes(out)


def decode(raw):
    if len(raw) != W * H * 2:
        raise ValueError(f"unexpected framebuffer size {len(raw)}")
    px = np.frombuffer(raw, dtype=">u2").reshape(H, W)  # byte-swapped RGB565
    r = ((px >> 11) & 0x1F) << 3
    g = ((px >> 5) & 0x3F) << 2
    b = (px & 0x1F) << 3
    r |= r >> 5
    g |= g >> 6
    b |= b >> 5
    return Image.fromarray(np.dstack([r, g, b]).astype(np.uint8), "RGB")


def live(badge, scale):
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("Tildagon")
    label = tk.Label(root)
    label.pack()

    def tick():
        img = badge.grab().resize((W * scale, H * scale), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        label.configure(image=photo)
        label.image = photo
        root.after(1, tick)

    tick()
    root.mainloop()


def find_port():
    """The badge re-enumerates (ACM0/ACM1...), so find it by USB VID:PID."""
    from serial.tools import list_ports

    for p in list_ports.comports():
        if (p.vid, p.pid) == (0x16D0, 0x120E):
            return p.device
    sys.exit("Tildagon not found; pass --port")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--gif")
    ap.add_argument("-n", "--frames", type=int, default=30)
    ap.add_argument("-d", "--delay", type=float, default=0.2)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args()

    badge = Badge(a.port or find_port())
    try:
        if a.live:
            live(badge, a.scale)
        elif a.gif:
            frames = []
            for _ in range(a.frames):
                t = time.time()
                frames.append(badge.grab())
                time.sleep(max(0, a.delay - (time.time() - t)))
            frames[0].save(a.gif, save_all=True, append_images=frames[1:],
                           duration=int(a.delay * 1000), loop=0)
            print(f"wrote {a.gif} ({len(frames)} frames)")
        else:
            out = a.out or time.strftime("grab_%Y%m%d_%H%M%S.png")
            badge.grab().save(out)
            print(f"wrote {out}")
    finally:
        badge.close()


if __name__ == "__main__":
    sys.exit(main())
