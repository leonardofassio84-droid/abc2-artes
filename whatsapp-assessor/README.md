# Assessor no WhatsApp Business (com Claude)

Fale com o Claude pelo WhatsApp, encaminhe mensagens de clientes para ele resumir e registrar,
e consulte seus registros quando quiser.

## O que ele faz

| Você manda no WhatsApp | O assessor faz |
|---|---|
| Qualquer pergunta ou pedido ("escreve uma legenda pro post do Bonde") | Responde como o Claude |
| **Encaminha** uma mensagem de cliente | Resume, salva como registro e sugere uma resposta |
| "Registra: Maria pediu 3 camisetas, R$ 150, entrega sexta" | Cria o registro (categoria, contato, valor, data) |
| "O que a Maria pediu?" / "Quanto vendi esse mês?" | Busca nos registros |
| "Corrige o registro 12: o valor é 180" / "apaga o 12" | Edita ou apaga |
| "Manda pro 11 99999-9999: seu pedido está pronto" | Mostra o texto, pede seu ok e envia |
| Foto, print ou PDF (com ou sem legenda) | Lê o conteúdo e trabalha em cima dele |
| `/limpar` · `/ajuda` | Zera a conversa · mostra os comandos |

Os registros ficam num banco SQLite (`assessor.db`) e podem ser baixados em planilha:
`https://SEU-SERVIDOR/registros.csv?token=SEU_EXPORT_TOKEN` (abre no Excel/Google Sheets).

Só os números em `ALLOWED_NUMBERS` são atendidos; mensagens de outros números são ignoradas.

## Passo a passo

### 1. Criar o app na Meta (WhatsApp Cloud API)
1. Em <https://developers.facebook.com> → **Meus apps** → **Criar app** → tipo **Empresa**.
2. Adicione o produto **WhatsApp** e vincule sua conta do WhatsApp Business.
3. Em **WhatsApp → Configuração da API**, copie o **ID do número de telefone**.
4. No **Business Manager → Usuários do sistema**, crie um usuário de sistema, dê acesso ao app e gere
   um **token permanente** com as permissões `whatsapp_business_messaging` e `whatsapp_business_management`.
5. Em **Configurações do app → Básico**, copie a **Chave secreta do app**.

> O número usado pela API não pode estar ao mesmo tempo no aplicativo WhatsApp Business do celular.
> O mais simples é usar um número separado para o assessor e falar com ele a partir do seu WhatsApp.

### 2. Chave da Claude
Crie uma chave em <https://console.anthropic.com> → **API Keys**.

### 3. Configurar e rodar
```bash
cd whatsapp-assessor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # preencha os valores
uvicorn app:app --host 0.0.0.0 --port 8000
```

O servidor precisa de um endereço **HTTPS público**. Opções:
- Teste rápido no seu computador: `ngrok http 8000` (ou Cloudflare Tunnel).
- Produção: Render, Railway, Fly.io ou uma VPS. Use disco persistente para o `assessor.db`.

### 4. Ligar o webhook
Em **WhatsApp → Configuração → Webhook**:
- **URL de callback:** `https://SEU-SERVIDOR/webhook`
- **Token de verificação:** o mesmo valor de `WHATSAPP_VERIFY_TOKEN`
- Clique em **Verificar e salvar** e assine o campo **messages**.

Pronto: mande "oi" para o número do assessor.

## Observações
- **Janela de 24 h da Meta:** o assessor só consegue enviar mensagem livre para alguém que falou com o
  número do assessor nas últimas 24 h. Para quem não falou, a Meta exige modelo (template) aprovado.
  Se o envio for recusado, o assessor avisa.
- **Áudio** ainda não é lido (texto, imagem, PDF, contato e localização sim).
- Modelo: `claude-opus-5-5` (troque em `CLAUDE_MODEL`). `CLAUDE_EFFORT` aceita `low`, `medium` e `high`:
  `low` responde mais rápido e gasta menos, `high` pensa mais.
- Se o Claude recusar um pedido por política de segurança, a API tenta de novo num modelo alternativo
  automaticamente (`fallbacks: "default"`).

## Arquivos
- `app.py`: servidor (webhook, filtro de números, exportação CSV)
- `assessor.py`: Claude, instruções e ferramentas (registrar, buscar, editar, apagar, enviar)
- `db.py`: banco SQLite (histórico, registros)
- `whatsapp.py`: chamadas à API do WhatsApp
