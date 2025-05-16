#!/usr/bin/env python3
"""
PixBoard — cooperative pixel-art board over TCP (single-file version)
Run without arguments to *host* a board:
    python pixboard.py
Run with --connect <host_ip> to *join* an existing board:
    python pixboard.py --connect 192.168.1.23
"""

import argparse
import colorsys
import json
import queue
import socket
import threading
import time
import tkinter as tk
from enum import Enum, auto
from typing import Callable, List
from collections import deque

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIZE = 32          # canvas is SIZE × SIZE pixels
PIX  = 14          # each pixel's on-screen square size (px)
PORT = 7007        # default TCP porta
PALETTE_SIZE = 32  # palette grid 32 × 32 → occupies same visual area

# ---------------------------------------------------------------------------
# Tool selector
# ---------------------------------------------------------------------------

class ToolMode(Enum):
    POINT = auto()
    LINE = auto()
    FILL = auto()

# ---------------------------------------------------------------------------
# Local IP helper
# ---------------------------------------------------------------------------

def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]

# ---------------------------------------------------------------------------
# Palette generation
# ---------------------------------------------------------------------------

def generate_palette() -> List[str]:
    
    colors: List[str] = []
    for y in range(PALETTE_SIZE):
        l = 0.9 - 0.8 * (y / (PALETTE_SIZE - 1))
        for x in range(PALETTE_SIZE):
            if x == 0 and y == 0:
                colors.append("")
                continue
            h = x / PALETTE_SIZE
            r, g, b = colorsys.hls_to_rgb(h, l, 1.0)
            colors.append(f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}")

    # Add grayscale row
    for x in range(PALETTE_SIZE):
        g = int((x / (PALETTE_SIZE - 1)) * 255)
        colors.append(f"#{g:02X}{g:02X}{g:02X}")
    return colors


PALETTE_COLORS = generate_palette()

# ---------------------------------------------------------------------------
# Network layer (host ↔ client)
# ---------------------------------------------------------------------------

class NetPeer:
    def __init__(self, listen: bool, host_addr: str | None, port: int, on_px: Callable[[int, int, str], None]):
        self.on_px = on_px
        self.q_out = queue.Queue()
        self.keepalive_interval = 3
        self.active = True

        if listen:
            threading.Thread(target=self._host_loop, args=(port,), daemon=True).start()
        else:
            threading.Thread(target=self._client_loop, args=(host_addr, port), daemon=True).start()

    @staticmethod
    def _recv_json(sock, buffer: bytearray) -> dict:
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("peer closed")
            buffer.extend(chunk)
        line_end = buffer.index(b"\n")
        line = buffer[:line_end]
        del buffer[:line_end + 1]
        return json.loads(line.decode())


    @staticmethod
    def _send_json(sock, obj):
        msg = json.dumps(obj) + "\n"
        sock.sendall(msg.encode())

    def send_px(self, x, y, color):
        self.q_out.put({"x": x, "y": y, "c": color})

    def _host_loop(self, port):
        board = [[""] * SIZE for _ in range(SIZE)]
        clients: dict[str, socket.socket] = {}
        lock = threading.Lock()

        def handle_client(conn, addr):
            ip = addr[0]
            try:
                buffer = bytearray()
                self._send_json(conn, {"snapshot": board})
                while True:
                    msg = self._recv_json(conn, buffer)
                    if msg.get("noop"):
                        print(f"[Host] NOOP (Keep-alive) from {ip}")
                        self._send_json(conn, {"ack": "noop"})
                        continue

                    if "x" in msg:
                        x, y, c = msg["x"], msg["y"], msg["c"]
                        board[y][x] = c
                        self.on_px(x, y, c)
                        with lock:
                            for p in clients.values():
                                try:
                                    self._send_json(p, msg)
                                except:
                                    pass
            except Exception as e:
                print(f"[Host] Disconnected: {ip}: {e}")
            finally:
                conn.close()
                with lock:
                    if ip in clients and clients[ip] == conn:
                        del clients[ip]
                        print(f"[Host] Client {ip} removed.")

        def forward_host_events():
            while True:
                msg = self.q_out.get()
                x, y, c = msg["x"], msg["y"], msg["c"]
                board[y][x] = c
                self.on_px(x, y, c)
                with lock:
                    for p in clients.values():
                        try:
                            self._send_json(p, msg)
                        except:
                            pass

        threading.Thread(target=forward_host_events, daemon=True).start()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen()
        print(f"[Host] Listening on {local_ip()}:{port}")

        while True:
            conn, addr = srv.accept()
            ip = addr[0]
            with lock:
                if ip in clients:
                    try:
                        clients[ip].close()
                    except:
                        pass
                    print(f"[Host] Replacing existing connection from {ip}")
                clients[ip] = conn
            print(f"[Host] Client connected: {ip}")
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

    def _client_loop(self, host, port):
        buffer = bytearray()
        def sender(sock):
            while self.active:
                try:
                    msg = self.q_out.get(timeout=1)
                    self._send_json(sock, msg)
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[Client] Sender error: {e}")
                    break

        def keep_alive(sock):
            while self.active:
                try:
                    time.sleep(self.keepalive_interval)
                    self._send_json(sock, {"noop": 1})
                except Exception as e:
                    print(f"[Client] Keep-alive error: {e}")
                    break

        try:
            s = socket.create_connection((host, port), timeout=10)
            print(f"[Client] Connected to {host}:{port}")
            threading.Thread(target=sender, args=(s,), daemon=True).start()
            threading.Thread(target=keep_alive, args=(s,), daemon=True).start()
            while True:
                msg = self._recv_json(s, buffer)
                if "snapshot" in msg:
                    snap = msg["snapshot"]
                    for y in range(SIZE):
                        for x in range(SIZE):
                            self.on_px(x, y, snap[y][x])
                elif "x" in msg:
                    self.on_px(msg["x"], msg["y"], msg["c"])
                elif "ack" in msg:
                    if msg["ack"] == "noop":
                        print(f"[Client] Keep-alive ACK received")
        except Exception as e:
            print(f"[Client] Disconnected or error: {e}")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PixBoardGUI:
    def __init__(self, peer: NetPeer) -> None:
        self.peer = peer
        self.root = tk.Tk()
        self.root.title("PixBoard")

        self.tool_mode = ToolMode.POINT
        self.selected_palette_id = None


        self.canvas = tk.Canvas(
            self.root, width=SIZE * PIX, height=SIZE * PIX, bg="white"
        )
        self.canvas.pack(side="top")
        self.px_ids: List[List[int]] = [[None] * SIZE for _ in range(SIZE)]
        for y in range(SIZE):
            for x in range(SIZE):
                x0, y0 = x * PIX, y * PIX
                self.px_ids[y][x] = self.canvas.create_rectangle(
                    x0,
                    y0,
                    x0 + PIX,
                    y0 + PIX,
                    fill="",
                    outline="#EEE",
                )
        self.canvas.bind("<Button-1>", self._board_click)
        self.canvas.bind("<B1-Motion>", self._board_drag)

        self.palette_canvas = tk.Canvas(
            self.root,
            width=SIZE * PIX,
            height=(SIZE + 1) * PIX, # extra row for Grayscale palette
            highlightthickness=0,
        )
        self.palette_canvas.pack(side="bottom")
        for idx, col in enumerate(PALETTE_COLORS):
            px = idx % PALETTE_SIZE
            py = idx // PALETTE_SIZE
            x0, y0 = px * PIX, py * PIX
            self.palette_canvas.create_rectangle(
                x0,
                y0,
                x0 + PIX,
                y0 + PIX,
                fill=col if col else "white",
                outline="#DDD",
            )
            if col == "":
                self.palette_canvas.create_line(x0, y0, x0 + PIX, y0 + PIX, fill="#888")
                self.palette_canvas.create_line(x0 + PIX, y0, x0, y0 + PIX, fill="#888")
        self.palette_canvas.bind("<Button-1>", self._palette_click)

        self.current_color = "#000000"
        peer.on_px = self.set_px

        self._add_toolbar()

    def _add_toolbar(self):
        frame = tk.Frame(self.root)
        frame.pack(side="bottom", pady=5)

        ip_label = tk.Label(frame, text=f"Your IP: {local_ip()}", fg="blue")
        ip_label.pack(side="top", pady=2)
        for mode in ToolMode:
            b = tk.Button(
                frame,
                text=mode.name.title(),
                command=lambda m=mode: self._set_tool(m),
                width=10
            )
            b.pack(side="left", padx=5)

    def _set_tool(self, mode):
        self.tool_mode = mode
        print(f"[GUI] Tool mode set to: {mode.name}")

    def _board_click(self, evt):
        x, y = evt.x // PIX, evt.y // PIX
        if not (0 <= x < SIZE and 0 <= y < SIZE): return
        print(f"[GUI] Click at ({x}, {y}) with tool {self.tool_mode.name} and color {self.current_color or 'transparent'}")
        if self.tool_mode == ToolMode.FILL:
            self._fill(x, y, self.current_color)
        else:
            self.set_px(x, y, self.current_color)
            self.peer.send_px(x, y, self.current_color)

    def _board_drag(self, evt):
        if self.tool_mode != ToolMode.LINE:
            return
        self._board_click(evt)

    def _palette_click(self, evt):
        x, y = evt.x // PIX, evt.y // PIX
        if 0 <= x < PALETTE_SIZE and 0 <= y < PALETTE_SIZE + 1:
            idx = y * PALETTE_SIZE + x
            self.current_color = PALETTE_COLORS[idx]
            print(f"[GUI] Color selected: {self.current_color or 'transparent'}")

            # Remove previous highlight
            if self.selected_palette_id is not None:
                self.palette_canvas.delete(self.selected_palette_id)

            # Draw new highlight
            x0, y0 = x * PIX, y * PIX
            x1, y1 = x0 + PIX, y0 + PIX

            def invert_color(hex_color):
                if not hex_color:
                    return "#000000"
                r = 255 - int(hex_color[1:3], 16)
                g = 255 - int(hex_color[3:5], 16)
                b = 255 - int(hex_color[5:7], 16)
                return f"#{r:02X}{g:02X}{b:02X}"

            highlight_color = invert_color(self.current_color)
            self.selected_palette_id = self.palette_canvas.create_rectangle(
                x0, y0, x1, y1,
                outline=highlight_color,
                width=2
            )


    def set_px(self, x: int, y: int, color: str):
        fill = color
        print(f"[GUI] set_px({x}, {y}, '{color}')")
        self.canvas.itemconfigure(self.px_ids[y][x], fill=fill)

    def _fill(self, x: int, y: int, new_color: str):
        current = self.canvas.itemcget(self.px_ids[y][x], "fill")
        if current == new_color:
            return
        visited = set()
        q = deque([(x, y)])
        print(f"[GUI] Starting flood fill at ({x}, {y}) from '{current}' to '{new_color}'")
        while q:
            cx, cy = q.popleft()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            if not (0 <= cx < SIZE and 0 <= cy < SIZE):
                continue
            item = self.px_ids[cy][cx]
            col = self.canvas.itemcget(item, "fill")
            if col != current:
                continue
            self.set_px(cx, cy, new_color)
            self.peer.send_px(cx, cy, new_color)
            q.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])

    def run(self) -> None:
        print("[GUI] PixBoard GUI started.")
        self.root.mainloop()

# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", help="IP of host to join")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    peer = NetPeer(
        listen=args.connect is None,
        host_addr=args.connect,
        port=args.port,
        on_px=lambda *_: None,
    )
    gui = PixBoardGUI(peer)
    gui.run()
