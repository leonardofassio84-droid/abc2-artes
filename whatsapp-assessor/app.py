"""Servidor do webhook do WhatsApp Business.

Rodar:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
import csv
import io
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import PlainTextResponse, Response  # noqa: E402

import assessor  # noqa: E402
import db  # noqa: E402
import whatsapp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("assessor")

db.init(os.getenv("DB_PATH", "assessor.db"))
AUTORIZADOS = {n.strip() for n in os.getenv("ALLOWED_NUMBERS", "").split(",") if n.strip()}

app = FastAPI(title="Assessor WhatsApp + Claude")


@app.get("/")
def saude():
    return {"status": "ok"}


# 1) Verificação do webhook (feita uma vez pela Meta ao configurar)
@app.get("/webhook")
def verificar(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN"):
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403)


# 2) Recebimento de mensagens
@app.post("/webhook")
async def receber(request: Request, tarefas: BackgroundTasks):
    corpo = await request.body()
    if not whatsapp.assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="assinatura inválida")

    dados = await request.json()
    for entry in dados.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            nomes = {c.get("wa_id"): c.get("profile", {}).get("name") for c in valor.get("contacts", [])}
            for msg in valor.get("messages", []):
                # Responde 200 na hora; o Claude trabalha em segundo plano.
                tarefas.add_task(processar, msg, nomes.get(msg.get("from")))
    return {"status": "recebido"}


def _texto_da_mensagem(msg: dict) -> tuple[str, str | None]:
    """Extrai (texto, media_id) de qualquer tipo de mensagem suportado."""
    tipo = msg.get("type")
    if tipo == "text":
        return msg["text"]["body"], None
    if tipo in ("image", "document"):
        m = msg[tipo]
        legenda = m.get("caption", "")
        if tipo == "document" and m.get("filename"):
            legenda = f"[arquivo: {m['filename']}] {legenda}".strip()
        return legenda, m.get("id")
    if tipo == "contacts":
        linhas = []
        for c in msg["contacts"]:
            nome = c.get("name", {}).get("formatted_name", "")
            fones = ", ".join(p.get("phone", "") for p in c.get("phones", []))
            linhas.append(f"Contato compartilhado: {nome} – {fones}")
        return "\n".join(linhas), None
    if tipo == "location":
        loc = msg["location"]
        return (f"Localização: {loc.get('name', '')} {loc.get('address', '')} "
                f"({loc.get('latitude')}, {loc.get('longitude')})"), None
    if tipo == "interactive":
        i = msg["interactive"]
        return (i.get("button_reply") or i.get("list_reply") or {}).get("title", ""), None
    if tipo == "button":
        return msg["button"].get("text", ""), None
    return "", None


def processar(msg: dict, nome: str | None) -> None:
    numero = msg.get("from", "")
    wamid = msg.get("id", "")
    if db.ja_processada(wamid):
        return

    if AUTORIZADOS and numero not in AUTORIZADOS:
        log.info("Mensagem ignorada de número não autorizado: %s", numero)
        return

    whatsapp.marcar_lida(wamid)
    try:
        texto, media_id = _texto_da_mensagem(msg)
        anexos = []
        if media_id:
            anexo = assessor.anexo_de_midia(media_id)
            if anexo:
                anexos.append(anexo)
            else:
                texto = (texto + "\n(arquivo em formato que não consigo ler)").strip()

        if not texto and not anexos:
            whatsapp.enviar_texto(numero, "Por enquanto entendo texto, imagens, PDFs, contatos e "
                                          "localização. Áudio ainda não. 🙏")
            return

        contexto = msg.get("context", {})
        encaminhada = bool(contexto.get("forwarded") or contexto.get("frequently_forwarded"))
        resposta = assessor.responder(numero, texto, anexos, encaminhada)
        whatsapp.enviar_texto(numero, resposta)
    except Exception:
        log.exception("Erro ao processar mensagem %s", wamid)
        try:
            whatsapp.enviar_texto(numero, "Tive um problema para processar isso agora. "
                                          "Tenta de novo em instantes? ⚠️")
        except Exception:
            log.exception("Falha ao avisar o usuário")


# 3) Exportar registros em CSV (abre direto no Excel / Google Sheets)
@app.get("/registros.csv")
def exportar(token: str = Query(...)):
    if token != os.getenv("EXPORT_TOKEN") or not os.getenv("EXPORT_TOKEN"):
        raise HTTPException(status_code=403)
    regs = db.todos_registros()
    buf = io.StringIO()
    campos = ["id", "categoria", "titulo", "conteudo", "contato", "valor", "data_ref",
              "origem", "criado_em", "numero"]
    w = csv.DictWriter(buf, fieldnames=campos, extrasaction="ignore", delimiter=";")
    w.writeheader()
    w.writerows(regs)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=registros.csv"})
