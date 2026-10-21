"""Ronda 9 (achado G4 da ronda 2) — revogar um acesso vale sempre, não só no caso fácil.

Na ronda 8 o servidor passou a reler o basalt.toml quando ele muda. Ficavam três buracos:
  · mudar a senha com o servidor parado (ou mudar e reiniciar antes de qualquer pedido): a sessão gravada não sabia com que
    senha abriu, e o cookie antigo continuava a valer — agora cada sessão guarda a impressão da senha e só vale com ela;
  · apagar o ÚLTIMO utilizador (ou o ficheiro inteiro, ou deixá-lo com um erro): o toml não carregava e ficavam os utilizadores
    anteriores, com token e sessões — agora um toml que mudou e não carrega fecha a porta até voltar a carregar;
  · um pedido que chegasse a meio da releitura via a marca nova, saltava a releitura e entrava com o token já apagado — agora a
    marca só muda depois de lido, dentro do trinco, e esse pedido espera.
Cada teste falha no código anterior a esta ronda pela razão certa (menos os controlos)."""
import os
import threading
from pathlib import Path

from basalt import auth, config
from conftest import SEGREDO_PROXY, toml_de

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
SENHA = "uma senha comprida"


def _toml(tmp_path, modo, utilizadores):
    p = tmp_path / "basalt.toml"
    p.write_text(toml_de(0, tmp_path / "dados", modo, None, utilizadores), encoding="utf-8")
    return p


def _rescrever(p: Path, texto: str):
    """escreve e garante que a marca (mtime) muda mesmo num sistema de ficheiros grosseiro."""
    antes = p.stat().st_mtime_ns
    p.write_text(texto, encoding="utf-8")
    os.utime(p, ns=(antes + 2_000_000, antes + 2_000_000))


def _cookie(aut, tok):
    return aut.utilizador_do_pedido({"Cookie": f"{auth.COOKIE}={tok}"}, "127.0.0.1")


def _bearer(aut, tok):
    return aut.utilizador_do_pedido({"Authorization": "Bearer " + tok}, "127.0.0.1")


def test_senha_mudada_com_o_servidor_parado_termina_a_sessao(tmp_path):
    h_ana, h_bea = auth.hash_senha(SENHA), auth.hash_senha(SENHA)
    p = _toml(tmp_path, "senha", [{"email": ANA, "espaco": "a", "senha_hash": h_ana}, {"email": BEA, "espaco": "b", "senha_hash": h_bea}])
    sessoes = tmp_path / "sessoes.json"
    aut = auth.Autenticador(config.carregar(p), sessoes)
    ok_a, tok_a = aut.entrar(ANA, SENHA, "127.0.0.1")
    ok_b, tok_b = aut.entrar(BEA, SENHA, "127.0.0.1")
    assert ok_a and ok_b and _cookie(aut, tok_a).email == ANA
    # o servidor pára; a senha da ana muda no toml; o servidor volta a arrancar (lê o toml novo e as sessões gravadas)
    _rescrever(p, p.read_text(encoding="utf-8").replace(h_ana, auth.hash_senha("outra senha comprida")))
    depois = auth.Autenticador(config.carregar(p), sessoes)
    assert _cookie(depois, tok_a) is None, "a sessão aberta com a senha antiga continuou a valer depois de reiniciar"
    assert _cookie(depois, tok_b).email == BEA                                 # controlo: a outra conta, sem mudança, continua
    assert depois.entrar(ANA, "outra senha comprida", "127.0.0.1")[0]


def test_sessao_gravada_sem_impressao_da_senha_nao_vale(tmp_path):
    """uma sessão de antes desta ronda (sem a impressão) não se sabe com que senha abriu: pede-se para entrar outra vez."""
    p = _toml(tmp_path, "senha", [{"email": ANA, "espaco": "a", "senha_hash": auth.hash_senha(SENHA)}])
    sessoes = tmp_path / "sessoes.json"
    aut = auth.Autenticador(config.carregar(p), sessoes)
    ok, tok = aut.entrar(ANA, SENHA, "127.0.0.1")
    assert ok
    import json
    d = json.loads(sessoes.read_text(encoding="utf-8"))
    for v in d.values():
        v.pop("cred", None)
    sessoes.write_text(json.dumps(d), encoding="utf-8")
    assert _cookie(auth.Autenticador(config.carregar(p), sessoes), tok) is None


def test_apagar_o_ultimo_utilizador_revoga_token_e_sessao(tmp_path):
    token, h = auth.gerar_token()
    p = _toml(tmp_path, "senha", [{"email": ANA, "espaco": "a", "senha_hash": auth.hash_senha(SENHA), "mcp_token_hash": h}])
    aut = auth.Autenticador(config.carregar(p), tmp_path / "sessoes.json")
    ok, tok = aut.entrar(ANA, SENHA, "127.0.0.1")
    assert ok and _bearer(aut, token).email == ANA and _cookie(aut, tok).email == ANA
    _rescrever(p, toml_de(0, tmp_path / "dados", "senha", None, []))          # o procedimento de PRIVACIDADE.md: apagar o bloco
    assert _bearer(aut, token) is None, "o token do único utilizador, apagado do toml, continuou a entrar"
    assert _cookie(aut, tok) is None
    assert not aut.entrar(ANA, SENHA, "127.0.0.1")[0]


def test_toml_com_erro_ou_apagado_fecha_a_porta_ate_voltar(tmp_path):
    token, h = auth.gerar_token()
    p = _toml(tmp_path, "cabecalho", [{"email": ANA, "espaco": "a", "mcp_token_hash": h}])
    bom = p.read_text(encoding="utf-8")
    aut = auth.Autenticador(config.carregar(p), tmp_path / "sessoes.json")
    proxy = {"X-Basalt-Email": ANA, "X-Basalt-Proxy-Segredo": SEGREDO_PROXY}    # r16: o proxy manda sempre o segredo
    assert aut.utilizador_do_pedido(proxy).email == ANA
    _rescrever(p, bom.replace("[auth]", "[auth\n"))                          # um erro ao editar
    assert aut.utilizador_do_pedido(proxy) is None and _bearer(aut, token) is None, "com o toml estragado entrou-se na mesma"
    _rescrever(p, bom)                                                        # corrigido: volta a abrir, sem reiniciar
    assert aut.utilizador_do_pedido(proxy).email == ANA and _bearer(aut, token).email == ANA
    p.rename(tmp_path / "basalt.toml.fora")                                   # apagado (ou movido)
    assert aut.utilizador_do_pedido(proxy) is None and _bearer(aut, token) is None, "sem o toml entrou-se com os utilizadores antigos"


def test_pedido_a_meio_da_releitura_espera_por_ela(tmp_path, monkeypatch):
    token, h = auth.gerar_token()
    p = _toml(tmp_path, "cabecalho", [{"email": ANA, "espaco": "a", "mcp_token_hash": h}])
    aut = auth.Autenticador(config.carregar(p), tmp_path / "sessoes.json")
    assert _bearer(aut, token).email == ANA
    a_ler, soltar = threading.Event(), threading.Event()
    carregar = config.carregar

    def lento(*a, **k):
        a_ler.set()
        soltar.wait(10)
        return carregar(*a, **k)

    monkeypatch.setattr(auth.config, "carregar", lento)
    _rescrever(p, toml_de(0, tmp_path / "dados", "cabecalho", None, [{"email": ANA, "espaco": "a"}]))   # token apagado
    primeiro = threading.Thread(target=lambda: aut.utilizador_do_pedido({"X-Basalt-Email": ANA, "X-Basalt-Proxy-Segredo": SEGREDO_PROXY}),
                               daemon=True)
    primeiro.start()
    assert a_ler.wait(5)
    resultado = []
    segundo = threading.Thread(target=lambda: resultado.append(_bearer(aut, token)), daemon=True)
    segundo.start()
    segundo.join(0.5)
    entrou_antes = bool(resultado and resultado[0] is not None)
    soltar.set()
    primeiro.join(5)
    segundo.join(5)
    assert not entrou_antes, "um pedido a meio da releitura entrou com o token que o toml novo já tinha apagado"
    assert resultado == [None]
