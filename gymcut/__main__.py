"""Start GymCut: python -m gymcut [--no-lan] [--internet] [--port 8000] [--https-port 8443] [--open]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
import webbrowser


def main():
    ap = argparse.ArgumentParser(prog="gymcut")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port (studio; LAN needs the access key)")
    ap.add_argument("--https-port", type=int, default=8443, help="HTTPS port for phones on the LAN")
    ap.add_argument("--no-lan", action="store_true", help="only listen on localhost")
    ap.add_argument("--open", action="store_true", help="open the browser when ready")
    ap.add_argument("--internet", action="store_true", help="also reachable over the internet (Cloudflare quick tunnel)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import uvicorn

    from .certs import ensure_cert, lan_ips
    from .server import RUNTIME, access_key, app

    RUNTIME.update(http_port=args.port, https_port=args.https_port, lan=not args.no_lan)
    key = access_key()
    cert, keyfile = ensure_cert()
    log_cfg = {"log_level": "warning"}
    http_host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    servers = [uvicorn.Server(uvicorn.Config(app, host=http_host, port=args.port, ws_max_size=32 * 2**20, **log_cfg))]
    https_host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    servers.append(uvicorn.Server(uvicorn.Config(app, host=https_host, port=args.https_port, ssl_certfile=cert,
                                                 ssl_keyfile=keyfile, ws_max_size=32 * 2**20, **log_cfg)))

    print("\n  GymCut is starting...")
    print(f"  Desktop:  http://localhost:{args.port}")
    if not args.no_lan:
        for ip in lan_ips():
            print(f"  LAN:      http://{ip}:{args.port}/?k={key}   (studio on other devices)")
            print(f"  Phones:   https://{ip}:{args.https_port}/live?k={key}   (camera; use the QR code in the app)")
    print("  Press Ctrl+C to stop.\n")

    def warm():
        # load CUDA + model in the background so the first analysis starts fast
        try:
            from .config import settings
            from .pipeline import get_engine
            get_engine(settings.get()["pose_model"]).warmup()
            print("  Pose model ready on", "GPU" if get_engine(settings.get()["pose_model"]).half else "CPU")
        except Exception as e:  # noqa: BLE001
            print("  Model warm-up failed:", e)

    threading.Thread(target=warm, daemon=True).start()
    if args.internet:
        from . import tunnel
        tunnel.start(args.port)
        print("  Internet: starting Cloudflare tunnel... (address shown in the app under Connect phone)")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    async def run_all():
        await asyncio.gather(*(s.serve() for s in servers))

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        pass
    finally:
        from . import tunnel
        tunnel.stop()


if __name__ == "__main__":
    main()
