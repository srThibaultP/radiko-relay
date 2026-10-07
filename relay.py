#!/usr/bin/env python3
"""radiko -> plain HTTP MP3 relay. GET /<STATION_ID>.mp3 (e.g. /FMJ.mp3 for J-WAVE).
ffmpeg runs only while at least one client listens; token is renewed on every start."""
import base64, hashlib, os, random, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTHKEY = "bcd151073c03b352e1ef2fd66c32209da9ca0afa"  # public pc_html5 key
UA = "Mozilla/5.0"
BITRATE = os.environ.get("BITRATE", "128k")

def auth():
    h = {"X-Radiko-App": "pc_html5", "X-Radiko-App-Version": "0.0.1",
         "X-Radiko-User": "dummy_user", "X-Radiko-Device": "pc", "User-Agent": UA}
    r = urllib.request.urlopen(urllib.request.Request("https://radiko.jp/v2/api/auth1", headers=h), timeout=15)
    token = r.headers["X-Radiko-AuthToken"]
    off, ln = int(r.headers["X-Radiko-KeyOffset"]), int(r.headers["X-Radiko-KeyLength"])
    partial = base64.b64encode(AUTHKEY[off:off + ln].encode()).decode()
    h2 = {"X-Radiko-AuthToken": token, "X-Radiko-PartialKey": partial,
          "X-Radiko-User": "dummy_user", "X-Radiko-Device": "pc", "User-Agent": UA}
    area = urllib.request.urlopen(urllib.request.Request("https://radiko.jp/v2/api/auth2", headers=h2), timeout=15).read().decode().strip()
    print("auth ok, area:", area, flush=True)
    return token

class Station:
    def __init__(self, sid):
        self.sid, self.clients, self.proc, self.lock = sid, [], None, threading.Lock()

    def start(self):
        token = auth()
        lsid = hashlib.md5(str(random.random()).encode()).hexdigest()
        # radiko's current playlist_create_url scheme
        url = (f"https://radiko.jp/v3/station/stream/pc_html5/{self.sid}.xml")
        xml = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15).read().decode()
        import re
        m = re.search(r'<playlist_create_url>(https://[^<]+)</playlist_create_url>', xml) or \
            re.search(r'<url[^>]*areafree="0"[^>]*>\s*<playlist_create_url>(https://[^<]+)', xml)
        base = m.group(1) if m else f"https://f-radiko.smartstream.ne.jp/{self.sid}/_definst_/simul-stream.stream/playlist.m3u8"
        sep = "&" if "?" in base else "?"
        pl = f"{base}{sep}station_id={self.sid}&l=15&lsid={lsid}&type=b"
        cmd = ["ffmpeg", "-loglevel", "warning", "-f", "aac", "-i", "-", "-vn",
               "-c:a", "libmp3lame", "-b:a", BITRATE, "-f", "mp3", "-"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self.feed, args=(self.proc, pl, token), daemon=True).start()
        threading.Thread(target=self.pump, args=(self.proc,), daemon=True).start()

    def feed(self, proc, pl, token):
        """Follow the HLS playlist ourselves (ffmpeg's HLS demuxer rejects radiko's medialist)."""
        import re
        H = {"X-Radiko-AuthToken": token, "User-Agent": UA}
        get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=15).read()
        try:
            master = get(pl).decode()
            med = re.search(r"https://\S+", master.split("#EXT-X-STREAM-INF")[1].split("\n", 1)[1]).group(0)
            seen, order = set(), []
            while proc.poll() is None:
                for u in re.findall(r"^https://\S+$", get(med).decode(), re.M):
                    if u not in seen:
                        seen.add(u); order.append(u)
                        proc.stdin.write(get(u)); proc.stdin.flush()
                        if len(order) > 50: seen.discard(order.pop(0))
                time.sleep(2)
        except Exception as e:
            print("feed stopped:", e, flush=True)
        finally:
            try: proc.stdin.close()
            except Exception: pass
            if proc.poll() is None: proc.terminate()

    def pump(self, proc):
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk: break
            for q in list(self.clients):
                try: q.append(chunk)
                except Exception: pass
        proc.wait()

    def attach(self, q):
        with self.lock:
            self.clients.append(q)
            if not self.proc or self.proc.poll() is not None:
                self.start()

    def detach(self, q):
        with self.lock:
            self.clients[:] = [c for c in self.clients if c is not q]
            if not self.clients and self.proc and self.proc.poll() is None:
                self.proc.terminate()

stations, slock = {}, threading.Lock()

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        print('GET', self.client_address[0], self.path, self.headers.get('User-Agent'), flush=True)
        if self.path.startswith("/logo/"):
            f = "/logos/" + os.path.basename(self.path.split("?")[0])
            if os.path.isfile(f):
                d = open(f, "rb").read()
                self.send_response(200); self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
            else: self.send_error(404)
            return
        sid = self.path.strip("/").split(".")[0].split("?")[0]
        if not sid.replace("-", "").isalnum():
            self.send_error(404); return
        with slock: st = stations.setdefault(sid, Station(sid))
        q = []
        try: st.attach(q)
        except Exception as e:
            print("attach failed:", e, flush=True); self.send_error(502); return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("icy-name", f"radiko {sid}")
        self.end_headers()
        try:
            last = time.time()
            while True:
                if q:
                    self.wfile.write(q.pop(0)); last = time.time()
                else:
                    if time.time() - last > 30 or (st.proc and st.proc.poll() is not None): break
                    time.sleep(0.02)
        except Exception: pass
        finally: st.detach(q)
    def log_message(self, fmt, *a): print('req', self.client_address[0], fmt % a, self.headers.get('User-Agent'), flush=True)

ThreadingHTTPServer(("0.0.0.0", 8765), H).serve_forever()
