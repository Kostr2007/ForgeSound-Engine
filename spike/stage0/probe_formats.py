"""Stage 0, S0-8: can every sound of a mod folder be decoded? Read-only.

usage: python -I spike/stage0/probe_formats.py SOUNDS_DIR

The type comes from the first bytes, not the extension (a mod may keep
Doom-format (DMX) lumps under .wav). Order: libsndfile (WAV, FLAC, Ogg,
MP3), then the DMX reader, then ffmpeg. Nothing is written.
"""
import collections
import os
import struct
import subprocess
import sys

import numpy as np
import soundfile as sf


def sniff(head):
    if head[:4] == b"RIFF":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:2] == b"\x03\x00":
        return "dmx"
    return "unknown"


def read_dmx(path):
    # Doom sound lump: u16 format (3), u16 rate, u32 count, 16 pad bytes,
    # unsigned 8-bit samples, 16 pad bytes
    b = open(path, "rb").read()
    fmt, rate, n = struct.unpack("<HHI", b[:8])
    pcm = np.frombuffer(b[8 + 16:8 + n - 16], np.uint8).astype(np.float32)
    return (pcm - 128) / 128, rate


def read_any(path):
    kind = sniff(open(path, "rb").read(4))
    if kind == "dmx":
        x, sr = read_dmx(path)
        return kind, "dmx", x, sr, 1
    try:
        x, sr = sf.read(path, dtype="float32", always_2d=True)
        return kind, "libsndfile", x, sr, x.shape[1]
    except Exception:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "f32le", "-"],
                           capture_output=True, check=True)
        x = np.frombuffer(r.stdout, np.float32)
        return kind, "ffmpeg", x, None, None


def main():
    root = sys.argv[1]
    stats = collections.Counter()
    fails = []
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            ext = os.path.splitext(f)[1].lower()
            try:
                kind, via, x, sr, ch = read_any(p)
                ok = np.isfinite(x).all() and x.size > 0
                stats[(ext, kind, via, sr, ch, "ok" if ok else "bad")] += 1
                if not ok:
                    fails.append(p)
            except Exception as e:
                stats[(ext, "?", "error", None, None, type(e).__name__)] += 1
                fails.append(p)
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print("%4d  ext=%-5s type=%-7s via=%-10s sr=%-6s ch=%-4s %s" % ((v,) + tuple(str(i) for i in k)))
    print("\n%d files, %d failed" % (sum(stats.values()), len(fails)))
    for p in fails[:20]:
        print("  FAIL", p)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
