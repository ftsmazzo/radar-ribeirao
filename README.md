# Radar Municipal · Ribeirão Preto

Demo da Inteligência Eleitoral para controle de narrativa municipal: escuta → afirmação → base oficial + web → carimbo → nota de crise → alerta à imprensa.

## Rodar local

```bash
cd C:\Users\anjo_\Projects\radar-ribeirao
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# opcional: cole OPENROUTER_API_KEY no .env para busca web ao vivo
uvicorn app.main:app --reload --port 8010
```

Abra http://127.0.0.1:8010

- Sem `OPENROUTER_API_KEY`: demo completa com a base oficial (KB).
- Com chave: mesma mesa + busca web via OpenRouter (Claude + plugin web).

## Apresentação

1. Abrir a Sala.
2. Clicar **Iniciar programa**.
3. Acompanhar o pipeline e os quatro casos (3 a corrigir / 1 confirmado).
4. Mostrar fontes oficiais e a Sala de Imprensa.

## EasyPanel (depois)

`Dockerfile` pronto. Variáveis em `.env.example`. Banco SQLite serve a demo; em produção use `DATABASE_URL` Postgres dedicado.
