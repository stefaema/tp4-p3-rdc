import socket
def local_ip():
    # Crea un socket UDP “falso” hacia Internet para descubrir
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))   # no se envía nada realmente
        return s.getsockname()[0]

