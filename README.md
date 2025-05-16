# tp4-p3-rdc


# PixBoard — Explicación del Código

PixBoard es una aplicación colaborativa de pixel art sobre red TCP que actúa como la Parte III del TP 4 para la materia de Redes de Computadoras, de la Facultad de Ciencias Exactas, Físicas y Naturales de la Universidad Nacional de Córdoba. Utiliza `tkinter` para la interfaz gráfica y puede funcionar como servidor o cliente según los argumentos al ejecutarse.

---

## 1. Configuración general

```python
SIZE = 32          # Lienzo de 32 × 32 píxeles
PIX  = 14          # Tamaño en pantalla de cada píxel
PORT = 7007        # Puerto TCP por defecto
PALETTE_SIZE = 32  # Dimensiones de la paleta de colores
```

---

## 2. Modos de herramienta

```python
class ToolMode(Enum):
    POINT = auto()
    LINE = auto()
    FILL = auto()
```

Define los modos de interacción:
- `POINT`: coloca un único píxel.
- `LINE`: traza una línea al arrastrar el mosuee.
- `FILL`: rellena una zona conectada del mismo color.

---

## 3. Obtención de IP local

La función `local_ip()` conecta brevemente a una IP pública (sin enviar datos) para detectar la IP local del equipo, útil para mostrar al host en pantalla.

---

## 4. Generación de paleta

`generate_palette()` crea una paleta con:
- 32x32 colores calculados en HLS.
- Una fila adicional con escala de grises.
- Un color "transparente" en la esquina superior izquierda.

---

## 5. Comunicación en red

La clase `NetPeer` maneja la conexión y sincronización entre clientes y host.

- `listen=True` → inicia como servidor.
- `listen=False` → actúa como cliente.

Usa colas para mensajes salientes y mantiene un hilo de keep-alive.

---

## 6. Servidor (host)

En `_host_loop`:
- Escucha conexiones entrantes.
- Envía el estado inicial del tablero a nuevos clientes.
- Reenvía las actualizaciones a todos los conectados.
- Responde a mensajes `noop` con `{"ack": "noop"}` para mantener activa la conexión.

---

## 7. Cliente

En `_client_loop`:
- Se conecta al servidor y lanza dos hilo:
  - Uno para enviar actualizaciones.
  -  Otro para enviar `noop` periódicos como keep-alive.
- Recibe y aplica mensajes:
  - `snapshot` para dibujar todo el tablero.
  - `x, y, c` para actualizar un solo píxel.
  - `ack` para confirmar keep-alive.

---

## 8. Interfaz gráfica

Clase `PixBoardGUI`:
- Crea la grilla de dibujo principal.
- Muestra la paleta con colores y grises.
- Muestra botones para cambiar de herramienta.
- Indica la IP local del host.

Permite pintar con clics, arrastrar para líneas, o hacer rellenos con el modo `FILL`.

Destaca el color seleccionado con un borde resaltado (invertido).

---

## 9. Ejecución

En el bloque principal:

- Si no se pasa `--connect`, se actúa como host.
- Si se pasa una IP, se conecta como cliente.
- Se lanza la GUI y la capa de red.

---

## 10. Keep-alive y desconexión

Inicialmente el cliente podía desconectarse por `timeout` porque el servidor no respondía a los `noop`.

Este comportamiento fue corregido agregando una respuesta explícita con `{"ack": "noop"}` desde el host, asegurando que haya tráfico bidireccional y se mantenga viva la conexión.
