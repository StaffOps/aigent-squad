# Design: Fix Blockers

## Abordagem

Mudanças cirúrgicas, sem refatorar arquitetura (isso é a spec 02). Cada blocker é independente e pode ser feito em paralelo.

## B1 — Dockerfile raiz

O supervisor precisa de `src/` inteiro (importa `src.supervisor`, `src.core.*`). Reusar o mesmo padrão dos agentes:

```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
EXPOSE 8000
CMD ["python", "-m", "src.supervisor.server"]
```

> Nota: `USER` não-root entra na spec 04 (segurança), para não misturar escopo. Aqui só destravar o build.

## B2 — `src/api/server.py`

Decisão: **reescrever mínimo** (manter a feature Slack, que é citada no README). Novo fluxo:

```
Slack event → verifica assinatura → supervisor.process_request(text, user_id, session_id) → chat_postMessage
```

- Remove `StateStore`, `HumanMessage`, `supervisor.graph`.
- `session_id = f"{channel}:{thread_ts}"` (mantém o conceito original).
- O histórico já é gerido pelo `ChatStorage` dentro do supervisor — o webhook não precisa gerir estado.
- `process_request` é async → handler async com `await`.

Alternativa considerada: deletar o arquivo. Rejeitada porque a integração Slack é um diferencial citado e o custo de manter é baixo após o fix.

## B3 — `gitlab_client.py`

Truncar o arquivo no final da primeira definição completa da classe (antes do bloco duplicado) e deixar um único singleton. Sem mudança de assinatura.

Verificar antes que `devops/agent.py` use apenas métodos presentes na primeira definição (ele usa `self.gitlab = gitlab_client`).

## B4 — `mcp-server.py`

Remover o segundo par de linhas `app = FastAPI(...)` / `SUPERVISOR_URL = ...`.

## Invariantes

- Não alterar contrato HTTP de nenhum serviço.
- Não alterar `requirements.txt` (exceto se B2 exigir — não exige).
- Comportamento read-only preservado.

## Dependências externas

Nenhuma nova. Usa o que já está em `requirements.txt` (`slack-sdk` já presente para B2).

## Verificação

Build via Docker (sem SDK local, per `dev-environment.md`):

```bash
docker compose build supervisor mcp-server
docker run --rm -v $(pwd):/src -w /src python:3.12-alpine \
  sh -c "pip install -q -r requirements.txt && python -c 'import src.core.gitlab_client'"
```
