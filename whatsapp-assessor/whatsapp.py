"""Cliente mínimo da WhatsApp Business Cloud API (Meta)."""
import hashlib
import hmac
import os

import httpx


def _base() -> str:
    return f"https://graph.facebook.com/{os.getenv('GRAPH_API_VERSION', 'v23.0')}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}"}


def assinatura_valida(corpo: bytes, assinatura: str | None) -> bool:
    """Confere o cabeçalho X-Hub-Signature-256 enviado pela Meta."""
    segredo = os.getenv("WHATSAPP_APP_SECRET")
    if not segredo:  # sem segredo configurado, não dá para validar (apenas para testes)
        return True
    if not assinatura or not assinatura.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura.removeprefix("sha256="))


def _quebrar(texto: str, limite: int = 4000) -> list[str]:
    """O WhatsApp aceita até 4096 caracteres por mensagem."""
    partes = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = limite
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip("\n")
    partes.append(texto)
    return [p for p in partes if p.strip()]


def enviar_texto(para: str, texto: str) -> dict:
    url = f"{_base()}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    ultimo: dict = {}
    for parte in _quebrar(texto):
        r = httpx.post(
            url,
            headers=_headers(),
            json={
                "messaging_product": "whatsapp",
                "to": para,
                "type": "text",
                "text": {"body": parte, "preview_url": False},
            },
            timeout=30,
        )
        r.raise_for_status()
        ultimo = r.json()
    return ultimo


def marcar_lida(wamid: str) -> None:
    url = f"{_base()}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    try:
        httpx.post(url, headers=_headers(), timeout=15, json={
            "messaging_product": "whatsapp", "status": "read", "message_id": wamid,
        })
    except httpx.HTTPError:
        pass


def baixar_midia(media_id: str) -> tuple[bytes, str]:
    """Devolve (bytes, mime_type) de uma mídia recebida."""
    info = httpx.get(f"{_base()}/{media_id}", headers=_headers(), timeout=30)
    info.raise_for_status()
    dados = info.json()
    arq = httpx.get(dados["url"], headers=_headers(), timeout=60)
    arq.raise_for_status()
    return arq.content, dados.get("mime_type", "application/octet-stream")
