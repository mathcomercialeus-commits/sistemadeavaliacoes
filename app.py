import os
import sqlite3
from datetime import datetime
from functools import wraps
from html import escape
from urllib.parse import quote

from flask import Flask, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-essa-chave-no-deploy")

DATABASE = os.environ.get("DATABASE_PATH", "avaliacao_entregadores.db")
DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "FARMALIMA")
DEFAULT_ADMIN_NAME = os.environ.get("ADMIN_NAME", "Administrador")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Farma@lima3535")
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "1") == "1"

db_initialized = False


def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'cashier', 'driver')),
            password_hash TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            comment TEXT,
            ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        );
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ratings_driver_id ON ratings(driver_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_ip ON ratings(ip);")

    try:
        conn.execute("ALTER TABLE ratings ADD COLUMN ip TEXT;")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE ratings ADD COLUMN comment TEXT;")
    except sqlite3.OperationalError:
        pass

    admin = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (DEFAULT_ADMIN_USERNAME,),
    ).fetchone()

    if admin is None:
        conn.execute(
            """
            INSERT INTO users (username, name, role, password_hash)
            VALUES (?, ?, 'admin', ?)
            """,
            (
                DEFAULT_ADMIN_USERNAME,
                DEFAULT_ADMIN_NAME,
                generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            ),
        )

    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    del error
    db = g.pop("db", None)
    if db is not None:
        db.close()


def esc(value):
    return escape(str(value), quote=True)


def format_rating_date(value):
    if not value:
        return "-"
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def render_page(title, body_html):
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>{esc(title)} | Avaliacao de Entregas</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {{
            --brand-1: #00bcd4;
            --brand-2: #008ba3;
            --bg-1: #e0f7fa;
            --text-main: #173042;
            --text-muted: #607d8b;
            --card-bg: #ffffff;
            --danger-1: #ef5350;
            --danger-2: #d32f2f;
            --warning-1: #ffb300;
            --warning-2: #f57c00;
            --line: #e6edf1;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            color: var(--text-main);
            background: linear-gradient(145deg, var(--bg-1), var(--brand-1));
        }}

        .topbar {{
            position: sticky;
            top: 0;
            z-index: 10;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 56px;
            padding: 12px 16px;
            background: linear-gradient(135deg, var(--brand-1), var(--brand-2));
            color: white;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.18);
        }}

        .page {{
            display: flex;
            justify-content: center;
            padding: 18px 12px 32px;
        }}

        .card {{
            width: 100%;
            max-width: 940px;
            background: var(--card-bg);
            border-radius: 22px;
            padding: 24px 18px 28px;
            box-shadow: 0 18px 45px rgba(0, 0, 0, 0.14);
        }}

        @media (min-width: 768px) {{
            .card {{
                padding: 34px 32px 36px;
            }}
        }}

        h1, h2, h3 {{
            margin-top: 0;
            text-align: center;
        }}

        h1 {{
            margin-bottom: 8px;
        }}

        p {{
            margin: 0.45rem 0;
        }}

        .subtitle-center {{
            text-align: center;
            color: var(--text-muted);
            margin-bottom: 18px;
        }}

        .section {{
            margin-bottom: 20px;
        }}

        .section-title {{
            margin-bottom: 6px;
            font-size: 1rem;
            font-weight: 700;
        }}

        .section-subtitle {{
            margin-bottom: 10px;
            font-size: 0.92rem;
            color: var(--text-muted);
        }}

        form {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}

        label {{
            font-size: 0.92rem;
            color: var(--text-muted);
            font-weight: 600;
        }}

        input[type=text],
        input[type=password],
        select,
        textarea {{
            width: 100%;
            padding: 11px 12px;
            border: 1px solid #d2dbe0;
            border-radius: 12px;
            font-size: 0.96rem;
            outline: none;
            background: white;
        }}

        input[type=text]:focus,
        input[type=password]:focus,
        select:focus,
        textarea:focus {{
            border-color: var(--brand-1);
            box-shadow: 0 0 0 3px rgba(0, 188, 212, 0.15);
        }}

        textarea {{
            min-height: 110px;
            resize: vertical;
        }}

        button {{
            border: none;
            border-radius: 999px;
            padding: 11px 14px;
            font-size: 0.95rem;
            font-weight: 700;
            color: white;
            cursor: pointer;
            background: linear-gradient(135deg, var(--brand-1), var(--brand-2));
            box-shadow: 0 10px 22px rgba(0, 0, 0, 0.12);
        }}

        button:hover {{
            filter: brightness(1.03);
            transform: translateY(-1px);
        }}

        .btn-outline {{
            background: white;
            color: var(--brand-2);
            border: 1px solid rgba(0, 139, 163, 0.24);
            box-shadow: none;
        }}

        .btn-danger {{
            background: linear-gradient(135deg, var(--danger-1), var(--danger-2));
        }}

        .btn-warning {{
            background: linear-gradient(135deg, var(--warning-1), var(--warning-2));
        }}

        .btn-full {{
            width: 100%;
        }}

        .btn-sm {{
            padding: 7px 12px;
            font-size: 0.82rem;
            box-shadow: none;
        }}

        .msg,
        .erro {{
            border-radius: 12px;
            padding: 11px 12px;
            margin-bottom: 14px;
            font-size: 0.92rem;
        }}

        .msg {{
            background: #eaf7ff;
            border: 1px solid #b6dcff;
        }}

        .erro {{
            background: #ffebee;
            border: 1px solid #ef9a9a;
        }}

        .table-wrapper {{
            overflow-x: auto;
            margin-top: 12px;
        }}

        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 8px;
            font-size: 0.9rem;
        }}

        th {{
            text-align: left;
            padding: 8px 10px;
            color: var(--text-muted);
            background: rgba(23, 48, 66, 0.05);
        }}

        td {{
            padding: 10px;
            background: white;
            border-top: 1px solid var(--line);
            border-bottom: 1px solid var(--line);
        }}

        td:first-child {{
            border-left: 1px solid var(--line);
            border-top-left-radius: 12px;
            border-bottom-left-radius: 12px;
        }}

        td:last-child {{
            border-right: 1px solid var(--line);
            border-top-right-radius: 12px;
            border-bottom-right-radius: 12px;
        }}

        code {{
            display: inline-block;
            max-width: 260px;
            overflow-wrap: break-word;
            padding: 4px 6px;
            border-radius: 8px;
            background: #f3f8fb;
            font-size: 0.8rem;
        }}

        .table-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}

        .rating-container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            margin: 14px 0 10px;
        }}

        .rating-label {{
            color: var(--text-muted);
            font-size: 0.92rem;
        }}

        .stars {{
            display: flex;
            flex-direction: row-reverse;
            justify-content: center;
            gap: 4px;
        }}

        .stars input {{
            display: none;
        }}

        .stars label {{
            font-size: 34px;
            color: #cfd8dc;
            cursor: pointer;
            transition: color 0.12s ease, transform 0.12s ease;
        }}

        .stars label:hover,
        .stars label:hover ~ label {{
            color: #ffd54f;
            transform: translateY(-1px);
        }}

        .stars input:checked ~ label {{
            color: #ffc107;
        }}

        .rating-text {{
            min-height: 18px;
            color: var(--text-muted);
            font-size: 0.88rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 18px;
        }}

        .stat-card {{
            padding: 16px;
            border-radius: 16px;
            background: linear-gradient(180deg, rgba(0, 188, 212, 0.10), rgba(224, 247, 250, 0.65));
            border: 1px solid rgba(0, 139, 163, 0.16);
        }}

        .stat-label {{
            color: var(--text-muted);
            font-size: 0.86rem;
            margin-bottom: 6px;
        }}

        .stat-value {{
            font-size: 1.55rem;
            font-weight: 800;
        }}

        .reviews-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .review-card {{
            padding: 16px;
            border-radius: 16px;
            border: 1px solid var(--line);
            background: #fbfdfe;
        }}

        .review-top {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 8px;
            flex-wrap: wrap;
        }}

        .review-score {{
            font-weight: 800;
        }}

        .review-date {{
            color: var(--text-muted);
            font-size: 0.86rem;
        }}

        .review-comment {{
            white-space: pre-wrap;
            line-height: 1.45;
        }}

        .review-empty {{
            color: var(--text-muted);
            font-style: italic;
        }}
    </style>
    <script>
        function setupRatingText() {{
            var radios = document.querySelectorAll('input[name="score"]');
            var label = document.getElementById('rating-text');
            if (!radios.length || !label) return;

            var texts = {{
                1: "Muito ruim",
                2: "Ruim",
                3: "Ok",
                4: "Muito bom",
                5: "Excelente"
            }};

            radios.forEach(function (radio) {{
                radio.addEventListener("change", function () {{
                    label.textContent = texts[parseInt(this.value, 10)] || "";
                }});
            }});
        }}

        document.addEventListener("DOMContentLoaded", setupRatingText);
    </script>
</head>
<body>
    <header class="topbar">Avaliacao de Entregas</header>
    <main class="page">
        <div class="card">
            {body_html}
        </div>
    </main>
</body>
</html>
"""


def get_client_ip():
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("index"))
            if role and user["role"] != role:
                return "Acesso negado", 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def authenticate(username, password, role):
    user = get_db().execute(
        "SELECT * FROM users WHERE username = ? AND role = ?",
        (username, role),
    ).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def find_driver_by_lookup(lookup):
    return get_db().execute(
        """
        SELECT *
        FROM users
        WHERE role = 'driver'
          AND (LOWER(name) = LOWER(?) OR LOWER(username) = LOWER(?))
        ORDER BY name
        """,
        (lookup, lookup),
    ).fetchall()


@app.before_request
def ensure_db():
    global db_initialized
    if not db_initialized:
        init_db()
        db_initialized = True


@app.route("/")
def index():
    user = current_user()
    if user:
        role_map = {
            "admin": "Administrador",
            "cashier": "Caixa",
            "driver": "Motorista",
        }
        role_text = role_map.get(user["role"], "Usuario")
        buttons = ""
        if user["role"] == "admin":
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/admin/dashboard\'">Painel do Administrador</button></p>'
        elif user["role"] == "cashier":
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/cashier/dashboard\'">Painel da Caixa</button></p>'
        else:
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/driver/painel\'">Painel do Motorista</button></p>'
        buttons += '<p><button class="btn-full btn-outline" onclick="window.location.href=\'/logout\'">Sair</button></p>'

        body = f"""
        <h1>Bem-vindo</h1>
        <p class="subtitle-center">Controle profissional de avaliacao de entregas.</p>
        <p style="text-align:center; margin-bottom: 18px;">
            Logado como: <strong>{esc(user['name'])} ({esc(role_text)})</strong>
        </p>
        {buttons}
        """
    else:
        body = """
        <h1>Avaliacao de Entregas</h1>
        <p class="subtitle-center">
            Administrador cadastra os acessos.
            Caixa imprime as etiquetas.
            Motorista compartilha o QR Code.
        </p>
        <div class="section">
            <button class="btn-full" onclick="window.location.href='/admin/login'">Sou Administrador</button>
        </div>
        <div class="section">
            <button class="btn-full btn-outline" onclick="window.location.href='/cashier/login'">Sou Caixa</button>
        </div>
        <div class="section">
            <button class="btn-full btn-outline" onclick="window.location.href='/driver/login'">Sou Motorista</button>
        </div>
        """

    return render_page("Inicio", body)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    msg = ""
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            "admin",
        )
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("admin_dashboard"))
        msg = "Usuario ou senha invalidos."

    msg_html = f'<div class="erro">{esc(msg)}</div>' if msg else ""
    body = f"""
    <h1>Login do Administrador</h1>
    <p class="subtitle-center">Cadastre motoristas e caixas a partir desta area.</p>
    {msg_html}
    <form method="post" class="section">
        <label>Usuario</label>
        <input type="text" name="username" value="{esc(DEFAULT_ADMIN_USERNAME)}">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """
    return render_page("Login Admin", body)


@app.route("/cashier/login", methods=["GET", "POST"])
def cashier_login():
    msg = ""
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            "cashier",
        )
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("cashier_dashboard"))
        msg = "Usuario ou senha invalidos."

    msg_html = f'<div class="erro">{esc(msg)}</div>' if msg else ""
    body = f"""
    <h1>Login da Caixa</h1>
    <p class="subtitle-center">Entre para gerar e imprimir as etiquetas de avaliacao.</p>
    {msg_html}
    <form method="post" class="section">
        <label>Usuario</label>
        <input type="text" name="username">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """
    return render_page("Login Caixa", body)


@app.route("/driver/login", methods=["GET", "POST"])
def driver_login():
    msg = ""
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            "driver",
        )
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("driver_panel"))
        msg = "Usuario ou senha invalidos."

    msg_html = f'<div class="erro">{esc(msg)}</div>' if msg else ""
    body = f"""
    <h1>Login do Motorista</h1>
    <p class="subtitle-center">Entre para visualizar o QR Code do seu link de avaliacao.</p>
    {msg_html}
    <form method="post" class="section">
        <label>Usuario</label>
        <input type="text" name="username">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """
    return render_page("Login Motorista", body)


@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    drivers = get_db().execute(
        """
        SELECT u.id, u.name, u.username,
               COUNT(r.id) AS total_avaliacoes,
               COALESCE(ROUND(AVG(r.score), 2), 0) AS media
        FROM users u
        LEFT JOIN ratings r ON u.id = r.driver_id
        WHERE u.role = 'driver'
        GROUP BY u.id, u.name, u.username
        ORDER BY u.name;
        """
    ).fetchall()

    msg = request.args.get("msg", "").strip()
    error = request.args.get("error", "").strip()
    msg_html = f'<div class="msg">{esc(msg)}</div>' if msg else ""
    error_html = f'<div class="erro">{esc(error)}</div>' if error else ""

    rows = ""
    base_url = request.url_root.rstrip("/")
    for driver in drivers:
        rate_url = f"{base_url}{url_for('rate_driver', driver_id=driver['id'])}"
        rows += f"""
        <tr>
            <td>{esc(driver['name'])}</td>
            <td>{esc(driver['username'])}</td>
            <td>{driver['media']}</td>
            <td>{driver['total_avaliacoes']}</td>
            <td><code>{esc(rate_url)}</code></td>
            <td>
                <div class="table-actions">
                    <form method="post" action="/admin/reset_ratings/{driver['id']}">
                        <button class="btn-warning btn-sm" onclick="return confirm('Zerar as avaliacoes deste motorista?');">Zerar</button>
                    </form>
                    <form method="post" action="/admin/delete_driver/{driver['id']}">
                        <button class="btn-danger btn-sm" onclick="return confirm('Excluir este motorista e as avaliacoes dele?');">Excluir</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    body = f"""
    <h1>Painel do Administrador</h1>
    <p class="subtitle-center">Administrador cadastra logins e senhas para motoristas e caixas.</p>
    {msg_html}
    {error_html}

    <div class="section">
        <div class="section-title">Equipe da caixa</div>
        <div class="section-subtitle">A caixa entra em uma area separada e usa apenas a impressao.</div>
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/cashiers'">Gerenciar caixas</button>
    </div>

    <div class="section">
        <div class="section-title">Cadastrar motorista</div>
        <div class="section-subtitle">Crie o login e a senha do motorista.</div>
        <form method="post" action="/admin/create_driver">
            <label>Nome do motorista</label>
            <input type="text" name="name" required>
            <label>Usuario para login do motorista</label>
            <input type="text" name="username" required>
            <label>Senha para login do motorista</label>
            <input type="password" name="password" required>
            <button type="submit">Cadastrar motorista</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Motoristas cadastrados</div>
        <div class="section-subtitle">Acompanhe as notas e o link publico de avaliacao.</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Nome</th>
                        <th>Usuario</th>
                        <th>Media</th>
                        <th>Avaliacoes</th>
                        <th>Link</th>
                        <th>Acoes</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <form method="post" action="/admin/reset_all_ratings">
            <button class="btn-danger btn-full" onclick="return confirm('Zerar todas as avaliacoes do sistema?');">Zerar todas as avaliacoes</button>
        </form>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar ao inicio</button>
    </div>
    """
    return render_page("Painel Admin", body)


@app.route("/admin/cashiers")
@login_required(role="admin")
def admin_cashiers():
    cashiers = get_db().execute(
        """
        SELECT id, name, username
        FROM users
        WHERE role = 'cashier'
        ORDER BY name;
        """
    ).fetchall()

    msg = request.args.get("msg", "").strip()
    error = request.args.get("error", "").strip()
    msg_html = f'<div class="msg">{esc(msg)}</div>' if msg else ""
    error_html = f'<div class="erro">{esc(error)}</div>' if error else ""

    rows = ""
    for cashier in cashiers:
        rows += f"""
        <tr>
            <td>{esc(cashier['name'])}</td>
            <td>{esc(cashier['username'])}</td>
            <td>
                <div class="table-actions">
                    <form method="post" action="/admin/delete_cashier/{cashier['id']}">
                        <button class="btn-danger btn-sm" onclick="return confirm('Excluir este caixa?');">Excluir</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    body = f"""
    <h1>Gestao de Caixas</h1>
    <p class="subtitle-center">O administrador cria os acessos da equipe do caixa.</p>
    {msg_html}
    {error_html}

    <div class="section">
        <div class="section-title">Cadastrar caixa</div>
        <div class="section-subtitle">Esse login acessa apenas a area de impressao.</div>
        <form method="post" action="/admin/create_cashier">
            <label>Nome do caixa</label>
            <input type="text" name="name" required>
            <label>Usuario para login do caixa</label>
            <input type="text" name="username" required>
            <label>Senha para login do caixa</label>
            <input type="password" name="password" required>
            <button type="submit">Cadastrar caixa</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Caixas cadastrados</div>
        <div class="section-subtitle">Esses acessos usam o mesmo modo operacional separado do motorista.</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Nome</th>
                        <th>Usuario</th>
                        <th>Acoes</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/dashboard'">Voltar para o admin</button>
    </div>
    """
    return render_page("Gestao de Caixas", body)


@app.route("/admin/create_driver", methods=["POST"])
@login_required(role="admin")
def create_driver():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not name or not username or not password:
        return "Dados invalidos", 400

    try:
        get_db().execute(
            """
            INSERT INTO users (username, name, role, password_hash)
            VALUES (?, ?, 'driver', ?)
            """,
            (username, name, generate_password_hash(password)),
        )
        get_db().commit()
    except sqlite3.IntegrityError:
        return redirect(url_for("admin_dashboard", error="Ja existe um usuario com esse login."))

    return redirect(url_for("admin_dashboard", msg="Motorista cadastrado com sucesso."))


@app.route("/admin/create_cashier", methods=["POST"])
@login_required(role="admin")
def create_cashier():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not name or not username or not password:
        return "Dados invalidos", 400

    try:
        get_db().execute(
            """
            INSERT INTO users (username, name, role, password_hash)
            VALUES (?, ?, 'cashier', ?)
            """,
            (username, name, generate_password_hash(password)),
        )
        get_db().commit()
    except sqlite3.IntegrityError:
        return redirect(url_for("admin_cashiers", error="Ja existe um usuario com esse login."))

    return redirect(url_for("admin_cashiers", msg="Caixa cadastrado com sucesso."))


@app.route("/admin/reset_ratings/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def reset_ratings(driver_id):
    db = get_db()
    db.execute("DELETE FROM ratings WHERE driver_id = ?", (driver_id,))
    db.commit()
    return redirect(url_for("admin_dashboard", msg="Avaliacoes do motorista zeradas."))


@app.route("/admin/reset_all_ratings", methods=["POST"])
@login_required(role="admin")
def reset_all_ratings():
    db = get_db()
    db.execute("DELETE FROM ratings")
    db.commit()
    return redirect(url_for("admin_dashboard", msg="Todas as avaliacoes foram zeradas."))


@app.route("/admin/delete_driver/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def delete_driver(driver_id):
    db = get_db()
    db.execute("DELETE FROM ratings WHERE driver_id = ?", (driver_id,))
    db.execute("DELETE FROM users WHERE id = ? AND role = 'driver'", (driver_id,))
    db.commit()
    return redirect(url_for("admin_dashboard", msg="Motorista removido com sucesso."))


@app.route("/admin/delete_cashier/<int:cashier_id>", methods=["POST"])
@login_required(role="admin")
def delete_cashier(cashier_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ? AND role = 'cashier'", (cashier_id,))
    db.commit()
    return redirect(url_for("admin_cashiers", msg="Caixa removido com sucesso."))


@app.route("/cashier/dashboard")
@login_required(role="cashier")
def cashier_dashboard():
    user = current_user()
    drivers = get_db().execute(
        """
        SELECT id, name, username
        FROM users
        WHERE role = 'driver'
        ORDER BY name;
        """
    ).fetchall()
    driver_options = "".join(
        f'<option value="{driver["id"]}">{esc(driver["name"])} ({esc(driver["username"])})</option>'
        for driver in drivers
    )
    select_html = (
        f"""
        <select name="driver_id" required>
            <option value="">Selecione um motorista</option>
            {driver_options}
        </select>
        """
        if drivers
        else """
        <select name="driver_id" disabled>
            <option value="">Nenhum motorista cadastrado</option>
        </select>
        """
    )
    submit_attrs = "" if drivers else ' disabled'

    msg = request.args.get("msg", "").strip()
    error = request.args.get("error", "").strip()
    msg_html = f'<div class="msg">{esc(msg)}</div>' if msg else ""
    error_html = f'<div class="erro">{esc(error)}</div>' if error else ""

    body = f"""
    <h1>Painel da Caixa</h1>
    <p class="subtitle-center">
        Login ativo: <strong>{esc(user['name'])}</strong>.
        Digite o motorista cadastrado e gere a etiqueta.
    </p>
    {msg_html}
    {error_html}

    <div class="section">
        <div class="section-title">Imprimir etiqueta</div>
        <div class="section-subtitle">Mesmo modo operacional separado do motorista: a caixa entra com login proprio e escolhe o motorista cadastrado para imprimir.</div>
        <form method="get" action="/cashier/print_label" target="_blank">
            <label>Motorista cadastrado</label>
            {select_html}
            <button type="submit"{submit_attrs}>Gerar etiqueta com QR Code</button>
        </form>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """
    return render_page("Painel da Caixa", body)


@app.route("/driver/painel")
@login_required(role="driver")
def driver_panel():
    user = current_user()
    summary = get_db().execute(
        """
        SELECT COUNT(*) AS total_avaliacoes,
               COALESCE(ROUND(AVG(score), 2), 0) AS media
        FROM ratings
        WHERE driver_id = ?
        """,
        (user["id"],),
    ).fetchone()
    ratings = get_db().execute(
        """
        SELECT score, comment, created_at
        FROM ratings
        WHERE driver_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (user["id"],),
    ).fetchall()

    reviews_html = ""
    for rating in ratings:
        comment = (rating["comment"] or "").strip()
        comment_html = (
            f'<div class="review-comment">{esc(comment)}</div>'
            if comment
            else '<div class="review-empty">Cliente nao deixou comentario nesta avaliacao.</div>'
        )
        reviews_html += f"""
        <div class="review-card">
            <div class="review-top">
                <div class="review-score">Nota: {rating['score']}/5</div>
                <div class="review-date">{esc(format_rating_date(rating['created_at']))}</div>
            </div>
            {comment_html}
        </div>
        """

    if not reviews_html:
        reviews_html = """
        <div class="review-card">
            <div class="review-empty">Ainda nao ha avaliacoes para este motorista.</div>
        </div>
        """

    body = f"""
    <h1>Painel do Motorista</h1>
    <p class="subtitle-center">Aqui voce acompanha apenas as suas avaliacoes e os comentarios enviados pelos clientes.</p>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Sua media atual</div>
            <div class="stat-value">{summary['media']}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total de avaliacoes</div>
            <div class="stat-value">{summary['total_avaliacoes']}</div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Avaliacoes e comentarios recebidos</div>
        <div class="section-subtitle">Cada linha mostra a nota individual e o comentario deixado pelo cliente.</div>
        <div class="reviews-list">
            {reviews_html}
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """
    return render_page("Painel do Motorista", body)


@app.route("/avaliar/<int:driver_id>", methods=["GET", "POST"])
def rate_driver(driver_id):
    driver = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND role = 'driver'",
        (driver_id,),
    ).fetchone()

    if not driver:
        return "Motorista nao encontrado", 404

    msg = ""
    ip = get_client_ip()

    if request.method == "POST":
        row = get_db().execute(
            """
            SELECT COUNT(*) AS total
            FROM ratings
            WHERE ip = ?
              AND created_at >= datetime('now', '-7 days')
            """,
            (ip,),
        ).fetchone()

        if row["total"] > 0:
            msg = "Voce ja fez uma avaliacao recentemente. E permitido apenas 1 envio por semana neste dispositivo."
        else:
            try:
                score = int(request.form.get("score", "0"))
            except ValueError:
                score = 0

            if score < 1 or score > 5:
                msg = "Selecione uma nota entre 1 e 5 estrelas."
            else:
                comment = request.form.get("comment", "").strip()
                if len(comment) > 500:
                    comment = comment[:500]
                db = get_db()
                db.execute(
                    "INSERT INTO ratings (driver_id, score, comment, ip) VALUES (?, ?, ?, ?)",
                    (driver_id, score, comment, ip),
                )
                db.commit()

                body = f"""
                <h1>Obrigado pela sua avaliacao</h1>
                <p class="subtitle-center">Sua opiniao ajuda a melhorar a qualidade das entregas.</p>
                <p style="text-align:center; margin-top: 14px;">
                    Motorista avaliado: <strong>{esc(driver['name'])}</strong>
                </p>
                """
                return render_page("Obrigado", body)

    msg_html = f'<div class="erro">{esc(msg)}</div>' if msg else ""
    body = f"""
    <h1>Avalie sua entrega</h1>
    <p class="subtitle-center">
        Motorista: <strong>{esc(driver['name'])}</strong><br>
        Como voce avalia o atendimento e o tempo de entrega?
    </p>
    {msg_html}
    <form method="post">
        <div class="rating-container">
            <div class="rating-label">Toque nas estrelas para escolher a nota:</div>
            <div class="stars">
                <input type="radio" id="score-5" name="score" value="5">
                <label for="score-5">&#9733;</label>
                <input type="radio" id="score-4" name="score" value="4">
                <label for="score-4">&#9733;</label>
                <input type="radio" id="score-3" name="score" value="3">
                <label for="score-3">&#9733;</label>
                <input type="radio" id="score-2" name="score" value="2">
                <label for="score-2">&#9733;</label>
                <input type="radio" id="score-1" name="score" value="1">
                <label for="score-1">&#9733;</label>
            </div>
            <div id="rating-text" class="rating-text"></div>
        </div>
        <label>Comentario sobre a entrega ou atendimento</label>
        <textarea name="comment" maxlength="500" placeholder="Escreva aqui o que achou do atendimento do motorista e da entrega."></textarea>
        <button type="submit" class="btn-full">Enviar avaliacao</button>
    </form>
    """
    return render_page("Avaliar", body)


@app.route("/cashier/print_label")
@login_required(role="cashier")
def print_label():
    try:
        driver_id = int(request.args.get("driver_id", "0"))
    except ValueError:
        driver_id = 0

    if driver_id <= 0:
        return redirect(url_for("cashier_dashboard", error="Selecione um motorista cadastrado para imprimir a etiqueta."))

    driver = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND role = 'driver'",
        (driver_id,),
    ).fetchone()
    if not driver:
        return redirect(url_for("cashier_dashboard", error="Motorista nao encontrado. Confira a lista cadastrada."))

    rate_url = request.url_root.rstrip("/") + url_for("rate_driver", driver_id=driver["id"])
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=320x320&data={quote(rate_url, safe='')}"

    body = f"""
    <style>
        .label-shell {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
        }}

        .label-card {{
            width: 100%;
            max-width: 420px;
            padding: 18px;
            text-align: center;
            border-radius: 18px;
            background: white;
            border: 2px dashed rgba(23, 48, 66, 0.18);
            box-shadow: 0 14px 34px rgba(0, 0, 0, 0.10);
        }}

        .label-eyebrow {{
            margin-bottom: 8px;
            color: #008ba3;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .label-title {{
            margin-bottom: 8px;
            font-size: 1.35rem;
            font-weight: 800;
            line-height: 1.2;
        }}

        .label-driver {{
            margin-bottom: 14px;
            font-size: 1rem;
        }}

        .label-qr img {{
            width: 220px;
            height: 220px;
            object-fit: contain;
        }}

        .label-footer {{
            margin-top: 12px;
            color: #526674;
            font-size: 0.92rem;
        }}

        .label-actions {{
            display: flex;
            gap: 10px;
            width: 100%;
            max-width: 420px;
        }}

        .label-actions button {{
            flex: 1;
        }}

        @media print {{
            body {{
                background: white !important;
            }}

            .topbar,
            .label-actions {{
                display: none !important;
            }}

            .page {{
                padding: 0 !important;
                min-height: auto !important;
            }}

            .card {{
                max-width: none !important;
                padding: 0 !important;
                border-radius: 0 !important;
                box-shadow: none !important;
            }}

            .label-card {{
                width: 90mm;
                min-height: 60mm;
                margin: 0 auto;
                border: 1px solid #d6dde2;
                box-shadow: none;
                page-break-inside: avoid;
            }}
        }}
    </style>

    <div class="label-shell">
        <div class="label-card">
            <div class="label-eyebrow">Etiqueta de avaliacao</div>
            <div class="label-title">Avalie nosso entregador e nossa entrega</div>
            <div class="label-driver">Motorista: <strong>{esc(driver['name'])}</strong></div>
            <div class="label-qr">
                <img src="{esc(qr_url)}" alt="QR Code para avaliar a entrega">
            </div>
            <div class="label-footer">
                Aponte a camera do celular para o QR Code e deixe sua nota.
            </div>
        </div>

        <div class="label-actions">
            <button type="button" onclick="window.print()">Imprimir novamente</button>
            <button type="button" class="btn-outline" onclick="window.close()">Fechar</button>
        </div>
    </div>

    <script>
        window.addEventListener("load", function () {{
            setTimeout(function () {{
                window.print();
            }}, 250);
        }});
    </script>
    """
    return render_page("Imprimir Etiqueta", body)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
