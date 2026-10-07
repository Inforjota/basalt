"""A interface em inglês: `[basalt] idioma = pt | en | auto` (e `BASALT_IDIOMA`).

O português não muda nada (a página sai byte a byte igual); em «en» a página traz `lang="en"` e o dicionário `EN` traduz TODOS
os textos visíveis da página. Os testes que não precisam de navegador (configuração, escolha pelo Accept-Language, cobertura do
dicionário) correm sempre; os de página usam o Chromium do Playwright contra o servidor simulado de `pagina_falsa.py`."""
import json
import re
from html.parser import HTMLParser

import pytest

from conftest import RAIZ
from pagina_falsa import ESTADO, esperar, pronta

INDEX = RAIZ / "basalt" / "static" / "index.html"
ENTRAR = RAIZ / "basalt" / "static" / "entrar.html"

# valores do dicionário que podem ser IGUAIS à chave: nomes próprios e siglas que não se traduzem
IGUAIS = {"Basalt", "🪨 Basalt", "OK", "Markdown", "MCP", "Obsidian", "Original", "Git", "API", "PDF", "IA", "AI"}


# ───────────────────────── configuração ─────────────────────────

def test_config_idioma_valores(tmp_path, monkeypatch):
    from basalt import config
    from basalt.config import ConfigInvalida
    from conftest import toml_de

    def carregar(idioma=None, validar=True):
        t = toml_de(0, tmp_path / "dados")
        if idioma is not None:
            t = t.replace("[basalt]\n", f'[basalt]\nidioma = "{idioma}"\n', 1)
        p = tmp_path / "basalt.toml"
        p.write_text(t, encoding="utf-8")
        return config.carregar(p, validar=validar)

    assert carregar().idioma == "pt"                                 # por omissão: português, como sempre
    for v in ("pt", "en", "auto"):
        c = carregar(v)                                              # carregar() já valida
        assert c.idioma == v
        c.validar()                                                  # não levanta
    with pytest.raises(ConfigInvalida, match="idioma"):
        carregar("xx")
    with pytest.raises(ConfigInvalida, match="idioma"):
        carregar("xx", validar=False).validar()
    monkeypatch.setenv("BASALT_IDIOMA", "en")                        # a variável de ambiente manda mais que o toml
    assert carregar("pt").idioma == "en"
    assert carregar().idioma == "en"
    monkeypatch.setenv("BASALT_IDIOMA", "klingon")
    with pytest.raises(ConfigInvalida, match="idioma"):
        carregar("pt")


@pytest.mark.parametrize("configurado, cabecalho, esperado", [
    ("pt", "en-US", "pt"),                                           # «pt» e «en» mandam sempre
    ("en", "pt-PT", "en"),
    ("auto", "en-US,en;q=0.9,pt;q=0.5", "en"),
    ("auto", "pt-PT,pt;q=0.9,en;q=0.8", "pt"),
    ("auto", "", "pt"),
    ("auto", "fr", "pt"),                                            # nenhuma das duas → português
    ("auto", "en;q=0.5,pt;q=0.5", "en"),                             # empate em q: inglês (quem o pede, quer-o)
    ("auto", "pt;q=0.5,en;q=0.5", "en"),
    ("auto", "en;q=0,pt;q=0.1", "pt"),                               # q=0 = «não aceito»
    ("auto", "fr,en-GB;q=0.8", "en"),
    ("auto", "EN-us", "en"),                                         # maiúsculas
    ("auto", "en;q=lixo,pt", "pt"),                                  # q inválido: essa etiqueta ignora-se
    ("xx", "en", "pt"),                                              # valor desconhecido nunca estraga a página
])
def test_idioma_do_pedido(configurado, cabecalho, esperado):
    from basalt.web import idioma_do_pedido
    assert idioma_do_pedido(configurado, cabecalho) == esperado
    assert idioma_do_pedido(configurado, None) == ("en" if configurado == "en" else "pt")


def test_indice_no_idioma():
    from basalt.web import indice_no_idioma
    original = INDEX.read_bytes()
    assert indice_no_idioma(original, "pt") == original              # o português fica byte a byte igual
    em = indice_no_idioma(original, "en")
    assert b'<html lang="en" data-idioma="en">' in em
    assert b'<html lang="pt-PT"' not in em
    assert em.count(b"<html ") == 1
    assert len(original.replace(b'<html lang="pt-PT">', b"", 1)) == len(em.replace(b'<html lang="en" data-idioma="en">', b"", 1))
    entrar = ENTRAR.read_bytes()                                     # o ecrã de entrada usa lang="pt" (sem -PT): a mesma regra
    assert indice_no_idioma(entrar, "pt") == entrar
    em = indice_no_idioma(entrar, "en")
    assert b'<html lang="en" data-idioma="en">' in em and b'<html lang="pt">' not in em and em.count(b"<html ") == 1


def test_servir_indice_respeita_idioma(servidor_local):
    s = servidor_local()
    s.cfg.idioma = "auto"
    r = s.get("/", cabecalhos={"Accept-Language": "en-US"})
    assert r.codigo == 200 and b'<html lang="en"' in r.corpo
    assert "Accept-Language" in r.cabecalhos.get("Vary", "")
    r = s.get("/", cabecalhos={"Accept-Language": "pt-PT"})
    assert r.codigo == 200 and b'<html lang="pt-PT">' in r.corpo
    assert "Accept-Language" in r.cabecalhos.get("Vary", "")
    r = s.get("/")                                                   # sem cabeçalho: português
    assert b'<html lang="pt-PT">' in r.corpo
    s.cfg.idioma = "pt"                                              # fixo em português: o cabeçalho não conta
    r = s.get("/", cabecalhos={"Accept-Language": "en-US"})
    assert b'<html lang="pt-PT">' in r.corpo and "Vary" not in r.cabecalhos
    assert r.corpo == INDEX.read_bytes()
    s.cfg.idioma = "en"
    r = s.get("/", cabecalhos={"Accept-Language": "pt-PT"})
    assert b'<html lang="en"' in r.corpo and "Vary" not in r.cabecalhos


def test_servir_ecra_de_entrada_respeita_idioma(servidor_local):
    s = servidor_local(modo="senha")                                 # /entrar só existe no modo senha; é público (sem sessão)
    s.cfg.idioma = "auto"
    r = s.get("/entrar", email=None, cabecalhos={"Accept-Language": "en-US"})
    assert r.codigo == 200 and b'<html lang="en" data-idioma="en">' in r.corpo and "Accept-Language" in r.cabecalhos.get("Vary", "")
    r = s.get("/entrar", email=None, cabecalhos={"Accept-Language": "pt-PT"})
    assert r.codigo == 200 and b'<html lang="pt">' in r.corpo
    s.cfg.idioma = "pt"
    r = s.get("/entrar", email=None, cabecalhos={"Accept-Language": "en-US"})
    assert r.corpo == ENTRAR.read_bytes() and "Vary" not in r.cabecalhos
    s.cfg.idioma = "en"
    r = s.get("/entrar", email=None, cabecalhos={"Accept-Language": "pt-PT"})
    assert b'<html lang="en" data-idioma="en">' in r.corpo


@pytest.mark.lento
@pytest.mark.parametrize("idioma, titulo, botao, senha, erro_esperado", [
    ("en", "Basalt — sign in", "Sign in", "Password", "Wrong e-mail or password."),
    ("pt", "Basalt — entrar", "Entrar", "Palavra-passe", "E-mail ou senha errados."),
])
def test_ecra_de_entrada_no_idioma(servidor_local, contexto, idioma, titulo, botao, senha, erro_esperado):
    """a página de entrada traduz o que mostra E a resposta (em português) do servidor a uma senha errada."""
    s = servidor_local(modo="senha")
    s.cfg.idioma = idioma
    page = contexto().new_page()
    page.goto(f"http://127.0.0.1:{s.porta}/entrar")
    assert page.evaluate("document.documentElement.lang") == idioma
    assert page.title() == titulo
    assert page.inner_text("#ok") == botao
    assert page.inner_text("label[for=senha]") == senha
    page.fill("#email", "ninguem@exemplo.pt")
    page.fill("#senha", "senha-errada-123")
    page.click("#ok")
    esperar(page, "document.getElementById('erro').textContent.length > 0")
    assert page.inner_text("#erro") == erro_esperado


# ───────────────────────── cobertura do dicionário ─────────────────────────

def _desescapar(s):
    mapa = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def um(m):
        c = m.group(1)
        if c[0] == "u":
            return chr(int(c[1:], 16))
        if c[0] == "x":
            return chr(int(c[1:], 16))
        return mapa.get(c, c)
    return re.sub(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", um, s, flags=re.S)


def _scripts(html):
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)]


CHAMADA = re.compile(r"""(?<![\w$])(?:t|tf|basaltT|basaltTf)\(\s*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')""")


def _chaves_usadas(html):
    """todas as cadeias literais passadas a t()/tf()/basaltT()/basaltTf() nos <script> da página."""
    usadas = set()
    for js in _scripts(html):
        for m in CHAMADA.finditer(js):
            usadas.add(_desescapar(m.group(1) if m.group(1) is not None else m.group(2)))
    return usadas


class _Marcacao(HTMLParser):
    """os textos da marcação estática que o `traduzirMarcacao()` traduz: nós de texto (fora de script/style), os atributos
    placeholder/title/aria-label/alt e o value dos botões."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.textos, self.fundo = set(), 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.fundo += 1
        a = dict(attrs)
        for nome in ("placeholder", "title", "aria-label", "alt"):
            if a.get(nome):
                self.textos.add(a[nome].strip())
        if (tag == "button" or (tag == "input" and a.get("type") in ("button", "submit"))) and a.get("value"):
            self.textos.add(a["value"].strip())
        if tag == "meta" and a.get("name") == "description" and a.get("content"):
            self.textos.add(a["content"].strip())

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.fundo:
            self.fundo -= 1

    def handle_data(self, data):
        if not self.fundo and data.strip():
            self.textos.add(data.strip())


def _marcacao(html):
    p = _Marcacao()
    p.feed(html)
    return {t for t in p.textos if re.search(r"[^\W\d_]", t)}      # sem letras (ícones, números) não há nada para traduzir


def _dicionario_en(html):
    m = re.search(r"const EN = (\{.*?\n\});", html, re.S)
    assert m, "não encontro `const EN = {…};` na página"
    return json.loads(m.group(1))


def test_cobertura_do_dicionario():
    html = INDEX.read_text(encoding="utf-8")
    en = _dicionario_en(html)
    usadas = _chaves_usadas(html)
    estaticas = {t for t in _marcacao(html) if t not in IGUAIS}
    assert len(usadas) > 300 and len(estaticas) > 10, f"a extracção encontrou pouco ({len(usadas)} chaves, {len(estaticas)} textos)"
    faltam = sorted(c for c in usadas | estaticas if c not in en)
    assert not faltam, f"{len(faltam)} textos sem tradução em EN:\n" + "\n".join("  " + repr(c) for c in faltam[:40])
    vazios = [k for k, v in en.items() if not isinstance(v, str) or not v.strip()]
    assert not vazios, f"traduções vazias: {vazios[:10]}"
    iguais = [k for k, v in en.items() if k == v and k not in IGUAIS]
    assert not iguais, f"traduções iguais ao português (esquecidas?): {iguais[:10]}"
    acentuadas = [f"{k!r} → {v!r}" for k, v in en.items() if re.search("[ãõçáéíóúâêô]", v)]
    assert not acentuadas, "letras portuguesas numa tradução:\n" + "\n".join(acentuadas[:10])
    marcadores = [k for k, v in en.items() if sorted(re.findall(r"\{(\w+)\}", k)) != sorted(re.findall(r"\{(\w+)\}", v))]
    assert not marcadores, f"os {{marcadores}} não coincidem: {marcadores[:10]}"
    sobras = sorted(set(en) - usadas - estaticas - IGUAIS)
    assert len(sobras) <= 12, f"entradas do dicionário que nada usa ({len(sobras)}): {sobras[:10]}"


# ───────────────────────── a página, no navegador ─────────────────────────

# o que o utilizador vê: nós de texto visíveis (fora dos blocos de nota/código/editor) + os atributos que ele lê (title, placeholder…)
VISIVEL = """() => {
  const fora = {SCRIPT: 1, STYLE: 1, NOSCRIPT: 1};
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const texto = [], attrs = [];
  while (w.nextNode()) {
    const n = w.currentNode, p = n.parentElement;
    if (!p || fora[p.nodeName] || !n.nodeValue.trim() || p.closest("textarea, pre, code, .md, .nota-corpo")) continue;
    if (p.getClientRects().length === 0) continue;
    texto.push(n.nodeValue.trim());
  }
  document.querySelectorAll("[title],[placeholder],[aria-label]").forEach(el => {
    if (el.getClientRects().length === 0) return;
    ["title", "placeholder", "aria-label"].forEach(a => { const v = el.getAttribute(a); if (v) attrs.push(v.trim()); });
  });
  return {texto, attrs};
}"""

ROTAS = ["#/", "#/notas", "#/nota/n1", "#/grafo", "#/entrada", "#/arrumar", "#/recentes", "#/diario"]
RE_PT = re.compile(r"ção|ções|ões|[ãçáéíóúâêô]", re.I)


def _abrir(base, contexto, idioma):
    ESTADO["idioma"] = idioma
    page = contexto().new_page()
    page.goto(base + "/")
    assert pronta(page)
    esperar(page, "document.body.innerText.length > 50")
    return page


def _ver(page, rota, ajuda=False):
    """o que se vê (texto + atributos) numa rota; `ajuda`: com o painel da ajuda aberto."""
    page.evaluate("r => { location.hash = r; }", rota)
    page.wait_for_timeout(500)
    if ajuda:
        page.evaluate("document.getElementById('ajuda-btn').click()")   # por JS: o fundo da ajuda tapa o botão
        page.wait_for_timeout(300)
    v = page.evaluate(VISIVEL)
    if ajuda:
        page.evaluate("document.getElementById('ajuda-btn').click()")   # por JS: o fundo da ajuda tapa o botão
    return v["texto"] + v["attrs"]


@pytest.mark.lento
def test_pagina_em_ingles(base, contexto):
    page = _abrir(base, contexto, "en")
    assert page.evaluate("document.documentElement.lang") == "en"
    assert page.evaluate("document.documentElement.getAttribute('data-idioma')") == "en"
    inicio = _ver(page, "#/")
    conhecidos = ["Home", "Notes", "Graph", "Inbox", "Tidy up", "Recent", "Journal", "Toggle theme", "Help for this page",
                  "Jump to note", "Save an idea", "Latest changes"]
    achados = [c for c in conhecidos if c in inicio]
    assert len(achados) >= 8, f"a página em inglês devia ter rótulos ingleses; no que se vê achei só {achados}:\n{inicio}"
    portugues = {}
    for rota in ROTAS:
        for ajuda in (False, True):
            for t in _ver(page, rota, ajuda):
                if RE_PT.search(t):
                    portugues.setdefault(t, rota)
    assert not portugues, "texto visível ainda em português com idioma=en:\n" + "\n".join(f"  {r}: {t!r}" for t, r in list(portugues.items())[:20])
    assert not RE_PT.search(page.evaluate("document.title"))


@pytest.mark.lento
def test_pagina_em_portugues_continua_igual(base, contexto):
    page = _abrir(base, contexto, "pt")
    assert page.evaluate("document.documentElement.lang") == "pt-PT"
    assert page.evaluate("document.documentElement.getAttribute('data-idioma')") is None
    inicio = _ver(page, "#/")
    conhecidos = ["Início", "Notas", "Grafo", "Entrada", "Arrumar", "Recentes", "Diário", "Alternar tema", "Ajuda desta página",
                  "Saltar para nota", "Guardar uma ideia", "Últimas alterações"]
    achados = [c for c in conhecidos if c in inicio]
    assert len(achados) >= 8, f"a página em português devia manter os rótulos; no que se vê achei só {achados}:\n{inicio}"
    ingles = [c for c in ("Tidy up", "Toggle theme", "Save an idea", "Latest changes", "Help for this page") if c in inicio]
    assert not ingles, f"inglês numa página em português: {ingles}"
    assert page.evaluate("document.title") == "Basalt" or "Basalt" in page.evaluate("document.title")
