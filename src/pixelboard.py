#!/usr/bin/env python3
# PixBoard: simple cooperative pixel-art board over TCP
import argparse, json, queue, socket, threading, tkinter as tk

SIZE = 32          # board is SIZE x SIZE
PIX  = 16          # pixel size on screen
PORT = 7007

# -------- network layer --------
class NetPeer:
    """
    If 'listen' is True -> act as host, else connect to host_addr.
    Provides .send_px() and .on_px callback.
    """
    def __init__(self, listen: bool, host_addr: str | None, port: int, on_px):
        self.on_px = on_px
        self.q_out = queue.Queue()
        if listen:
            threading.Thread(target=self._host_loop, args=(port,), daemon=True).start()
        else:
            threading.Thread(target=self._client_loop, args=(host_addr, port), daemon=True).start()

    # ----- common helpers -----
    def send_px(self, x: int, y: int, color: str):
        self.q_out.put({"x": x, "y": y, "c": color})

    @staticmethod
    def _recv_json(sock: socket.socket):
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError
            data += chunk
        return json.loads(data.decode())

    @staticmethod
    def _send_json(sock: socket.socket, obj):
        sock.sendall((json.dumps(obj) + "\n").encode())

    # ----- host -----
    def _host_loop(self, port: int):
        board = [["#FFFFFF"] * SIZE for _ in range(SIZE)]
        clients = []

        def client_thread(conn):
            try:
                # 1) send snapshot
                self._send_json(conn, {"snapshot": board})
                # 2) forward outgoing px from queue
                while True:
                    msg = self._recv_json(conn)
                    if "x" in msg:
                        x, y, c = msg["x"], msg["y"], msg["c"]
                        board[y][x] = c
                        self.on_px(x, y, c)          # local update
                        # broadcast to everyone
                        for peer in clients:
                            try:
                                self._send_json(peer, msg)
                            except Exception:
                                pass
            except Exception:
                pass
            finally:
                conn.close()
                clients.remove(conn)

        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen()
        print(f"Hosting on *:{port}")

        threading.Thread(target=self._host_outgoing, args=(clients,), daemon=True).start()

        while True:
            c, _ = srv.accept()
            clients.append(c)
            threading.Thread(target=client_thread, args=(c,), daemon=True).start()

    def _host_outgoing(self, clients):
        while True:
            msg = self.q_out.get()
            for peer in clients:
                try:
                    self._send_json(peer, msg)
                except Exception:
                    pass

    # ----- client -----
    def _client_loop(self, host, port):
        while True:  # reconnect loop
            try:
                with socket.create_connection((host, port)) as s:
                    threading.Thread(target=self._client_sender, args=(s,), daemon=True).start()
                    # receive forever
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
                print("Disconnected, retrying in 2 s …", e)
                time.sleep(2)

    def _client_sender(self, sock):
        while True:
            msg = self.q_out.get()
            self._send_json(sock, msg)

# -------- GUI layer --------
class PixBoardGUI:
    def __init__(self, peer: NetPeer):
        self.peer = peer
        self.root = tk.Tk()
        self.root.title("PixBoard")
        self.canvas = tk.Canvas(self.root, width=SIZE*PIX, height=SIZE*PIX, bg="white")
        self.canvas.pack()
        self.px_ids = [[None]*SIZE for _ in range(SIZE)]
        self.current_color = "#000000"

        # draw grid
        for y in range(SIZE):
            for x in range(SIZE):
                x0, y0 = x*PIX, y*PIX
                self.px_ids[y][x] = self.canvas.create_rectangle(
                    x0, y0, x0+PIX, y0+PIX, fill="#FFFFFF", outline="#EEE"
                )
        self.canvas.bind("<Button-1>", self.click)

        # simple palette
        palette = ["#000000", "#FF0000", "#00AA00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF"]
        frm = tk.Frame(self.root); frm.pack()
        for col in palette:
            b = tk.Button(frm, bg=col, width=2, command=lambda c=col: self.set_color(c))
            b.pack(side="left", padx=2, pady=2)

        # register callback from net
        peer.on_px = self.set_px

    def set_color(self, c): self.current_color = c

    def click(self, evt):
        x, y = evt.x // PIX, evt.y // PIX
        if 0 <= x < SIZE and 0 <= y < SIZE:
            self.set_px(x, y, self.current_color)
            self.peer.send_px(x, y, self.current_color)

    def set_px(self, x, y, color):
        self.canvas.itemconfigure(self.px_ids[y][x], fill=color)

    def run(self): self.root.mainloop()

# -------- main --------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", help="IP del host al que unirse")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    peer = NetPeer(listen=args.connect is None, host_addr=args.connect, port=args.port, on_px=lambda *_: None)
    gui  = PixBoardGUI(peer)
    gui.run()
