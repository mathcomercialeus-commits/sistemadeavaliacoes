import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps
from html import escape
from urllib.parse import quote

from flask import Flask, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-essa-chave-no-deploy")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
DATABASE = os.environ.get("DATABASE_PATH", "avaliacao_entregadores.db")
DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "FARMALIMA")
DEFAULT_ADMIN_NAME = os.environ.get("ADMIN_NAME", "Administrador")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Farma@lima3535")
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "1") == "1"
LABEL_TOKEN_EXPIRY_HOURS = int(os.environ.get("LABEL_TOKEN_EXPIRY_HOURS", "48"))

db_initialized = False


class PostgresConnection:
    def __init__(self, url):
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary nao instalado. Rode pip install -r requirements.txt.")
        self.conn = psycopg2.connect(url, cursor_factory=RealDictCursor)

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(to_postgres_sql(sql), params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def to_postgres_sql(sql):
    return (
        sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        .replace("?", "%s")
        .replace("datetime('now', '-7 days')", "NOW() - INTERVAL '7 days'")
        .replace("ORDER BY datetime(created_at) DESC", "ORDER BY created_at DESC")
    )


def connect_db():
    if USE_POSTGRES:
        return PostgresConnection(DATABASE_URL)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
DB_OPERATIONAL_ERRORS = (sqlite3.OperationalError,)
if psycopg2 is not None:
    DB_INTEGRITY_ERRORS = DB_INTEGRITY_ERRORS + (psycopg2.IntegrityError,)
    DB_OPERATIONAL_ERRORS = DB_OPERATIONAL_ERRORS + (psycopg2.Error,)


def init_db():
    conn = connect_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'cashier', 'driver', 'manager')),
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
        """
        CREATE TABLE IF NOT EXISTS rating_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            driver_id INTEGER NOT NULL,
            cashier_id INTEGER,
            invoice_number TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users(id),
            FOREIGN KEY (cashier_id) REFERENCES users(id)
        );
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ratings_driver_id ON ratings(driver_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_ip ON ratings(ip);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rating_tokens_token ON rating_tokens(token);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rating_tokens_driver_id ON rating_tokens(driver_id);")

    if USE_POSTGRES:
        conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;")
        conn.execute(
            """
            ALTER TABLE users
            ADD CONSTRAINT users_role_check
            CHECK (role IN ('admin', 'cashier', 'driver', 'manager'));
            """
        )
        conn.execute("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS ip TEXT;")
        conn.execute("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS comment TEXT;")
        conn.execute("ALTER TABLE rating_tokens ADD COLUMN IF NOT EXISTS invoice_number TEXT NOT NULL DEFAULT '';")
    else:
        try:
            conn.execute("ALTER TABLE ratings ADD COLUMN ip TEXT;")
        except DB_OPERATIONAL_ERRORS:
            pass

        try:
            conn.execute("ALTER TABLE ratings ADD COLUMN comment TEXT;")
        except DB_OPERATIONAL_ERRORS:
            pass

        try:
            conn.execute("ALTER TABLE rating_tokens ADD COLUMN invoice_number TEXT NOT NULL DEFAULT '';")
        except DB_OPERATIONAL_ERRORS:
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
        g.db = connect_db()
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
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M")
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
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
            if role:
                allowed_roles = role if isinstance(role, (tuple, list, set)) else (role,)
                if user["role"] not in allowed_roles:
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


def get_driver_score_rows():
    return get_db().execute(
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


def date_filter_sql(column_name, start_date, end_date):
    clauses = []
    params = []
    if start_date:
        clauses.append(f"DATE({column_name}) >= ?")
        params.append(start_date)
    if end_date:
        clauses.append(f"DATE({column_name}) <= ?")
        params.append(end_date)
    return clauses, params


def get_print_log_rows(limit=200, start_date="", end_date=""):
    clauses, params = date_filter_sql("rt.created_at", start_date, end_date)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    return get_db().execute(
        """
        SELECT rt.id,
               rt.invoice_number,
               rt.cashier_id,
               rt.created_at,
               rt.used_at,
               d.name AS driver_name,
               d.username AS driver_username,
               c.name AS cashier_name,
               c.username AS cashier_username
        FROM rating_tokens rt
        JOIN users d ON d.id = rt.driver_id
        LEFT JOIN users c ON c.id = rt.cashier_id
        {where_sql}
        ORDER BY rt.created_at DESC, rt.id DESC
        LIMIT ?
        """.format(where_sql=where_sql),
        tuple(params),
    ).fetchall()


def get_cashier_for_prints(cashier_id):
    return get_db().execute(
        """
        SELECT id, name, username
        FROM users
        WHERE id = ? AND role = 'cashier'
        """,
        (cashier_id,),
    ).fetchone()


def get_cashier_print_rows(cashier_id, start_date="", end_date="", limit=500):
    clauses, params = date_filter_sql("rt.created_at", start_date, end_date)
    clauses.append("rt.cashier_id = ?")
    params.append(cashier_id)
    params.append(limit)
    return get_db().execute(
        """
        SELECT rt.id,
               rt.invoice_number,
               rt.created_at,
               rt.used_at,
               d.name AS driver_name,
               d.username AS driver_username
        FROM rating_tokens rt
        JOIN users d ON d.id = rt.driver_id
        WHERE {where_sql}
        ORDER BY rt.created_at DESC, rt.id DESC
        LIMIT ?
        """.format(where_sql=" AND ".join(clauses)),
        tuple(params),
    ).fetchall()


def get_driver_for_comments(driver_id):
    return get_db().execute(
        """
        SELECT id, name, username
        FROM users
        WHERE id = ? AND role = 'driver'
        """,
        (driver_id,),
    ).fetchone()


def get_driver_comment_rows(driver_id):
    return get_db().execute(
        """
        SELECT score, comment, created_at
        FROM ratings
        WHERE driver_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (driver_id,),
    ).fetchall()


def render_manager_dashboard():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    drivers = get_driver_score_rows()
    logs = get_print_log_rows(start_date=start_date, end_date=end_date)

    driver_rows = ""
    for driver in drivers:
        driver_rows += f"""
        <tr>
            <td>{esc(driver['name'])}</td>
            <td>{esc(driver['username'])}</td>
            <td>{driver['media']}</td>
            <td>{driver['total_avaliacoes']}</td>
            <td>
                <button class="btn-sm" type="button" onclick="window.location.href='/manager/driver/{driver['id']}/comments'">Ver comentarios</button>
            </td>
        </tr>
        """

    log_rows = ""
    for item in logs:
        used_text = "Usado" if item["used_at"] else "Ainda nao usado"
        cashier_name = item["cashier_name"] or "Caixa removido"
        cashier_username = item["cashier_username"] or "-"
        cashier_cell = f"{esc(cashier_name)}<br><small>{esc(cashier_username)}</small>"
        if item["cashier_id"]:
            cashier_cell += f'<br><button class="btn-sm" type="button" onclick="window.location.href=\'/manager/cashier/{item["cashier_id"]}/prints\'">Ver notas impressas</button>'
        log_rows += f"""
        <tr>
            <td>{esc(format_rating_date(item['created_at']))}</td>
            <td>{cashier_cell}</td>
            <td>{esc(item['invoice_number'])}</td>
            <td>{esc(item['driver_name'])}<br><small>{esc(item['driver_username'])}</small></td>
            <td>{used_text}</td>
        </tr>
        """

    if not driver_rows:
        driver_rows = '<tr><td colspan="5">Nenhum motorista cadastrado.</td></tr>'
    if not log_rows:
        log_rows = '<tr><td colspan="5">Nenhuma etiqueta impressa ainda.</td></tr>'

    body = f"""
    <h1>Painel do Gestor</h1>
    <p class="subtitle-center">Consulta de notas dos motoristas e log das impressoes das caixas.</p>

    <div class="section">
        <div class="section-title">Notas dos motoristas</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Motorista</th>
                        <th>Usuario</th>
                        <th>Media</th>
                        <th>Avaliacoes</th>
                        <th>Comentarios</th>
                    </tr>
                </thead>
                <tbody>{driver_rows}</tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Log de impressoes das caixas</div>
        <div class="section-subtitle">Mostra caixa, numero da nota fiscal e motorista de cada etiqueta emitida.</div>
        <form method="get" action="/manager/dashboard" class="section">
            <label>Data inicial</label>
            <input type="date" name="start_date" value="{esc(start_date)}">
            <label>Data final</label>
            <input type="date" name="end_date" value="{esc(end_date)}">
            <button type="submit" class="btn-full">Filtrar log</button>
        </form>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Data</th>
                        <th>Caixa</th>
                        <th>Nota fiscal</th>
                        <th>Motorista</th>
                        <th>Status do QR</th>
                    </tr>
                </thead>
                <tbody>{log_rows}</tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """
    return render_page("Painel Gestor", body)


def render_manager_driver_comments(driver_id):
    driver = get_driver_for_comments(driver_id)
    if not driver:
        return "Motorista nao encontrado", 404

    ratings = get_driver_comment_rows(driver_id)
    comments_html = ""
    for rating in ratings:
        comment = (rating["comment"] or "").strip()
        comment_html = (
            f'<div class="review-comment">{esc(comment)}</div>'
            if comment
            else '<div class="review-empty">Cliente nao deixou comentario nesta avaliacao.</div>'
        )
        comments_html += f"""
        <div class="review-card">
            <div class="review-top">
                <div class="review-score">Nota: {rating['score']}/5</div>
                <div class="review-date">{esc(format_rating_date(rating['created_at']))}</div>
            </div>
            {comment_html}
        </div>
        """

    if not comments_html:
        comments_html = """
        <div class="review-card">
            <div class="review-empty">Ainda nao ha avaliacoes para este motorista.</div>
        </div>
        """

    body = f"""
    <h1>Comentarios do Motorista</h1>
    <p class="subtitle-center">
        Motorista: <strong>{esc(driver['name'])}</strong><br>
        Usuario: {esc(driver['username'])}
    </p>

    <div class="section">
        <div class="section-title">Avaliacoes individuais</div>
        <div class="section-subtitle">Cada item mostra a nota e o comentario deixado pelo cliente.</div>
        <div class="reviews-list">
            {comments_html}
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/manager/dashboard'">Voltar para o gestor</button>
    </div>
    """
    return render_page("Comentarios do Motorista", body)


def render_manager_cashier_prints(cashier_id):
    cashier = get_cashier_for_prints(cashier_id)
    if not cashier:
        return "Caixa nao encontrado", 404

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    prints = get_cashier_print_rows(cashier_id, start_date=start_date, end_date=end_date)

    rows = ""
    for item in prints:
        used_text = "Usado" if item["used_at"] else "Ainda nao usado"
        rows += f"""
        <tr>
            <td>{esc(format_rating_date(item['created_at']))}</td>
            <td>{esc(item['invoice_number'])}</td>
            <td>{esc(item['driver_name'])}<br><small>{esc(item['driver_username'])}</small></td>
            <td>{used_text}</td>
        </tr>
        """

    if not rows:
        rows = '<tr><td colspan="4">Nenhuma nota impressa neste periodo.</td></tr>'

    body = f"""
    <h1>Notas Impressas</h1>
    <p class="subtitle-center">
        Caixa: <strong>{esc(cashier['name'])}</strong><br>
        Usuario: {esc(cashier['username'])}
    </p>

    <div class="section">
        <div class="section-title">Filtrar por data</div>
        <form method="get" action="/manager/cashier/{cashier['id']}/prints">
            <label>Data inicial</label>
            <input type="date" name="start_date" value="{esc(start_date)}">
            <label>Data final</label>
            <input type="date" name="end_date" value="{esc(end_date)}">
            <button type="submit" class="btn-full">Filtrar notas impressas</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Notas fiscais impressas</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Data</th>
                        <th>Nota fiscal</th>
                        <th>Motorista</th>
                        <th>Status do QR</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/manager/dashboard'">Voltar para o gestor</button>
    </div>
    """
    return render_page("Notas Impressas", body)


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
            "manager": "Gestor",
        }
        role_text = role_map.get(user["role"], "Usuario")
        buttons = ""
        if user["role"] == "admin":
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/admin/dashboard\'">Painel do Administrador</button></p>'
        elif user["role"] == "cashier":
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/cashier/dashboard\'">Painel da Caixa</button></p>'
        elif user["role"] == "driver":
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/driver/painel\'">Painel do Motorista</button></p>'
        else:
            buttons += '<p><button class="btn-full" onclick="window.location.href=\'/manager/dashboard\'">Painel do Gestor</button></p>'
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
        <div class="section">
            <button class="btn-full btn-outline" onclick="window.location.href='/manager/login'">Sou Gestor</button>
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


@app.route("/manager/login", methods=["GET", "POST"])
def manager_login():
    msg = ""
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
            "manager",
        )
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("manager_dashboard"))
        msg = "Usuario ou senha invalidos."

    msg_html = f'<div class="erro">{esc(msg)}</div>' if msg else ""
    body = f"""
    <h1>Login do Gestor</h1>
    <p class="subtitle-center">Entre para consultar notas dos motoristas e o log das impressoes.</p>
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
    return render_page("Login Gestor", body)


@app.route("/manager/dashboard")
@login_required(role="manager")
def manager_dashboard():
    return render_manager_dashboard()


@app.route("/manager/driver/<int:driver_id>/comments")
@login_required(role="manager")
def manager_driver_comments(driver_id):
    return render_manager_driver_comments(driver_id)


@app.route("/manager/cashier/<int:cashier_id>/prints")
@login_required(role="manager")
def manager_cashier_prints(cashier_id):
    return render_manager_cashier_prints(cashier_id)


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
    for driver in drivers:
        rows += f"""
        <tr>
            <td>{esc(driver['name'])}</td>
            <td>{esc(driver['username'])}</td>
            <td>{driver['media']}</td>
            <td>{driver['total_avaliacoes']}</td>
            <td>QR unico gerado pela caixa</td>
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
        <div class="section-title">Gestores</div>
        <div class="section-subtitle">Gestores acessam apenas notas dos motoristas e log de impressoes.</div>
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/managers'">Gerenciar gestores</button>
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
        <div class="section-subtitle">Acompanhe as notas. As avaliacoes entram por QR unico gerado pela caixa.</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Nome</th>
                        <th>Usuario</th>
                        <th>Media</th>
                        <th>Avaliacoes</th>
                        <th>Etiqueta</th>
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


@app.route("/admin/managers")
@login_required(role="admin")
def admin_managers():
    managers = get_db().execute(
        """
        SELECT id, name, username
        FROM users
        WHERE role = 'manager'
        ORDER BY name;
        """
    ).fetchall()

    msg = request.args.get("msg", "").strip()
    error = request.args.get("error", "").strip()
    msg_html = f'<div class="msg">{esc(msg)}</div>' if msg else ""
    error_html = f'<div class="erro">{esc(error)}</div>' if error else ""

    rows = ""
    for manager in managers:
        rows += f"""
        <tr>
            <td>{esc(manager['name'])}</td>
            <td>{esc(manager['username'])}</td>
            <td>
                <div class="table-actions">
                    <form method="post" action="/admin/delete_manager/{manager['id']}">
                        <button class="btn-danger btn-sm" onclick="return confirm('Excluir este gestor?');">Excluir</button>
                    </form>
                </div>
            </td>
        </tr>
        """

    body = f"""
    <h1>Gestao de Gestores</h1>
    <p class="subtitle-center">O gestor acessa somente notas dos motoristas e log de impressoes.</p>
    {msg_html}
    {error_html}

    <div class="section">
        <div class="section-title">Cadastrar gestor</div>
        <div class="section-subtitle">Esse login nao altera cadastros nem avaliacoes.</div>
        <form method="post" action="/admin/create_manager">
            <label>Nome do gestor</label>
            <input type="text" name="name" required>
            <label>Usuario para login do gestor</label>
            <input type="text" name="username" required>
            <label>Senha para login do gestor</label>
            <input type="password" name="password" required>
            <button type="submit">Cadastrar gestor</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Gestores cadastrados</div>
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
                    {rows if rows else '<tr><td colspan="3">Nenhum gestor cadastrado.</td></tr>'}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/dashboard'">Voltar para o admin</button>
    </div>
    """
    return render_page("Gestao de Gestores", body)


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
    except DB_INTEGRITY_ERRORS:
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
    except DB_INTEGRITY_ERRORS:
        return redirect(url_for("admin_cashiers", error="Ja existe um usuario com esse login."))

    return redirect(url_for("admin_cashiers", msg="Caixa cadastrado com sucesso."))


@app.route("/admin/create_manager", methods=["POST"])
@login_required(role="admin")
def create_manager():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not name or not username or not password:
        return "Dados invalidos", 400

    try:
        get_db().execute(
            """
            INSERT INTO users (username, name, role, password_hash)
            VALUES (?, ?, 'manager', ?)
            """,
            (username, name, generate_password_hash(password)),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        return redirect(url_for("admin_managers", error="Ja existe um usuario com esse login."))

    return redirect(url_for("admin_managers", msg="Gestor cadastrado com sucesso."))


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
    db.execute("DELETE FROM rating_tokens WHERE driver_id = ?", (driver_id,))
    db.execute("DELETE FROM users WHERE id = ? AND role = 'driver'", (driver_id,))
    db.commit()
    return redirect(url_for("admin_dashboard", msg="Motorista removido com sucesso."))


@app.route("/admin/delete_cashier/<int:cashier_id>", methods=["POST"])
@login_required(role="admin")
def delete_cashier(cashier_id):
    db = get_db()
    db.execute("UPDATE rating_tokens SET cashier_id = NULL WHERE cashier_id = ?", (cashier_id,))
    db.execute("DELETE FROM users WHERE id = ? AND role = 'cashier'", (cashier_id,))
    db.commit()
    return redirect(url_for("admin_cashiers", msg="Caixa removido com sucesso."))


@app.route("/admin/delete_manager/<int:manager_id>", methods=["POST"])
@login_required(role="admin")
def delete_manager(manager_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ? AND role = 'manager'", (manager_id,))
    db.commit()
    return redirect(url_for("admin_managers", msg="Gestor removido com sucesso."))


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
        Escolha o motorista, informe a nota fiscal e gere a etiqueta.
    </p>
    {msg_html}
    {error_html}

    <div class="section">
        <div class="section-title">Imprimir etiqueta</div>
        <div class="section-subtitle">A caixa informa a nota fiscal para registrar o log antes da impressao.</div>
        <form method="post" action="/cashier/print_label" target="_blank">
            <label>Numero da nota fiscal</label>
            <input type="text" name="invoice_number" maxlength="80" required>
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
    body = """
    <h1>Use a etiqueta atual</h1>
    <p class="subtitle-center">
        As avaliacoes agora usam um QR Code unico por impressao.
    </p>
    <p style="text-align:center;">
        Solicite uma nova etiqueta no caixa para registrar a avaliacao.
    </p>
    """
    return render_page("Nova etiqueta necessaria", body), 410


@app.route("/avaliar/token/<token>", methods=["GET", "POST"])
def rate_token(token):
    token_row = get_db().execute(
        """
        SELECT rt.id,
               rt.driver_id,
               CASE WHEN rt.used_at IS NOT NULL THEN 1 ELSE 0 END AS is_used,
               CASE WHEN rt.expires_at < CURRENT_TIMESTAMP THEN 1 ELSE 0 END AS is_expired,
               u.name AS driver_name
        FROM rating_tokens rt
        JOIN users u ON u.id = rt.driver_id AND u.role = 'driver'
        WHERE rt.token = ?
        """,
        (token,),
    ).fetchone()

    if not token_row:
        body = """
        <h1>QR Code invalido</h1>
        <p class="subtitle-center">Esta etiqueta nao foi encontrada. Solicite uma nova impressao no caixa.</p>
        """
        return render_page("QR invalido", body), 404

    if token_row["is_used"]:
        body = f"""
        <h1>QR Code ja utilizado</h1>
        <p class="subtitle-center">
            A etiqueta do motorista <strong>{esc(token_row['driver_name'])}</strong> ja foi usada em uma avaliacao.
        </p>
        <p style="text-align:center;">Se precisar, peca uma nova etiqueta no caixa.</p>
        """
        return render_page("QR ja utilizado", body), 410

    if token_row["is_expired"]:
        body = f"""
        <h1>QR Code expirado</h1>
        <p class="subtitle-center">
            A etiqueta do motorista <strong>{esc(token_row['driver_name'])}</strong> venceu.
        </p>
        <p style="text-align:center;">Solicite uma nova etiqueta no caixa.</p>
        """
        return render_page("QR expirado", body), 410

    driver = {"id": token_row["driver_id"], "name": token_row["driver_name"]}

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
                    (driver["id"], score, comment, ip),
                )
                db.execute(
                    "UPDATE rating_tokens SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (token_row["id"],),
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

@app.route("/cashier/print_label", methods=["GET", "POST"])
@login_required(role="cashier")
def print_label():
    cashier = current_user()
    try:
        driver_id = int(request.values.get("driver_id", "0"))
    except ValueError:
        driver_id = 0
    invoice_number = request.values.get("invoice_number", "").strip()

    if driver_id <= 0:
        return redirect(url_for("cashier_dashboard", error="Selecione um motorista cadastrado para imprimir a etiqueta."))
    if not invoice_number:
        return redirect(url_for("cashier_dashboard", error="Informe o numero da nota fiscal para imprimir a etiqueta."))
    if len(invoice_number) > 80:
        invoice_number = invoice_number[:80]

    driver = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND role = 'driver'",
        (driver_id,),
    ).fetchone()
    if not driver:
        return redirect(url_for("cashier_dashboard", error="Motorista nao encontrado. Confira a lista cadastrada."))

    token = uuid.uuid4().hex
    expires_at = datetime.utcnow() + timedelta(hours=LABEL_TOKEN_EXPIRY_HOURS)
    db = get_db()
    db.execute(
        """
        INSERT INTO rating_tokens (token, driver_id, cashier_id, invoice_number, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (token, driver["id"], cashier["id"] if cashier else None, invoice_number, expires_at),
    )
    db.commit()

    rate_url = request.url_root.rstrip("/") + url_for("rate_token", token=token)
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
            <div class="label-driver">Nota fiscal: <strong>{esc(invoice_number)}</strong></div>
            <div class="label-qr">
                <img src="{esc(qr_url)}" alt="QR Code para avaliar a entrega">
            </div>
            <div class="label-footer">
                Aponte a camera do celular para o QR Code e deixe sua nota. Este QR Code vale para uma unica avaliacao.
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
