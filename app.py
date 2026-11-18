import io
import json
import os
import random
import re
import sqlite3
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

from flask import Flask, abort, g, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

try:
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    TEM_PIL = True
except Exception:
    TEM_PIL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
FOTOS_DIR = os.path.join(UPLOAD_DIR, "fotos")
os.makedirs(FOTOS_DIR, exist_ok=True)

NOTAS = ["A", "B", "C", "D", "E"]
NOTA_VALOR = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
POSICOES = ["GOE", "ZAG", "LAT", "VOL", "MEA", "ATA"]
POS_NOME = {
    "GOE": "Goleiro",
    "ZAG": "Zagueiro",
    "LAT": "Lateral",
    "VOL": "Volante",
    "MEA": "Meia",
    "ATA": "Atacante",
}
EXT_IMG = {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif"}
PADRAO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


# ---------------------------------------------------------------- banco
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS jogadores(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            posicao TEXT NOT NULL,
            nota TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS churrasco(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quinta TEXT NOT NULL,
            nome TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            UNIQUE(quinta, nome)
        );
        CREATE TABLE IF NOT EXISTS comunicados(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            titulo TEXT NOT NULL,
            texto TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fotos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quinta TEXT NOT NULL,
            arquivo TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config(
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
        """
    )
    db.commit()
    try:
        db.execute("ALTER TABLE churrasco ADD COLUMN pago INTEGER NOT NULL DEFAULT 0")
        db.commit()
    except sqlite3.OperationalError:
        pass  # coluna já existe
    db.close()


# banco criado também na importação (WSGI do PythonAnywhere)
init_db()


def cfg_get(chave, padrao=None):
    row = get_db().execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
    return row["valor"] if row else padrao


def cfg_set(chave, valor):
    db = get_db()
    db.execute(
        "INSERT INTO config(chave, valor) VALUES(?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
        (chave, valor),
    )
    db.commit()


# ---------------------------------------------------------------- datas
# servidores (ex.: PythonAnywhere) rodam em UTC -> usamos o horário de Brasília (UTC-3)
TZ_BR = timezone(timedelta(hours=-3))


def agora_local():
    """Horário atual de Brasília (sem fuso, para comparar com deadlines)."""
    return datetime.now(TZ_BR).replace(tzinfo=None)


def proxima_quinta():
    """Quinta do próximo jogo (hoje se for quinta, senão a próxima)."""
    hoje = agora_local().date()
    return hoje + timedelta(days=(3 - hoje.weekday()) % 7)


def quinta_passada():
    """Quinta mais recente que já aconteceu (hoje, se for quinta)."""
    hoje = agora_local().date()
    return hoje - timedelta(days=(hoje.weekday() - 3) % 7)


def parse_data(txt):
    if not txt or not PADRAO_DATA.match(txt):
        return None
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date()
    except ValueError:
        return None


def agora_iso():
    return agora_local().isoformat(timespec="seconds")


# ---------------------------------------------------------------- geração de times
def _deal(pool, tamanhos):
    """Distribui (ordem já definida) respeitando tamanhos, equilibrando
    progresso relativo e soma das notas entre os grupos."""
    grupos = [[] for _ in tamanhos]
    counts = [0] * len(tamanhos)
    sums = [0] * len(tamanhos)
    for p in pool:
        melhor, melhor_chave = None, None
        for i in range(len(tamanhos)):
            if counts[i] >= tamanhos[i]:
                continue
            chave = (counts[i] / tamanhos[i], sums[i], i)
            if melhor_chave is None or chave < melhor_chave:
                melhor_chave, melhor = chave, i
        grupos[melhor].append(p)
        counts[melhor] += 1
        sums[melhor] += p["valor"]
    return grupos


def _custo(grupos):
    todos = [p for g in grupos for p in g]
    if not todos:
        return 0.0
    n = len(todos)
    media = sum(p["valor"] for p in todos) / n
    custo = sum((sum(p["valor"] for p in g) - len(g) * media) ** 2 for g in grupos)
    # distribuição de posições (peso menor que a força dos times)
    for pos in POSICOES:
        total_pos = sum(1 for p in todos if p["posicao"] == pos)
        if not total_pos:
            continue
        for g in grupos:
            esperado = len(g) * total_pos / n
            custo += 0.5 * (sum(1 for p in g if p["posicao"] == pos) - esperado) ** 2
    return custo


def _refinar(grupos):
    """Trocas entre grupos para igualar a força dos times E das reservas,
    sem descuidar da distribuição de posições."""
    custo = _custo(grupos)
    for _ in range(60):
        melhorou = False
        for i in range(len(grupos)):
            for j in range(i + 1, len(grupos)):
                for a in range(len(grupos[i])):
                    for b in range(len(grupos[j])):
                        if grupos[i][a]["valor"] == grupos[j][b]["valor"] and grupos[i][a]["posicao"] == grupos[j][b]["posicao"]:
                            continue
                        grupos[i][a], grupos[j][b] = grupos[j][b], grupos[i][a]
                        novo = _custo(grupos)
                        if novo < custo - 1e-9:
                            custo, melhorou = novo, True
                        else:
                            grupos[i][a], grupos[j][b] = grupos[j][b], grupos[i][a]
        if not melhorou:
            break
    return grupos


def gerar_times(jogadores, num_times, por_time, goleiro_fixo, seed=None):
    if not jogadores:
        raise ValueError("Cadastre ao menos um jogador antes de gerar os times.")
    if not (1 <= num_times <= 10):
        raise ValueError("Número de times deve estar entre 1 e 10.")
    if not (1 <= por_time <= 15):
        raise ValueError("Jogadores por time deve estar entre 1 e 15.")

    rng = random.Random(seed)
    pool = [
        {
            "nome": j["nome"],
            "posicao": j["posicao"],
            "nota": j["nota"].upper(),
            "valor": NOTA_VALOR.get(j["nota"].upper(), 3),
        }
        for j in jogadores
    ]
    rng.shuffle(pool)
    pool.sort(key=lambda p: -p["valor"])

    times = [[] for _ in range(num_times)]

    if not goleiro_fixo:
        goleiros = [p for p in pool if p["posicao"] == "GOE"]
        nao_goe = [p for p in pool if p["posicao"] != "GOE"]
        for i, gk in enumerate(goleiros[:num_times]):
            if len(times[i]) < por_time:
                times[i].append(gk)
        pool = nao_goe + goleiros[num_times:]
        pool.sort(key=lambda p: -p["valor"])

    caps = [por_time - len(t) for t in times]
    res_cap = max(0, len(pool) - sum(caps))
    tamanhos = list(caps) + ([res_cap] if res_cap > 0 else [])

    grupos = _deal(pool, tamanhos)
    for i, t in enumerate(times):
        t.extend(grupos[i])
    reservas = grupos[len(times)] if res_cap > 0 else []

    _refinar(times + ([reservas] if reservas else []))

    todos = [p for t in times for p in t] + reservas
    media = sum(p["valor"] for p in todos) / len(todos) if todos else 0

    def montar(g, letra=None):
        out = []
        for k, p in enumerate(g, start=1):
            out.append(
                {
                    "num": k,
                    "nome": p["nome"],
                    "posicao": p["posicao"],
                    "pos_nome": POS_NOME.get(p["posicao"], p["posicao"]),
                    "nota": p["nota"],
                }
            )
        return out

    resultado = {
        "times": [
            {
                "nome": f"Time {i + 1}",
                "cor": i,
                "jogadores": montar(times[i]),
                "media": round(
                    sum(p["valor"] for p in times[i]) / len(times[i]), 1
                )
                if times[i]
                else 0,
            }
            for i in range(num_times)
        ],
        "reservas": montar(reservas),
        "reserva_media": round(sum(p["valor"] for p in reservas) / len(reservas), 1)
        if reservas
        else None,
        "gerado_em": agora_local().strftime("%d/%m/%Y %H:%M"),
    }
    return resultado, times, reservas


def estrutura_de_matriz(times, reservas):
    return {"times_matriz": times, "reservas_matriz": reservas}


def resposta_de_matriz(estrutura, params=None):
    times = estrutura["times_matriz"]
    reservas = estrutura["reservas_matriz"]
    saida = {
        "times": [
            {
                "nome": f"Time {i + 1}",
                "cor": i,
                "jogadores": [
                    {
                        "num": k,
                        "nome": p["nome"],
                        "posicao": p["posicao"],
                        "pos_nome": POS_NOME.get(p["posicao"], p["posicao"]),
                        "nota": p["nota"],
                    }
                    for k, p in enumerate(t, start=1)
                ],
                "media": round(sum(p["valor"] for p in t) / len(t), 1) if t else 0,
            }
            for i, t in enumerate(times)
        ],
        "reservas": [
            {
                "num": k,
                "nome": p["nome"],
                "posicao": p["posicao"],
                "pos_nome": POS_NOME.get(p["posicao"], p["posicao"]),
                "nota": p["nota"],
            }
            for k, p in enumerate(reservas, start=1)
        ],
        "reserva_media": round(sum(p["valor"] for p in reservas) / len(reservas), 1)
        if reservas
        else None,
        "params": params or {},
        "gerado_em": agora_local().strftime("%d/%m/%Y %H:%M"),
    }
    return saida


# ---------------------------------------------------------------- páginas/estáticas
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/<path:filename>")
def uploads(filename):
    caminho = os.path.normpath(os.path.join(UPLOAD_DIR, filename))
    if not caminho.startswith(os.path.normpath(UPLOAD_DIR)):
        abort(404)
    return send_from_directory(UPLOAD_DIR, filename)


# ---------------------------------------------------------------- config
@app.route("/api/config", methods=["GET"])
def config_get():
    logo = cfg_get("logo")
    if logo and os.path.exists(os.path.join(UPLOAD_DIR, logo)):
        logo_url = f"/uploads/{logo}"
    else:
        logo_url = "/static/img/logo.jpg"
    return {
        "nome": cfg_get("nome", "Amigos do Recreio"),
        "horario": cfg_get("horario", "Quintas • 19:30 às 21:30"),
        "quota": cfg_get("quota", "30"),
        "logo": logo_url,
    }


@app.route("/api/config", methods=["POST"])
def config_post():
    data = request.get_json(silent=True) or {}
    if "nome" in data:
        nome = str(data["nome"]).strip()
        if nome:
            cfg_set("nome", nome[:60])
    if "horario" in data:
        hor = str(data["horario"]).strip()
        if hor:
            cfg_set("horario", hor[:60])
    if "quota" in data:
        try:
            valor = float(str(data["quota"]).replace(",", "."))
            if valor >= 0:
                cfg_set("quota", f"{valor:.2f}")
        except ValueError:
            pass
    return config_get()


@app.route("/api/qrcode.png")
def qrcode_png():
    import qrcode

    url = request.url_root.rstrip("/")
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/api/config/logo", methods=["POST"])
def config_logo():
    arquivo = request.files.get("logo")
    if not arquivo or not arquivo.filename:
        return jsonify(erro="Envie um arquivo de imagem."), 400
    ext = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        return jsonify(erro="Formato não suportado (use PNG, JPG, WEBP ou GIF)."), 400
    nome = f"logo.{ext}"
    caminho = os.path.join(UPLOAD_DIR, nome)
    if os.path.exists(caminho):
        os.remove(caminho)
    arquivo.save(caminho)
    cfg_set("logo", nome)
    return config_get()


# ---------------------------------------------------------------- jogadores
def linha_jogador(row):
    return {
        "id": row["id"],
        "nome": row["nome"],
        "posicao": row["posicao"],
        "pos_nome": POS_NOME.get(row["posicao"], row["posicao"]),
        "nota": row["nota"],
    }


def validar_jogador(data):
    nome = str(data.get("nome", "")).strip()
    posicao = str(data.get("posicao", "")).strip().upper()
    nota = str(data.get("nota", "")).strip().upper()
    if not nome:
        return None, "Informe o nome do jogador."
    if posicao not in POSICOES:
        return None, "Posição inválida."
    if nota not in NOTAS:
        return None, "Nota inválida (use A, B, C, D ou E)."
    return {"nome": nome[:60], "posicao": posicao, "nota": nota}, None


@app.route("/api/jogadores", methods=["GET"])
def jogadores_get():
    rows = get_db().execute("SELECT * FROM jogadores ORDER BY nome COLLATE NOCASE").fetchall()
    return {"jogadores": [linha_jogador(r) for r in rows]}


@app.route("/api/jogadores", methods=["POST"])
def jogadores_post():
    dados, erro = validar_jogador(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    db = get_db()
    db.execute(
        "INSERT INTO jogadores(nome, posicao, nota, criado_em) VALUES(?,?,?,?)",
        (dados["nome"], dados["posicao"], dados["nota"], agora_iso()),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/jogadores/<int:pid>", methods=["PUT"])
def jogadores_put(pid):
    dados, erro = validar_jogador(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    db = get_db()
    db.execute(
        "UPDATE jogadores SET nome=?, posicao=?, nota=? WHERE id=?",
        (dados["nome"], dados["posicao"], dados["nota"], pid),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/jogadores/<int:pid>", methods=["DELETE"])
def jogadores_delete(pid):
    db = get_db()
    db.execute("DELETE FROM jogadores WHERE id=?", (pid,))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- churrasco
def dados_churrasco(quinta):
    db = get_db()
    linhas = db.execute(
        "SELECT * FROM churrasco WHERE quinta=? ORDER BY criado_em", (quinta.isoformat(),)
    ).fetchall()
    deadline = datetime.combine(quinta, dtime(12, 0))
    return {
        "quinta": quinta.isoformat(),
        "quinta_fmt": quinta.strftime("%d/%m/%Y"),
        "deadline": deadline.isoformat(),
        "aberto": agora_local() < deadline,
        "nomes": [{"id": r["id"], "nome": r["nome"], "pago": bool(r["pago"])} for r in linhas],
    }


@app.route("/api/churrasco", methods=["GET"])
def churrasco_get():
    txt = request.args.get("data")
    quinta = parse_data(txt) if txt else proxima_quinta()
    if quinta is None:
        return jsonify(erro="Data inválida."), 400
    return dados_churrasco(quinta)


@app.route("/api/churrasco", methods=["POST"])
def churrasco_post():
    quinta = proxima_quinta()
    if agora_local() >= datetime.combine(quinta, dtime(12, 0)):
        return jsonify(erro="Prazo encerrado (fecha às 12h da quinta)."), 403
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome", "")).strip()
    if not nome:
        return jsonify(erro="Informe seu nome."), 400
    db = get_db()
    existe = db.execute(
        "SELECT id FROM churrasco WHERE quinta=? AND lower(nome)=lower(?)",
        (quinta.isoformat(), nome),
    ).fetchone()
    if existe:
        return jsonify(erro="Esse nome já está na lista."), 409
    db.execute(
        "INSERT INTO churrasco(quinta, nome, criado_em) VALUES(?,?,?)",
        (quinta.isoformat(), nome[:60], agora_iso()),
    )
    db.commit()
    return dados_churrasco(quinta)


@app.route("/api/churrasco/<int:cid>", methods=["PUT"])
def churrasco_pagamento(cid):
    data = request.get_json(silent=True) or {}
    pago = 1 if data.get("pago") else 0
    db = get_db()
    db.execute("UPDATE churrasco SET pago=? WHERE id=?", (pago, cid))
    db.commit()
    return jsonify(ok=True, pago=bool(pago))


@app.route("/api/churrasco/<int:cid>", methods=["DELETE"])
def churrasco_delete(cid):
    quinta = proxima_quinta()
    if agora_local() >= datetime.combine(quinta, dtime(12, 0)):
        return jsonify(erro="Prazo encerrado (fecha às 12h da quinta)."), 403
    db = get_db()
    db.execute("DELETE FROM churrasco WHERE id=? AND quinta=?", (cid, quinta.isoformat()))
    db.commit()
    return dados_churrasco(quinta)


# ---------------------------------------------------------------- comunicados
@app.route("/api/comunicados", methods=["GET"])
def comunicados_get():
    rows = get_db().execute(
        "SELECT * FROM comunicados ORDER BY data DESC, id DESC"
    ).fetchall()
    return {
        "comunicados": [
            {
                "id": r["id"],
                "data": r["data"],
                "data_fmt": datetime.strptime(r["data"], "%Y-%m-%d").strftime("%d/%m/%Y"),
                "titulo": r["titulo"],
                "texto": r["texto"],
            }
            for r in rows
        ]
    }


@app.route("/api/comunicados", methods=["POST"])
def comunicados_post():
    data = request.get_json(silent=True) or {}
    titulo = str(data.get("titulo", "")).strip()
    texto = str(data.get("texto", "")).strip()
    d = parse_data(str(data.get("data", "")))
    if not d:
        return jsonify(erro="Data inválida."), 400
    if not titulo:
        return jsonify(erro="Informe o título."), 400
    if not texto:
        return jsonify(erro="Escreva o comunicado."), 400
    db = get_db()
    db.execute(
        "INSERT INTO comunicados(data, titulo, texto, criado_em) VALUES(?,?,?,?)",
        (d.isoformat(), titulo[:80], texto[:1000], agora_iso()),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/comunicados/<int:cid>", methods=["DELETE"])
def comunicados_delete(cid):
    db = get_db()
    db.execute("DELETE FROM comunicados WHERE id=?", (cid,))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- galeria
def salvar_imagem(arquivo, destino):
    ext = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else "jpg"
    nome = f"{uuid.uuid4().hex[:12]}.jpg"
    if TEM_PIL:
        img = Image.open(arquivo.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "P", "LA"):
            fundo = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            fundo.paste(img, mask=img.split()[-1])
            img = fundo
        else:
            img = img.convert("RGB")
        img.thumbnail((1600, 1600))
        img.save(os.path.join(destino, nome), "JPEG", quality=82, optimize=True)
    else:
        if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
            return None
        nome = f"{uuid.uuid4().hex[:12]}.{ext}"
        arquivo.save(os.path.join(destino, nome))
    return nome


@app.route("/api/galeria", methods=["GET"])
def galeria_get():
    db = get_db()
    datas = db.execute(
        "SELECT quinta, COUNT(*) AS qtd FROM fotos GROUP BY quinta ORDER BY quinta DESC"
    ).fetchall()
    lista_datas = [{"data": r["quinta"], "qtd": r["qtd"]} for r in datas]
    txt = request.args.get("data")
    if txt:
        quinta = parse_data(txt)
        if quinta is None:
            return jsonify(erro="Data inválida."), 400
    elif lista_datas:
        quinta = parse_data(lista_datas[0]["data"])
    else:
        quinta = quinta_passada()
    rows = db.execute(
        "SELECT * FROM fotos WHERE quinta=? ORDER BY criado_em DESC", (quinta.isoformat(),)
    ).fetchall()
    return {
        "quinta": quinta.isoformat(),
        "datas": lista_datas,
        "fotos": [
            {
                "id": r["id"],
                "url": f"/uploads/{r['arquivo']}",
                "quinta": r["quinta"],
            }
            for r in rows
        ],
    }


@app.route("/api/galeria", methods=["POST"])
def galeria_post():
    quinta = parse_data(request.form.get("data", "")) or quinta_passada()
    arquivos = request.files.getlist("fotos")
    arquivos = [a for a in arquivos if a and a.filename]
    if not arquivos:
        return jsonify(erro="Selecione ao menos uma foto."), 400
    destino = os.path.join(FOTOS_DIR, quinta.isoformat())
    os.makedirs(destino, exist_ok=True)
    db = get_db()
    salvas = 0
    for a in arquivos:
        ext = a.filename.rsplit(".", 1)[-1].lower() if "." in a.filename else ""
        if ext not in EXT_IMG:
            continue
        nome = salvar_imagem(a, destino)
        if not nome:
            continue
        rel = f"fotos/{quinta.isoformat()}/{nome}"
        db.execute(
            "INSERT INTO fotos(quinta, arquivo, criado_em) VALUES(?,?,?)",
            (quinta.isoformat(), rel, agora_iso()),
        )
        salvas += 1
    db.commit()
    if not salvas:
        return jsonify(erro="Nenhuma imagem válida enviada."), 400
    return jsonify(ok=True, salvas=salvas)


@app.route("/api/galeria/<int:fid>", methods=["DELETE"])
def galeria_delete(fid):
    db = get_db()
    row = db.execute("SELECT * FROM fotos WHERE id=?", (fid,)).fetchone()
    if not row:
        return jsonify(erro="Foto não encontrada."), 404
    caminho = os.path.normpath(os.path.join(UPLOAD_DIR, row["arquivo"]))
    if caminho.startswith(os.path.normpath(UPLOAD_DIR)) and os.path.exists(caminho):
        os.remove(caminho)
    db.execute("DELETE FROM fotos WHERE id=?", (fid,))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- geração
@app.route("/api/times", methods=["GET"])
def times_get():
    bruto = cfg_get("geracao")
    if not bruto:
        return {"geracao": None}
    return {"geracao": json.loads(bruto)}


@app.route("/api/times", methods=["POST"])
def times_post():
    data = request.get_json(silent=True) or {}

    # gerar do zero
    rows = get_db().execute("SELECT * FROM jogadores ORDER BY nome COLLATE NOCASE").fetchall()
    jogadores = [linha_jogador(r) for r in rows]
    try:
        num_times = int(data.get("num_times", 0))
        por_time = int(data.get("por_time", 6))
        goleiro_fixo = bool(data.get("goleiro_fixo", True))
    except (TypeError, ValueError):
        return jsonify(erro="Parâmetros inválidos."), 400

    try:
        resultado, times, reservas = gerar_times(
            jogadores, num_times, por_time, goleiro_fixo, seed=data.get("seed")
        )
    except ValueError as e:
        return jsonify(erro=str(e)), 400

    params = {"num_times": num_times, "por_time": por_time, "goleiro_fixo": goleiro_fixo}
    estrutura = estrutura_de_matriz(times, reservas)
    resultado["params"] = params
    resultado["estrutura"] = estrutura
    cfg_set("geracao", json.dumps(resultado))
    return jsonify(resultado)


# ---------------------------------------------------------------- main
def ip_local():
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    init_db()
    ip = ip_local()
    print("=" * 52)
    print("  AMIGOS DO RECREIO - sistema da pelada")
    print(f"  Celular na mesma rede:  http://{ip}:8000")
    print(f"  Este computador:        http://127.0.0.1:8000")
    print("=" * 52)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
