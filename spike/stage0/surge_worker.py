"""Surge render worker: the only process that imports surgepy.

Protocol: one JSON request per line on stdin, one JSON reply per line on
stdout. Audio goes through files (float32 .npy) so the pipe stays small.

  {"id": 1, "op": "hello"}
  {"id": 2, "op": "render", "sr": 48000, "patch": null | "/abs/x.fxp",
   "params": {"scene/0/osc/0/type": 1.0, ...}, "note": 48, "vel": 120,
   "gate_s": 0.05, "dur_s": 0.5, "input": null | "/abs/in.npy",
   "out": "/abs/out.npy"}

Started by the core as `python -s spike/stage0/surge_worker.py` (-s, not -I:
-I would drop PYTHONPATH); the core puts surgepy on PYTHONPATH for this
process only.
"""
import json
import os
import sys
import time
import traceback

import numpy as np

try:
    import surgepy
except ImportError as e:  # reported to the core instead of crashing silently
    surgepy = None
    IMPORT_ERROR = str(e)


def resolve(patch_dict, path):
    # "scene/0/osc/0/type" -> the named param inside getPatch()
    node = patch_dict
    for part in path.split("/"):
        node = node[int(part)] if part.isdigit() else node[part]
    return node


SPARE = {}  # sample rate -> a fresh, never used Surge instance


def fresh_surge(sr):
    # every render gets an untouched instance (no state left from the last
    # one); creating it costs ~60 ms, so one is prepared ahead (prepare_spare)
    s = SPARE.pop(sr, None)
    return s if s is not None else surgepy.createSurge(sr)


def prepare_spare(sr):
    if sr not in SPARE:
        SPARE[sr] = surgepy.createSurge(sr)


def render(req):
    sr = float(req.get("sr", 48000))
    t_start = time.perf_counter()
    s = fresh_surge(sr)
    if req.get("patch"):
        s.loadPatch(req["patch"])
    pd = s.getPatch()
    for path, val in (req.get("params") or {}).items():
        s.setParamVal(resolve(pd, path), float(val))
    bs = s.getBlockSize()
    n_blocks = int(np.ceil(req.get("dur_s", 0.5) * sr / bs))
    gate_blocks = max(1, int(round(req.get("gate_s", 0.05) * sr / bs)))
    out = s.createMultiBlock(n_blocks)
    inp = None
    if req.get("input"):
        x = np.load(req["input"]).astype(np.float32)
        if x.ndim == 1:
            x = np.stack([x, x])
        inp = s.createMultiBlock(n_blocks)
        n = min(inp.shape[1], x.shape[1])
        inp[:, :n] = x[:, :n]
    t0 = time.perf_counter()
    # let parameter changes settle for one block before the note (not rendered)
    s.process()
    s.playNote(0, int(req.get("note", 60)), int(req.get("vel", 120)), 0)
    if inp is None:
        s.processMultiBlock(out, 0, gate_blocks)
        s.releaseNote(0, int(req.get("note", 60)), 0)
        s.processMultiBlock(out, gate_blocks, n_blocks - gate_blocks)
    else:
        s.processMultiBlockWithInput(inp, out, 0, gate_blocks)
        s.releaseNote(0, int(req.get("note", 60)), 0)
        s.processMultiBlockWithInput(inp, out, gate_blocks, n_blocks - gate_blocks)
    ms = (time.perf_counter() - t0) * 1000
    np.save(req["out"], out)
    return {"render_ms": round(ms, 2), "total_ms": round((time.perf_counter() - t_start) * 1000, 2),
            "frames": int(out.shape[1]), "block": bs, "sr": sr}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        rep = {"id": req.get("id"), "ok": True}
        after = None
        try:
            if surgepy is None:
                raise RuntimeError("surgepy import failed: " + IMPORT_ERROR)
            if req["op"] == "hello":
                rep.update(version=surgepy.getVersion(), pid=os.getpid(),
                           data_home=os.environ.get("SURGE_DATA_HOME"))
            elif req["op"] == "render":
                rep.update(render(req))
                after = rep["sr"]
            else:
                raise ValueError("unknown op " + req["op"])
        except Exception as e:
            rep = {"id": req.get("id"), "ok": False, "error": repr(e),
                   "trace": traceback.format_exc()}
        sys.stdout.write(json.dumps(rep) + "\n")
        sys.stdout.flush()
        if after is not None:
            prepare_spare(after)  # after the reply: off the critical path


if __name__ == "__main__":
    main()
