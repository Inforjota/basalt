"""r21 (auditoria externa à r14, ponto 6) — em modo cabecalho (login Google pelo oauth2-proxy) o botão «Terminar sessão» estava
sempre escondido (a página só o mostrava no modo senha) e o GOOGLE.md §5 descrevia um botão que chamava o /api/sair. Não havia
maneira de sair pela página.
Agora (SAIR_PROXY_v1): `[auth] sair_url` (ex. "/oauth2/sign_out?rd=%2F", o sign-out do oauth2-proxy) — com ele, em modo cabecalho,
o /api/eu diz o endereço e a página mostra o botão, que faz o mesmo de sempre ao browser (envia o que falta, tira a cópia deste
aparelho e guarda à parte o que não subiu) e depois vai a esse endereço (o /api/sair não serve de nada neste modo). Sem
`sair_url`, tudo como antes. Só aceita um caminho deste sítio («/…», não «//…») ou um https://.
Os testes de servidor falham no 68ad69d (r20); os da página falham com a página da r20 (BASALT_STATIC=<r20>/basalt/static)."""
import pytest

from basalt import config
from conftest import ServidorLocal, toml_de
from pagina_falsa import ESTADO, NOTA, esperar, esperar_py, reset
from test_ronda8_contas import GUARDAR, IDB_CRU, _abrir

ANA = "ana@exemplo.pt"
SAIR = "/oauth2/sign_out?rd=%2F"


def _toml(tmp_path, modo, auth_extra):
    texto = toml_de(0, tmp_path / "dados", modo, None, [{"email": ANA, "espaco": "a", "senha_hash": ""}], "")
    return texto.replace(f'modo = "{modo}"\n', f'modo = "{modo}"\n{auth_extra}', 1)


def _servidor(tmp_path, modo, auth_extra):
    import threading

    from basalt import web
    s = object.__new__(ServidorLocal)
    s.dados = tmp_path / "dados"
    s.toml = tmp_path / "basalt.toml"
    tmp_path.mkdir(parents=True, exist_ok=True)
    s.toml.write_text(_toml(tmp_path, modo, auth_extra), encoding="utf-8")
    s.cfg = config.carregar(s.toml)
    s.srv = web.criar_servidor(s.cfg, host="127.0.0.1", porta=0)
    s.porta = s.srv.server_address[1]
    s.fio = threading.Thread(target=s.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    s.fio.start()
    return s


@pytest.fixture
def servidor(tmp_path):
    feitos = []

    def fazer(modo="cabecalho", auth_extra=""):
        s = _servidor(tmp_path / f"s{len(feitos)}", modo, auth_extra)
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


def test_eu_diz_o_sair_url_em_modo_cabecalho(servidor):
    s = servidor(auth_extra=f'sair_url = "{SAIR}"\n')
    eu = s.get("/api/eu", email=ANA, dono=False).json
    assert eu.get("sair_url") == SAIR, eu                            # antes: o /api/eu não dizia por onde se sai


def test_sem_sair_url_o_eu_fica_como_antes(servidor):
    eu = servidor().get("/api/eu", email=ANA, dono=False).json
    assert "sair_url" not in eu and eu["modo"] == "cabecalho"


@pytest.mark.parametrize("mau", ["javascript:alert(1)", "//outro.exemplo/x", "http://basalt.exemplo.pt/oauth2/sign_out",
                                 "oauth2/sign_out", "/oauth2/sign_out\n", "/a b", "https://", "/\\outro.exemplo"])
def test_sair_url_que_nao_e_deste_sitio_nem_https_e_recusado(tmp_path, mau):
    (tmp_path / "basalt.toml").write_text(_toml(tmp_path, "cabecalho", f"sair_url = {mau!r}\n".replace("'", '"')
                                                if "\n" not in mau else 'sair_url = "/oauth2/sign_out\\n"\n'), encoding="utf-8")
    with pytest.raises(config.ConfigInvalida, match="sair_url"):     # antes: aceite (e ignorado)
        config.carregar(tmp_path / "basalt.toml")


@pytest.mark.parametrize("bom", [SAIR, "https://contas.exemplo.pt/sair?volta=https%3A%2F%2Fbasalt.exemplo.pt%2F", "/"])
def test_sair_url_deste_sitio_ou_https_e_aceite(tmp_path, bom):
    (tmp_path / "basalt.toml").write_text(_toml(tmp_path, "cabecalho", f'sair_url = "{bom}"\n'), encoding="utf-8")
    assert config.carregar(tmp_path / "basalt.toml").auth.sair_url == bom


# ───────────────────────── a página ─────────────────────────

VISIVEL = "!!document.getElementById('sair-btn') && !document.getElementById('sair-btn').hidden"


@pytest.mark.lento
def test_pagina_cabecalho_com_sair_url_mostra_o_botao_tira_a_copia_e_vai_ao_proxy(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], modo_auth="cabecalho", sair_url=SAIR, sinc_503=True)
    ESTADO["sinc_solta"].set()
    a = _abrir(contexto(), base)
    r = a.evaluate(GUARDAR, ["EDICAO DA ANA", a.evaluate("window.__t.geracao()")])
    botao = esperar(a, VISIVEL, 5000)
    assert r == "guardou" and botao, f"controlo: guardou {r}; o botão «Terminar sessão» devia aparecer em modo cabecalho com sair_url"
    a.click("#sair-btn")
    saiu = esperar_py(a, lambda: "/oauth2/sign_out" in a.url, 10000)
    assert saiu, f"o botão devia levar ao sign-out do proxy ({SAIR}); ficou em {a.url}"
    a.wait_for_timeout(300)
    cru = a.evaluate(IDB_CRU)
    assert ESTADO["sair"] == 0, "em modo cabecalho o /api/sair não serve de nada (não há sessão do Basalt)"
    assert cru.get("notas") == 0 and cru.get("fila") == [], f"a cópia ficou no browser depois de terminar a sessão: {cru}"
    assert cru.get("guardado") == ["EDICAO DA ANA"] and cru.get("dono") == "", f"o que não subiu devia ficar à parte: {cru}"


@pytest.mark.lento
def test_pagina_cabecalho_sem_sair_url_continua_sem_botao(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], modo_auth="cabecalho")
    a = _abrir(contexto(), base)
    a.wait_for_timeout(800)
    assert a.evaluate("!!window.BASALT_EU") and not a.evaluate(VISIVEL), "sem sair_url o botão fica escondido (como antes)"


@pytest.mark.lento
def test_pagina_modo_senha_continua_a_ir_ao_api_sair(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], modo_auth="senha", sair_url=SAIR)
    a = _abrir(contexto(), base)
    assert esperar(a, VISIVEL, 5000)
    a.click("#sair-btn")
    assert esperar_py(a, lambda: ESTADO["sair"] == 1 and a.url.endswith("/entrar"), 10000), (a.url, ESTADO["sair"])
