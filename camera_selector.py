"""
camera_selector.py
------------------
Ventana de selección de cámaras al arrancar.

Detecta en segundo plano las cámaras USB y las cámaras IP / NVR de la red,
las lista con casillas y devuelve las que el usuario decida usar.
"""
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, List, Optional

import config
import network_discovery as nd
from camera_manager import detect_cameras

# Las cámaras de red usan claves a partir de este valor para no chocar con
# los índices USB (0..MAX_CAMERAS_SCAN-1).
NETWORK_KEY_BASE = 100


@dataclass
class CameraSpec:
    key: int
    label: str
    url: Optional[str] = None      # None = cámara USB (key = índice)


class CameraSelector:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Video Replay — Selección de cámaras")
        self.root.configure(bg=config.COLOR_BG)
        self.root.geometry("680x600")
        self.root.minsize(560, 420)

        self._queue: "queue.Queue" = queue.Queue()
        self._scan_id = 0
        self._pending = 0

        self._settings = nd.load_settings()
        self._usb: List[int] = []
        self._devices: List[nd.NetworkDevice] = []
        self._manual: List[nd.StreamSource] = []
        self._checked: Dict[str, bool] = {}
        self._vars: Dict[str, tk.BooleanVar] = {}
        self._result: Optional[List[CameraSpec]] = None

        self._load_manual_sources()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def run(self) -> Optional[List[CameraSpec]]:
        self._start_scan()
        self.root.mainloop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        return self._result

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        bg, panel = config.COLOR_BG, config.COLOR_PANEL

        tk.Label(self.root, text="🎥  Selecciona las cámaras a usar",
                 bg=bg, fg=config.COLOR_TEXT,
                 font=("Arial", 14, "bold")).pack(pady=(14, 2))
        self._status = tk.StringVar(value="Buscando cámaras…")
        tk.Label(self.root, textvariable=self._status, bg=bg,
                 fg=config.COLOR_ACCENT, font=("Arial", 10)).pack()
        self._progress = ttk.Progressbar(self.root, mode="indeterminate",
                                         length=300)
        self._progress.pack(pady=6)

        body = tk.Frame(self.root, bg=panel)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        self._canvas = tk.Canvas(body, bg=panel, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical",
                               command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._list = tk.Frame(self._canvas, bg=panel)
        win = self._canvas.create_window((0, 0), window=self._list, anchor=tk.NW)
        self._list.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(win, width=e.width))
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

        bar = tk.Frame(self.root, bg=bg)
        bar.pack(fill=tk.X, padx=12, pady=(4, 12))
        self._btn_rescan = ttk.Button(bar, text="🔄 Re-escanear",
                                      command=self._start_scan)
        self._btn_rescan.pack(side=tk.LEFT)
        ttk.Button(bar, text="➕ URL manual…",
                   command=self._add_manual).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Marcar todas",
                   command=lambda: self._set_all(True)).pack(side=tk.LEFT)
        ttk.Button(bar, text="Ninguna",
                   command=lambda: self._set_all(False)).pack(side=tk.LEFT, padx=6)
        self._btn_start = ttk.Button(bar, text="▶ Iniciar", command=self._on_start)
        self._btn_start.pack(side=tk.RIGHT)

    def _on_wheel(self, event) -> None:
        if self._canvas.winfo_exists():
            self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _render(self) -> None:
        for key, var in self._vars.items():
            self._checked[key] = var.get()
        for w in self._list.winfo_children():
            w.destroy()
        self._vars = {}

        panel = config.COLOR_PANEL
        self._section("Cámaras USB / locales")
        if not self._usb:
            self._note("No se detectaron cámaras USB.")
        for idx in self._usb:
            self._check(f"usb:{idx}", f"Cam {idx + 1} (USB)", True)

        self._section("Cámaras de red (IP / NVR)")
        if not self._devices and not self._manual:
            try:
                hosts, _ = nd.hosts_to_scan()
                where = f" (red escaneada: {hosts[0].rsplit('.', 1)[0]}.x)" if hosts else ""
            except Exception:
                where = ""
            self._note("No se encontraron cámaras IP" + where + ". Verifica que "
                       "el equipo esté en la misma red que el NVR/cámara o "
                       "agrega una URL RTSP manualmente.")
        for dev in self._devices:
            row = tk.Frame(self._list, bg=panel)
            row.pack(fill=tk.X, padx=8, pady=(6, 0))
            note = "  🔒 requiere usuario/contraseña" if (
                dev.needs_auth and not dev.streams) else ""
            tk.Label(row, text=f"📡 {dev.title}{note}", bg=panel,
                     fg=config.COLOR_TEXT, font=("Arial", 10, "bold")
                     ).pack(side=tk.LEFT)
            if dev.rtsp_port:
                ttk.Button(row, text="Configurar acceso…",
                           command=lambda d=dev: self._configure_device(d)
                           ).pack(side=tk.RIGHT)
            for s in dev.streams:
                self._check(f"url:{s.url}", s.label, False, indent=22)
        for s in self._manual:
            self._check(f"url:{s.url}", s.label, True)

        self._update_start_state()

    def _section(self, text: str) -> None:
        tk.Label(self._list, text=text, bg=config.COLOR_PANEL,
                 fg=config.COLOR_ACCENT, font=("Arial", 11, "bold")
                 ).pack(anchor=tk.W, padx=8, pady=(10, 2))

    def _note(self, text: str) -> None:
        tk.Label(self._list, text=text, bg=config.COLOR_PANEL,
                 fg=config.COLOR_TEXT_DIM, font=("Arial", 9),
                 wraplength=560, justify=tk.LEFT).pack(anchor=tk.W, padx=16)

    def _check(self, key: str, text: str, default: bool, indent: int = 8) -> None:
        var = tk.BooleanVar(value=self._checked.get(key, default))
        var.trace_add("write", lambda *_: self._update_start_state())
        self._vars[key] = var
        tk.Checkbutton(self._list, text=text, variable=var,
                       bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
                       selectcolor=config.COLOR_BG,
                       activebackground=config.COLOR_PANEL,
                       activeforeground=config.COLOR_TEXT,
                       font=("Arial", 10)).pack(anchor=tk.W, padx=indent)

    def _set_all(self, value: bool) -> None:
        for var in self._vars.values():
            var.set(value)

    def _update_start_state(self) -> None:
        any_sel = any(v.get() for v in self._vars.values())
        state = tk.NORMAL if (any_sel and self._pending == 0) else tk.DISABLED
        self._btn_start.config(state=state)

    # ------------------------------------------------------------------
    # Escaneo en segundo plano
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        self._scan_id += 1
        sid = self._scan_id
        self._pending = 2
        self._btn_rescan.config(state=tk.DISABLED)
        self._progress.start(12)
        self._status.set("Buscando cámaras USB y de red…")
        self._update_start_state()
        threading.Thread(target=self._usb_worker, args=(sid,), daemon=True).start()
        threading.Thread(target=self._net_worker, args=(sid,), daemon=True).start()

    def _usb_worker(self, sid: int) -> None:
        try:
            found = detect_cameras(max_index=config.MAX_CAMERAS_SCAN)
        except Exception:
            found = []
        self._queue.put(("usb", sid, found))

    def _net_worker(self, sid: int) -> None:
        try:
            devices = nd.discover_network_devices(
                progress=lambda m: self._queue.put(("status", sid, m)))
            creds = self._settings.get("credentials", {})
            for dev in devices:
                saved = creds.get(dev.ip)
                if saved and dev.rtsp_port:
                    self._queue.put(("status", sid, f"Conectando a {dev.ip}…"))
                    brand, streams, _ = nd.probe_channels(
                        dev.ip, dev.rtsp_port, saved.get("user", ""),
                        saved.get("password", ""), saved.get("brand", "Auto"),
                        int(saved.get("channels", 8)),
                        bool(saved.get("substream", False)))
                    if streams:
                        dev.streams, dev.brand = streams, brand
                        dev.needs_auth = False
        except Exception:
            devices = []
        self._queue.put(("net", sid, devices))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, sid, payload = self._queue.get_nowait()
                if sid != self._scan_id:
                    continue
                if kind == "status":
                    self._status.set(payload)
                elif kind == "usb":
                    self._usb = payload
                    self._scan_step_done()
                elif kind == "net":
                    self._devices = payload
                    self._scan_step_done()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll_queue)

    def _scan_step_done(self) -> None:
        self._pending -= 1
        if self._pending > 0:
            return
        self._progress.stop()
        self._btn_rescan.config(state=tk.NORMAL)
        total = (len(self._usb) + len(self._manual)
                 + sum(len(d.streams) for d in self._devices))
        self._status.set(
            f"{total} cámara(s) disponibles. Marca las que quieras usar."
            if total else
            "No se encontraron cámaras. Configura el acceso o agrega una URL.")
        self._render()

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def _save_settings(self) -> None:
        try:
            nd.save_settings(self._settings)
        except OSError as exc:
            messagebox.showwarning(
                "No se pudo guardar la configuración",
                "Las cámaras seguirán disponibles durante esta sesión, "
                "pero la configuración no se conservará al cerrar el programa.\n\n"
                f"Archivo: {nd.SETTINGS_FILE}\nDetalle: {exc}",
                parent=self.root)

    def _load_manual_sources(self) -> None:
        urls = list(config.MANUAL_NETWORK_CAMERA_URLS)
        urls += [u for u in self._settings.get("manual_urls", [])
                 if isinstance(u, str)]
        for url in dict.fromkeys(u for u in urls if u):
            self._manual.append(nd.manual_source(url))

    def _add_manual(self) -> None:
        url = simpledialog.askstring(
            "URL manual",
            "URL de la cámara (RTSP o HTTP/MJPEG):\n"
            "ej. rtsp://usuario:clave@192.168.1.50:554/stream1",
            parent=self.root)
        if not url or not url.strip():
            return
        url = url.strip()
        if "://" not in url:
            messagebox.showerror("URL inválida",
                                 "Debe comenzar con rtsp:// o http://",
                                 parent=self.root)
            return
        if any(s.url == url for s in self._manual):
            return
        self._manual.append(nd.manual_source(url))
        saved = self._settings.setdefault("manual_urls", [])
        if url not in saved:
            saved.append(url)
            self._save_settings()
        self._checked[f"url:{url}"] = True
        self._render()

    def _configure_device(self, dev: nd.NetworkDevice) -> None:
        saved = self._settings.get("credentials", {}).get(dev.ip, {})
        top = tk.Toplevel(self.root)
        top.title(f"Acceso a {dev.ip}")
        top.configure(bg=config.COLOR_BG)
        top.transient(self.root)
        top.resizable(False, False)

        user = tk.StringVar(value=saved.get("user", "admin"))
        pwd = tk.StringVar(value=saved.get("password", ""))
        brand = tk.StringVar(value=saved.get("brand", dev.brand or "Auto"))
        chans = tk.IntVar(value=int(saved.get("channels", 8)))
        sub = tk.BooleanVar(value=bool(saved.get("substream", False)))
        msg = tk.StringVar(value="")

        def row(r, text, widget):
            tk.Label(top, text=text, bg=config.COLOR_BG, fg=config.COLOR_TEXT,
                     font=("Arial", 10)).grid(row=r, column=0, sticky="e",
                                              padx=8, pady=4)
            widget.grid(row=r, column=1, sticky="we", padx=8, pady=4)

        row(0, "Usuario", ttk.Entry(top, textvariable=user, width=24))
        row(1, "Contraseña", ttk.Entry(top, textvariable=pwd, show="•", width=24))
        row(2, "Marca", ttk.Combobox(top, textvariable=brand, values=nd.BRANDS,
                                     state="readonly", width=22))
        row(3, "Canales a buscar", ttk.Spinbox(top, from_=1, to=64,
                                               textvariable=chans, width=6))
        row(4, "", ttk.Checkbutton(top, text="Usar stream secundario (más liviano)",
                                   variable=sub))
        tk.Label(top, textvariable=msg, bg=config.COLOR_BG, fg="#ff8888",
                 wraplength=300, font=("Arial", 9)
                 ).grid(row=5, column=0, columnspan=2, padx=8)
        btn = ttk.Button(top, text="Buscar canales")
        btn.grid(row=6, column=0, columnspan=2, pady=10)

        box: Dict[str, object] = {}

        def work():
            try:
                box["res"] = nd.probe_channels(
                    dev.ip, dev.rtsp_port, user.get(), pwd.get(), brand.get(),
                    max(1, int(chans.get())), sub.get())
            except Exception as exc:
                box["res"] = (brand.get(), [], f"Error: {exc}")

        def finish():
            if "res" not in box:
                top.after(100, finish)
                return
            used, streams, err = box["res"]
            btn.config(state=tk.NORMAL)
            if err:
                msg.set(err)
                return
            dev.streams, dev.brand, dev.needs_auth = streams, used, False
            self._settings.setdefault("credentials", {})[dev.ip] = {
                "user": user.get(), "password": pwd.get(), "brand": used,
                "channels": int(chans.get()), "substream": bool(sub.get())}
            self._save_settings()
            for s in streams:
                self._checked[f"url:{s.url}"] = True
            top.destroy()
            self._render()

        def start():
            msg.set("Buscando canales…")
            btn.config(state=tk.DISABLED)
            box.clear()
            threading.Thread(target=work, daemon=True).start()
            top.after(100, finish)

        btn.config(command=start)
        top.grab_set()

    def _on_start(self) -> None:
        specs: List[CameraSpec] = []
        net_key = NETWORK_KEY_BASE
        seen = set()
        sources = [s for d in self._devices for s in d.streams] + self._manual
        for idx in self._usb:
            var = self._vars.get(f"usb:{idx}")
            if var and var.get():
                specs.append(CameraSpec(key=idx, label=f"Cam {idx + 1}"))
        for s in sources:
            var = self._vars.get(f"url:{s.url}")
            if var and var.get() and s.url not in seen:
                seen.add(s.url)
                specs.append(CameraSpec(key=net_key, label=s.label, url=s.url))
                net_key += 1
        if not specs:
            return
        self._result = specs
        self.root.quit()

    def _on_cancel(self) -> None:
        self._result = None
        self.root.quit()


def select_cameras() -> Optional[List[CameraSpec]]:
    """Muestra el selector y devuelve las cámaras elegidas (None si se cancela)."""
    return CameraSelector().run()
