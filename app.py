import hmac
import io
import os
import random
import re
import secrets
import sqlite3
import unicodedata
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from functools import wraps

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
)
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

NOTAS = ["5", "6", "7", "8", "9", "10"]
NOTA_VALOR = {str(n): n for n in range(5, 11)}
POSICOES = ["GOE", "ZAG", "LAT", "VOL", "MEA", "ATA"]
POS_NOME = {
    "GOE": "Goleiro",
    "ZAG": "Zagueiro",
    "LAT": "Lateral",
    "VOL": "Volante",
    "MEA": "Meia",
    "ATA": "Atacante",
}
PAPEL = {"GOE": "GOL", "ZAG": "DEF", "LAT": "DEF", "VOL": "MEI", "MEA": "MEI", "ATA": "ATA"}
PAPEL_NOME = {"GOL": "Goleiro", "DEF": "Defesa", "MEI": "Meio", "ATA": "Ataque"}
PAPEIS = ["GOL", "DEF", "MEI", "ATA"]
ORDEM_POS = {"GOE": 0, "ZAG": 1, "LAT": 1, "VOL": 2, "MEA": 2, "ATA": 3}
FAIXAS = ["20-30", "31-40", "41-50", "50+"]
FAIXA_NOME = {"20-30": "20 a 30", "31-40": "31 a 40", "41-50": "41 a 50", "50+": "50+"}
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
        CREATE TABLE IF NOT EXISTS presencas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quinta TEXT NOT NULL,
            jogador_id INTEGER,
            nome TEXT NOT NULL,
            posicao TEXT NOT NULL DEFAULT 'MEA',
            posicao2 TEXT NOT NULL DEFAULT '',
            nota TEXT NOT NULL DEFAULT '7',
            idade INTEGER,
            criado_em TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_presencas_jog
            ON presencas(quinta, jogador_id);
        """
    )
    db.commit()
    for sql in (
        "ALTER TABLE churrasco ADD COLUMN pago INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jogadores ADD COLUMN posicao2 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE jogadores ADD COLUMN idade INTEGER",
    ):
        try:
            db.execute(sql)
            db.commit()
        except sqlite3.OperationalError:
            pass  # coluna já existe

    # notas antigas em letra (A–E) passam para a escala 5–10
    mapa_nota = {"A": "10", "B": "8", "C": "7", "D": "6", "E": "5"}
    for tabela in ("jogadores", "presencas"):
        for letra, numero in mapa_nota.items():
            db.execute(f"UPDATE {tabela} SET nota=? WHERE nota=?", (numero, letra))
    db.commit()
    db.close()


def faixa_idade(idade):
    """Faixa etária usada no equilíbrio dos times."""
    if idade is None:
        return None
    if idade <= 30:
        return "20-30"
    if idade <= 40:
        return "31-40"
    if idade <= 50:
        return "41-50"
    return "50+"


# banco criado também na importação (WSGI do PythonAnywhere)
init_db()


def garantir_secret():
    """Chave de sessão do admin — criada uma vez e guardada no banco."""
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT valor FROM config WHERE chave='session_secret'").fetchone()
    if row:
        valor = row[0]
    else:
        valor = secrets.token_hex(32)
        db.execute("INSERT INTO config(chave, valor) VALUES('session_secret', ?)", (valor,))
        db.commit()
    db.close()
    return valor


app.secret_key = garantir_secret()


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


# ---------------------------------------------------------------- admin
SENHA_PADRAO = "1234"


def admin_logado():
    return bool(session.get("admin"))


def exige_admin(fn):
    """Só entra se o admin estiver com a senha confirmada na sessão."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not admin_logado():
            return jsonify(erro="Área restrita — informe a senha do admin."), 401
        return fn(*args, **kwargs)

    return wrapper


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    senha = str(data.get("senha", ""))
    if not senha or not hmac.compare_digest(senha, cfg_get("senha_admin", SENHA_PADRAO)):
        return jsonify(erro="Senha incorreta."), 401
    session["admin"] = True
    session.permanent = True
    return jsonify(ok=True)


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify(ok=True)


@app.route("/api/admin/senha", methods=["POST"])
@exige_admin
def admin_senha():
    data = request.get_json(silent=True) or {}
    senha = str(data.get("senha", "")).strip()
    if len(senha) < 4:
        return jsonify(erro="A senha precisa ter ao menos 4 caracteres."), 400
    cfg_set("senha_admin", senha)
    return jsonify(ok=True)


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
    """Custo de desequilíbrio de TODOS os grupos (times + reservas).

    Considera, para cada grupo: soma das notas, quantidade por papel
    (goleiro/defesa/meio/ataque) e quantidade por faixa etária.
    """
    todos = [p for g in grupos for p in g]
    if not todos:
        return 0.0
    n = len(todos)
    ng = len(grupos)
    media = sum(p["valor"] for p in todos) / n
    custo = 0.0

    # 1) força do grupo (soma das notas x tamanho esperado)
    for g in grupos:
        custo += (sum(p["valor"] for p in g) - len(g) * media) ** 2

    # 2) distribuição de papeis (goleiro, zagueiro/lateral, meio, atacante)
    for papel in PAPEIS:
        total = sum(1 for p in todos if p["papel"] == papel)
        if not total:
            continue
        for g in grupos:
            esperado = len(g) * total / n
            custo += 0.8 * (sum(1 for p in g if p["papel"] == papel) - esperado) ** 2
        # grupo sem ninguém daquele papel (mesmo havendo sobra no elenco)
        if total >= ng:
            for g in grupos:
                if not any(papel in (p.get("cobre") or [p["papel"]]) for p in g):
                    custo += 8.0

    # 3) faixas etárias (20-30, 31-40, 41-50, 50+)
    for fx in FAIXAS:
        total = sum(1 for p in todos if p["faixa"] == fx)
        if not total:
            continue
        for g in grupos:
            esperado = len(g) * total / n
            custo += 0.45 * (sum(1 for p in g if p["faixa"] == fx) - esperado) ** 2

    return custo


def _refinar(grupos, reserva_idx=None, protegidos=None):
    """Trocas entre grupos para igualar a força dos times E das reservas,
    sem descuidar da distribuição de posições, papeis e idades.
    Jogadores protegidos (especiais) podem trocar entre times, mas não caem
    nas reservas."""
    custo = _custo(grupos)
    for _ in range(80):
        melhorou = False
        for i in range(len(grupos)):
            for j in range(i + 1, len(grupos)):
                for a in range(len(grupos[i])):
                    for b in range(len(grupos[j])):
                        if grupos[i][a]["valor"] == grupos[j][b]["valor"] and grupos[i][a]["posicao"] == grupos[j][b]["posicao"]:
                            continue
                        if reserva_idx is not None and (i == reserva_idx) != (j == reserva_idx):
                            ti, ta = (j, b) if i == reserva_idx else (i, a)
                            if _norm_nome(grupos[ti][ta]["nome"]) in (protegidos or ()):
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


TRAPACA_FORTE = "Dinho"
TRAPACA_FORA = ("Betão", "Wilson")
TRAPACA_CICLO = 3


def _norm_nome(s):
    """Nome minúsculo e sem acento para comparações."""
    txt = unicodedata.normalize("NFD", str(s or ""))
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return txt.strip().casefold()


TRAPACA_NORMS = {_norm_nome(n) for n in (TRAPACA_FORTE,) + TRAPACA_FORA}


def _espalsar_especiais(times, pool, alvos):
    """Dinho, Betão e Wilson, quando estão no sorteio, começam um em cada
    time (round-robin); o resto do sorteio equilibra em volta."""
    especiais = [p for p in pool if _norm_nome(p["nome"]) in TRAPACA_NORMS]
    if not especiais:
        return pool
    ids = {id(p) for p in especiais}
    resto = [p for p in pool if id(p) not in ids]
    for k, p in enumerate(especiais):
        for vez in range(len(times)):
            i = (k + vez) % len(times)
            if len(times[i]) < alvos[i]:
                times[i].append(p)
                break
        else:
            resto.append(p)
    return resto


def _fixar_no_forte(times, reservas, nome):
    """Move o jogador para o time de maior soma trocando com o de valor mais
    próximo (sem quebrar o goleiro) — a força total quase não muda."""
    if not nome or not times:
        return
    alvo = None
    origem = None
    for grupo in times + ([reservas] if reservas else []):
        for p in grupo:
            if _norm_nome(p["nome"]) == _norm_nome(nome):
                alvo, origem = p, grupo
    if alvo is None:
        return
    d = alvo["valor"]
    somas = [sum(p["valor"] for p in t) for t in times]
    if any(origem is t for t in times) and sum(p["valor"] for p in origem) == max(somas):
        return
    melhor = None
    for fi in range(len(times)):
        if times[fi] is origem:
            continue
        forte = times[fi]
        goleiros = [p for p in forte if p["posicao"] == "GOE"]
        for cand in forte:
            if alvo["posicao"] != "GOE" and cand["posicao"] == "GOE" and len(goleiros) == 1:
                continue
            if alvo["posicao"] == "GOE" and cand["posicao"] != "GOE" and goleiros:
                continue
            delta = abs(cand["valor"] - d)
            if melhor is not None and delta >= melhor[0]:
                continue
            s_f = somas[fi] - cand["valor"] + d
            outras = [
                (sum(p["valor"] for p in origem) - d + cand["valor"])
                if times[k] is origem
                else somas[k]
                for k in range(len(times))
                if k != fi
            ]
            if outras and s_f < max(outras) - 1e-9:
                continue
            melhor = (delta, fi, cand)
    if melhor is None:
        return
    _, fi, cand = melhor
    i = origem.index(alvo)
    j = times[fi].index(cand)
    origem[i], times[fi][j] = times[fi][j], origem[i]


def _tira_do_forte(times, nomes, forte):
    """Tira Betão e Wilson do time do Dinho (quando a rotina manda manter
    separados), trocando por alguém de outro time de valor próximo — o
    tamanho dos times não muda e o goleiro não é quebrado."""
    if not forte or len(times) < 2:
        return
    alvo = _norm_nome(forte)
    time_forte = next(
        (t for t in times if any(_norm_nome(p["nome"]) == alvo for p in t)), None
    )
    if time_forte is None:
        return
    for nome in nomes:
        sai = next(
            (p for p in time_forte if _norm_nome(p["nome"]) == _norm_nome(nome)), None
        )
        if sai is None:
            continue
        melhor = None
        for outro in times:
            if outro is time_forte:
                continue
            goleiros = [p for p in outro if p["posicao"] == "GOE"]
            for cand in outro:
                if _norm_nome(cand["nome"]) in TRAPACA_NORMS:
                    continue
                if sai["posicao"] != "GOE" and cand["posicao"] == "GOE" and len(goleiros) == 1:
                    continue
                if sai["posicao"] == "GOE" and cand["posicao"] != "GOE" and goleiros:
                    continue
                delta = abs(cand["valor"] - sai["valor"])
                if melhor is None or delta < melhor[0]:
                    melhor = (delta, outro, cand)
        if melhor is None:
            continue
        _, outro, cand = melhor
        i = time_forte.index(sai)
        j = outro.index(cand)
        time_forte[i], outro[j] = outro[j], time_forte[i]


def calcular_num_times(n_jogadores, por_time):
    """Quantos times cabem: conta pela lotação de cada time (padrão 6),
    arredondando para cima para ninguém ficar sem time."""
    if n_jogadores <= 0:
        return 1
    return max(1, min(10, -(-n_jogadores // max(1, por_time))))


def alvos_times(n_jogadores, num_times, por_time):
    """Tamanho de cada time: todos lotados com por_time e a sobra inteira
    no último time. Só vira reserva o que estourar o limite de times."""
    sobra = n_jogadores - por_time * (num_times - 1)
    if sobra > por_time:
        return [por_time] * num_times
    return [por_time] * (num_times - 1) + [max(1, sobra)]


def _aplicar_trapaca(jogadores):
    """Rotina interna do sorteio (ninguém sai do sorteio): Dinho, se marcado,
    é fixado no time mais forte. Betão e Wilson ficam fora do time dele em 2 de
    cada 3 giros; a cada 3ª geração eles podem cair com o Dinho.
    Contador de giros guardado no config."""
    giro = int(cfg_get("trapaca_giro", "0") or 0) + 1
    cfg_set("trapaca_giro", str(giro))
    lista = list(jogadores)
    fixar = (
        TRAPACA_FORTE
        if _norm_nome(TRAPACA_FORTE) in {_norm_nome(j["nome"]) for j in lista}
        else None
    )
    return lista, fixar, giro % TRAPACA_CICLO != 0


def gerar_times(jogadores, num_times, por_time, goleiro_fixo, seed=None, fixar=None, separar=False):
    if not jogadores:
        raise ValueError("Cadastre ao menos um jogador antes de gerar os times.")
    if not (1 <= num_times <= 10):
        raise ValueError("Número de times deve estar entre 1 e 10.")
    if not (1 <= por_time <= 15):
        raise ValueError("Jogadores por time deve estar entre 1 e 15.")

    rng = random.Random(seed)
    pool = []
    for j in jogadores:
        nota = str(j["nota"]).strip()
        posicao = j["posicao"]
        posicao2 = j.get("posicao2") or ""
        idade = j.get("idade")
        faixa = faixa_idade(idade)
        papel = PAPEL.get(posicao, "MEI")
        cobre = [papel]
        if posicao2:
            p2 = PAPEL.get(posicao2, "MEI")
            if p2 not in cobre:
                cobre.append(p2)
        pool.append(
            {
                "nome": j["nome"],
                "posicao": posicao,
                "posicao2": posicao2,
                "nota": nota,
                "valor": NOTA_VALOR.get(nota, 7),
                "idade": idade,
                "faixa": faixa,
                "papel": papel,
                "cobre": cobre,
            }
        )
    rng.shuffle(pool)
    pool.sort(key=lambda p: -p["valor"])

    times = [[] for _ in range(num_times)]
    alvos = alvos_times(len(jogadores), num_times, por_time)

    if not goleiro_fixo:
        goleiros = [p for p in pool if p["posicao"] == "GOE"]
        nao_goe = [p for p in pool if p["posicao"] != "GOE"]
        sem_vaga = []
        for i, gk in enumerate(goleiros[:num_times]):
            if len(times[i]) < alvos[i]:
                times[i].append(gk)
            else:
                sem_vaga.append(gk)
        pool = nao_goe + sem_vaga + goleiros[num_times:]
        pool.sort(key=lambda p: -p["valor"])

    pool = _espalsar_especiais(times, pool, alvos)

    caps = [max(0, alvos[i] - len(t)) for i, t in enumerate(times)]
    sobrando = len(pool) - sum(caps)
    if sobrando > 0:
        # quem já lotou não encolhe: a folga vai para os times que ainda recebem
        for i in range(num_times):
            if sobrando <= 0:
                break
            livre = por_time - (len(times[i]) + caps[i])
            add = min(livre, sobrando)
            caps[i] += add
            sobrando -= add
    res_cap = max(0, sobrando)
    tamanhos = list(caps) + ([res_cap] if res_cap > 0 else [])

    grupos = _deal(pool, tamanhos)
    for i, t in enumerate(times):
        t.extend(grupos[i])
    reservas = grupos[len(times)] if res_cap > 0 else []

    _refinar(
        times + ([reservas] if reservas else []),
        reserva_idx=len(times) if reservas else None,
        protegidos=TRAPACA_NORMS,
    )
    if fixar:
        _fixar_no_forte(times, reservas, fixar)
        if separar:
            _tira_do_forte(times, TRAPACA_FORA, fixar)

    todos = [p for t in times for p in t] + reservas
    media = sum(p["valor"] for p in todos) / len(todos) if todos else 0

    resultado = {
        "times": [
            {
                "nome": f"Time {i + 1}",
                "cor": i,
                "jogadores": montar_grupo(times[i]),
                "media": round(
                    sum(p["valor"] for p in times[i]) / len(times[i]), 1
                )
                if times[i]
                else 0,
                "resumo": resumo_grupo(times[i]),
            }
            for i in range(num_times)
        ],
        "reservas": montar_grupo(reservas),
        "reserva_media": round(sum(p["valor"] for p in reservas) / len(reservas), 1)
        if reservas
        else None,
        "resumo_reservas": resumo_grupo(reservas) if reservas else None,
        "gerado_em": agora_local().strftime("%d/%m/%Y %H:%M"),
    }
    return resultado, times, reservas


def montar_grupo(g):
    out = []
    for p in g:
        pos2 = p.get("posicao2") or ""
        faixa = p.get("faixa")
        papel = p.get("papel", "MEI")
        out.append(
            {
                "num": 0,
                "nome": p["nome"],
                "posicao": p["posicao"],
                "posicao2": pos2,
                "pos_nome": POS_NOME.get(p["posicao"], p["posicao"]),
                "pos_nome2": POS_NOME.get(pos2, pos2) if pos2 else "",
                "nota": p["nota"],
                "idade": p.get("idade"),
                "faixa": faixa,
                "faixa_nome": FAIXA_NOME.get(faixa, "—") if faixa else "—",
                "papel": papel,
                "papel_nome": PAPEL_NOME.get(papel, "—"),
            }
        )
    # tabela na ordem: zagueiro, meia e atacante por último (goleiro no topo)
    out.sort(key=lambda p: ORDEM_POS.get(p["posicao"], 4))
    for k, p in enumerate(out, start=1):
        p["num"] = k
    return out


def resumo_grupo(g):
    papeis = {p: 0 for p in PAPEIS}
    faixas = {f: 0 for f in FAIXAS}
    sem_idade = 0
    for p in g:
        papeis[p.get("papel", "MEI")] += 1
        fx = p.get("faixa")
        if fx:
            faixas[fx] += 1
        else:
            sem_idade += 1
    forca = sum(p["valor"] for p in g)
    return {
        "forca": forca,
        "media": round(forca / len(g), 1) if g else 0,
        "papeis": papeis,
        "faixas": faixas,
        "sem_idade": sem_idade,
    }


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
        "admin": admin_logado(),
    }


@app.route("/api/config", methods=["POST"])
@exige_admin
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
@exige_admin
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
    idade = row["idade"]
    faixa = faixa_idade(idade)
    return {
        "id": row["id"],
        "nome": row["nome"],
        "posicao": row["posicao"],
        "posicao2": row["posicao2"] or "",
        "pos_nome": POS_NOME.get(row["posicao"], row["posicao"]),
        "pos_nome2": POS_NOME.get(row["posicao2"], row["posicao2"]) if row["posicao2"] else "",
        "nota": row["nota"],
        "idade": idade,
        "faixa": faixa,
        "faixa_nome": FAIXA_NOME.get(faixa, "—") if faixa else "—",
        "papel": PAPEL.get(row["posicao"], "MEI"),
    }


def validar_jogador(data):
    nome = str(data.get("nome", "")).strip()
    posicao = str(data.get("posicao", "")).strip().upper()
    posicao2 = str(data.get("posicao2", "") or "").strip().upper()
    nota = str(data.get("nota", "")).strip()
    idade_txt = str(data.get("idade", "") or "").strip()
    if not nome:
        return None, "Informe o nome do jogador."
    if posicao not in POSICOES:
        return None, "Posição inválida."
    if posicao2:
        if posicao2 not in POSICOES:
            return None, "2ª posição inválida."
        if posicao2 == posicao:
            return None, "A 2ª posição deve ser diferente da principal."
    if nota not in NOTAS:
        return None, "Classe inválida (use um valor de 5 a 10)."
    idade = None
    if idade_txt:
        try:
            idade = int(float(idade_txt.replace(",", ".")))
        except ValueError:
            return None, "Idade inválida."
        if not 10 <= idade <= 100:
            return None, "Idade deve estar entre 10 e 100 anos."
    return {
        "nome": nome[:60],
        "posicao": posicao,
        "posicao2": posicao2,
        "nota": nota,
        "idade": idade,
    }, None


@app.route("/api/jogadores", methods=["GET"])
def jogadores_get():
    rows = get_db().execute("SELECT * FROM jogadores ORDER BY nome COLLATE NOCASE").fetchall()
    return {"jogadores": [linha_jogador(r) for r in rows]}


@app.route("/api/jogadores", methods=["POST"])
@exige_admin
def jogadores_post():
    dados, erro = validar_jogador(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    db = get_db()
    db.execute(
        "INSERT INTO jogadores(nome, posicao, posicao2, nota, idade, criado_em) VALUES(?,?,?,?,?,?)",
        (dados["nome"], dados["posicao"], dados["posicao2"], dados["nota"], dados["idade"], agora_iso()),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/jogadores/<int:pid>", methods=["PUT"])
@exige_admin
def jogadores_put(pid):
    dados, erro = validar_jogador(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    db = get_db()
    db.execute(
        "UPDATE jogadores SET nome=?, posicao=?, posicao2=?, nota=?, idade=? WHERE id=?",
        (dados["nome"], dados["posicao"], dados["posicao2"], dados["nota"], dados["idade"], pid),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/jogadores/<int:pid>", methods=["DELETE"])
@exige_admin
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


# ---------------------------------------------------------------- presenças
def dados_presencas(quinta):
    """Lista de quem foi marcado para a pelada: cadastrados + visitantes."""
    db = get_db()
    iso = quinta.isoformat()
    linhas = db.execute(
        "SELECT * FROM presencas WHERE quinta=? ORDER BY nome COLLATE NOCASE", (iso,)
    ).fetchall()

    marcados = {}
    visitantes = []
    for r in linhas:
        jid = r["jogador_id"]
        if jid:
            if db.execute("SELECT id FROM jogadores WHERE id=?", (jid,)).fetchone():
                marcados[jid] = r["id"]
                continue
        # visitante (ou cadastro já excluído) — fica pela foto do momento
        visitantes.append(
            {
                "id": r["id"],
                "nome": r["nome"],
                "posicao": r["posicao"],
                "posicao2": r["posicao2"] or "",
                "nota": r["nota"],
                "idade": r["idade"],
            }
        )

    jogadores = [
        {
            "id": row["id"],
            "nome": row["nome"],
            "posicao": row["posicao"],
            "posicao2": row["posicao2"] or "",
            "nota": row["nota"],
            "idade": row["idade"],
            "presente": row["id"] in marcados,
            "presenca_id": marcados.get(row["id"]),
        }
        for row in db.execute("SELECT * FROM jogadores ORDER BY nome COLLATE NOCASE").fetchall()
    ]

    return {
        "quinta": iso,
        "quinta_fmt": quinta.strftime("%d/%m/%Y"),
        "jogadores": jogadores,
        "visitantes": visitantes,
        "marcados": len(marcados),
        "total": len(marcados) + len(visitantes),
    }


@app.route("/api/presencas", methods=["GET"])
def presencas_get():
    txt = request.args.get("data")
    quinta = parse_data(txt) if txt else proxima_quinta()
    if quinta is None:
        return jsonify(erro="Data inválida."), 400
    return dados_presencas(quinta)


@app.route("/api/presencas", methods=["POST"])
def presencas_post():
    data = request.get_json(silent=True) or {}
    quinta = parse_data(str(data.get("data", ""))) or proxima_quinta()
    if quinta is None:
        return jsonify(erro="Data inválida."), 400
    db = get_db()
    iso = quinta.isoformat()

    jid = data.get("jogador_id")
    if jid not in (None, ""):
        try:
            jid = int(jid)
        except (TypeError, ValueError):
            return jsonify(erro="Jogador inválido."), 400
        j = db.execute("SELECT * FROM jogadores WHERE id=?", (jid,)).fetchone()
        if not j:
            return jsonify(erro="Jogador não encontrado."), 404
        if db.execute(
            "SELECT id FROM presencas WHERE quinta=? AND jogador_id=?", (iso, jid)
        ).fetchone():
            return jsonify(erro="Esse jogador já está marcado."), 409
        db.execute(
            "INSERT INTO presencas(quinta, jogador_id, nome, posicao, posicao2, nota, idade, criado_em) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (iso, jid, j["nome"], j["posicao"], j["posicao2"], j["nota"], j["idade"], agora_iso()),
        )
    else:
        nome = str(data.get("nome", "")).strip()
        posicao = str(data.get("posicao", "")).strip().upper()
        posicao2 = str(data.get("posicao2", "") or "").strip().upper()
        nota = str(data.get("nota", "") or "7").strip()
        idade_txt = str(data.get("idade", "") or "").strip()
        if not nome:
            return jsonify(erro="Informe o nome do visitante."), 400
        if posicao not in POSICOES:
            return jsonify(erro="Posição inválida."), 400
        if posicao2 and (posicao2 not in POSICOES or posicao2 == posicao):
            posicao2 = ""
        if nota not in NOTAS:
            nota = "7"
        idade = None
        if idade_txt:
            try:
                idade = int(float(idade_txt.replace(",", ".")))
            except ValueError:
                return jsonify(erro="Idade inválida."), 400
            if not 10 <= idade <= 100:
                return jsonify(erro="Idade deve estar entre 10 e 100 anos."), 400
        if db.execute(
            "SELECT id FROM presencas WHERE quinta=? AND jogador_id IS NULL AND lower(nome)=lower(?)",
            (iso, nome),
        ).fetchone():
            return jsonify(erro="Esse visitante já está na lista."), 409
        db.execute(
            "INSERT INTO presencas(quinta, jogador_id, nome, posicao, posicao2, nota, idade, criado_em) "
            "VALUES(?,NULL,?,?,?,?,?,?)",
            (iso, nome[:60], posicao, posicao2, nota, idade, agora_iso()),
        )

    db.commit()
    return dados_presencas(quinta)


@app.route("/api/presencas/<int:pid>", methods=["DELETE"])
def presencas_delete(pid):
    db = get_db()
    db.execute("DELETE FROM presencas WHERE id=?", (pid,))
    db.commit()
    return jsonify(ok=True)


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
def jogadores_para_geracao(quinta):
    """Quem entra na geração: os marcados na presença (cadastro + visitantes).
    Se ninguém marcou, volta para todos os cadastrados."""
    db = get_db()
    iso = quinta.isoformat()
    linhas = db.execute("SELECT * FROM presencas WHERE quinta=?", (iso,)).fetchall()
    if not linhas:
        rows = db.execute("SELECT * FROM jogadores ORDER BY nome COLLATE NOCASE").fetchall()
        return [linha_jogador(r) for r in rows], False

    jogadores = []
    for r in linhas:
        atual = None
        if r["jogador_id"]:
            atual = db.execute("SELECT * FROM jogadores WHERE id=?", (r["jogador_id"],)).fetchone()
        # cadastro atualizado quando existe; senão a foto salva na marcação
        jogadores.append(linha_jogador(atual if atual else r))
    return jogadores, True


@app.route("/api/times", methods=["POST"])
def times_post():
    """Gera todos os times de uma vez, só para visualização/WhatsApp —
    nada é gravado no banco."""
    data = request.get_json(silent=True) or {}
    quinta = parse_data(str(data.get("data", ""))) or proxima_quinta()
    if quinta is None:
        return jsonify(erro="Data inválida."), 400
    try:
        por_time = int(data.get("por_time", 6))
        goleiro_fixo = bool(data.get("goleiro_fixo", True))
    except (TypeError, ValueError):
        return jsonify(erro="Parâmetros inválidos."), 400
    if not (1 <= por_time <= 15):
        return jsonify(erro="Jogadores por time deve estar entre 1 e 15."), 400

    jogadores, com_presenca = jogadores_para_geracao(quinta)
    qtd_fonte = len(jogadores)
    jogadores, fixar, separar = _aplicar_trapaca(jogadores)
    num_times = calcular_num_times(len(jogadores), por_time)
    try:
        resultado, _times, _reservas = gerar_times(
            jogadores, num_times, por_time, goleiro_fixo,
            seed=data.get("seed"), fixar=fixar, separar=separar,
        )
    except ValueError as e:
        return jsonify(erro=str(e)), 400

    resultado["params"] = {"num_times": num_times, "por_time": por_time, "goleiro_fixo": goleiro_fixo}
    resultado["quinta"] = quinta.isoformat()
    resultado["com_presenca"] = com_presenca
    resultado["qtd_fonte"] = qtd_fonte
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
