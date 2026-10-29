"""The sample library: originals stored byte for byte under their sha256,
metadata next to them (asset.json), decoded audio cached in memory.

The type of a file comes from its first bytes, not its extension: a Doom
mod may keep DMX lumps under .wav (Trailblazer does, stage 0 S0-8).
"""
import datetime
import hashlib
import io
import json
import os
import struct
import subprocess

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


def decode(data):
    """bytes -> (mono float64, sample rate, channels, kind)"""
    kind = sniff(data[:4])
    if kind == "dmx":
        # u16 format (3), u16 rate, u32 count, 16 pad bytes, unsigned 8-bit samples, 16 pad bytes
        _, rate, n = struct.unpack("<HHI", data[:8])
        pcm = np.frombuffer(data[8 + 16:8 + n - 16], np.uint8).astype(np.float64)
        return (pcm - 128) / 128, rate, 1, kind
    try:
        x, sr = sf.read(io.BytesIO(data), dtype="float64", always_2d=True)
        return x.mean(axis=1), sr, x.shape[1], kind
    except Exception:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "f64le", "-ac", "1", "-ar", "48000", "-"],
                           input=data, capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.float64), 48000, 1, kind


class Library:
    def __init__(self, root):
        self.root = root
        self.cache = {}
        os.makedirs(os.path.join(root, "objects"), exist_ok=True)

    def _dir(self, sha):
        return os.path.join(self.root, "objects", "sha256", sha[:2], sha)

    def add(self, data, filename):
        sha = hashlib.sha256(data).hexdigest()
        d = self._dir(sha)
        x, sr, ch, kind = decode(data)
        if not os.path.exists(os.path.join(d, "asset.json")):
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "original"), "wb") as f:
                f.write(data)
            meta = {
                "sha256": sha, "kind": "original",
                "source": {"filename": filename, "imported_at": datetime.datetime.now().astimezone().isoformat()},
                # unknown until the user (or the Freesound importer) fills it in;
                # the export policy treats unknown as "not for distribution"
                "license": {"spdx": None, "status": "unknown"},
                "audio": {"format": kind, "sample_rate": sr, "channels": ch, "duration_s": round(len(x) / sr, 4)},
            }
            with open(os.path.join(d, "asset.json"), "w") as f:
                json.dump(meta, f, indent=1, ensure_ascii=False)
        self.cache[sha] = {"audio": x, "sr": sr}
        return self.info(sha)

    def info(self, sha):
        with open(os.path.join(self._dir(sha), "asset.json")) as f:
            return json.load(f)

    def assets(self):
        # what engine.render reads: sha -> {"audio", "sr"}
        return self.cache

    def load(self, sha):
        if sha not in self.cache:
            with open(os.path.join(self._dir(sha), "original"), "rb") as f:
                x, sr, _, _ = decode(f.read())
            self.cache[sha] = {"audio": x, "sr": sr}
        return self.cache[sha]
