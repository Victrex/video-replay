"""
main.py
-------
Punto de entrada del Sistema de Video Replay Multi-Cámara.

Pasos al arrancar:
  1. Muestra una ventana splash mientras escanea las cámaras disponibles.
  2. Si no hay ninguna cámara, muestra un error y sale.
  3. Crea un CameraCapture por cada cámara encontrada y los inicia.
  4. Abre la ventana principal VideoReplayApp.
"""
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import config
from camera_manager import CameraCapture, detect_cameras
from ui import VideoReplayApp


# ------------------------------------------------------------------
# Ventana splash mientras se detectan cámaras
# ------------------------------------------------------------------

def _run_splash_and_detect():
    """
    Abre una ventana splash, detecta cámaras disponibles y la cierra.
    Devuelve la lista de índices detectados.
    """
    splash = tk.Tk()
    splash.title("Video Replay — Iniciando…")
    splash.configure(bg=config.COLOR_BG)
    splash.geometry("420x180")
    splash.resizable(False, False)
    splash.overrideredirect(True)   # Sin bordes del sistema

    # Centrar en pantalla
    splash.update_idletasks()
    sw = splash.winfo_screenwidth()
    sh = splash.winfo_screenheight()
    splash.geometry(f"+{(sw - 420)//2}+{(sh - 180)//2}")

    # Contenido
    tk.Label(
        splash, text="🎥  Sistema de Video Replay",
        bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        font=("Arial", 15, "bold")
    ).pack(pady=(28, 6))

    tk.Label(
        splash, text="Para Taekwondo · Multi-Cámara",
        bg=config.COLOR_BG, fg=config.COLOR_TEXT_DIM,
        font=("Arial", 10)
    ).pack()

    msg_var = tk.StringVar(value="Detectando cámaras…")
    tk.Label(
        splash, textvariable=msg_var,
        bg=config.COLOR_BG, fg=config.COLOR_ACCENT,
        font=("Arial", 10)
    ).pack(pady=(12, 0))

    # Barra de progreso
    pb = ttk.Progressbar(splash, length=300, mode="determinate",
                         maximum=config.MAX_CAMERAS_SCAN)
    pb.pack(pady=10)
    splash.update()

    # Detectar cámaras actualizando la barra de progreso
    detected = []

    def _progress(current, total):
        msg_var.set(f"Escaneando índice {current} / {total - 1}…")
        pb["value"] = current
        splash.update()

    detected = detect_cameras(
        max_index=config.MAX_CAMERAS_SCAN,
        progress_callback=_progress
    )

    splash.destroy()
    return detected


# ------------------------------------------------------------------
# Punto de entrada
# ------------------------------------------------------------------

def main():
    cam_indices = _run_splash_and_detect()

    if not cam_indices:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Sin cámaras",
            "No se detectó ninguna cámara conectada.\n\n"
            "• Conecta al menos una webcam USB.\n"
            "• Asegúrate de que ninguna otra aplicación\n"
            "  esté usando la cámara.\n"
            "• Vuelve a intentarlo."
        )
        root.destroy()
        sys.exit(1)

    # Crear e iniciar un CameraCapture por cada cámara
    cameras = {}
    for idx in cam_indices:
        cam = CameraCapture(
            camera_index=idx,
            buffer_seconds=config.DEFAULT_BUFFER_DURATION
        )
        cam.start()
        cameras[idx] = cam

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
