"""
main.py
-------
Punto de entrada del Sistema de Video Replay Multi-Cámara.

Pasos al arrancar:
  1. Abre el selector: detecta cámaras USB y cámaras IP / NVR de la red.
  2. El usuario elige cuáles usar (si cancela, la app sale).
  3. Crea un CameraCapture por cada cámara elegida y los inicia.
  4. Abre la ventana principal VideoReplayApp.
"""
import sys
import tkinter as tk

import config
from camera_manager import CameraCapture
from camera_selector import select_cameras
from ui import VideoReplayApp


# ------------------------------------------------------------------
# Punto de entrada
# ------------------------------------------------------------------

def main():
    specs = select_cameras()

    if not specs:
        sys.exit(0)

    # Crear e iniciar un CameraCapture por cada cámara elegida
    cameras = {}
    for spec in specs:
        cam = CameraCapture(
            camera_index=spec.key,
            buffer_seconds=config.DEFAULT_BUFFER_DURATION,
            source_url=spec.url,
            label=spec.label
        )
        cam.start()
        cameras[spec.key] = cam

    # Ventana principal
    root = tk.Tk()
    root.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}")

    # Centrar en pantalla
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(
        f"+{max(0, (sw - config.WINDOW_WIDTH)//2)}"
        f"+{max(0, (sh - config.WINDOW_HEIGHT)//2)}"
    )

    app = VideoReplayApp(root, cameras)

    def on_close():
        app.destroy()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
