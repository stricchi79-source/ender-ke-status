"""Schermino di stato Ender-3 V3 KE: serve index.html e /status (JSON) sulla LAN.

Uso: python server.py [IP_stampante] [porta]   (default 192.168.1.12, 80)
Dal telefono apri http://IP-DEL-PC  (la porta 80 funziona meglio di 8000 sui telefoni)
"""
import asyncio, json, os, sys, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import websockets

PRINTER = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.12"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 80
HERE = os.path.dirname(os.path.abspath(__file__))
state: dict = {}
last = 0.0


async def pump():
    global last
    while True:
        try:
            async with websockets.connect(f"ws://{PRINTER}:9999", open_timeout=8, ping_interval=20) as ws:
                async def hb():
                    while True:
                        await asyncio.sleep(10)
                        await ws.send(json.dumps({"ModeCode": "heart_beat"}))
                t = asyncio.create_task(hb())
                try:
                    async for msg in ws:
                        try:
                            d = json.loads(msg)
                        except ValueError:
                            continue
                        if isinstance(d, dict):
                            state.update(d)
                            last = time.time()
                finally:
                    t.cancel()
        except Exception as e:
            print("websocket:", e, flush=True)
        await asyncio.sleep(3)


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def do_GET(self):
        if self.path.startswith("/status"):
            body = json.dumps({"age": round(time.time() - last, 1) if last else None,
                               "printer": PRINTER, "s": state}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, *a):
        pass


threading.Thread(target=lambda: asyncio.run(pump()), daemon=True).start()
print(f"Schermino su http://0.0.0.0:{PORT}  (stampante {PRINTER})", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
