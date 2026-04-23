# Sistema de Avaliacao de Entregas

Aplicacao Flask simples para:

- cadastrar motoristas
- cadastrar acessos da caixa
- gerar etiquetas com QR Code na area da caixa
- permitir que clientes avaliem a entrega

## Perfis

- `admin`: cadastra logins e senhas de motoristas e caixas
- `cashier`: faz login na area da caixa e imprime etiquetas
- `driver`: faz login no proprio painel e exibe o QR Code

## Rodando localmente

```bash
pip install -r requirements.txt
python app.py
```

## Variaveis de ambiente

- `SECRET_KEY`
- `DATABASE_PATH`
- `ADMIN_USERNAME`
- `ADMIN_NAME`
- `ADMIN_PASSWORD`
- `PORT`

## Deploy no Render

O repositiorio inclui `render.yaml` com:

- `gunicorn app:app`
- disco persistente para SQLite
- `SECRET_KEY` gerada no deploy
- `DATABASE_PATH` apontando para o disco do Render

Defina `ADMIN_PASSWORD` no Render antes de publicar.
