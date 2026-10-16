#!/usr/bin/env python3
"""Bot de Telegram -> notas .md en un repo de GitHub (carpeta 00_Inbox).

Solo usa la biblioteca estandar de Python. Los tokens llegan por variables de
entorno y nunca se escriben en los logs.
"""
import base64
import json
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


def extract_links(text):
    seen = []
    for link in URL_RE.findall(text):
        link = link.rstrip(".,;:!?)]}")
        if link not in seen:
            seen.append(link)
    return seen


def build_note(msg, update_id):
    """Devuelve (ruta, contenido) o None si el mensaje no tiene texto."""
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text or text.startswith("/"):
        return None
    sender = msg.get("from") or {}
    name = sender.get("first_name") or sender.get("username") or "desconocido"
    when = datetime.fromtimestamp(msg["date"], timezone.utc)
    links = extract_links(text)
    front = [
        "---",
        "titulo: " + json.dumps("Mensaje de Telegram " + when.strftime("%Y-%m-%d %H:%M"), ensure_ascii=False),
        "fuente: telegram",
        "enviado_por: " + json.dumps(name, ensure_ascii=False),
        "fecha: " + when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "enlaces: " + json.dumps(links, ensure_ascii=False),
        "estado: pendiente",
        "aplica_a: ",
        "tags: [inbox, telegram]",
        "---",
        "",
        text,
        "",
    ]
    path = f"{INBOX}/{when.strftime('%Y-%m-%d_%H%M%S')}_{update_id}.md"
    return path, "\n".join(front)


def save_to_github(path, content):
    url = (
        f"https://api.github.com/repos/{GH_REPO}/contents/"
        + urllib.parse.quote(path)
    )
    body = {
        "message": "Telegram: nuevo mensaje",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
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
        urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as err:
        if err.code == 422:
            log(f"Ya existia {path}; se omite.")
            return
        raise


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
    note = build_note(msg, update["update_id"])
    if note is None:
        log("Mensaje sin texto o comando: ignorado.")
        return
    path, content = note
    for attempt in range(1, 4):
        try:
            save_to_github(path, content)
            log(f"Guardado {path}")
            return
        except Exception as exc:  # noqa: BLE001
            log(f"Fallo al guardar (intento {attempt}/3): {exc}")
            time.sleep(5 * attempt)
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
