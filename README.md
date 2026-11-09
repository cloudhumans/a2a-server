# A2A Reference Agent

Agente de referência para o protocolo [A2A (Agent2Agent)](https://a2a-protocol.org/latest/specification/), feito com [LangGraph](https://www.langchain.com/langgraph) e o [SDK oficial do A2A em Python](https://github.com/a2aproject/a2a-python). Ele serve para duas coisas:

1. **Testar uma integração A2A de ponta a ponta**, incluindo o modo assíncrono com push notification, sem depender do agente de um fornecedor.
2. **Servir de ponto de partida** para quem quer expor o próprio agente via A2A: troque o grafo em `app/agent.py` e mantenha o resto.

Vem junto com um painel que mostra, ao vivo, cada chamada que chega ao agente e cada push que ele envia, e com o [A2A Inspector](https://github.com/a2aproject/a2a-inspector), a ferramenta visual oficial do projeto A2A para conversar com agentes.

## O que ele suporta

| Recurso | Como |
|---|---|
| Protocolo | A2A 1.0, com compatibilidade 0.3 no mesmo endpoint |
| Transportes | JSON-RPC em `/a2a/jsonrpc` e HTTP+JSON em `/a2a/rest` |
| Modo síncrono | `SendMessage` bloqueante, ou `returnImmediately: true` seguido de `GetTask` |
| Streaming | `SendStreamingMessage` (SSE) |
| Modo assíncrono | Push notification para o webhook informado em `taskPushNotificationConfig`, com o token em `X-A2A-Notification-Token` |
| Input required | A tarefa para em `input-required` e continua na mesma `taskId` quando o cliente responde |
| Autenticação | Nenhuma, token fixo Bearer, ou API key num header configurável |
| LLM | OpenAI quando há `OPENAI_API_KEY`; sem chave, responde em modo eco |

## Subindo com Docker

```bash
cp .env.example .env
docker compose up --build
```

Isso sobe dois serviços:

- **Agente** em `http://localhost:8000`. O agent card fica em `http://localhost:8000/.well-known/agent-card.json` e o painel de tráfego em `http://localhost:8000/dashboard`.
- **A2A Inspector** em `http://localhost:8080`, já apontando para o agente.

O Inspector exige um token de sessão. Ele imprime a URL completa no log ao iniciar:

```bash
docker compose logs inspector
```

Abra a URL que aparece ali (com `?token=...`). Abrir só `http://localhost:8080` não entra.

As portas ficam presas a `127.0.0.1`. Para mudar, use `AGENT_PORT` e `INSPECTOR_PORT` no `.env`.

## Rodando sem Docker

Requer [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv run python -m app
```

Sem Docker, o agente escuta em `127.0.0.1:8000` por padrão (`HOST` e `PORT` mudam isso). Para usar o Inspector junto, instale-o com `go install github.com/a2aproject/a2a-inspector@latest` e rode `a2a-inspector -a http://localhost:8000`.

## Controlando o comportamento pela mensagem

Para testar estados e tempos da tarefa sem mexer no código, inclua comandos no texto da mensagem. Eles são removidos antes de o texto chegar ao LLM.

| Comando | Efeito |
|---|---|
| `[delay=N]` | Espera N segundos antes de responder, emitindo uma atualização `working` a cada `PROGRESS_INTERVAL_SECONDS` |
| `[input-required]` | Termina o turno em `input-required` pedindo um dado. A próxima mensagem na mesma tarefa recebe a resposta normal |
| `[fail]` | Termina a tarefa em `failed` |

Exemplos: `[delay=420] gere o relatório` exercita o caminho assíncrono de uma tarefa longa; `[input-required] quero remarcar minha viagem` exercita a retomada da tarefa.

`DEFAULT_DELAY_SECONDS` aplica um atraso a toda mensagem que não tenha `[delay=N]`.

## Autenticação

| `A2A_AUTH_MODE` | O cliente envia |
|---|---|
| `none` (padrão) | nada |
| `bearer` | `Authorization: Bearer <A2A_AUTH_TOKEN>` |
| `api_key` | `<A2A_API_KEY_HEADER>: <A2A_AUTH_TOKEN>` (o header padrão é `X-API-Key`) |

A autenticação vale para tudo em `/a2a/*`. O agent card e o painel continuam abertos, e o card passa a declarar o esquema exigido em `securitySchemes`. Requisição sem credencial válida recebe `401`.

Para o Inspector do compose já conectar autenticado no modo `bearer`, preencha `INSPECTOR_AUTH="Bearer <token>"` no `.env`. No modo `api_key`, informe o header na tela de conexão do Inspector.

## Painel de tráfego

`/dashboard` mostra, em tempo real:

- cada requisição recebida (`SendMessage`, `GetTask`, leitura do card), com status HTTP, duração, `taskId` e o estado da tarefa na resposta;
- cada push notification enviada, com a URL de destino, o tipo de evento e **a resposta que o webhook do cliente devolveu**, que é o ponto que mais falha em integração assíncrona;
- corpo e headers completos ao clicar numa linha, com credenciais mascaradas.

O histórico fica em memória (as últimas `TRAFFIC_CAPACITY` entradas, 500 por padrão) e some ao reiniciar.

## Teste ponta a ponta

Com o agente no ar, `scripts/smoke_test.py` confere card, modo síncrono, `returnImmediately` com `GetTask`, push para um webhook local, `input-required` e, quando você passa um token, a recusa sem credencial:

```bash
uv run python scripts/smoke_test.py --url http://localhost:8000
uv run python scripts/smoke_test.py --url http://localhost:8000 --token "$A2A_AUTH_TOKEN"
uv run python scripts/smoke_test.py --url http://localhost:8000 --token "$A2A_AUTH_TOKEN" --api-key-header X-API-Key
```

O script sobe o próprio webhook em `127.0.0.1:8099`. Se o agente roda em outra máquina ou container, passe uma URL que ele alcance com `--webhook-url`. Ele também funciona contra qualquer outro agente A2A 1.0 que tenha push notification, o que ajuda a validar um agente antes de integrá-lo.

## Usando como base para o seu agente

- `app/agent.py`: o grafo LangGraph. Troque por qualquer grafo que receba e devolva mensagens.
- `app/executor.py`: traduz a execução do grafo em estados de tarefa A2A (`working`, `input-required`, `completed`, `failed`).
- `app/server.py`: agent card, rotas A2A, push notification e painel.
- `app/auth.py` e `app/traffic.py`: autenticação por token fixo e registro de tráfego.

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `OPENAI_API_KEY` | vazio | Liga o LLM. Sem ela, o agente responde em eco |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo usado |
| `AGENT_SYSTEM_PROMPT` | prompt curto em inglês | Instruções do agente |
| `AGENT_NAME` | `A2A Reference Agent` | Nome no card e no painel |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Onde o servidor escuta |
| `PUBLIC_URL` | vazio | URL publicada no card. Vazio usa o host de cada requisição |
| `A2A_AUTH_MODE` | `none` | `none`, `bearer` ou `api_key` |
| `A2A_AUTH_TOKEN` | vazio | Token aceito nos modos com autenticação |
| `A2A_API_KEY_HEADER` | `X-API-Key` | Header lido no modo `api_key` |
| `DEFAULT_DELAY_SECONDS` | `0` | Atraso padrão de cada resposta |
| `PROGRESS_INTERVAL_SECONDS` | `10` | Intervalo das atualizações `working` durante o atraso |
| `PUSH_URL_VALIDATION` | `off` | `strict` recusa webhooks em endereços privados ou de loopback |
| `TRAFFIC_CAPACITY` | `500` | Entradas guardadas pelo painel |

## Limites

- Tarefas, configurações de push e histórico ficam em memória. É um agente de teste, não um servidor de produção.
- O painel não tem autenticação e mostra o conteúdo das mensagens. Não exponha a porta do agente na internet sem colocar algo na frente.
- OAuth2 `client_credentials` não está implementado.

## Licença

Apache 2.0.
