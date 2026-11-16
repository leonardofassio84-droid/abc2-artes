"""Armazenamento local (SQLite): histórico de conversa, registros e deduplicação."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

_lock = threading.Lock()
_path = "assessor.db"


def init(path: str) -> None:
    global _path
    _path = path
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT NOT NULL,
                papel TEXT NOT NULL,          -- 'user' ou 'assistant'
                conteudo TEXT NOT NULL,
                criado_em TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_msg_numero ON mensagens(numero, id);

            CREATE TABLE IF NOT EXISTS registros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT NOT NULL,         -- quem criou
                categoria TEXT NOT NULL,
                titulo TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                contato TEXT,                 -- pessoa/cliente relacionado
                valor REAL,
                data_ref TEXT,                -- data a que o registro se refere (AAAA-MM-DD)
                origem TEXT,                  -- 'conversa' ou 'encaminhada'
                criado_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processadas (
                wamid TEXT PRIMARY KEY,
                criado_em TEXT NOT NULL
            );
            """
        )


@contextmanager
def _conn():
    with _lock:
        conn = sqlite3.connect(_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- deduplicação (a Meta reenvia webhooks) ----------
def ja_processada(wamid: str) -> bool:
    """Marca a mensagem como processada; devolve True se ela já tinha sido vista."""
    with _conn() as c:
        try:
            c.execute("INSERT INTO processadas VALUES (?, ?)", (wamid, _agora()))
            return False
        except sqlite3.IntegrityError:
            return True


# ---------- histórico ----------
def salvar_mensagem(numero: str, papel: str, conteudo: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO mensagens (numero, papel, conteudo, criado_em) VALUES (?,?,?,?)",
            (numero, papel, conteudo, _agora()),
        )


def historico(numero: str, limite: int = 30) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT papel, conteudo FROM mensagens WHERE numero=? ORDER BY id DESC LIMIT ?",
            (numero, limite),
        ).fetchall()
    msgs = [{"role": r["papel"], "content": r["conteudo"]} for r in reversed(rows)]
    # A API exige que a conversa comece com 'user' e alterne os papéis.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    alternado: list[dict] = []
    for m in msgs:
        if alternado and alternado[-1]["role"] == m["role"]:
            alternado[-1]["content"] += "\n\n" + m["content"]
        else:
            alternado.append(dict(m))
    return alternado


def limpar_historico(numero: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM mensagens WHERE numero=?", (numero,))


# ---------- registros ----------
def criar_registro(numero, categoria, titulo, conteudo, contato=None, valor=None,
                   data_ref=None, origem="conversa") -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO registros
               (numero, categoria, titulo, conteudo, contato, valor, data_ref, origem, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (numero, categoria.strip().lower(), titulo, conteudo, contato, valor,
             data_ref, origem, _agora()),
        )
        return cur.lastrowid


def buscar_registros(texto=None, categoria=None, contato=None, desde=None, ate=None,
                     limite=20) -> list[dict]:
    sql = "SELECT * FROM registros WHERE 1=1"
    args: list = []
    if texto:
        sql += " AND (titulo LIKE ? OR conteudo LIKE ? OR contato LIKE ?)"
        args += [f"%{texto}%"] * 3
    if categoria:
        sql += " AND categoria = ?"
        args.append(categoria.strip().lower())
    if contato:
        sql += " AND contato LIKE ?"
        args.append(f"%{contato}%")
    if desde:
        sql += " AND COALESCE(data_ref, substr(criado_em,1,10)) >= ?"
        args.append(desde)
    if ate:
        sql += " AND COALESCE(data_ref, substr(criado_em,1,10)) <= ?"
        args.append(ate)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limite)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def atualizar_registro(reg_id: int, campos: dict) -> bool:
    permitidos = {"categoria", "titulo", "conteudo", "contato", "valor", "data_ref"}
    campos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if not campos:
        return False
    sets = ", ".join(f"{k}=?" for k in campos)
    with _conn() as c:
        cur = c.execute(f"UPDATE registros SET {sets} WHERE id=?", [*campos.values(), reg_id])
        return cur.rowcount > 0


def apagar_registro(reg_id: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM registros WHERE id=?", (reg_id,)).rowcount > 0


def resumo_categorias() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT categoria, COUNT(*) AS total, SUM(valor) AS soma_valores "
            "FROM registros GROUP BY categoria ORDER BY total DESC"
        ).fetchall()]


def todos_registros() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM registros ORDER BY id").fetchall()]
