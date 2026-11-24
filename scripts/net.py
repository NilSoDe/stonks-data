"""Capa de red común: reintentos con espera creciente, caché en disco, decodificación segura y estadísticas.

- request(): requests con reintentos ante fallos de red, timeouts y 429/5xx (espera 1,5 s, 3 s, 6 s... con algo
  de azar, o lo que diga Retry-After). Un 4xx que no sea 429 no se reintenta (un 404 de Finviz ya no cuesta 30 s).
  Con expect="texto", una respuesta 200 que no lo contiene (página de error o de mantenimiento) también se reintenta.
- get_bytes(url, cache=True): para documentos que no cambian (Form 4, PDF de los reguladores, anuncios RNS). Se
  guardan en la carpeta STONKS_CACHE y los intentos siguientes de la mañana no los vuelven a pedir.
- text() / response_text(): decodifican sin inventar caracteres: UTF-8 si es válido; si no, la codificación
  declarada o Windows-1252. requests supone ISO-8859-1 cuando el servidor no declara nada, y eso convertía
  "é" en "Ã©" (SEC) o en "?"; aquí nunca sale "�" por elegir mal la codificación.
- summary(): por dominio, peticiones, aciertos de caché, reintentos, errores y latencia (va a status["http"]).
"""
from __future__ import annotations

import hashlib
import os
import random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; stonks-data)"}
RETRY_STATUS = {429, 500, 502, 503, 504}
_lock = threading.Lock()
_stats: dict[str, dict] = {}


def cache_dir() -> Path | None:
    d = os.environ.get("STONKS_CACHE", "").strip()
    return Path(d) if d else None


def _note(host: str, ms: float | None = None, *, retry: bool = False, error: bool = False, cached: bool = False) -> None:
    with _lock:
        s = _stats.setdefault(host, {"n": 0, "cache": 0, "retries": 0, "errors": 0, "ms_sum": 0.0, "ms_max": 0.0})
        if cached:
            s["cache"] += 1
        elif retry:
            s["retries"] += 1
        elif error:
            s["errors"] += 1
        if ms is not None:
            s["n"] += 1
            s["ms_sum"] += ms
            s["ms_max"] = max(s["ms_max"], ms)


def summary() -> dict:
    with _lock:
        return {h: {"n": s["n"], "cache": s["cache"], "retries": s["retries"], "errors": s["errors"],
                    "ms_avg": round(s["ms_sum"] / s["n"]) if s["n"] else None, "ms_max": round(s["ms_max"])}
                for h, s in sorted(_stats.items())}


def text(data: bytes, declared: str | None = None) -> str:
    """Bytes -> texto. UTF-8 si es válido; si no, la codificación declarada; si no, Windows-1252 o Latin-1."""
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in (declared, "cp1252"):
        if enc and enc.lower().replace("-", "") != "utf8":
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
    return data.decode("latin-1")  # no falla nunca y no inventa "�"


def response_text(r: requests.Response) -> str:
    m = re.search(r"charset=[\"']?([\w-]+)", r.headers.get("content-type", ""), re.I)
    declared = m.group(1) if m else None
    if not declared:
        m2 = re.search(rb"charset=[\"']?([\w-]+)", r.content[:2048], re.I)
        declared = m2.group(1).decode("ascii") if m2 else None
    return text(r.content, declared)


def _retry_after(r: requests.Response) -> float | None:
    try:
        return min(float(r.headers.get("Retry-After", "")), 60.0)
    except ValueError:
        return None


def request(method: str, url: str, *, session: requests.Session | None = None, headers: dict | None = None,
            retries: int = 3, timeout: float = 30, backoff: float = 1.5, ok: tuple = (), expect: str | None = None,
            wait=None, **kw) -> requests.Response:
    """ok: códigos que se devuelven sin error (p. ej. 404). wait: función que se llama antes de cada intento
    (limitador de ritmo). expect: texto que debe aparecer en una respuesta 200."""
    host = urlsplit(url).netloc
    last: Exception | None = None
    for attempt in range(retries):
        if wait:
            wait()
        delay = None
        t0 = time.monotonic()
        try:
            r = (session or requests).request(method, url, headers=headers or UA, timeout=timeout, **kw)
            ms = (time.monotonic() - t0) * 1000
            if r.status_code in ok:
                _note(host, ms)
                return r
            if r.status_code in RETRY_STATUS:
                last = requests.HTTPError(f"{r.status_code} en {host}", response=r)
                delay = _retry_after(r)
            elif r.status_code >= 400:
                _note(host, ms, error=True)
                r.raise_for_status()
            elif expect and expect not in response_text(r):
                last = ValueError(f"respuesta de {host} sin '{expect}' (página de error o de mantenimiento)")
            else:
                _note(host, ms)
                return r
        except requests.HTTPError:
            raise
        except requests.RequestException as e:
            last = e
        if attempt < retries - 1:
            _note(host, retry=True)
            time.sleep(delay if delay is not None else backoff * 2 ** attempt + random.uniform(0, 0.5))
    _note(host, error=True)
    raise last  # type: ignore[misc]


def get_text(url: str, **kw) -> str:
    return response_text(request("GET", url, **kw))


def get_bytes(url: str, *, cache: bool = False, **kw) -> bytes | None:
    """Contenido de la URL, o None si el servidor responde con un código de 'ok' distinto de 200 (p. ej. 404)."""
    d = cache_dir() if cache else None
    path = d / hashlib.sha1(url.encode()).hexdigest() if d else None
    if path and path.exists():
        _note(urlsplit(url).netloc, cached=True)
        return path.read_bytes()
    r = request("GET", url, **kw)
    if r.status_code != 200:
        return None
    if path and r.content:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
    return r.content


def prune_cache(days: int = 5) -> None:
    d = cache_dir()
    if not d or not d.is_dir():
        return
    limit = time.time() - days * 86400
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass


def write_atomic(path: Path, content: str) -> None:
    """Escribe a un temporal y lo renombra: un corte a mitad no deja el archivo truncado."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def fix_encoding(r: requests.Response) -> requests.Response:
    """Deja r.text bien decodificado aunque el servidor no declare la codificación."""
    try:
        r.content.decode("utf-8")
        r.encoding = "utf-8"
    except UnicodeDecodeError:
        m = re.search(r"charset=[\"']?([\w-]+)", r.headers.get("content-type", ""), re.I)
        enc = m.group(1) if m and m.group(1).lower().replace("-", "") != "utf8" else "cp1252"
        r.encoding = enc
    return r


def breve(err) -> str:
    """Texto de error apto para el mail y el informe web: los errores largos (con URLs) rompen el ancho en el móvil.
    Si es corto y sin 'palabras' de más de 40 caracteres se deja; si no, solo el tipo (p. ej. 'ConnectionError')."""
    s = str(err or "")
    if len(s) <= 60 and not re.search(r"\S{41,}", s):
        return s
    return s.split(":", 1)[0].strip()[:40]
