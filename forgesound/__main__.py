"""python -m forgesound [--port N] [--no-browser]: start the local studio."""
import argparse
import threading
import webbrowser

import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="forgesound")
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = "http://127.0.0.1:%d/" % a.port
    print("ForgeSound: " + url)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    # 127.0.0.1 only: a local program, not a network service
    uvicorn.run("forgesound.server:app", host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
