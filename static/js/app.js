/* Amigos do Recreio — frontend */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const CORES = ["#2563eb", "#dc2626", "#ea580c", "#7c3aed", "#0d9488", "#db2777", "#65a30d", "#4f46e5"];
const PAPEIS = ["GOL", "DEF", "MEI", "ATA"];
const PAPEL_NOME = { GOL: "Goleiro", DEF: "Defesa", MEI: "Meio", ATA: "Ataque" };
const FAIXAS = ["20-30", "31-40", "41-50", "50+"];
const FAIXA_NOME = { "20-30": "20 a 30", "31-40": "31 a 40", "41-50": "41 a 50", "50+": "50+" };
let jogadores = [];
let geracao = null;
let edicaoId = null;
let fotoSelecionada = null;
let configAtual = null;
let adminLogado = false;

async function api(metodo, url, body) {
  const opts = { method: metodo, headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(url, opts);
  let dados = {};
  try { dados = await r.json(); } catch (_) {}
  if (r.status === 401 && !url.startsWith("/api/admin/login")) {
    adminLogado = false;
    pedirSenha();
  }
  if (!r.ok) throw new Error(dados.erro || "Erro na requisição.");
  return dados;
}

function mostrarAviso(el, texto, ok = false) {
  el.textContent = texto;
  el.classList.toggle("ok-msg", ok);
  el.hidden = false;
  if (ok) setTimeout(() => { el.hidden = true; }, 3500);
}

function fmtData(iso) {
  const [a, m, d] = iso.split("-");
  return `${d}/${m}/${a}`;
}

function hojeIso() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function brl(v) {
  return "R$ " + v.toFixed(2).replace(".", ",");
}

/* ---------------- tema (claro/escuro) ---------------- */
function aplicarTema() {
  const salvo = localStorage.getItem("tema") || "auto";
  const escuro =
    salvo === "escuro" ||
    (salvo === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.tema = escuro ? "escuro" : "claro";
}
aplicarTema();
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", aplicarTema);

/* ---------------- copiar texto (fallback p/ http) ---------------- */
async function copiarTexto(txt) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(txt);
      return true;
    } catch (_) {}
  }
  const ta = document.createElement("textarea");
  ta.value = txt;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) {}
  ta.remove();
  return ok;
}

/* ---------------- abas / admin ---------------- */
let abaAnterior = "churrasco";

function abrirAba(nome) {
  $$("#abas .aba-btn").forEach((x) => x.classList.toggle("ativo", x.dataset.aba === nome));
  $$(".aba").forEach((x) => x.classList.remove("ativo"));
  $("#aba-" + nome).classList.add("ativo");
  if (nome !== "ajustes") abaAnterior = nome;
  $("#btnAdmin").classList.toggle("aberto", nome === "ajustes");
  window.scrollTo(0, 0);
}

$$("#abas .aba-btn").forEach((b) => {
  b.addEventListener("click", () => abrirAba(b.dataset.aba));
});

/* ---------------- admin (senha) ---------------- */
function pedirSenha() {
  $("#loginSenha").value = "";
  $("#loginAviso").hidden = true;
  $("#loginAdmin").hidden = false;
  setTimeout(() => $("#loginSenha").focus(), 60);
}

function abrirAdmin() {
  if (adminLogado) {
    abrirAba("ajustes");
    carregarJogadores();
    return;
  }
  pedirSenha();
}

$("#btnAdmin").addEventListener("click", abrirAdmin);
$("#btnFecharAdmin").addEventListener("click", () => abrirAba(abaAnterior));
$("#loginCancelar").addEventListener("click", () => { $("#loginAdmin").hidden = true; });
$("#loginAdmin").addEventListener("click", (e) => {
  if (e.target === $("#loginAdmin")) $("#loginAdmin").hidden = true;
});

$("#formLogin").addEventListener("submit", async (e) => {
  e.preventDefault();
  const av = $("#loginAviso");
  av.hidden = true;
  try {
    await api("POST", "/api/admin/login", { senha: $("#loginSenha").value });
    adminLogado = true;
    $("#loginAdmin").hidden = true;
    $("#loginSenha").value = "";
    abrirAba("ajustes");
    await carregarJogadores();
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

$("#btnSairAdmin").addEventListener("click", async () => {
  try { await api("POST", "/api/admin/logout"); } catch (_) {}
  adminLogado = false;
  abrirAba(abaAnterior);
});

$("#formSenha").addEventListener("submit", async (e) => {
  e.preventDefault();
  const av = $("#senhaAviso");
  av.hidden = true;
  try {
    await api("POST", "/api/admin/senha", { senha: $("#cfgSenha").value });
    $("#cfgSenha").value = "";
    mostrarAviso(av, "Senha alterada!", true);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

/* ---------------- config ---------------- */
async function carregarConfig() {
  const c = await api("GET", "/api/config");
  configAtual = c;
  adminLogado = !!c.admin;
  $("#nomePelada").textContent = c.nome;
  $("#subPelada").textContent = c.horario;
  document.title = c.nome;
  if (c.logo) {
    $("#logo").src = c.logo;
    $("#logo").hidden = false;
    $("#logoPreview").src = c.logo;
    $("#logoPreview").hidden = false;
  }
  $("#cfgNome").value = c.nome;
  $("#cfgHorario").value = c.horario;
  $("#cfgQuota").value = parseFloat(c.quota);
  $("#linkAcesso").textContent = location.origin;
  return c;
}

$("#formConfig").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/config", {
      nome: $("#cfgNome").value,
      horario: $("#cfgHorario").value,
      quota: $("#cfgQuota").value,
    });
    await carregarConfig();
    await carregarChurrasco();
    mostrarAviso($("#cfgAviso"), "Salvo!", true);
  } catch (err) {
    mostrarAviso($("#cfgAviso"), err.message);
  }
});

$("#btnLogo").addEventListener("click", () => $("#logoArquivo").click());
$("#logoArquivo").addEventListener("change", async () => {
  const f = $("#logoArquivo").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("logo", f);
  try {
    await api("POST", "/api/config/logo", fd);
    $("#logoArquivo").value = "";
    await carregarConfig();
    mostrarAviso($("#cfgAviso"), "Logo atualizado!", true);
  } catch (err) {
    mostrarAviso($("#cfgAviso"), err.message);
  }
});

$("#enderecoAcesso").textContent = location.origin;

/* ---------------- churrasco ---------------- */
let churrascoAtual = null;

async function carregarChurrasco() {
  const d = await api("GET", "/api/churrasco");
  churrascoAtual = d;
  $("#churrascoQuinta").textContent = "Quinta, " + d.quinta_fmt;
  const st = $("#churrascoStatus");
  st.textContent = d.aberto ? "Aberto" : "Encerrado";
  st.classList.toggle("aberto", d.aberto);
  st.classList.toggle("fechado", !d.aberto);
  $("#formChurrasco").style.display = d.aberto ? "flex" : "none";
  $("#churrascoContagem").textContent = `${d.nomes.length} confirmado${d.nomes.length === 1 ? "" : "s"}`;

  const quota = parseFloat(configAtual?.quota || "30");
  const pagos = d.nomes.filter((n) => n.pago).length;
  $("#quotaValor").textContent = brl(quota);
  $("#quotaTotal").textContent =
    `Total ${brl(quota * d.nomes.length)} • ${pagos}/${d.nomes.length} pago${d.nomes.length === 1 ? "" : "s"}`;

  const ul = $("#listaChurrasco");
  ul.innerHTML = "";
  if (!d.nomes.length) {
    ul.innerHTML = '<li class="vazio">Ninguém confirmou ainda.</li>';
  }
  d.nomes.forEach((n) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "nome";
    span.textContent = n.nome;
    const pagar = document.createElement("button");
    pagar.className = "pagar" + (n.pago ? " pago" : "");
    pagar.textContent = n.pago ? "✓ Pago" : "Pendente";
    pagar.title = "Alternar pagamento da quota";
    pagar.onclick = () => alternarPagamento(n.id, !n.pago);
    li.append(span, pagar);
    if (d.aberto) {
      const btn = document.createElement("button");
      btn.className = "remover";
      btn.textContent = "✕";
      btn.title = "Remover";
      btn.onclick = () => removerChurrasco(n.id);
      li.appendChild(btn);
    }
    ul.appendChild(li);
  });
  aplicarConfirmadosNoSelect();
  atualizarPrazo();
}

/* select do churrasco baseado no cadastro permanente de jogadores */
function atualizarSelectChurrasco() {
  const sel = $("#churrascoSelect");
  if (!sel) return;
  const atual = sel.value;
  sel.innerHTML = '<option value="">Selecione seu nome…</option>';
  if (!jogadores.length) {
    sel.insertAdjacentHTML(
      "beforeend",
      '<option value="" disabled>Cadastre os jogadores na aba Times</option>'
    );
    return;
  }
  jogadores.forEach((j) => {
    const o = document.createElement("option");
    o.value = j.nome;
    o.textContent = `${j.nome} — ${j.posicao2 ? j.posicao + "/" + j.posicao2 : j.posicao}${j.idade ? " • " + j.idade + " anos" : ""}`;
    sel.appendChild(o);
  });
  if (atual) sel.value = atual;
  aplicarConfirmadosNoSelect();
}

function aplicarConfirmadosNoSelect() {
  const sel = $("#churrascoSelect");
  if (!sel || !churrascoAtual) return;
  const set = new Set(churrascoAtual.nomes.map((n) => n.nome.toLowerCase()));
  Array.from(sel.options).forEach((o) => {
    if (o.value) o.disabled = set.has(o.value.toLowerCase());
  });
  if (sel.value && set.has(sel.value.toLowerCase())) sel.value = "";
}

async function alternarPagamento(id, pago) {
  try {
    await api("PUT", "/api/churrasco/" + id, { pago });
    await carregarChurrasco();
  } catch (err) {
    mostrarAviso($("#churrascoAviso"), err.message);
  }
}

function atualizarPrazo() {
  if (!churrascoAtual) return;
  const dl = new Date(churrascoAtual.deadline);
  const agora = new Date();
  const alvo = churrascoAtual.aberto ? dl : null;
  if (alvo && alvo > agora) {
    const s = Math.floor((alvo - agora) / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), seg = s % 60;
    const p = (n) => String(n).padStart(2, "0");
    $("#churrascoPrazo").textContent = `Encerra hoje às 12:00 — falta ${p(h)}h ${p(m)}min ${p(seg)}s`;
  } else if (churrascoAtual.aberto) {
    $("#churrascoPrazo").textContent = "Encerra às 12:00 do dia da pelada.";
  } else {
    $("#churrascoPrazo").textContent = "Prazo encerrado — lista da quinta encerrada às 12:00.";
  }
}
setInterval(atualizarPrazo, 1000);

$("#formChurrasco").addEventListener("submit", async (e) => {
  e.preventDefault();
  const av = $("#churrascoAviso");
  av.hidden = true;
  try {
    await api("POST", "/api/churrasco", { nome: $("#churrascoSelect").value });
    $("#churrascoSelect").value = "";
    await carregarChurrasco();
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

async function removerChurrasco(id) {
  try {
    await api("DELETE", "/api/churrasco/" + id);
    await carregarChurrasco();
  } catch (err) {
    mostrarAviso($("#churrascoAviso"), err.message);
  }
}

/* ---------------- jogadores ---------------- */
async function carregarJogadores() {
  const d = await api("GET", "/api/jogadores");
  jogadores = d.jogadores;
  $("#qtdJogadores").textContent = `(${jogadores.length})`;
  const ul = $("#listaJogadores");
  ul.innerHTML = "";
  if (!jogadores.length) ul.innerHTML = '<li class="vazio">Nenhum jogador cadastrado.</li>';
  jogadores.forEach((j) => {
    const li = document.createElement("li");
    const pos2 = j.posicao2 ? `<span class="pos-badge sec">${escapeHtml(j.posicao2)}</span>` : "";
    const idade = j.idade ? `<span class="idade-badge">${j.idade}</span>` : `<span class="idade-badge vazia">—</span>`;
    li.innerHTML = `<span class="nome"></span><span class="pos-badge">${escapeHtml(j.posicao)}</span>${pos2}
      ${idade}<span class="nota-badge">${escapeHtml(j.nota)}</span>
      <span class="acoes"><button class="editar" title="Editar">✏️</button><button class="apagar" title="Excluir">🗑</button></span>`;
    li.querySelector(".nome").textContent = j.nome;
    li.querySelector(".editar").onclick = () => iniciarEdicao(j);
    li.querySelector(".apagar").onclick = async () => {
      if (!confirm(`Excluir ${j.nome}?`)) return;
      await api("DELETE", "/api/jogadores/" + j.id);
      await carregarJogadores();
    };
    ul.appendChild(li);
  });
  const padrao = Math.max(1, Math.floor(jogadores.length / 6));
  if (!$("#inpTimes").dataset.tocado) $("#inpTimes").value = padrao;
  atualizarSelectChurrasco();
}

function iniciarEdicao(j) {
  edicaoId = j.id;
  $("#jogNome").value = j.nome;
  $("#jogPosicao").value = j.posicao;
  $("#jogPosicao2").value = j.posicao2 || "";
  $("#jogNota").value = j.nota;
  $("#jogIdade").value = j.idade ?? "";
  $("#btnJogador").textContent = "Salvar";
  $("#btnCancelarEdicao").hidden = false;
  $("#jogNome").focus();
}

function cancelarEdicao() {
  edicaoId = null;
  $("#formJogador").reset();
  $("#jogNota").value = "7";
  $("#jogPosicao2").value = "";
  $("#jogIdade").value = "";
  $("#btnJogador").textContent = "Incluir";
  $("#btnCancelarEdicao").hidden = true;
}
$("#btnCancelarEdicao").addEventListener("click", cancelarEdicao);

$("#inpTimes").addEventListener("input", (e) => { e.target.dataset.tocado = "1"; });

$("#formJogador").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    nome: $("#jogNome").value,
    posicao: $("#jogPosicao").value,
    posicao2: $("#jogPosicao2").value,
    nota: $("#jogNota").value,
    idade: $("#jogIdade").value,
  };
  try {
    if (edicaoId) await api("PUT", "/api/jogadores/" + edicaoId, body);
    else await api("POST", "/api/jogadores", body);
    cancelarEdicao();
    await carregarJogadores();
  } catch (err) {
    alert(err.message);
  }
});

/* ---------------- presenças ---------------- */
let presencaInfo = null;

function atualizarFonteGeracao() {
  const el = $("#gerarFonte");
  if (!el) return;
  const d = presencaInfo;
  if (!d) {
    el.textContent = "—";
    return;
  }
  el.textContent =
    d.total > 0
      ? `Vai gerar com quem marcou presença: ${d.total} jogador(es) na quinta ${d.quinta_fmt}.`
      : `Ninguém marcado ainda — será gerado com todos os ${d.jogadores.length} cadastros.`;
}

async function carregarPresencas(dataSel) {
  const url = dataSel ? "/api/presencas?data=" + dataSel : "/api/presencas";
  const d = await api("GET", url);
  presencaInfo = d;
  $("#presData").value = d.quinta;
  $("#presQuinta").textContent = "Quinta, " + d.quinta_fmt;
  $("#presContagem").textContent = `${d.total} na pelada`;
  $("#presContagem").classList.toggle("aberto", d.total > 0);
  $("#presContagem").classList.toggle("fechado", d.total === 0);

  const chips = $("#chipsPresenca");
  chips.innerHTML = "";
  if (!d.jogadores.length) {
    chips.innerHTML = '<span class="vazio">Nenhum jogador cadastrado — cadastre na lista abaixo ou adicione visitantes.</span>';
  }
  d.jogadores.forEach((j) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (j.presente ? " ativo" : "");
    b.textContent = j.nome;
    b.title = `${j.posicao}${j.posicao2 ? "/" + j.posicao2 : ""}${
      j.idade ? " • " + j.idade + " anos" : ""
    } — ${j.presente ? "remover marcação" : "marcar presença"}`;
    b.onclick = () => alternarPresenca(j);
    chips.appendChild(b);
  });

  const ul = $("#listaVisitantes");
  ul.innerHTML = "";
  if (!d.visitantes.length) {
    ul.innerHTML = '<li class="vazio">Nenhum visitante nesta quinta.</li>';
  }
  d.visitantes.forEach((v) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "nome";
    span.textContent = v.nome;
    const pos = document.createElement("span");
    pos.className = "pos-badge";
    pos.textContent = v.posicao + (v.posicao2 ? "/" + v.posicao2 : "");
    const nota = document.createElement("span");
    nota.className = "nota-badge";
    nota.textContent = v.nota + (v.idade ? " • " + v.idade : "");
    const rm = document.createElement("button");
    rm.className = "remover";
    rm.textContent = "✕";
    rm.title = "Remover visitante";
    rm.onclick = () => removerPresenca(v.id);
    li.append(span, pos, nota, rm);
    ul.appendChild(li);
  });

  atualizarFonteGeracao();
}

async function alternarPresenca(j) {
  const av = $("#presAviso");
  av.hidden = true;
  try {
    if (j.presenca_id) await api("DELETE", "/api/presencas/" + j.presenca_id);
    else await api("POST", "/api/presencas", { data: $("#presData").value, jogador_id: j.id });
    await carregarPresencas($("#presData").value);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
}

async function removerPresenca(id) {
  const av = $("#presAviso");
  av.hidden = true;
  try {
    await api("DELETE", "/api/presencas/" + id);
    await carregarPresencas($("#presData").value);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
}

$("#formVisitante").addEventListener("submit", async (e) => {
  e.preventDefault();
  const av = $("#presAviso");
  av.hidden = true;
  try {
    await api("POST", "/api/presencas", {
      data: $("#presData").value,
      nome: $("#visNome").value,
      posicao: $("#visPosicao").value,
      nota: $("#visNota").value,
      idade: $("#visIdade").value,
    });
    $("#formVisitante").reset();
    $("#visPosicao").value = "MEA";
    $("#visNota").value = "7";
    await carregarPresencas($("#presData").value);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

$("#presData").addEventListener("change", (e) => {
  if (e.target.value) carregarPresencas(e.target.value);
});
$("#btnPresRecarregar").addEventListener("click", () => carregarPresencas($("#presData").value));

/* ---------------- geração de times ---------------- */
function badgesPosicao(p) {
  const a = `<span class="pos-badge">${escapeHtml(p.posicao)}</span>`;
  const b = p.posicao2 ? `<span class="pos-badge sec">${escapeHtml(p.posicao2)}</span>` : "";
  return a + b;
}

function linhaResumo(r) {
  if (!r) return "";
  const itens = [`<span class="rs-item rs-forca">Força <b>${r.forca ?? 0}</b></span>`];
  PAPEIS.forEach((p) => {
    const q = (r.papeis || {})[p];
    if (q) itens.push(`<span class="rs-item rs-${p}">${PAPEL_NOME[p]} <b>${q}</b></span>`);
  });
  FAIXAS.forEach((f) => {
    const q = (r.faixas || {})[f];
    if (q) itens.push(`<span class="rs-item rs-faixa">${FAIXA_NOME[f]} <b>${q}</b></span>`);
  });
  if (r.sem_idade) itens.push(`<span class="rs-item rs-faixa">sem idade <b>${r.sem_idade}</b></span>`);
  return `<div class="time-resumo">${itens.join("")}</div>`;
}

function tabelaTime(lista) {
  return `
    <table class="tabela-time">
      <thead><tr><th>Posição</th><th>Jogador</th><th>Classe</th><th>Idade</th></tr></thead>
      <tbody>
        ${lista
          .map(
            (p) => `<tr>
              <td class="t-pos">${badgesPosicao(p)}</td>
              <td class="t-nome">${escapeHtml(p.nome)}</td>
              <td class="t-nota"><span class="nota-badge tab">${escapeHtml(p.nota)}</span></td>
              <td class="t-idade">${p.idade ?? "—"}</td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function renderizarTimes(g) {
  const cont = $("#resultadoTimes");
  cont.innerHTML = "";
  $("#btnWhats").disabled = !g;
  $("#btnLimpar").disabled = !g;
  if (!g) return;

  const times = g.times || [];
  const reservas = g.reservas || [];
  const total = times.reduce((s, t) => s + (t.jogadores || []).length, 0) + reservas.length;

  const cab = document.createElement("div");
  cab.className = "card";
  const origem = g.com_presenca
    ? `Fonte: ${g.qtd_fonte ?? total} marcado(s) na presença${g.quinta ? " • quinta " + fmtData(g.quinta) : ""}`
    : `Fonte: todos os ${g.qtd_fonte ?? total} cadastros (nenhuma presença marcada)`;
  cab.innerHTML = `<h2>Times gerados</h2>
    <p class="muted">Gerado em ${escapeHtml(g.gerado_em || "—")} • ${total} jogador(es) divididos em ${times.length} time(s)${
      reservas.length ? ` + ${reservas.length} reserva(s)` : ""
    }</p>
    <p class="muted">${origem}</p>
    <p class="muted">Equilíbrio de todos os grupos: força (classe 5 a 10), papel (goleiro, defesa, meio e atacante) e faixa etária (20-30, 31-40, 41-50, 50+).</p>`;
  cont.appendChild(cab);

  const grade = document.createElement("div");
  grade.className = "grade-times";
  cont.appendChild(grade);

  times.forEach((t) => {
    const lista = t.jogadores || [];
    const div = document.createElement("div");
    div.className = "time-card";
    div.innerHTML = `
      <div class="time-cabeca" style="background:${CORES[(t.cor || 0) % CORES.length]}">
        <strong>${escapeHtml((t.nome || "").toUpperCase())}</strong>
        <span class="media">${lista.length} jogadores</span>
      </div>
      ${linhaResumo(t.resumo)}
      ${tabelaTime(lista)}`;
    grade.appendChild(div);
  });

  if (reservas.length) {
    const div = document.createElement("div");
    div.className = "time-card";
    div.innerHTML = `
      <div class="time-cabeca reserva-cabeca">
        <strong>RESERVAS — PRÓXIMO TIME</strong>
        <span class="media">${reservas.length} jogadores</span>
      </div>
      ${linhaResumo(g.resumo_reservas)}
      <p class="aviso-reserva">Não entraram nos times da vez — ficam para o próximo time.</p>
      ${tabelaTime(reservas)}`;
    grade.appendChild(div);
  } else {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Todos os jogadores caberam nos times — sem reservas.";
    grade.appendChild(p);
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#btnGerar").addEventListener("click", async () => {
  const av = $("#timesAviso");
  av.hidden = true;
  try {
    geracao = await api("POST", "/api/times", {
      acao: "gerar",
      data: $("#presData").value || undefined,
      num_times: parseInt($("#inpTimes").value, 10),
      por_time: parseInt($("#inpPorTime").value, 10),
      goleiro_fixo: $("#chkGoleiroFixo").checked,
      seed: Date.now(),
    });
    renderizarTimes(geracao);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

$("#btnWhats").addEventListener("click", () => {
  if (!geracao) return;
  window.open("https://wa.me/?text=" + encodeURIComponent(textoTimes(geracao)), "_blank");
});

$("#btnLimpar").addEventListener("click", () => {
  const av = $("#timesAviso");
  av.hidden = true;
  if (!geracao) return;
  if (!confirm("Limpar os times da tela?\n\nCadastros e presenças continuam intactos.")) return;
  geracao = null;
  renderizarTimes(null);
  mostrarAviso(av, "Times limpos. Gere outro quando quiser.", true);
});

function textoTimes(g) {
  const linhas = (arr) =>
    arr
      .map(
        (p) =>
          `• ${p.nome} (${p.posicao2 ? p.posicao + "/" + p.posicao2 : p.posicao}${
            p.idade ? `, ${p.idade} anos` : ""
          })`
      )
      .join("\n");
  const quando = g.quinta ? fmtData(g.quinta) : new Date().toLocaleDateString("pt-BR");
  let txt = `⚽ *TIMES* — quinta ${quando}\n\n`;
  (g.times || []).forEach((t) => {
    txt += `*${t.nome.toUpperCase()}*${t.resumo ? ` — força ${t.resumo.forca}` : ""}\n${linhas(t.jogadores || [])}\n\n`;
  });
  if (g.reservas && g.reservas.length) {
    txt += `*RESERVAS (próximo time)*\n${linhas(g.reservas)}\n`;
  }
  return txt;
}

$("#btnCopiarLink").addEventListener("click", async () => {
  const ok = await copiarTexto(location.origin);
  mostrarAviso($("#cfgAviso"), ok ? "Link copiado!" : "Não foi possível copiar.", ok);
});

/* ---------------- comunicados ---------------- */
async function carregarComunicados() {
  const d = await api("GET", "/api/comunicados");
  const cont = $("#listaComunicados");
  cont.innerHTML = "";
  if (!d.comunicados.length) {
    cont.innerHTML = '<div class="card"><p class="vazio">Nenhum comunicado.</p></div>';
    return;
  }
  d.comunicados.forEach((c) => {
    const div = document.createElement("div");
    div.className = "com-card";
    div.innerHTML = `<div class="data">${c.data_fmt}</div><h3></h3><p></p>
      <div class="acoes"><button class="compartilhar">compartilhar</button><button class="excluir">excluir</button></div>`;
    div.querySelector("h3").textContent = c.titulo;
    div.querySelector("p").textContent = c.texto;
    div.querySelector(".excluir").onclick = async () => {
      if (!confirm("Excluir este comunicado?")) return;
      await api("DELETE", "/api/comunicados/" + c.id);
      await carregarComunicados();
    };
    div.querySelector(".compartilhar").onclick = () => {
      const txt = `📢 *${c.titulo}*\n${c.texto}\n\n📅 ${c.data_fmt} — ${location.origin}`;
      window.open("https://wa.me/?text=" + encodeURIComponent(txt), "_blank");
    };
    cont.appendChild(div);
  });
}

$("#formComunicado").addEventListener("submit", async (e) => {
  e.preventDefault();
  const av = $("#comAviso");
  av.hidden = true;
  try {
    await api("POST", "/api/comunicados", {
      data: $("#comData").value,
      titulo: $("#comTitulo").value,
      texto: $("#comTexto").value,
    });
    $("#comTitulo").value = "";
    $("#comTexto").value = "";
    mostrarAviso(av, "Comunicado publicado!", true);
    await carregarComunicados();
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

/* ---------------- galeria ---------------- */
async function carregarGaleria(dataSel) {
  const url = dataSel ? "/api/galeria?data=" + dataSel : "/api/galeria";
  const d = await api("GET", url);
  $("#fotoData").value = d.quinta;
  $("#fotoInfo").textContent = `Quinta ${fmtData(d.quinta)} — ${d.fotos.length} foto${d.fotos.length === 1 ? "" : "s"}`;
  const grid = $("#gradeFotos");
  grid.innerHTML = "";
  if (!d.fotos.length) grid.innerHTML = '<p class="vazio" style="grid-column:1/-1">Sem fotos nesta quinta ainda.</p>';
  d.fotos.forEach((f) => {
    const img = document.createElement("img");
    img.src = f.url;
    img.loading = "lazy";
    img.onclick = () => abrirFoto(f);
    grid.appendChild(img);
  });
  const chips = $("#chipsDatas");
  chips.innerHTML = "";
  d.datas.forEach((x) => {
    const b = document.createElement("button");
    b.className = "chip" + (x.data === d.quinta ? " ativo" : "");
    b.textContent = fmtData(x.data) + ` (${x.qtd})`;
    b.onclick = () => carregarGaleria(x.data);
    chips.appendChild(b);
  });
}

$("#fotoData").addEventListener("change", (e) => {
  if (e.target.value) carregarGaleria(e.target.value);
});

$("#btnFoto").addEventListener("click", () => $("#fotoArquivo").click());
$("#fotoArquivo").addEventListener("change", async () => {
  const arquivos = $("#fotoArquivo").files;
  if (!arquivos.length) return;
  const fd = new FormData();
  fd.append("data", $("#fotoData").value || hojeIso());
  for (const f of arquivos) fd.append("fotos", f);
  $("#btnFoto").disabled = true;
  $("#btnFoto").textContent = "Enviando…";
  try {
    const r = await api("POST", "/api/galeria", fd);
    await carregarGaleria($("#fotoData").value);
  } catch (err) {
    alert(err.message);
  } finally {
    $("#fotoArquivo").value = "";
    $("#btnFoto").disabled = false;
    $("#btnFoto").textContent = "+ Fotos";
  }
});

function abrirFoto(f) {
  fotoSelecionada = f;
  $("#lightboxImg").src = f.url;
  $("#lightbox").hidden = false;
}
$("#lightboxFechar").addEventListener("click", () => { $("#lightbox").hidden = true; });
$("#lightbox").addEventListener("click", (e) => {
  if (e.target === $("#lightbox")) $("#lightbox").hidden = true;
});
$("#lightboxExcluir").addEventListener("click", async () => {
  if (!fotoSelecionada) return;
  if (!confirm("Excluir esta foto?")) return;
  await api("DELETE", "/api/galeria/" + fotoSelecionada.id);
  $("#lightbox").hidden = true;
  await carregarGaleria($("#fotoData").value);
});

/* ---------------- logo 3D (inclinação ao passar o mouse) ---------------- */
const topoEl = document.querySelector(".topo");
const logoEl = $("#logo");
if (topoEl && logoEl && window.matchMedia("(hover: hover)").matches) {
  topoEl.addEventListener("pointermove", (e) => {
    const r = topoEl.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    logoEl.style.transform =
      `perspective(340px) rotateY(${px * 18}deg) rotateX(${-py * 14}deg) rotateZ(-2deg) scale(1.06)`;
  });
  topoEl.addEventListener("pointerleave", () => {
    logoEl.style.transform = "";
  });
}

/* ---------------- init ---------------- */
async function iniciar() {
  $("#comData").value = hojeIso();
  $("#selTema").value = localStorage.getItem("tema") || "auto";
  $("#selTema").addEventListener("change", (e) => {
    localStorage.setItem("tema", e.target.value);
    aplicarTema();
  });
  await carregarConfig();
  await Promise.all([
    carregarChurrasco(),
    carregarJogadores(),
    carregarPresencas(),
    carregarComunicados(),
    carregarGaleria(),
  ]);
  renderizarTimes(null);
}
iniciar();
