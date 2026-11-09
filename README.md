# A2A Reference Agent

Um agente pronto para testar integrações [A2A (Agent2Agent)](https://a2a-protocol.org/latest/specification/) de ponta a ponta: modo síncrono, assíncrono com push notification, `input-required` e falhas. Feito com [LangGraph](https://www.langchain.com/langgraph) e o [SDK oficial do A2A em Python](https://github.com/a2aproject/a2a-python), e sobe com um painel que mostra cada chamada recebida e cada push enviado.

![Painel A2A Server](docs/a2a-server.png)

## Começando

```bash
cp .env.example .env        # opcional: coloque sua OPENAI_API_KEY
docker compose up --build
```

| O que | Onde |
|---|---|
| **A2A Server**, o painel de tráfego do agente | http://localhost:8000/dashboard |
| **Agent Card**, a URL que se cadastra na sua plataforma | http://localhost:8000/.well-known/agent-card.json |
| **A2A Inspector**, um cliente visual para conversar com o agente | link com token em `docker compose logs inspector` |

Sem `OPENAI_API_KEY`, o agente responde em modo eco (`Echo: <sua mensagem>`), o que já basta para testar o protocolo.

## O que dá para testar

Escreva estes comandos no texto da mensagem para forçar cada situação:

| Na mensagem | O que acontece |
|---|---|
| qualquer texto | Resposta normal, tarefa termina em `completed` |
| `[delay=30]` | O agente demora 30 s, mandando atualizações `working` no caminho. Bom para o modo assíncrono |
| `[input-required]` | A tarefa para pedindo um dado. A próxima mensagem na mesma tarefa a conclui |
| `[fail]` | A tarefa termina em `failed` |

Quando o cliente manda um texto fixo e você não controla a mensagem, use a variação que sempre falha:

```bash
docker compose --profile variants up -d agent-fail     # http://localhost:8001
```

## Conceitos em 30 segundos

- **Agent Card**: a ficha pública do agente, definida pelo protocolo. Diz o nome, as habilidades, os endereços e a autenticação exigida. Todo cliente começa por ela.
- **Tarefa (task)**: cada pedido vira uma tarefa com estado (`submitted`, `working`, `input-required`, `completed`, `failed`...).
- **Síncrono**: o cliente espera a resposta, ou recebe a tarefa na hora (`returnImmediately`) e consulta com `GetTask`.
- **Assíncrono**: o cliente informa um webhook (`taskPushNotificationConfig`) e o agente avisa cada mudança de estado com um POST, mandando o token em `X-A2A-Notification-Token`.
- **Status e artefato**: o status diz em que pé a tarefa está; o artefato é o resultado. A pergunta de um `input-required` vem no status, a resposta final vem no artefato.

## Autenticação

No `.env`:

| `A2A_AUTH_MODE` | O cliente envia |
|---|---|
| `none` (padrão) | nada |
| `bearer` | `Authorization: Bearer <A2A_AUTH_TOKEN>` |
| `api_key` | `<A2A_API_KEY_HEADER>: <A2A_AUTH_TOKEN>` (padrão `X-API-Key`) |

Vale para tudo em `/a2a/*`; o Agent Card e o painel continuam abertos, e o card declara o esquema exigido. Sem credencial válida, a resposta é `401`. Para o Inspector já entrar autenticado no modo `bearer`, preencha `INSPECTOR_AUTH="Bearer <token>"`.

## Teste automático

```bash
docker compose --profile test run --rm --use-aliases smoke
```

Confere o Agent Card, o modo síncrono, `returnImmediately` com `GetTask`, a entrega de push a um webhook, `input-required` e, se houver autenticação configurada, a recusa sem credencial. O script (`scripts/smoke_test.py`) também roda contra qualquer outro agente A2A 1.0 com push, o que ajuda a validar um agente antes de integrá-lo.

## Sobre o A2A Inspector

O [Inspector](https://github.com/a2aproject/a2a-inspector) é a ferramenta visual oficial do projeto A2A e funciona com qualquer agente, não só com este. O compose já o abre apontando para o agente e recebendo push no próprio webhook: marque "Push notifications" no chat e acompanhe a entrega no painel do A2A Server.

- O log sempre mostra a porta interna `8080`; se você mudou `INSPECTOR_PORT`, troque a porta no link e mantenha o token.
- O projeto ainda não publica imagem pronta, então o primeiro `docker compose up --build` compila o Inspector a partir do código-fonte (tag `v0.2.0`) e leva alguns minutos. Depois fica em cache.

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `OPENAI_API_KEY` | vazio | Liga o LLM. Sem ela, modo eco |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo usado |
| `AGENT_SYSTEM_PROMPT` | prompt curto | Instruções do agente |
| `AGENT_NAME` | `A2A Reference Agent` | Nome no card e no painel |
| `PUBLIC_URL` | vazio | URL publicada no card. Vazio usa o host de cada requisição |
| `A2A_AUTH_MODE` / `A2A_AUTH_TOKEN` / `A2A_API_KEY_HEADER` | `none` / vazio / `X-API-Key` | Autenticação |
| `DEFAULT_DELAY_SECONDS` | `0` | Atraso de toda resposta |
| `PROGRESS_INTERVAL_SECONDS` | `10` | Intervalo das atualizações `working` durante o atraso |
| `FORCE_FAIL` / `FORCE_FAIL_MESSAGE` | `false` / mensagem padrão | Toda tarefa termina em `failed` com esse texto |
| `PUSH_URL_VALIDATION` | `off` | `strict` recusa webhooks em endereços privados ou de loopback |
| `TRAFFIC_CAPACITY` | `500` | Registros guardados pelo painel |
| `AGENT_PORT` / `AGENT_FAIL_PORT` / `INSPECTOR_PORT` | `8000` / `8001` / `8080` | Portas publicadas no seu computador |

## Usando como base para o seu agente

- `app/agent.py`: o grafo LangGraph. Troque por qualquer grafo que receba e devolva mensagens.
- `app/executor.py`: traduz a execução em estados de tarefa A2A.
- `app/server.py`: Agent Card, rotas A2A, push notification e painel.
- `app/auth.py` e `app/traffic.py`: autenticação por token fixo e registro de tráfego.

## Limites

- Tarefas, configurações de push e histórico ficam em memória e somem ao reiniciar. É um agente de teste, não um servidor de produção.
- O painel não tem autenticação e mostra o conteúdo das mensagens. As portas ficam presas a `127.0.0.1`; não exponha na internet sem colocar algo na frente.
- OAuth2 `client_credentials` não está implementado.

## Licença

Apache 2.0.
