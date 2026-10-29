"""Render a ForgeSound project (a small JSON graph) into audio.

A project is a list of layers on one timeline. Every layer has a source
(a Surge note, a noise burst or a sample), a gain, a start time and an
amplitude envelope of breakpoints (ms, gain). The mix goes through a simple
overload guard. Everything here is deterministic: noise draws from the
layer's seed, Surge runs with retrigger on (see forgesound/surge.py).
"""
import base64
import io
import math
import time
from fractions import Fraction

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, sosfilt, butter

SR = 48000

SURGE_DEFAULTS = {"osc": "sine", "note": 50, "fine": 0.0, "gate_ms": 60, "decay_ms": 120, "sustain": 0.0,
                  "filter": "lp12", "cutoff_hz": 2400, "resonance": 0.15}
NOISE_DEFAULTS = {"freq_hz": 3500, "q": 1.2, "decay_ms": 6, "seed": 1}
SAMPLE_DEFAULTS = {"asset": None, "semitones": 0.0, "trim_ms": 0.0, "reverse": False}

DEFAULT_ENV = [[0, 0.0], [0.5, 1.0], [40, 0.45], [260, 0.0]]


def default_project():
    return {
        "rev": 0,
        "name": "Новый звук",
        "sr": SR,
        "duration_ms": 500,
        "layers": [
            {"id": "L1", "kind": "noise", "name": "Щелчок (шум)", "mute": False, "gain_db": -4.0, "start_ms": 0.0,
             "env": [[0, 0.0], [0.2, 1.0], [8, 0.3], [30, 0.0]], "params": dict(NOISE_DEFAULTS)},
            {"id": "L2", "kind": "surge", "name": "Тело (Surge)", "mute": False, "gain_db": 0.0, "start_ms": 0.5,
             "env": [list(p) for p in DEFAULT_ENV], "params": dict(SURGE_DEFAULTS)},
        ],
        "master": {"gain_db": 0.0, "ceiling_db": -1.0},
    }


# ------------------------------------------------------------ sources
def noise_burst(p, n, sr):
    rng = np.random.default_rng(int(p.get("seed", 1)))
    x = rng.standard_normal(n)
    f = float(p.get("freq_hz", 3500))
    q = max(0.3, float(p.get("q", 1.2)))
    lo, hi = f / (1 + 0.5 / q), min(f * (1 + 0.5 / q), sr * 0.45)
    x = sosfilt(butter(2, [lo, hi], "bandpass", fs=sr, output="sos"), x)
    t = np.arange(n) / sr
    x *= np.exp(-t / max(0.0005, float(p.get("decay_ms", 6)) / 1000))
    return x / (np.abs(x).max() + 1e-12)


def varispeed(x, semitones):
    # tape-style: pitch and speed change together (a transient stays a transient)
    if abs(semitones) < 1e-6:
        return x
    r = Fraction(2 ** (semitones / 12)).limit_denominator(400)
    return resample_poly(x, r.denominator, r.numerator)


def sample_source(p, assets, sr):
    a = assets.get(p.get("asset"))
    if a is None:
        return np.zeros(0)
    x = a["audio"]
    if a["sr"] != sr:
        g = math.gcd(int(a["sr"]), sr)
        x = resample_poly(x, sr // g, int(a["sr"]) // g)
    x = x[int(max(0.0, float(p.get("trim_ms", 0))) * sr / 1000):]
    if p.get("reverse"):
        x = x[::-1]
    return varispeed(x, float(p.get("semitones", 0)))


# ------------------------------------------------------------ envelope
def envelope_curve(points, n, sr):
    """Breakpoints (ms, gain) -> a gain per sample; flat before the first and after the last point."""
    pts = sorted((float(t), float(g)) for t, g in points) or [(0.0, 1.0)]
    t = np.arange(n) * 1000.0 / sr
    return np.interp(t, [p[0] for p in pts], [p[1] for p in pts])


# ------------------------------------------------------------ metrics
def a_weighted_rms_50ms(x, sr):
    # the loudness Trailblazer calibrates its hit sounds with (tools/f10_hitsounds.a_rms):
    # A-weighted RMS of the loudest 50 ms
    if len(x) == 0:
        return 0.0
    f = np.fft.rfftfreq(len(x), 1 / sr)
    f2 = f ** 2
    ra = (12194 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
    y = np.fft.irfft(np.fft.rfft(x) * ra * 1.2589, len(x))
    w = int(0.05 * sr)
    if len(y) <= w:
        return float(np.sqrt((y ** 2).mean()))
    return float(np.sqrt(np.convolve(y ** 2, np.ones(w) / w, "valid").max()))


def db(v):
    return 20 * math.log10(max(v, 1e-12))


def metrics(x, sr):
    peak = float(np.abs(x).max()) if len(x) else 0.0
    above = np.nonzero(np.abs(x) > peak * 10 ** (-60 / 20))[0] if peak > 0 else []
    onset = np.nonzero(np.abs(x) > peak * 0.1)[0] if peak > 0 else []
    imax = int(np.argmax(np.abs(x))) if peak > 0 else 0
    return {
        "peak_db": round(db(peak), 1),
        "loud_a50_db": round(db(a_weighted_rms_50ms(x, sr)), 1),
        "tail_ms": round((above[-1] + 1) * 1000 / sr, 1) if len(above) else 0.0,
        "attack_ms": round((imax - onset[0]) * 1000 / sr, 2) if len(onset) else 0.0,
    }


def peaks(x, buckets=600):
    if len(x) == 0:
        return []
    n = int(math.ceil(len(x) / buckets))
    pad = np.pad(x, (0, n * buckets - len(x)))
    m = pad.reshape(buckets, n)
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(m.min(1), m.max(1))]


def wav_bytes(x, sr, subtype="PCM_16"):
    buf = io.BytesIO()
    sf.write(buf, np.clip(x, -1, 1), sr, format="WAV", subtype=subtype)
    return buf.getvalue()


# ------------------------------------------------------------ render
def render(project, surge, assets):
    sr = int(project.get("sr", SR))
    n = int(float(project.get("duration_ms", 500)) * sr / 1000)
    t0 = time.perf_counter()
    mix = np.zeros(n)
    layers_out, layer_ms = {}, {}
    for L in project.get("layers", []):
        t1 = time.perf_counter()
        start = int(max(0.0, float(L.get("start_ms", 0))) * sr / 1000)
        room = max(0, n - start)
        p = L.get("params", {})
        if L["kind"] == "surge":
            x, _ = surge.render_note(sr, room / sr + 0.01, p)
        elif L["kind"] == "noise":
            x = noise_burst(p, room, sr)
        elif L["kind"] == "sample":
            x = sample_source(p, assets, sr)
        else:
            raise ValueError("unknown layer kind " + L["kind"])
        x = x[:room]
        x = x * envelope_curve(L.get("env", []), len(x), sr) * 10 ** (float(L.get("gain_db", 0)) / 20)
        placed = np.zeros(n)
        placed[start:start + len(x)] = x
        layers_out[L["id"]] = placed
        if not L.get("mute"):
            mix += placed
        layer_ms[L["id"]] = round((time.perf_counter() - t1) * 1000, 1)
    m = project.get("master", {})
    mix *= 10 ** (float(m.get("gain_db", 0)) / 20)
    ceiling = 10 ** (float(m.get("ceiling_db", -1)) / 20)
    pk = float(np.abs(mix).max()) if n else 0.0
    guard = pk > ceiling
    if guard:  # an overload guard, not a limiter: scales the whole mix down
        mix *= ceiling / pk
    return {
        "mix": mix, "sr": sr, "layers": layers_out, "layer_ms": layer_ms,
        "render_ms": round((time.perf_counter() - t0) * 1000, 1), "guard": guard,
    }


def render_response(project, result):
    sr = result["sr"]
    mix = result["mix"]
    return {
        "rev": project.get("rev", 0),
        "wav_b64": base64.b64encode(wav_bytes(mix, sr)).decode(),
        "peaks": {"master": peaks(mix), **{k: peaks(v) for k, v in result["layers"].items()}},
        "metrics": metrics(mix, sr),
        "layer_ms": result["layer_ms"],
        "render_ms": result["render_ms"],
        "guard": result["guard"],
    }


def export_wav(result, out_sr=44100):
    """The Trailblazer working format: 44.1 kHz, 16 bit, mono; deterministic TPDF dither."""
    x, sr = result["mix"], result["sr"]
    if sr != out_sr:
        g = math.gcd(out_sr, sr)
        x = resample_poly(x, out_sr // g, sr // g)
    rng = np.random.default_rng(0)
    d = (rng.random(len(x)) - rng.random(len(x))) / 32768.0
    pcm = np.clip(np.round((x + d) * 32767), -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, pcm, out_sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
