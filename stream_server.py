"""
Простой MJPEG-HTTP-сервер (без внешних зависимостей).

Ему просто отдают байты JPEG-кадра через set_frame(), а он раздаёт их всем,
кто подключился:
    http://<IP>:8080/             — страница с потоком (открывается в браузере)
    http://<IP>:8080/stream.mjpg  — сам MJPEG-поток (его читает OpenCV)
    http://<IP>:8080/snapshot.jpg — одиночный кадр

Работает в отдельном daemon-потоке и ничего не знает про Kivy.
"""

import base64
import hmac
import socket
import threading
import time
from urllib.parse import urlparse

BOUNDARY = 'frame'

INDEX_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Поток с телефона</title>
<style>
  body { background:#0a0b0e; color:#edeff3; margin:0; padding:16px;
         font-family: system-ui, -apple-system, Segoe UI, sans-serif; text-align:center; }
  h3 { font-weight:600; margin:0 0 12px; }
  img { max-width:100%; height:auto; border-radius:12px; background:#111; }
  p { color:#8b93a3; font-size:13px; }
</style></head>
<body>
  <h3>Поток с телефона</h3>
  <img src="/stream.mjpg" alt="live">
  <p>/snapshot.jpg — одиночный кадр</p>
</body></html>
"""


def local_ip():
    """IP телефона в текущей Wi-Fi-сети (без выхода в интернет)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


class MJPEGServer(threading.Thread):
    """HTTP-сервер, раздающий последний полученный кадр."""

    def __init__(self, host='0.0.0.0', port=8080, fps=15, password=None):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.fps = max(1, int(fps))
        self.password = password or None
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = None
        self.clients = 0

    # -------------------- API для приложения --------------------
    def set_frame(self, jpeg_bytes):
        """Кладём свежий кадр. Вызывается из потока анализа кадров."""
        with self._lock:
            self._frame = jpeg_bytes

    def snapshot(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    # -------------------- сервер --------------------
    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        s.settimeout(1.0)
        self._sock = s
        print('[stream] http://%s:%d/' % (local_ip(), self.port))
        while not self._stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()
        try:
            s.close()
        except OSError:
            pass

    def _handle(self, conn):
        conn.settimeout(5)
        try:
            req = b''
            while b'\r\n\r\n' not in req and len(req) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                req += chunk
            if not req:
                return

            head = req.split(b'\r\n\r\n', 1)[0].decode('latin-1', 'replace')
            lines = head.split('\r\n')
            parts = (lines[0].split(' ') + ['', ''])[:3]
            path = parts[1]

            headers = {}
            for line in lines[1:]:
                if ':' in line:
                    k, v = line.split(':', 1)
                    headers[k.strip().lower()] = v.strip()

            if not self._authorized(headers):
                conn.sendall(b'HTTP/1.1 401 Unauthorized\r\n'
                             b'WWW-Authenticate: Basic realm="cam"\r\n'
                             b'Content-Length: 0\r\n\r\n')
                return

            route = urlparse(path).path
            if route in ('/', '/index.html'):
                body = INDEX_PAGE.encode('utf-8')
                conn.sendall(b'HTTP/1.1 200 OK\r\n'
                             b'Content-Type: text/html; charset=utf-8\r\n'
                             b'Content-Length: %d\r\n\r\n' % len(body) + body)
            elif route in ('/stream.mjpg', '/stream', '/video'):
                self._stream(conn)
            elif route in ('/snapshot.jpg', '/snapshot'):
                frame = self.snapshot()
                if frame is None:
                    conn.sendall(b'HTTP/1.1 503 Service Unavailable\r\n'
                                 b'Content-Length: 0\r\n\r\n')
                else:
                    conn.sendall(b'HTTP/1.1 200 OK\r\n'
                                 b'Content-Type: image/jpeg\r\n'
                                 b'Content-Length: %d\r\n\r\n' % len(frame) + frame)
            else:
                conn.sendall(b'HTTP/1.1 404 Not Found\r\n'
                             b'Content-Length: 0\r\n\r\n')
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _stream(self, conn):
        self.clients += 1
        try:
            conn.sendall(('HTTP/1.1 200 OK\r\n'
                          'Connection: close\r\n'
                          'Cache-Control: no-store, no-cache, must-revalidate\r\n'
                          'Pragma: no-cache\r\n'
                          'Content-Type: multipart/x-mixed-replace; '
                          'boundary=%s\r\n\r\n' % BOUNDARY).encode())

            period = 1.0 / self.fps
            last = None
            while not self._stop.is_set():
                t0 = time.time()
                frame = self.snapshot()
                if frame is not None and frame is not last:
                    last = frame
                    conn.sendall(('--%s\r\nContent-Type: image/jpeg\r\n'
                                  'Content-Length: %d\r\n\r\n'
                                  % (BOUNDARY, len(frame))).encode())
                    conn.sendall(frame)
                    conn.sendall(b'\r\n')
                delay = period - (time.time() - t0)
                if delay > 0:
                    time.sleep(delay)
        except OSError:
            pass
        finally:
            self.clients -= 1

    def _authorized(self, headers):
        if not self.password:
            return True
        auth = headers.get('authorization', '')
        if not auth.lower().startswith('basic '):
            return False
        try:
            raw = base64.b64decode(auth.split(None, 1)[1]).decode('utf-8')
        except Exception:
            return False
        _, _, supplied = raw.partition(':')
        return hmac.compare_digest(supplied, self.password)
