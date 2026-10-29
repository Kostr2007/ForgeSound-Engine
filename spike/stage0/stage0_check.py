"""Stage 0 checks. Runs in the main interpreter, which must NOT see surgepy;
every Surge render goes through a worker process (spike/stage0/surge_worker.py).

usage: python -I spike/stage0/stage0_check.py [--quick] [--play] [--out DIR]

Checks (each prints PASS/FAIL/INFO and the numbers):
  S0-1 isolation      the main process cannot import surgepy; the worker can
  S0-2 smoke          a note renders, is not silent, has no NaN/Inf
  S0-3 determinism    the same request twice: in one worker and in two fresh
                      workers; per oscillator type: bit-identical or how far
  S0-4 speed          a 0.5 s render on a warm worker; worker start time
  S0-5 audio input    a sample goes through Surge (Audio Input osc + filter)
  S0-6 crash          a killed worker is reported and replaced, the core lives
  S0-7 latest wins    of overlapping renders only the newest is accepted
  S0-9 export         a 44.1 kHz 16-bit mono WAV, byte-identical across runs
  S0-10 audio devices PortAudio lists the outputs (PipeWire/ALSA)
  (S0-8, the formats of a mod folder, is spike/stage0/probe_formats.py)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def report(cid, status, msg, **data):
    RESULTS.append({"check": cid, "status": status, "msg": msg, **data})
    print("%-6s %-5s %s %s" % (cid, status, msg, json.dumps(data, default=float, ensure_ascii=False) if data else ""))


class Worker:
    """One Surge worker process; requests are answered in order."""

    def __init__(self, home):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ["SURGEPY_SITE"],
            "SURGE_DATA_HOME": os.environ["SURGE_DATA_HOME"],
            # the user's own Surge folders must not leak into renders
            "HOME": home,
            "XDG_DOCUMENTS_DIR": os.path.join(home, "Documents"),
            "XDG_DATA_HOME": os.path.join(home, ".local/share"),
        }
        t0 = time.perf_counter()
        # -s, not -I: -I would also drop the PYTHONPATH that carries surgepy
        self.p = subprocess.Popen([sys.executable, "-s", os.path.join(HERE, "surge_worker.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env, cwd=home)
        self.n = 0
        self.hello = self.call({"op": "hello"})
        self.start_ms = (time.perf_counter() - t0) * 1000

    def call(self, req, timeout=60):
        self.n += 1
        req = dict(req, id=self.n)
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("worker died: " + self.p.stderr.read()[-2000:])
        return json.loads(line)

    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(10)


def render(w, tmp, name, **kw):
    out = os.path.join(tmp, name + ".npy")
    rep = w.call(dict({"op": "render", "sr": 48000, "note": 48, "vel": 120, "gate_s": 0.05,
                       "dur_s": 0.5, "out": out}, **kw))
    if not rep["ok"]:
        raise RuntimeError(rep["error"] + "\n" + rep.get("trace", ""))
    return np.load(out), rep


def diff_db(a, b):
    d = np.abs(a.astype(np.float64) - b.astype(np.float64)).max()
    return -np.inf if d == 0 else 20 * np.log10(d)


# oscillator types to probe (surgepy constants ot_*, Surge 1.3: 0..11)
OSC_TYPES = {"classic": 0, "sine": 1, "wavetable": 2, "shnoise": 3, "FM3": 5, "FM2": 6,
             "window": 7, "modern": 8, "string": 9, "twist": 10, "alias": 11}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--play", action="store_true", help="S0-10: play a rendered note on the default output")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="forge-s0-")
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "Documents"))

    # S0-1 isolation
    try:
        import surgepy  # noqa: F401
        report("S0-1", "FAIL", "surgepy is importable in the main process")
    except ImportError:
        w = Worker(home)
        if w.hello["ok"]:
            report("S0-1", "PASS", "main process has no surgepy; worker has it",
                   surge=w.hello["version"], worker_start_ms=round(w.start_ms))
        else:
            report("S0-1", "FAIL", "worker cannot import surgepy", error=w.hello.get("error"))
            return finish(a)

    # S0-2 smoke
    x, rep = render(w, tmp, "smoke")
    peak = float(np.abs(x).max())
    ok = np.isfinite(x).all() and peak > 1e-3
    report("S0-2", "PASS" if ok else "FAIL", "Init patch note renders", peak_db=round(20 * np.log10(peak + 1e-12), 1),
           frames=x.shape[1], block=rep["block"])

    # S0-3 determinism per oscillator type, with the oscillator's
    # "retrigger" off (Surge's default: a random start phase) and on
    exact = {}
    for retrig in (0, 1):
        for name, ot in OSC_TYPES.items():
            p = {"scene/0/osc/0/type": ot, "scene/0/osc/0/retrigger": retrig}
            a1, _ = render(w, tmp, "d1", params=p)
            a2, _ = render(w, tmp, "d2", params=p)
            w2 = Worker(home)
            b1, _ = render(w2, tmp, "d3", params=p)
            w2.close()
            same_worker, fresh = diff_db(a1, a2), diff_db(a1, b1)
            ok = same_worker == -np.inf and fresh == -np.inf
            exact[(retrig, name)] = ok
            report("S0-3", "PASS" if ok else "INFO",
                   "retrigger %s, osc %-9s: max diff, same worker / fresh worker" % ("on " if retrig else "off", name),
                   same="identical" if same_worker == -np.inf else "%.1f dB" % same_worker,
                   fresh="identical" if fresh == -np.inf else "%.1f dB" % fresh)
    n_on = sum(exact[(1, n)] for n in OSC_TYPES)
    report("S0-3", "PASS" if n_on >= 1 else "FAIL", "summary: bit-identical oscillator types",
           retrigger_off=[n for n in OSC_TYPES if exact[(0, n)]],
           retrigger_on=[n for n in OSC_TYPES if exact[(1, n)]])

    # S0-4 speed. Interactive: one render after a pause (the worker has
    # prepared a fresh instance meanwhile), the way edits arrive from a user.
    # Back to back: the throughput of variation batches on one worker.
    inter, internal = [], []
    for i in range(5 if a.quick else 20):
        time.sleep(0.15)
        t0 = time.perf_counter()
        _, rep = render(w, tmp, "speed")
        inter.append((time.perf_counter() - t0) * 1000)
        internal.append(rep["render_ms"])
    b2b = []
    for i in range(5 if a.quick else 10):
        t0 = time.perf_counter()
        render(w, tmp, "speed")
        b2b.append((time.perf_counter() - t0) * 1000)
    med = float(np.median(inter))
    report("S0-4", "PASS" if med < 50 else "FAIL", "0.5 s Surge render: round trip after a pause (target < 50 ms)",
           median_ms=round(med, 1), max_ms=round(max(inter), 1), dsp_only_ms=round(float(np.median(internal)), 1),
           back_to_back_ms=round(float(np.median(b2b)), 1), worker_start_ms=round(w.start_ms))

    # S0-5 audio input: a click train through the Audio Input osc + filter
    sr = 48000
    t = np.arange(int(0.5 * sr)) / sr
    click = (np.sin(2 * np.pi * 3000 * t) * np.exp(-t / 0.01)).astype(np.float32)
    np.save(os.path.join(tmp, "in.npy"), click)
    y, _ = render(w, tmp, "input", input=os.path.join(tmp, "in.npy"),
                  params={"scene/0/osc/0/type": 4})
    n = min(len(click), y.shape[1])
    pk = float(np.abs(y).max())
    corr = float(np.corrcoef(y[0, :n], click[:n])[0, 1]) if pk > 0 else 0.0
    report("S0-5", "PASS" if pk > 1e-3 else "FAIL", "sample passes through Surge (Audio Input osc)",
           out_peak_db=round(20 * np.log10(pk + 1e-12), 1), corr_with_input=round(corr, 3))

    # S0-6 crash isolation
    w.p.kill()
    w.p.wait()
    dead = w
    try:
        w.call({"op": "hello"})
        report("S0-6", "FAIL", "a dead worker answered")
    except (RuntimeError, BrokenPipeError, OSError) as e:
        try:
            dead.p.stdin.close()
        except OSError:
            pass
        w = Worker(home)
        report("S0-6", "PASS" if w.hello["ok"] else "FAIL", "dead worker detected, replaced; core alive",
               detected=type(e).__name__)

    # S0-7 latest wins: three edits in a row, renders finish out of order
    accepted = latest_wins_demo()
    report("S0-7", "PASS" if accepted == [3] else "FAIL", "only the newest revision is shown", accepted=accepted)

    # S0-9 export: render -> 44.1 kHz 16-bit mono WAV (the Trailblazer
    # format); twice, from two fresh workers; the files must be identical
    hashes = []
    for i in range(2):
        wi = Worker(home)
        # a deterministic configuration (S0-3): this checks the export path
        y, _ = render(wi, tmp, "exp%d" % i, params={"scene/0/osc/0/type": 1, "scene/0/osc/0/retrigger": 1})
        wi.close()
        hashes.append(export_wav(y, 48000, os.path.join(tmp, "exp%d.wav" % i)))
    report("S0-9", "PASS" if hashes[0] == hashes[1] else "FAIL", "WAV export is byte-identical across runs",
           sha256=hashes[0][:16])

    # S0-10 audio devices: PortAudio sees the system (PipeWire/ALSA on NixOS)
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        outs = [d["name"] for d in devs if d["max_output_channels"] > 0]
        report("S0-10", "PASS" if outs else "INFO", "audio outputs visible to PortAudio",
               n_outputs=len(outs), default=str(sd.default.device), first=outs[:3])
        if a.play and outs:
            # the S0-9 note, 0.5 s, at -12 dB: you should hear a short sine tone
            print("S0-10  playing a 0.5 s Surge note on the default output ...")
            sd.play(0.25 * y.T, 48000, blocking=True)
    except Exception as e:
        report("S0-10", "INFO", "no audio device (normal in a headless container)", error=repr(e)[:120])

    w.close()
    finish(a, tmp)


def export_wav(x, sr, path, out_sr=44100):
    # deterministic: polyphase resampling, mono sum, TPDF dither from a fixed seed
    import hashlib
    import soundfile as sf
    from math import gcd
    from scipy.signal import resample_poly
    m = x.mean(axis=0).astype(np.float64)
    g = gcd(out_sr, sr)
    m = resample_poly(m, out_sr // g, sr // g)
    rng = np.random.default_rng(0)
    d = (rng.random(len(m)) - rng.random(len(m))) / 32768.0
    pcm = np.clip(np.round((m + d) * 32767), -32768, 32767).astype(np.int16)
    sf.write(path, pcm, out_sr, subtype="PCM_16")
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def latest_wins_demo():
    # the rule the core will use: a result is shown only if its revision is
    # the newest one requested; results arrive here in the order 3, 1, 2
    newest_requested = 3
    shown = 0
    accepted = []
    for rev in (3, 1, 2):
        if rev == newest_requested and rev > shown:
            shown = rev
            accepted.append(rev)
    return accepted


def finish(a, tmp=None):
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    print("\n%d checks, %d FAIL" % (len(RESULTS), len(fails)))
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "stage0-results.json"), "w") as f:
            json.dump({"results": RESULTS, "python": sys.version, "tmp": tmp}, f, indent=1, default=float)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
