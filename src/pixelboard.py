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
from typing import Callable, List

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIZE = 32          # canvas is SIZE × SIZE pixels
PIX  = 16          # each pixel's on-screen square size (px)
PORT = 7007        # default TCP port

PALETTE_SIZE = 32  # palette grid 32 × 32 → occupies same visual area


# ---------------------------------------------------------------------------
# Palette generation
# ---------------------------------------------------------------------------

def generate_palette() -> List[str]:
    """
    Return a flat list of PALETTE_SIZE² HTML color strings.
    The first entry (0,0) is transparent: ''.
    Remaining cells: hue varies left→right, lightness varies top→bottom.
    """
    colors: List[str] = []
    for y in range(PALETTE_SIZE):
        # Lightness 0.9 → 0.1 (top clear, bottom dark)
        l = 0.9 - 0.8 * (y / (PALETTE_SIZE - 1))
        for x in range(PALETTE_SIZE):
            if x == 0 and y == 0:
                colors.append("")   # transparent
                continue
            h = x / PALETTE_SIZE
            r, g, b = colorsys.hls_to_rgb(h, l, 1.0)
            colors.append(f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}")
    return colors


PALETTE_COLORS = generate_palette()


# ---------------------------------------------------------------------------
# Network layer (host ↔ client)
# ---------------------------------------------------------------------------

class NetPeer:
    """
    Network abstraction. Creates either:
      • a *host* that accepts clients, keeps the board, broadcasts changes
      • a *client* that connects to a host and syncs changes

    Public API:
        send_px(x, y, color)      – push pixel change to the network
        on_px(x, y, color)        – callback invoked on any remote/local change
    """

    def __init__(
        self,
        listen: bool,
        host_addr: str | None,
        port: int,
        on_px: Callable[[int, int, str], None],
    ) -> None:
        self.on_px = on_px
        self.q_out: "queue.Queue[dict]" = queue.Queue()

        if listen:
            threading.Thread(
                target=self._host_loop, args=(port,), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._client_loop, args=(host_addr, port), daemon=True
            ).start()

    # ---------- Helpers ----------

    @staticmethod
    def _recv_json(sock: socket.socket) -> dict:
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("peer closed")
            data += chunk
        return json.loads(data.decode())

    @staticmethod
    def _send_json(sock: socket.socket, obj: dict) -> None:
        sock.sendall((json.dumps(obj) + "\n").encode())

    def send_px(self, x: int, y: int, color: str) -> None:
        """Queue a pixel change for network transmission."""
        self.q_out.put({"x": x, "y": y, "c": color})

    # ---------- Host mode ----------

    def _host_loop(self, port: int) -> None:
        board = [[""] * SIZE for _ in range(SIZE)]  # transparent board
        clients: list[socket.socket] = []

        def client_thread(conn: socket.socket) -> None:
            try:
                # 1) Send snapshot
                self._send_json(conn, {"snapshot": board})
                # 2) Receive pixel updates from this client
                while True:
                    msg = self._recv_json(conn)
                    if "x" in msg:
                        x, y, c = msg["x"], msg["y"], msg["c"]
                        if 0 <= x < SIZE and 0 <= y < SIZE:
                            board[y][x] = c
                            self.on_px(x, y, c)
                            # broadcast to everyone (including sender)
                            for peer in clients:
                                try:
                                    self._send_json(peer, msg)
                                except Exception:
                                    pass
            except Exception:
                pass
            finally:
                conn.close()
                if conn in clients:
                    clients.remove(conn)

        def outgoing_broadcast() -> None:
            """Forward host's own q_out changes to all clients."""
            while True:
                msg = self.q_out.get()
                x, y, c = msg["x"], msg["y"], msg["c"]
                board[y][x] = c
                # local display
                self.on_px(x, y, c)
                # broadcast
                for peer in clients:
                    try:
                        self._send_json(peer, msg)
                    except Exception:
                        pass

        threading.Thread(target=outgoing_broadcast, daemon=True).start()

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen()
        print(f"[PixBoard] Hosting on *:{port}")

        while True:
            conn, _ = srv.accept()
            clients.append(conn)
            threading.Thread(target=client_thread, args=(conn,), daemon=True).start()

    # ---------- Client mode ----------

    def _client_loop(self, host: str, port: int) -> None:
        def client_sender(sock: socket.socket) -> None:
            while True:
                msg = self.q_out.get()
                try:
                    self._send_json(sock, msg)
                except Exception:
                    break  # Sender thread terminates on failure

        while True:  # Reconnect loop
            try:
                with socket.create_connection((host, port), timeout=5) as s:
                    print(f"[PixBoard] Connected to {host}:{port}")
                    # Start sender
                    threading.Thread(target=client_sender, args=(s,), daemon=True).start()
                    # Receive loop
                    while True:
                        msg = self._recv_json(s)
                        if "snapshot" in msg:
                            snap = msg["snapshot"]
                            for y in range(SIZE):
                                for x in range(SIZE):
                                    self.on_px(x, y, snap[y][x])
                        else:
                            self.on_px(msg["x"], msg["y"], msg["c"])
            except Exception as e:
                print("[PixBoard] Disconnected:", e, "— retrying in 2 s")
                time.sleep(2)


# ---------------------------------------------------------------------------
# GUI layer
# ---------------------------------------------------------------------------

class PixBoardGUI:
    def __init__(self, peer: NetPeer) -> None:
        self.peer = peer
        self.root = tk.Tk()
        self.root.title("PixBoard")

        # ---------- Canvas (drawing board) ----------
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
                    fill="",            # transparent
                    outline="#EEE",
                )
        self.canvas.bind("<Button-1>", self._board_click)

        # ---------- Palette (same visual size as canvas) ----------
        self.palette_canvas = tk.Canvas(
            self.root,
            width=SIZE * PIX,
            height=SIZE * PIX,
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
            if col == "":  # mark transparent with ✕
                self.palette_canvas.create_line(x0, y0, x0 + PIX, y0 + PIX, fill="#888")
                self.palette_canvas.create_line(x0 + PIX, y0, x0, y0 + PIX, fill="#888")
        self.palette_canvas.bind("<Button-1>", self._palette_click)

        self.current_color = "#000000"  # default black

        # Register network callback
        peer.on_px = self.set_px

    # ---------- Event handlers ----------

    def _board_click(self, evt):
        x, y = evt.x // PIX, evt.y // PIX
        if 0 <= x < SIZE and 0 <= y < SIZE:
            self.set_px(x, y, self.current_color)
            self.peer.send_px(x, y, self.current_color)

    def _palette_click(self, evt):
        x, y = evt.x // PIX, evt.y // PIX
        if 0 <= x < PALETTE_SIZE and 0 <= y < PALETTE_SIZE:
            idx = y * PALETTE_SIZE + x
            self.current_color = PALETTE_COLORS[idx]

    # ---------- Pixel painting ----------

    def set_px(self, x: int, y: int, color: str):
        """
        Update one square. Empty string ('') = transparent (no fill).
        """
        fill = color  # '' works for Tk: removes fill
        self.canvas.itemconfigure(self.px_ids[y][x], fill=fill)

    # ---------- Main loop ----------

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PixBoard cooperative pixel-art board")
    parser.add_argument("--connect", help="IP of host to join")
    parser.add_argument("--port", type=int, default=PORT, help="TCP port (default 7007)")
    args = parser.parse_args()

    peer = NetPeer(
        listen=args.connect is None,
        host_addr=args.connect,
        port=args.port,
        on_px=lambda *_: None,  # temporary, GUI replaces it
    )
    gui = PixBoardGUI(peer)
    gui.run()
