"""The core's side of the Surge worker (forgesound/surge_worker.py).

The core process never imports surgepy. It talks to one worker process,
restarts it once if it died, and turns ForgeSound's own parameter names into
Surge's (scene/0/... paths, Surge's units).
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# oscillator types that render bit-identically with retrigger on (stage 0,
# S0-3); S&H noise and String are left out: they draw from Surge's clock-seeded RNG
OSC_TYPES = {"classic": 0, "sine": 1, "wavetable": 2, "fm3": 5, "fm2": 6,
             "window": 7, "modern": 8, "twist": 10, "alias": 11}
FILTER_TYPES = {"off": 0, "lp12": 1, "lp24": 2, "hp12": 4, "bp12": 6}


class SurgeUnavailable(RuntimeError):
    pass


class SurgeWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.p = None
        self.n = 0
        self.tmp = tempfile.mkdtemp(prefix="forgesound-surge-")
        self.version = None

    def available(self):
        return bool(os.environ.get("SURGEPY_SITE"))

    def _start(self):
        if not self.available():
            raise SurgeUnavailable("SURGEPY_SITE is not set: start ForgeSound with `nix run` or inside `nix develop`")
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ["SURGEPY_SITE"],
            "SURGE_DATA_HOME": os.environ.get("SURGE_DATA_HOME", ""),
            # the user's own Surge folders must not leak into renders
            "HOME": home,
            "XDG_DOCUMENTS_DIR": os.path.join(home, "Documents"),
            "XDG_DATA_HOME": os.path.join(home, ".local/share"),
        }
        self.p = subprocess.Popen([sys.executable, "-s", os.path.join(HERE, "surge_worker.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, env=env, cwd=home)
        rep = self._call({"op": "hello"})
        if not rep["ok"]:
            raise SurgeUnavailable(rep.get("error", "worker failed"))
        self.version = rep["version"]

    def _call(self, req):
        self.n += 1
        self.p.stdin.write(json.dumps(dict(req, id=self.n)) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise BrokenPipeError("Surge worker died")
        return json.loads(line)

    def call(self, req):
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.p is None or self.p.poll() is not None:
                        self._start()
                    return self._call(req)
                except (BrokenPipeError, OSError):
                    self.p = None
                    if attempt:
                        raise

    def render_note(self, sr, dur_s, params):
        """A mono note of the curated Surge layer parameters (see engine.SURGE_DEFAULTS)."""
        osc = OSC_TYPES.get(params.get("osc", "sine"), 1)
        cutoff = 12 * math.log2(max(20.0, float(params.get("cutoff_hz", 8000))) / 440.0)
        decay_s = max(0.004, float(params.get("decay_ms", 250)) / 1000)
        sp = {
            "scene/0/osc/0/type": osc,
            "scene/0/osc/0/retrigger": 1,
            "scene/0/osc/0/pitch": float(params.get("fine", 0.0)),
            "scene/0/filterunit/0/type": FILTER_TYPES.get(params.get("filter", "off"), 0),
            "scene/0/filterunit/0/cutoff": max(-60.0, min(70.0, cutoff)),
            "scene/0/filterunit/0/resonance": float(params.get("resonance", 0.0)),
            # Surge's amp EG: times are log2(seconds)
            "scene/0/adsr/0/a": -8.0,
            "scene/0/adsr/0/d": math.log2(decay_s),
            "scene/0/adsr/0/s": float(params.get("sustain", 0.0)),
            "scene/0/adsr/0/r": math.log2(0.02),
        }
        out = os.path.join(self.tmp, "out-%d.npy" % (self.n + 1))
        rep = self.call({"op": "render", "sr": sr, "note": int(params.get("note", 48)), "vel": 120,
                         "gate_s": float(params.get("gate_ms", 60)) / 1000, "dur_s": dur_s,
                         "params": sp, "out": out})
        if not rep["ok"]:
            raise RuntimeError(rep["error"])
        x = np.load(out).mean(axis=0).astype(np.float64)
        os.remove(out)
        return x, rep
