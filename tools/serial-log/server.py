"""Page that shows the message the room controller sends."""

from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

HTTP_PORT = 8090
TCP_PORT = 8091

lines = deque(maxlen=400)
lock = threading.Lock()
clients = []
status = "Waiting for the controller."


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Serial log</title>
<style>
  body { margin: 0; background: #111; color: #d7ffd7; font: 15px/1.45 ui-monospace, Consolas, monospace; }
  header { padding: 12px 16px; color: #9dffb0; border-bottom: 1px solid #243; }
  pre { margin: 0; padding: 12px 16px 48px; white-space: pre-wrap; word-break: break-all; }
</style>
</head>
<body>
<header id="status">Waiting for the controller.</header>
<pre id="log"></pre>
<script>
const status = document.getElementById("status");
const log = document.getElementById("log");
const events = new EventSource("/events");
events.onmessage = (event) => {
  log.textContent += event.data + "\\n";
  window.scrollTo(0, document.body.scrollHeight);
};
setInterval(() => {
  fetch("/status").then((response) => response.text()).then((text) => {
    status.textContent = text;
  }).catch(() => {
    status.textContent = "Log page lost the server.";
  });
}, 1000);
</script>
</body>
</html>
"""


class Waiter:
    def __init__(self):
        self.items = deque()
        self.cv = threading.Condition()

    def push(self, item):
        with self.cv:
            self.items.append(item)
            self.cv.notify()

    def pop(self, timeout):
        with self.cv:
            if not self.items:
                self.cv.wait(timeout)
            if not self.items:
                return None
            return self.items.popleft()


def add_line(text):
    with lock:
        lines.append(text)
        for waiter in clients:
            waiter.push(text)


def show(text):
    stamp = datetime.now().strftime("%H:%M:%S")
    add_line(f"{stamp}  {text}")


def bell_frame(frame):
    if frame[0] != 0x07 or frame[1] != 0x1F or frame[2] != 0x15:
        return False
    checksum = 0
    for byte in frame:
        checksum ^= byte
    return checksum == 0


def pull_messages(buf):
    while buf:
        if len(buf) >= 6 and bell_frame(buf[:6]):
            show("Bell")
            del buf[:6]
            continue
        newline = buf.find(b"\n")
        if newline != -1 and all(byte in (9, 13) or 32 <= byte < 127 for byte in buf[:newline]):
            text = buf[:newline].decode("ascii").strip()
            del buf[: newline + 1]
            if text:
                show(text)
            continue
        if len(buf) >= 6 or (newline != -1):
            del buf[0]
            continue
        return


def set_status(text):
    global status
    with lock:
        status = text


def tcp_session(conn, addr):
    set_status(f"Controller {addr[0]} connected.")
    conn.settimeout(0.05)
    buf = bytearray()
    try:
        while True:
            try:
                chunk = conn.recv(512)
                if chunk == b"":
                    break
                buf.extend(chunk)
            except socket.timeout:
                pass
            except OSError as error:
                print(f"serial log read failed: {error}", flush=True)
                break
            pull_messages(buf)
    finally:
        pull_messages(buf)
        conn.close()
        set_status("Waiting for the controller.")


def serve_tcp():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", TCP_PORT))
    server.listen(1)
    while True:
        conn, addr = server.accept()
        threading.Thread(target=tcp_session, args=(conn, addr), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/status":
            with lock:
                body = status.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            waiter = Waiter()
            with lock:
                pending = list(lines)
                clients.append(waiter)
            try:
                for line in pending:
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    line = waiter.pop(15)
                    if line is None:
                        self.wfile.write(b":\n\n")
                    else:
                        self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                with lock:
                    if waiter in clients:
                        clients.remove(waiter)
            return
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    threading.Thread(target=serve_tcp, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"Serial log at http://127.0.0.1:{HTTP_PORT}", flush=True)
    httpd.serve_forever()
