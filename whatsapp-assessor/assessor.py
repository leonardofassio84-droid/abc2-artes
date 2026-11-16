"""Cérebro do assessor: Claude + ferramentas de registro e envio de mensagens."""
import base64
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
from anthropic import beta_tool

import db
import whatsapp

client = anthropic.Anthropic()

MODELO = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
ESFORCO = os.getenv("CLAUDE_EFFORT", "medium")

SISTEMA = """Você é {nome}, assessor(a) pessoal de negócios que atende o dono pelo WhatsApp Business.
Responda sempre em português do Brasil, de forma direta e cordial, no estilo de conversa de WhatsApp:
frases curtas, listas simples, *negrito* com asteriscos quando ajudar. Nada de tabelas ou Markdown com #.

O que você faz:
1. Conversa e ajuda: responde dúvidas, redige textos, propostas, legendas, respostas para clientes etc.
2. Mensagens encaminhadas: quando o conteúdo chegar marcado como [ENCAMINHADA], o dono está repassando
   algo que recebeu de outra pessoa. Se ele não disser o que quer, faça por padrão:
   - salve um registro (ferramenta `registrar`, origem "encaminhada"), escolhendo a categoria certa;
   - responda com um resumo de 1-3 linhas, o que foi registrado (com o nº do registro) e,
     se fizer sentido, uma sugestão de resposta pronta para ele copiar.
3. Registros: use `registrar` sempre que o dono pedir para anotar/registrar/salvar/lembrar algo, e
   `buscar_registros` / `resumo_registros` para consultas ("o que o João pediu?", "quanto vendi essa semana?").
   Categorias sugeridas: cliente, pedido, venda, despesa, tarefa, ideia, reuniao, contato, nota.
   Preencha contato, valor (número, em reais) e data_ref (AAAA-MM-DD) quando a informação existir.
   Nunca invente registros: só confirme o que a ferramenta devolveu.
4. Enviar mensagem para outra pessoa: use `enviar_whatsapp` somente quando o dono pedir de forma explícita.
   Antes de enviar, mostre o texto final e o número e peça um "ok", a menos que ele já tenha dito
   exatamente o texto e o destinatário e mandado enviar.

Comandos que o dono pode usar: /limpar (zera o histórico da conversa), /ajuda.
Data e hora atuais: {agora}."""

AJUDA = (
    "*Como usar o assessor*\n"
    "• Converse normalmente: peça textos, ideias, respostas para clientes.\n"
    "• *Encaminhe* qualquer mensagem para cá: eu resumo e registro.\n"
    "• \"Registra: Maria pediu 3 camisetas, R$ 150, entrega sexta\"\n"
    "• \"O que a Maria pediu?\" / \"Resumo das vendas da semana\"\n"
    "• \"Manda pro 11 99999-9999: seu pedido está pronto\"\n"
    "• /limpar – começa uma conversa nova"
)


def _so_digitos(numero: str) -> str:
    d = re.sub(r"\D", "", numero or "")
    if len(d) in (10, 11):  # número brasileiro sem DDI
        d = "55" + d
    return d


def _ferramentas(numero_dono: str, origem_padrao: str):
    """Cria as ferramentas já ligadas ao número de quem está falando."""

    @beta_tool
    def registrar(categoria: str, titulo: str, conteudo: str, contato: str | None = None,
                  valor: float | None = None, data_ref: str | None = None,
                  origem: str | None = None) -> str:
        """Salva um registro permanente (anotação, pedido, venda, despesa, tarefa, contato...).

        Args:
            categoria: Uma palavra, ex.: cliente, pedido, venda, despesa, tarefa, ideia, reuniao, contato, nota.
            titulo: Título curto (até ~60 caracteres).
            conteudo: Texto completo do registro, com todos os detalhes relevantes.
            contato: Nome e/ou telefone da pessoa relacionada, se houver.
            valor: Valor em reais (número), se houver.
            data_ref: Data a que o registro se refere, no formato AAAA-MM-DD, se houver.
            origem: "conversa" ou "encaminhada".
        """
        reg_id = db.criar_registro(numero_dono, categoria, titulo, conteudo, contato, valor,
                                   data_ref, origem or origem_padrao)
        return json.dumps({"ok": True, "id": reg_id})

    @beta_tool
    def buscar_registros(texto: str | None = None, categoria: str | None = None,
                         contato: str | None = None, desde: str | None = None,
                         ate: str | None = None, limite: int = 20) -> str:
        """Busca registros salvos. Todos os filtros são opcionais e combinados com E.

        Args:
            texto: Palavra ou trecho a procurar no título, conteúdo ou contato.
            categoria: Filtrar por categoria exata.
            contato: Filtrar por nome/telefone do contato.
            desde: Data inicial AAAA-MM-DD.
            ate: Data final AAAA-MM-DD.
            limite: Máximo de resultados (padrão 20).
        """
        regs = db.buscar_registros(texto, categoria, contato, desde, ate, min(limite, 100))
        return json.dumps(regs, ensure_ascii=False)

    @beta_tool
    def resumo_registros() -> str:
        """Mostra quantos registros existem por categoria e a soma dos valores."""
        return json.dumps(db.resumo_categorias(), ensure_ascii=False)

    @beta_tool
    def atualizar_registro(id: int, categoria: str | None = None, titulo: str | None = None,
                           conteudo: str | None = None, contato: str | None = None,
                           valor: float | None = None, data_ref: str | None = None) -> str:
        """Corrige campos de um registro existente. Só envie os campos que mudam.

        Args:
            id: Número do registro.
            categoria: Nova categoria.
            titulo: Novo título.
            conteudo: Novo conteúdo completo.
            contato: Novo contato.
            valor: Novo valor em reais.
            data_ref: Nova data AAAA-MM-DD.
        """
        ok = db.atualizar_registro(id, dict(categoria=categoria, titulo=titulo, conteudo=conteudo,
                                            contato=contato, valor=valor, data_ref=data_ref))
        return json.dumps({"ok": ok})

    @beta_tool
    def apagar_registro(id: int) -> str:
        """Apaga um registro. Use apenas quando o dono pedir claramente.

        Args:
            id: Número do registro.
        """
        return json.dumps({"ok": db.apagar_registro(id)})

    @beta_tool
    def enviar_whatsapp(numero: str, texto: str) -> str:
        """Envia uma mensagem de texto pelo WhatsApp Business para outra pessoa.

        Só funciona se essa pessoa tiver mandado mensagem para este número nas últimas 24 horas
        (regra da Meta); caso contrário a API recusa.

        Args:
            numero: Telefone do destinatário com DDD (o DDI 55 é colocado automaticamente).
            texto: Texto exato a ser enviado.
        """
        destino = _so_digitos(numero)
        try:
            whatsapp.enviar_texto(destino, texto)
        except Exception as e:  # noqa: BLE001 - devolve o erro para o Claude explicar ao dono
            detalhe = getattr(getattr(e, "response", None), "text", str(e))
            return json.dumps({"ok": False, "erro": detalhe[:500]})
        db.criar_registro(numero_dono, "envio", f"Mensagem enviada para {destino}", texto,
                          contato=destino, origem="conversa")
        return json.dumps({"ok": True, "enviado_para": destino})

    return [registrar, buscar_registros, resumo_registros, atualizar_registro,
            apagar_registro, enviar_whatsapp]


def responder(numero: str, texto: str, anexos: list[dict] | None = None,
              encaminhada: bool = False) -> str:
    """Processa uma mensagem do dono e devolve o texto de resposta."""
    comando = texto.strip().lower()
    if comando in ("/limpar", "/reset"):
        db.limpar_historico(numero)
        return "Pronto, comecei uma conversa nova. Os registros continuam salvos. 👍"
    if comando in ("/ajuda", "/help", "ajuda"):
        return AJUDA

    if encaminhada:
        texto = f"[ENCAMINHADA]\n{texto}"

    conteudo_atual: list[dict] | str = texto
    if anexos:
        conteudo_atual = [*anexos, {"type": "text", "text": texto or "(sem legenda)"}]

    mensagens = db.historico(numero) + [{"role": "user", "content": conteudo_atual}]
    if len(mensagens) >= 2 and mensagens[-2]["role"] == "user":  # garante alternância
        mensagens.pop(-2)

    agora = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo")))
    sistema = SISTEMA.format(nome=os.getenv("NOME_ASSESSOR", "Assessor"),
                             agora=agora.strftime("%A, %d/%m/%Y %H:%M"))

    runner = client.beta.messages.tool_runner(
        model=MODELO,
        max_tokens=16000,
        system=sistema,
        tools=_ferramentas(numero, "encaminhada" if encaminhada else "conversa"),
        messages=mensagens,
        output_config={"effort": ESFORCO},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=10,
    )
    final = runner.until_done()

    if final.stop_reason == "refusal":
        resposta = "Desculpe, não consigo ajudar com esse pedido."
    else:
        resposta = "\n".join(b.text for b in final.content if b.type == "text").strip()
        resposta = resposta or "Feito. ✅"

    # O histórico guarda só texto (anexos viram uma marcação) para ficar leve.
    marcador = "".join(f"[{a['type']} anexado]\n" for a in (anexos or []))
    db.salvar_mensagem(numero, "user", marcador + texto)
    db.salvar_mensagem(numero, "assistant", resposta)
    return resposta


def anexo_de_midia(media_id: str) -> dict | None:
    """Converte uma mídia do WhatsApp num bloco de conteúdo do Claude (imagem ou PDF)."""
    dados, mime = whatsapp.baixar_midia(media_id)
    b64 = base64.standard_b64encode(dados).decode()
    mime = mime.split(";")[0].strip()
    if mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": mime, "data": b64}}
    return None
