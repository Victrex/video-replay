"""
network_discovery.py
--------------------
Descubrimiento de cámaras IP y NVR en la red local.

Estrategia (rápida y sin abrir streams de OpenCV para sondear):
  1. Escaneo TCP concurrente de la(s) subred(es) local(es) en los puertos
     típicos de cámaras/NVR (RTSP 554/8554, HTTP, SDK Hikvision/Dahua).
  2. En los hosts con RTSP abierto se envía un DESCRIBE por socket crudo:
       200 -> stream accesible sin credenciales
       401 -> requiere usuario/contraseña (típico de NVR y cámaras modernas)
  3. En los hosts con HTTP abierto se buscan rutas MJPEG conocidas.
  4. Con usuario/contraseña se sondean los canales 1..N de un NVR
     (Hikvision, Dahua o genérico) con autenticación Basic/Digest.

Las credenciales y URLs manuales se guardan en `network_cameras.json`
(excluido de git) junto al programa o, en la versión compilada, junto al
ejecutable.
"""
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import config


def _settings_path() -> str:
    program = sys.executable if getattr(sys, "frozen", False) else __file__
    return os.path.join(os.path.dirname(os.path.abspath(program)),
                        "network_cameras.json")


SETTINGS_FILE = _settings_path()

RTSP_PORTS = (554, 8554)
HTTP_PORTS = (80, 8080, 8081)
HIKVISION_HINT_PORTS = (8000,)
DAHUA_HINT_PORTS = (37777, 37778)

MJPEG_PATHS = (
    "/mjpg/video.mjpg",
    "/video.mjpg",
    "/cgi-bin/mjpg/video.cgi",
    "/videostream.cgi",
    "/video",
)

# Plantillas de ruta RTSP por marca: (principal, secundario)
BRAND_PATHS: Dict[str, Tuple[str, str]] = {
    "Hikvision": ("/Streaming/Channels/{ch}01", "/Streaming/Channels/{ch}02"),
    "Dahua": ("/cam/realmonitor?channel={ch}&subtype=0",
              "/cam/realmonitor?channel={ch}&subtype=1"),
    "Genérico": ("/h264/ch{ch}/main/av_stream", "/h264/ch{ch}/sub/av_stream"),
}
BRANDS = ("Auto",) + tuple(BRAND_PATHS)

ProgressCb = Optional[Callable[[str], None]]


@dataclass
class StreamSource:
    url: str          # puede contener credenciales: no mostrarla en la UI
    label: str


@dataclass
class NetworkDevice:
    ip: str
    open_ports: List[int] = field(default_factory=list)
    rtsp_port: Optional[int] = None
    brand: str = ""
    needs_auth: bool = False
    streams: List[StreamSource] = field(default_factory=list)

    @property
    def title(self) -> str:
        kind = f" ({self.brand})" if self.brand else ""
        return f"{self.ip}{kind}"


# ------------------------------------------------------------------
# Persistencia
# ------------------------------------------------------------------

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("credentials", {})
            data.setdefault("manual_urls", [])
            return data
    except (OSError, ValueError):
        pass
    return {"credentials": {}, "manual_urls": []}


def save_settings(data: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------
# Subredes y escaneo de puertos
# ------------------------------------------------------------------

def local_ipv4_addresses() -> List[str]:
    found: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass

    result: List[str] = []
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or not addr.is_private:
            continue
        if ip not in result:
            result.append(ip)
    return result


def hosts_to_scan() -> Tuple[List[str], List[str]]:
    """Devuelve (hosts a escanear, IPs propias a omitir)."""
    own = local_ipv4_addresses()
    subnet = (config.NETWORK_SCAN_SUBNET or "").strip()
    nets = []
    if subnet:
        try:
            nets.append(ipaddress.ip_network(subnet, strict=False))
        except ValueError:
            pass
    if not nets:
        for ip in own:
            nets.append(ipaddress.ip_network(f"{ip}/24", strict=False))

    hosts: List[str] = []
    for net in nets:
        # Límite de seguridad: máx. 1024 hosts por red
        for i, host in enumerate(net.hosts()):
            if i >= 1024:
                break
            h = str(host)
            if h not in hosts and h not in own:
                hosts.append(h)
    return hosts, own


def _tcp_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_ports(hosts: List[str], ports: List[int], timeout: float,
               progress: ProgressCb = None) -> Dict[str, List[int]]:
    open_map: Dict[str, List[int]] = {}
    tasks = [(h, p) for h in hosts for p in ports]
    total = len(tasks)
    done = 0

    def probe(task):
        return task, _tcp_open(task[0], task[1], timeout)

    with ThreadPoolExecutor(max_workers=128) as pool:
        for (host, port), ok in pool.map(probe, tasks):
            done += 1
            if ok:
                open_map.setdefault(host, []).append(port)
            if progress and done % 100 == 0:
                progress(f"Escaneando red… {done * 100 // total}%")
    return open_map


# ------------------------------------------------------------------
# RTSP DESCRIBE (con autenticación Basic/Digest)
# ------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _send_describe(sock: socket.socket, url: str, cseq: int,
                   auth: Optional[str]) -> Tuple[int, List[str]]:
    """Envía DESCRIBE por un socket abierto. Devuelve (estado, WWW-Authenticate)."""
    lines = [
        f"DESCRIBE {url} RTSP/1.0",
        f"CSeq: {cseq}",
        "Accept: application/sdp",
        "User-Agent: VideoReplay",
    ]
    if auth:
        lines.append(f"Authorization: {auth}")
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        return 0, []

    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
    rows = head.split("\r\n")
    match = re.match(r"RTSP/\d\.\d\s+(\d{3})", rows[0]) if rows else None
    if not match:
        return 0, []
    challenges = [r.split(":", 1)[1].strip() for r in rows[1:]
                  if r.lower().startswith("www-authenticate:")]
    return int(match.group(1)), challenges

def _build_auth(challenges: List[str], method: str, url: str,
                user: str, password: str) -> Optional[str]:
    import base64

    digest = next((c for c in challenges if c.lower().startswith("digest")), None)
    if digest:
        params = dict(re.findall(r'(\w+)="?([^",]*)"?', digest[6:]))
        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        ha1 = _md5(f"{user}:{realm}:{password}")
        ha2 = _md5(f"{method}:{url}")
        parts = [f'username="{user}"', f'realm="{realm}"',
                 f'nonce="{nonce}"', f'uri="{url}"']
        if "auth" in params.get("qop", ""):
            cnonce = os.urandom(8).hex()
            resp = _md5(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}")
            parts += ['qop=auth', 'nc=00000001', f'cnonce="{cnonce}"']
        else:
            resp = _md5(f"{ha1}:{nonce}:{ha2}")
        parts.append(f'response="{resp}"')
        return "Digest " + ", ".join(parts)

    if any(c.lower().startswith("basic") for c in challenges):
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode()
        return f"Basic {token}"
    return None


def rtsp_describe(ip: str, port: int, path: str, user: str = "",
                  password: str = "", timeout: float = 3.0) -> int:
    """
    Código RTSP del DESCRIBE a rtsp://ip:port/path (0 = sin respuesta).
    Con credenciales reintenta tras un 401 usando Digest/Basic, primero en
    la misma conexión (algunos Hikvision ligan el nonce a la conexión) y,
    si el equipo la cierra, en una conexión nueva.
    """
    url = f"rtsp://{ip}:{port}{path}"
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        return 0
    with sock:
        sock.settimeout(timeout)
        status, challenges = _send_describe(sock, url, 1, None)
        if status != 401 or not user:
            return status
        auth = _build_auth(challenges, "DESCRIBE", url, user, password)
        if not auth:
            return status
        status, challenges2 = _send_describe(sock, url, 2, auth)

    if status == 0:
        try:
            with socket.create_connection((ip, port), timeout=timeout) as s2:
                s2.settimeout(timeout)
                status, _ = _send_describe(s2, url, 1, auth)
        except OSError:
            return 0
    return status

def build_rtsp_url(ip: str, port: int, path: str,
                   user: str = "", password: str = "") -> str:
    cred = ""
    if user:
        cred = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    return f"rtsp://{cred}{ip}:{port}{path}"


# ------------------------------------------------------------------
# Sondeo de un host
# ------------------------------------------------------------------

def _http_mjpeg_stream(ip: str, port: int) -> Optional[str]:
    for path in MJPEG_PATHS:
        url = f"http://{ip}:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if resp.status == 200 and ("multipart" in ctype or "jpeg" in ctype):
                    return url
        except Exception:
            continue
    return None


def _probe_host(ip: str, ports: List[int]) -> Optional[NetworkDevice]:
    dev = NetworkDevice(ip=ip, open_ports=sorted(ports))
    if any(p in ports for p in DAHUA_HINT_PORTS):
        dev.brand = "Dahua"
    elif any(p in ports for p in HIKVISION_HINT_PORTS):
        dev.brand = "Hikvision"

    rtsp_port = next((p for p in RTSP_PORTS if p in ports), None)
    dev.rtsp_port = rtsp_port
    if rtsp_port:
        for path in [""] + list(config.NETWORK_DISCOVERY_PATHS):
            status = rtsp_describe(ip, rtsp_port, path)
            if status == 200:
                dev.streams.append(StreamSource(
                    f"rtsp://{ip}:{rtsp_port}{path}", f"IP {ip}"))
                break
            if status == 401:
                dev.needs_auth = True
                break

    if not dev.streams:
        for port in HTTP_PORTS:
            if port in ports:
                url = _http_mjpeg_stream(ip, port)
                if url:
                    dev.streams.append(StreamSource(url, f"IP {ip}"))
                    break

    if dev.rtsp_port or dev.streams:
        return dev
    return None


def discover_network_devices(progress: ProgressCb = None) -> List[NetworkDevice]:
    """Escanea la red local y devuelve cámaras/NVR candidatos."""
    hosts, _ = hosts_to_scan()
    if not hosts:
        return []

    ports = sorted(set(config.NETWORK_DISCOVERY_PORTS) | set(RTSP_PORTS)
                   | set(HTTP_PORTS) | set(HIKVISION_HINT_PORTS)
                   | set(DAHUA_HINT_PORTS))
    open_map = scan_ports(hosts, ports, config.NETWORK_SCAN_TIMEOUT, progress)
    candidates = {ip: p for ip, p in open_map.items()
                  if any(x in p for x in RTSP_PORTS + HTTP_PORTS)}
    if progress:
        progress(f"Analizando {len(candidates)} dispositivo(s)…")

    devices: List[NetworkDevice] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for dev in pool.map(lambda kv: _probe_host(*kv), candidates.items()):
            if dev:
                devices.append(dev)
    devices.sort(key=lambda d: tuple(int(x) for x in d.ip.split(".")))
    return devices


# ------------------------------------------------------------------
# Sondeo de canales de NVR / cámara con credenciales
# ------------------------------------------------------------------

def _ffmpeg_can_open(url: str) -> bool:
    """Respaldo: comprueba el stream con OpenCV/FFmpeg (negocia la auth solo)."""
    try:
        import cv2
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000])
        ok = False
        if cap is not None and cap.isOpened():
            ok, _ = cap.read()
        if cap is not None:
            cap.release()
        return bool(ok)
    except Exception:
        return False


def probe_channels(ip: str, port: int, user: str, password: str,
                   brand: str = "Auto", max_channels: int = 8,
                   substream: bool = False) -> Tuple[str, List[StreamSource], str]:
    """
    Busca canales 1..max_channels. Devuelve (marca, streams, mensaje_error).
    `mensaje_error` queda vacío si se encontró al menos un canal.
    """
    idx = 1 if substream else 0
    brands = list(BRAND_PATHS) if brand == "Auto" else [brand]
    # Hikvision primero si se detectó o se eligió
    brands.sort(key=lambda b: b != "Hikvision")

    chosen = None
    statuses: Dict[str, int] = {}
    for b in brands:
        path = BRAND_PATHS[b][idx].format(ch=1)
        status = rtsp_describe(ip, port, path, user, password)
        statuses[b] = status
        print(f"[RTSP] {b}: {build_rtsp_url(ip, port, path, user, password)}"
              f"  -> {status or 'sin respuesta'}", flush=True)
        if status == 200:
            chosen = b
            break

    if chosen is None:
        # Respaldo con FFmpeg por si la negociación manual falla
        for b in brands:
            url = build_rtsp_url(ip, port, BRAND_PATHS[b][idx].format(ch=1),
                                 user, password)
            print(f"[RTSP] FFmpeg {b}: {url}", flush=True)
            if _ffmpeg_can_open(url):
                chosen = b
                break

    if chosen is None:
        detail = ", ".join(f"{b}: {s or 'sin respuesta'}"
                           for b, s in statuses.items())
        if any(s == 401 for s in statuses.values()):
            return brand, [], (
                "El equipo rechazó las credenciales por RTSP "
                f"({detail}). Verifica que el usuario tenga permiso de "
                "'Vista en vivo remota' y que la cuenta no esté bloqueada "
                "(espera unos minutos si fallaste varias veces).")
        if any(s == 404 for s in statuses.values()):
            return brand, [], (f"Credenciales válidas pero no se halló el "
                               f"canal ({detail}). Prueba otra marca.")
        return brand, [], (f"No se obtuvo respuesta RTSP ({detail}). "
                           "Revisa que RTSP esté habilitado y el puerto "
                           f"({port}) sea el correcto.")

    template = BRAND_PATHS[chosen][idx]

    def check(ch: int):
        path = template.format(ch=ch)
        status = rtsp_describe(ip, port, path, user, password)
        return ch, path, status

    streams: List[StreamSource] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for ch, path, status in pool.map(check, range(1, max_channels + 1)):
            if status == 200:
                streams.append(StreamSource(
                    build_rtsp_url(ip, port, path, user, password),
                    f"{ip} · Canal {ch}"))
    if not streams:
        # El respaldo FFmpeg pasó pero no el DESCRIBE manual: ofrecer canal 1
        url = build_rtsp_url(ip, port, template.format(ch=1), user, password)
        streams.append(StreamSource(url, f"{ip} · Canal 1"))
    return chosen, streams, ""

def manual_source(url: str) -> StreamSource:
    host = urlparse(url).hostname or url
    return StreamSource(url, f"Manual {host}")
