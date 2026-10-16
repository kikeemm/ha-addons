#!/usr/bin/env python3
"""Bot de Telegram -> notas .md en un repo de GitHub (carpeta 00_Inbox).

Guarda texto, fotos, videos y otros archivos. Los adjuntos se suben a
00_Inbox/media/ y la nota .md los enlaza. Telegram solo deja descargar a los
bots archivos de hasta 20 MB; los mas grandes se anotan sin descargar.

Solo usa la biblioteca estandar de Python. Los tokens llegan por variables de
entorno y nunca se escriben en los logs.
"""
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_REPO = os.environ.get("GITHUB_REPO", "")
INBOX = os.environ.get("INBOX_PATH", "00_Inbox").strip("/")
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0") or 0)

MEDIA_MAX_BYTES = 20 * 1024 * 1024  # limite de descarga de Telegram para bots
URL_RE = re.compile(r"https?://[^\s<>\"]+")


def log(msg):
    text = str(msg)
    for secret in (TG_TOKEN, GH_TOKEN):
        if secret:
            text = text.replace(secret, "***")
    print(text, flush=True)


def tg(method, params, timeout=45):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram devolvio error en {method}")
    return data["result"]


def tg_download(file_id):
    """Descarga un archivo de Telegram y devuelve (bytes, ruta_remota)."""
    info = tg("getFile", {"file_id": file_id})
    remote = info["file_path"]
    url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{remote}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read(), remote


def extract_links(text):
    seen = []
    for link in URL_RE.findall(text):
        link = link.rstrip(".,;:!?)]}")
        if link not in seen:
            seen.append(link)
    return seen


def extract_media(msg):
    """Devuelve dict(kind, file_id, size, name, mime) o None si no hay adjunto."""
    if msg.get("photo"):
        best = max(msg["photo"], key=lambda p: p.get("file_size") or 0)
        return {"kind": "foto", "file_id": best["file_id"],
                "size": best.get("file_size"), "name": None, "mime": "image/jpeg"}
    for key, kind in (
        ("video", "video"),
        ("video_note", "video"),
        ("animation", "animacion"),
        ("document", "archivo"),
        ("audio", "audio"),
        ("voice", "audio"),
    ):
        obj = msg.get(key)
        if obj:
            return {"kind": kind, "file_id": obj["file_id"],
                    "size": obj.get("file_size"), "name": obj.get("file_name"),
                    "mime": obj.get("mime_type")}
    return None


def media_filename(base, media, remote_path):
    ext = os.path.splitext(remote_path or "")[1]
    if not ext and media.get("name"):
        ext = os.path.splitext(media["name"])[1]
    if not ext and media.get("mime"):
        ext = mimetypes.guess_extension(media["mime"]) or ""
    return f"{base}{ext or '.bin'}"


def build_note(msg, update_id, media=None, media_rel=None, media_status=None):
    """Devuelve (ruta, contenido) o None si no hay ni texto ni adjunto."""
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if text.startswith("/"):
        return None
    if not text and not media:
        return None
    sender = msg.get("from") or {}
    name = sender.get("first_name") or sender.get("username") or "desconocido"
    when = datetime.fromtimestamp(msg["date"], timezone.utc)
    links = extract_links(text)
    tags = ["inbox", "telegram"]
    if media:
        tags.append("media")
    front = [
        "---",
        "titulo: " + json.dumps("Mensaje de Telegram " + when.strftime("%Y-%m-%d %H:%M"), ensure_ascii=False),
        "fuente: telegram",
        "enviado_por: " + json.dumps(name, ensure_ascii=False),
        "fecha: " + when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "enlaces: " + json.dumps(links, ensure_ascii=False),
    ]
    if media:
        front.append("tipo_adjunto: " + media["kind"])
        front.append("adjunto: " + (media_rel or ""))
    front += [
        "estado: pendiente",
        "aplica_a: ",
        "tags: [" + ", ".join(tags) + "]",
        "---",
        "",
    ]
    if text:
        front += [text, ""]
    if media:
        if media_rel:
            front += [f"Adjunto ({media['kind']}): [{media_rel}]({media_rel})", ""]
        else:
            front += [f"Adjunto ({media['kind']}) NO guardado: {media_status}", ""]
    path = f"{INBOX}/{when.strftime('%Y-%m-%d_%H%M%S')}_{update_id}.md"
    return path, "\n".join(front)


def save_to_github(path, content):
    """Sube un archivo (str o bytes). Un 422 se trata como 'ya existe'."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    url = (
        f"https://api.github.com/repos/{GH_REPO}/contents/"
        + urllib.parse.quote(path)
    )
    body = {
        "message": "Telegram: nuevo mensaje",
        "content": base64.b64encode(content).decode("ascii"),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "telegram-buenas-practicas",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as err:
        if err.code == 422:
            log(f"Ya existia {path}; se omite.")
            return
        raise


def with_retries(label, func):
    for attempt in range(1, 4):
        try:
            func()
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"Fallo en {label} (intento {attempt}/3): {exc}")
            time.sleep(5 * attempt)
    return False


def store_media(msg, update_id, media):
    """Descarga y sube el adjunto. Devuelve (ruta_relativa_a_inbox, estado)."""
    size = media.get("size")
    if size and size > MEDIA_MAX_BYTES:
        mb = size / (1024 * 1024)
        return None, f"pesa {mb:.1f} MB y Telegram solo permite descargar hasta 20 MB"
    when = datetime.fromtimestamp(msg["date"], timezone.utc)
    base = f"{when.strftime('%Y-%m-%d_%H%M%S')}_{update_id}"
    holder = {}

    def download():
        holder["data"], holder["remote"] = tg_download(media["file_id"])

    if not with_retries("descarga del adjunto", download):
        return None, "fallo la descarga desde Telegram"
    if len(holder["data"]) > MEDIA_MAX_BYTES:
        return None, "archivo demasiado grande"
    fname = media_filename(base, media, holder["remote"])
    repo_path = f"{INBOX}/media/{fname}"
    if not with_retries("subida del adjunto", lambda: save_to_github(repo_path, holder["data"])):
        return None, "fallo la subida a GitHub"
    log(f"Guardado adjunto {repo_path} ({len(holder['data'])} bytes)")
    return f"media/{fname}", None


def handle_update(update):
    msg = update.get("message")
    if not msg:
        return
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if (msg.get("from") or {}).get("is_bot"):
        return
    if ALLOWED_CHAT_ID == 0:
        log(
            f"MODO CONFIGURACION: chat id={chat_id} tipo={chat.get('type')} "
            f"titulo={chat.get('title')!r}. No se guarda nada hasta que "
            "pongas ese id en allowed_chat_id."
        )
        return
    if chat_id != ALLOWED_CHAT_ID:
        log(f"Mensaje ignorado de otro chat (id={chat_id}).")
        return
    media = extract_media(msg)
    media_rel, media_status = (None, None)
    if media:
        media_rel, media_status = store_media(msg, update["update_id"], media)
    note = build_note(msg, update["update_id"], media, media_rel, media_status)
    if note is None:
        log("Mensaje sin texto ni adjunto, o comando: ignorado.")
        return
    path, content = note
    if with_retries(f"guardado de {path}", lambda: save_to_github(path, content)):
        log(f"Guardado {path}")
    else:
        log(f"No se pudo guardar {path}; se descarta.")


def main():
    if not (TG_TOKEN and GH_TOKEN and GH_REPO):
        log("Faltan telegram_token, github_token o github_repo en la configuracion.")
        while True:
            time.sleep(3600)
    offset = None
    while True:
        params = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = tg("getUpdates", params)
        except Exception as exc:  # noqa: BLE001
            log(f"Error al pedir mensajes a Telegram: {exc}")
            time.sleep(10)
            continue
        for update in updates:
            try:
                handle_update(update)
            except Exception as exc:  # noqa: BLE001
                log(f"Error procesando un mensaje: {exc}")
            offset = update["update_id"] + 1


if __name__ == "__main__":
    main()
