"""Engine and server tests. The Surge ones need SURGEPY_SITE (nix develop / nix flake check)."""
import io
import os
import struct
import tempfile

import numpy as np
import pytest
import soundfile as sf

os.environ.setdefault("FORGESOUND_HOME", tempfile.mkdtemp(prefix="forgesound-test-"))

from forgesound import engine, library  # noqa: E402
from forgesound.surge import SurgeWorker  # noqa: E402

needs_surge = pytest.mark.skipif(not os.environ.get("SURGEPY_SITE"), reason="needs surgepy (nix develop)")


def test_envelope_is_flat_outside_its_points():
    g = engine.envelope_curve([[10, 1.0], [20, 0.0]], 48 * 30, 48000)
    assert g[0] == 1.0 and g[48 * 15] == pytest.approx(0.5) and g[-1] == 0.0


def test_noise_is_seeded():
    p = dict(engine.NOISE_DEFAULTS)
    a = engine.noise_burst(p, 4800, 48000)
    assert np.array_equal(a, engine.noise_burst(p, 4800, 48000))
    assert not np.array_equal(a, engine.noise_burst(dict(p, seed=2), 4800, 48000))


def test_dmx_lump_is_read_by_its_bytes_not_its_extension():
    pcm = (128 + 100 * np.sin(np.arange(2205) / 5)).astype(np.uint8)
    data = struct.pack("<HHI", 3, 22050, len(pcm) + 32) + bytes(16) + pcm.tobytes() + bytes(16)
    x, sr, ch, kind = library.decode(data)
    assert (kind, sr, ch, len(x)) == ("dmx", 22050, 1, len(pcm))


def test_export_is_trailblazer_format():
    res = {"mix": np.sin(np.arange(24000) / 10) * 0.5, "sr": 48000}
    wav = engine.export_wav(res)
    info = sf.info(io.BytesIO(wav))
    assert (info.samplerate, info.channels, info.subtype) == (44100, 1, "PCM_16")
    assert wav == engine.export_wav(res)


@needs_surge
def test_default_project_renders_the_same_twice():
    w = SurgeWorker()
    p = engine.default_project()
    a = engine.render(p, w, {})["mix"]
    b = engine.render(p, w, {})["mix"]
    assert np.abs(a).max() > 1e-3
    assert np.array_equal(a, b)


@needs_surge
def test_an_old_revision_is_answered_as_stale():
    from forgesound import server
    p = engine.default_project()
    p["rev"] = 5
    assert server.render(p)["stale"] is False
    p["rev"] = 3
    assert server.render(p) == {"rev": 3, "stale": True}
