"""The local ForgeSound backend: serves the UI and renders on request.

Listens on 127.0.0.1 only. Render requests carry the project's revision;
a request older than the newest one seen is answered as stale and never
rendered, so a slow old render can not replace a newer sound.
"""
import base64
import datetime
import json
import os
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, engine
from .library import Library
from .surge import SurgeUnavailable, SurgeWorker

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
HOME = os.environ.get("FORGESOUND_HOME", os.path.expanduser("~/ForgeSound"))

app = FastAPI(title="ForgeSound", version=__version__)
surge = SurgeWorker()
library = Library(os.path.join(HOME, "library"))
render_lock = threading.Lock()
newest = {"rev": -1}


def assets_for(project):
    for L in project.get("layers", []):
        sha = L.get("params", {}).get("asset")
        if L.get("kind") == "sample" and sha:
            library.load(sha)
    return library.assets()


@app.get("/api/health")
def health():
    status = {"forgesound": __version__, "surge": None, "surge_error": None, "home": HOME}
    try:
        surge.call({"op": "hello"})
        status["surge"] = surge.version
    except (SurgeUnavailable, OSError) as e:
        status["surge_error"] = str(e)
    return status


@app.get("/api/project/default")
def project_default():
    return engine.default_project()


@app.post("/api/render")
def render(project: dict):
    rev = int(project.get("rev", 0))
    with render_lock:
        if rev < newest["rev"]:
            return {"rev": rev, "stale": True}
        newest["rev"] = rev
    try:
        result = engine.render(project, surge, assets_for(project))
    except SurgeUnavailable as e:
        raise HTTPException(503, "Surge недоступен: " + str(e))
    except Exception as e:
        raise HTTPException(500, "Ошибка рендера: %r" % e)
    out = engine.render_response(project, result)
    out["stale"] = rev < newest["rev"]
    return out


@app.post("/api/assets")
async def add_asset(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, "пустой файл")
    name = request.headers.get("x-filename", "sample")
    try:
        return library.add(data, name)
    except Exception as e:
        raise HTTPException(415, "Не удалось прочитать звук: %r" % e)


@app.post("/api/export")
def export(project: dict):
    result = engine.render(project, surge, assets_for(project))
    wav = engine.export_wav(result)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    d = os.path.join(HOME, "exports", stamp)
    os.makedirs(d, exist_ok=True)
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.get("name", "sound")) or "sound"
    with open(os.path.join(d, name + ".wav"), "wb") as f:
        f.write(wav)
    manifest = {"forgesound": __version__, "surge": surge.version, "rev": project.get("rev"),
                "format": "WAV PCM 16 bit, mono, 44100 Hz", "metrics": engine.metrics(result["mix"], result["sr"]),
                "project": project}
    with open(os.path.join(d, "export-manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    return {"dir": d, "file": name + ".wav", "wav_b64": base64.b64encode(wav).decode()}


@app.get("/")
def index():
    return FileResponse(os.path.join(UI, "index.html"))


app.mount("/ui", StaticFiles(directory=UI), name="ui")
