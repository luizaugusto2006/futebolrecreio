/* Amigos do Recreio — frontend */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const CORES = ["#2563eb", "#dc2626", "#ea580c", "#7c3aed", "#0d9488", "#db2777", "#65a30d", "#4f46e5"];
let jogadores = [];
let geracao = null;
let edicaoId = null;
let fotoSelecionada = null;

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

/* ---------------- abas ---------------- */
$$("#abas .aba-btn").forEach((b) => {
  b.addEventListener("click", () => {
    $$("#abas .aba-btn").forEach((x) => x.classList.remove("ativo"));
    $$(".aba").forEach((x) => x.classList.remove("ativo"));
    b.classList.add("ativo");
    $("#aba-" + b.dataset.aba).classList.add("ativo");
    window.scrollTo(0, 0);
  });
});

/* ---------------- config ---------------- */
async function carregarConfig() {
  const c = await api("GET", "/api/config");
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
}

$("#formConfig").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/config", { nome: $("#cfgNome").value, horario: $("#cfgHorario").value });
    await carregarConfig();
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
  const ul = $("#listaChurrasco");
  ul.innerHTML = "";
  if (!d.nomes.length) {
    ul.innerHTML = '<li class="vazio">Ninguém confirmou ainda.</li>';
  }
  d.nomes.forEach((n) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = n.nome;
    li.appendChild(span);
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
  atualizarPrazo();
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
    await api("POST", "/api/churrasco", { nome: $("#churrascoNome").value });
    $("#churrascoNome").value = "";
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

/* ---------------- extras (quem leva o quê) ---------------- */
async function carregarExtras() {
  const d = await api("GET", "/api/extras");
  const ul = $("#listaExtras");
  ul.innerHTML = "";
  if (!d.extras.length) ul.innerHTML = '<li class="vazio">Nada anotado ainda.</li>';
  d.extras.forEach((x) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = x.texto;
    const btn = document.createElement("button");
    btn.className = "remover";
    btn.textContent = "✕";
    btn.onclick = async () => { await api("DELETE", "/api/extras/" + x.id); carregarExtras(); };
    li.append(span, btn);
    ul.appendChild(li);
  });
}

$("#formExtras").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/extras", { texto: $("#extraTexto").value });
    $("#extraTexto").value = "";
    await carregarExtras();
  } catch (_) {}
});

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
    li.innerHTML = `<span class="nome"></span><span class="pos-badge">${j.posicao}</span>
      <span class="nota-badge">${j.nota}</span>
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
}

function iniciarEdicao(j) {
  edicaoId = j.id;
  $("#jogNome").value = j.nome;
  $("#jogPosicao").value = j.posicao;
  $("#jogNota").value = j.nota;
  $("#btnJogador").textContent = "Salvar";
  $("#btnCancelarEdicao").hidden = false;
  $("#jogNome").focus();
}

function cancelarEdicao() {
  edicaoId = null;
  $("#formJogador").reset();
  $("#jogNota").value = "B";
  $("#btnJogador").textContent = "Incluir";
  $("#btnCancelarEdicao").hidden = true;
}
$("#btnCancelarEdicao").addEventListener("click", cancelarEdicao);

$("#inpTimes").addEventListener("input", (e) => { e.target.dataset.tocado = "1"; });

$("#formJogador").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { nome: $("#jogNome").value, posicao: $("#jogPosicao").value, nota: $("#jogNota").value };
  try {
    if (edicaoId) await api("PUT", "/api/jogadores/" + edicaoId, body);
    else await api("POST", "/api/jogadores", body);
    cancelarEdicao();
    await carregarJogadores();
  } catch (err) {
    alert(err.message);
  }
});

/* ---------------- geração de times ---------------- */
function badgeNota(n) {
  return `<span class="nota-badge">${n}</span>`;
}

function renderizarTimes(g) {
  const cont = $("#resultadoTimes");
  cont.innerHTML = "";
  if (!g) return;
  const cab = document.createElement("div");
  cab.className = "card";
  cab.innerHTML = `<h2>Times gerados</h2><p class="muted">Gerado em ${g.gerado_em} • equilíbrio por posição e nota (A–E)</p>`;
  cont.appendChild(cab);

  const linhas = (jogadores, comNum) =>
    jogadores.map((p) => `<li>
      ${comNum ? `<span class="num">${p.num}.</span>` : ""}
      <span class="nome">${escapeHtml(p.nome)}</span>
      <span class="pos-badge">${p.pos_nome}</span>
      ${badgeNota(p.nota)}
    </li>`).join("");

  g.times.forEach((t) => {
    const div = document.createElement("div");
    div.className = "time-card";
    div.innerHTML = `
      <div class="time-cabeca" style="background:${CORES[t.cor % CORES.length]}">
        <strong>${t.nome} <span style="font-weight:400;font-size:.8rem">(${t.jogadores.length} jog.)</span></strong>
        <span class="media">média ${String(t.media).replace(".", ",")}</span>
      </div>
      <ul>${linhas(t.jogadores, true)}</ul>`;
    cont.appendChild(div);
  });

  if (g.reservas && g.reservas.length) {
    const div = document.createElement("div");
    div.className = "time-card";
    div.innerHTML = `
      <div class="time-cabeca reserva-cabeca">
        <strong>Reservas <span style="font-weight:400;font-size:.8rem">(${g.reservas.length})</span></strong>
        <span class="media">média ${String(g.reserva_media).replace(".", ",")}</span>
      </div>
      <ul>${linhas(g.reservas, true)}</ul>`;
    cont.appendChild(div);
  }
  $("#btnRodar").disabled = !(g.reservas && g.reservas.length);
  $("#btnCopiar").disabled = false;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function carregarGeracao() {
  try {
    const d = await api("GET", "/api/times");
    geracao = d.geracao;
    if (geracao && geracao.params) {
      if (geracao.params.num_times) $("#inpTimes").value = geracao.params.num_times;
      if (geracao.params.por_time) $("#inpPorTime").value = geracao.params.por_time;
      $("#chkGoleiroFixo").checked = !!geracao.params.goleiro_fixo;
    }
    renderizarTimes(geracao);
  } catch (_) {}
}

$("#btnGerar").addEventListener("click", async () => {
  const av = $("#timesAviso");
  av.hidden = true;
  try {
    geracao = await api("POST", "/api/times", {
      acao: "gerar",
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

$("#btnRodar").addEventListener("click", async () => {
  const av = $("#timesAviso");
  av.hidden = true;
  try {
    geracao = await api("POST", "/api/times", { acao: "rodar" });
    renderizarTimes(geracao);
    mostrarAviso(av, "Reservas rodadas!", true);
  } catch (err) {
    mostrarAviso(av, err.message);
  }
});

function textoTimes(g) {
  const linhas = (arr) =>
    arr.map((p) => `${p.num}. ${p.nome} (${p.pos_nome}) — ${p.nota}`).join("\n");
  let txt = `⚽ *TIMES* — quinta ${new Date().toLocaleDateString("pt-BR")}\n\n`;
  g.times.forEach((t) => {
    txt += `■ *${t.nome}* — média ${String(t.media).replace(".", ",")}\n${linhas(t.jogadores)}\n\n`;
  });
  if (g.reservas && g.reservas.length) {
    txt += `■ *RESERVAS* — média ${String(g.reserva_media).replace(".", ",")}\n${linhas(g.reservas)}\n`;
  }
  return txt;
}

$("#btnCopiar").addEventListener("click", async () => {
  if (!geracao) return;
  const txt = textoTimes(geracao);
  let ok = false;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(txt); ok = true; } catch (_) {}
  }
  if (!ok) {
    const ta = document.createElement("textarea");
    ta.value = txt;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand("copy"); } catch (_) {}
    ta.remove();
  }
  mostrarAviso($("#timesAviso"), ok ? "Texto copiado! Cole no grupo." : "Não foi possível copiar.", ok);
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
      <div class="acoes"><button>excluir</button></div>`;
    div.querySelector("h3").textContent = c.titulo;
    div.querySelector("p").textContent = c.texto;
    div.querySelector("button").onclick = async () => {
      if (!confirm("Excluir este comunicado?")) return;
      await api("DELETE", "/api/comunicados/" + c.id);
      await carregarComunicados();
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
$("#comData").value = hojeIso();
carregarConfig();
carregarChurrasco();
carregarExtras();
carregarJogadores();
carregarGeracao();
carregarComunicados();
carregarGaleria();
