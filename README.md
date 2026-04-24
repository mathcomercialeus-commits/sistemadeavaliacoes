# Sistema de Avaliacao de Entregas

Aplicacao Flask simples para:

- cadastrar motoristas
- cadastrar acessos da caixa
- gerar etiquetas com QR Code na area da caixa
- permitir que clientes avaliem a entrega

## Perfis

- `admin`: cadastra logins e senhas de motoristas e caixas
- `cashier`: faz login na area da caixa e imprime etiquetas
- `driver`: faz login no proprio painel e acompanha suas avaliacoes

## Rodando localmente

```bash
pip install -r requirements.txt
python app.py
```

Sem `DATABASE_URL`, o app usa SQLite local apenas para desenvolvimento.

## Variaveis de ambiente

- `SECRET_KEY`
- `DATABASE_URL`
- `ADMIN_USERNAME`
- `ADMIN_NAME`
- `ADMIN_PASSWORD`
- `PORT`
- `LABEL_TOKEN_EXPIRY_HOURS`

## Deploy no Render

O repositiorio inclui `render.yaml` com:

- `gunicorn app:app`
- Render Postgres para persistir cadastros e avaliacoes
- `SECRET_KEY` gerada no deploy
- `DATABASE_URL` apontando para o banco Postgres

Defina `ADMIN_PASSWORD` no Render antes de publicar.
