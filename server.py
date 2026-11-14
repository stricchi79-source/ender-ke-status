"""Schermino di stato Ender-3 V3 KE: serve index.html e /status (JSON) sulla LAN.

Uso: python server.py [IP_stampante|auto] [porta] [--ntfy ARGOMENTO]
     (default auto, porta 80)
Dal telefono apri http://IP-DEL-PC  (la porta 80 funziona meglio di 8000 sui telefoni)

Con "auto" (o se l'IP indicato non risponde più) il server cerca la stampante
nella rete locale (porta 9999 aperta): l'IP assegnato dal router può cambiare.

Con --ntfy arriva una notifica sul telefono (app gratuita ntfy) quando la stampa
finisce, si mette in pausa, viene annullata o va in errore, anche a schermo spento.
"""
import argparse, asyncio, json, os, socket, sys, threading, time, urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))

# Avviato con pythonw (esecuzione automatica, senza finestra): i messaggi vanno in server.log
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(HERE, "server.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log
    print(f"--- avvio {time.strftime('%Y-%m-%d %H:%M:%S')} ---", flush=True)

ap = argparse.ArgumentParser(description="Schermino di stato per Ender-3 V3 KE")
ap.add_argument("printer", nargs="?", default=os.environ.get("ENDER_IP", "auto"),
                help="IP della stampante, oppure 'auto' per cercarla nella rete (default auto)")
ap.add_argument("port", nargs="?", type=int, default=int(os.environ.get("ENDER_PORT", 80)),
                help="porta del server (default 80)")
ap.add_argument("--ntfy", default=os.environ.get("ENDER_NTFY", ""),
                help="argomento ntfy per le notifiche (es. ender-mario-7f3k), vuoto = spente")
ap.add_argument("--ntfy-server", default=os.environ.get("ENDER_NTFY_SERVER", "https://ntfy.sh"),
                help="server ntfy (default https://ntfy.sh)")
args = ap.parse_args()

AUTO = args.printer == "auto"
PORT = args.port
WS_PORT = 9999
printer = None if AUTO else args.printer   # IP in uso (None = da cercare)

# Lo stato viene scritto dal thread del websocket e letto dai thread HTTP:
# senza lucchetto json.dumps puo' fallire con "dictionary changed size during iteration".
lock = threading.Lock()
state: dict = {}
last = 0.0

# ---------- Notifiche (ntfy) ----------
NOMI_STATO = {1: "in stampa", 2: "finita", 3: "errore", 4: "annullata", 5: "in pausa"}


def notifica(titolo: str, testo: str, tag: str, priorita: int = 4) -> None:
    if not args.ntfy:
        return
    corpo = json.dumps({"topic": args.ntfy, "title": titolo, "message": testo,
                        "tags": [tag], "priority": priorita}).encode()

    def invia():
        try:
            req = urllib.request.Request(args.ntfy_server.rstrip("/") + "/", data=corpo,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            print("ntfy:", e, flush=True)

    threading.Thread(target=invia, daemon=True).start()


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Osservatore:
    """Confronta lo stato prima/dopo ogni messaggio e avvisa sui cambi importanti."""

    def __init__(self):
        self.stato = None
        self.errore = 0

    def controlla(self, s: dict) -> None:
        st = s.get("state")
        st = int(st) if num(st) is not None else None
        err = s.get("err") if isinstance(s.get("err"), dict) else {}
        code = int(num(err.get("errcode")) or 0)
        nome = (s.get("printFileName") or "").split("/")[-1] or "stampa"

        prima = self.stato
        if prima is not None and st != prima:
            if st == 2 and prima in (1, 5):
                durata = num(s.get("printJobTime"))
                extra = f" in {int(durata // 3600)}h {int(durata % 3600 // 60):02d}m" if durata else ""
                notifica("Stampa finita ✅", f"{nome} completata{extra}.", "white_check_mark")
            elif st == 5 and prima == 1:
                notifica("Stampa in pausa ⏸", nome, "pause_button")
            elif st == 4 and prima in (1, 5):
                notifica("Stampa annullata", nome, "x", 3)
            elif st == 1 and prima in (0, 2, 4, None):
                notifica("Stampa iniziata", nome, "printer", 2)
        if code and code != self.errore:
            notifica("Errore stampante ⚠️", f"Codice {code} durante {nome}.", "warning", 5)
        if st is not None:
            self.stato = st
        self.errore = code


osservatore = Osservatore()


# ---------- Ricerca della stampante nella rete ----------
def local_subnet():
    """Prefisso /24 della scheda di rete usata per uscire (es. '192.168.1.')."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 9))  # nessun pacchetto inviato: serve solo a scegliere l'interfaccia
            ip = s.getsockname()[0]
        return ip.rsplit(".", 1)[0] + "."
    except OSError:
        return None


async def find_printer():
    """Scansiona la /24 locale cercando la porta 9999 della stampante."""
    net = local_subnet()
    if not net:
        return None

    async def probe(ip):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, WS_PORT), 0.8)
            w.close()
            return ip
        except Exception:
            return None

    for ip in await asyncio.gather(*(probe(f"{net}{i}") for i in range(1, 255))):
        if ip:
            return ip
    return None


async def pump():
    global last, printer
    failures = 0
    while True:
        if printer is None or (failures >= 2 and AUTO) or failures >= 5:
            found = await find_printer()
            if found and found != printer:
                print(f"stampante trovata su {found}", flush=True)
            printer = found or printer
            failures = 0
            if printer is None:
                await asyncio.sleep(15)
                continue
        try:
            async with websockets.connect(f"ws://{printer}:{WS_PORT}", open_timeout=8, ping_interval=20) as ws:
                failures = 0
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
                            with lock:
                                state.update(d)
                                last = time.time()
                                copia = dict(state)
                            osservatore.controlla(copia)
                finally:
                    t.cancel()
        except Exception as e:
            failures += 1
            print("websocket:", e, flush=True)
        await asyncio.sleep(3)


# Solo questi file vengono serviti: non il resto della cartella (.git, server.log, script…)
PUBBLICI = ("/", "/index.html", "/manifest.webmanifest")
CARTELLE_PUBBLICHE = ("/fonts/", "/icons/")


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def end_headers(self):
        if not self.path.startswith("/status"):
            # i font e le icone non cambiano: il telefono li tiene per un giorno
            fisso = self.path.startswith(CARTELLE_PUBBLICHE)
            self.send_header("Cache-Control", "max-age=86400" if fisso else "no-cache")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/status"):
            with lock:
                body = json.dumps({"age": round(time.time() - last, 1) if last else None,
                                   "printer": printer or "ricerca stampante…", "s": state}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.path.split("?")[0]
        if path in PUBBLICI or (path.startswith(CARTELLE_PUBBLICHE) and ".." not in path):
            super().do_GET()
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


H.extensions_map.update({".webmanifest": "application/manifest+json", ".woff2": "font/woff2"})

threading.Thread(target=lambda: asyncio.run(pump()), daemon=True).start()
print(f"Schermino su http://0.0.0.0:{PORT}  (stampante {args.printer})", flush=True)
if args.ntfy:
    print(f"Notifiche ntfy attive: {args.ntfy_server.rstrip('/')}/{args.ntfy}", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
