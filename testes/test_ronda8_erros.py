"""Ronda 8 (nada privado nos erros) — o que NUNCA sai nas mensagens de erro: a chave da IA (ecoada por um servidor 401, ou levada por um redirect para outro
endereço), o texto das notas (os argumentos do motor repetidos num TimeoutExpired → registo), o conteúdo dos pendentes no relatório
da arrumação quando a saída não é um terminal (journal/cron), e caminhos internos nos erros do MCP.

Cada teste falha no código anterior a esta ronda (urlopen de série segue o 302 e leva o Authorization; os `print(str(e))` repetiam
a linha de comandos do motor com o texto da nota; o relatório imprimia a linha do pendente; o MCP devolvia `{e}` inteiro).
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from basalt import arrumar, ia, mcp, web                                  # noqa: E402
from basalt.config import AuthConfig, Config, IAConfig, Utilizador        # noqa: E402
from conftest import FM                                                   # noqa: E402

CHAVE = "sk-segredo-de-teste-0123456789abcdefXYZ"
TOKEN = "sk-proj-Ab3dEf9hIjKlMnOpQrStUvWxYz0123456789AbCd"   # com a forma que SEGREDO_RX reconhece


class _Servidor:
    """um http.server numa thread: `tratar(h)` escreve a resposta; `pedidos` guarda os cabeçalhos de cada pedido recebido."""

    def __init__(self, tratar):
        pai = self

        class H(BaseHTTPRequestHandler):
            def _qualquer(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                pai.pedidos.append(dict(self.headers))
                tratar(self)

            do_GET = do_POST = _qualquer

            def log_message(self, *a):
                pass

        self.pedidos = []
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.porta = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def parar(self):
        self.srv.shutdown()
        self.srv.server_close()


def _responde(h, codigo, corpo=b"", extra=()):
    h.send_response(codigo)
    for k, v in extra:
        h.send_header(k, v)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(corpo)))
    h.end_headers()
    h.wfile.write(corpo)


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv("BASALT_TESTE_CHAVE", CHAVE)
    return CHAVE


def _ia(porta):
    return ia.IA(IAConfig(fornecedor="openai", url=f"http://127.0.0.1:{porta}/v1", modelo_chat="m", chave_env="BASALT_TESTE_CHAVE",
                          embeddings_fornecedor="nenhum", tempo_limite=5))


class TestChaveNosErros:
    def test_401_que_ecoa_o_authorization_nao_chega_a_mensagem(self, chave):
        s = _Servidor(lambda h: _responde(h, 401, json.dumps(
            {"error": {"message": "chave inválida: " + h.headers.get("Authorization", "")}}, ensure_ascii=False).encode("utf-8")))
        try:
            with pytest.raises(ia.IAIndisponivel) as ex:
                _ia(s.porta).chat("olá")
        finally:
            s.parar()
        m = str(ex.value)
        assert "401" in m and "chave foi recusada" in m, m          # ronda 9 (achado G7 da ronda 2): a dica é nossa, não a mensagem dele
        assert CHAVE not in m and CHAVE[-12:] not in m, m

    def test_corpo_de_erro_nao_e_ecoado_cru(self, chave):
        """um proxy que ecoa o pedido inteiro em HTML: a mensagem fica curta, sem marcação e sem a chave."""
        s = _Servidor(lambda h: _responde(h, 502, ("<html><body><h1>Bad gateway</h1><pre>Authorization: " + h.headers.get(
            "Authorization", "") + "</pre>" + "x" * 500 + "</body></html>").encode("utf-8")))
        try:
            with pytest.raises(ia.IAIndisponivel) as ex:
                _ia(s.porta).chat("olá")
        finally:
            s.parar()
        m = str(ex.value)
        assert "502" in m and "problemas" in m and "<" not in m and CHAVE not in m and len(m) < 220, m

    def test_redirect_nao_e_seguido_e_a_chave_nao_sai_para_o_outro_servidor(self, chave):
        alvo = _Servidor(lambda h: _responde(h, 200, json.dumps({"choices": [{"message": {"content": "ok"}}]}, ensure_ascii=False).encode("utf-8")))
        origem = _Servidor(lambda h: _responde(h, 302, b"", [("Location", f"http://127.0.0.1:{alvo.porta}/v1/chat/completions")]))
        try:
            with pytest.raises(ia.IAIndisponivel) as ex:
                _ia(origem.porta).chat("olá")
        finally:
            origem.parar()
            alvo.parar()
        assert "redirec" in str(ex.value).lower() and CHAVE not in str(ex.value), str(ex.value)
        assert alvo.pedidos == [], alvo.pedidos          # o servidor do redirect nunca viu o pedido (nem o Authorization)

    def test_sem_chave_os_erros_normais_continuam_a_dizer_o_que_se_passa(self, monkeypatch):
        monkeypatch.delenv("BASALT_TESTE_CHAVE", raising=False)
        s = _Servidor(lambda h: _responde(h, 404, json.dumps({"error": "modelo m não existe"}, ensure_ascii=False).encode("utf-8")))
        try:
            with pytest.raises(ia.IAIndisponivel) as ex:
                _ia(s.porta).chat("olá")
        finally:
            s.parar()
        assert "404" in str(ex.value) and "o modelo não existem" in str(ex.value), str(ex.value)   # ronda 9: a dica pelo código


class TestTextoDasNotasNoRegisto:
    def test_timeout_do_motor_nao_poe_o_texto_da_nota_no_registo(self, servidor_local, monkeypatch, capsys):
        s = servidor_local()
        s.espaco()
        texto = "ideia privada XK-9931 sobre o barco"

        def falha(args, timeout=60, espaco=None):
            raise subprocess.TimeoutExpired(cmd=[sys.executable, "-m", "basalt.sig", *args], timeout=5)

        monkeypatch.setattr(web, "motor", falha)
        try:
            r = s.pedido("POST", "/api/guarda", {"texto": texto})
        finally:
            s.parar()
        assert r.codigo == 400, (r.codigo, r.corpo)
        out = capsys.readouterr().out
        assert "XK-9931" not in out and texto not in out, out
        assert "TimeoutExpired" in out and "5 s" in out, out

    def test_erro_da_fusao_nao_mostra_segredos_nem_o_tipo(self, memoria, monkeypatch):
        """a mensagem que a página mostra depois de uma fusão falhada: sem o nome da excepção e sem o que pareça uma chave."""
        for n in ("a", "b"):
            memoria.nota(n, FM.format(nome=n) + f"\nnota {n}\n")

        def rebenta(*a, **k):
            raise RuntimeError(f"falhou ao ligar com a chave {TOKEN}")

        monkeypatch.setattr(web.unir, "fundir_llm", rebenta)
        j = {"id": "j1", "manter": "a", "fundir": "b"}
        monkeypatch.setattr(web, "_job_grava", lambda j: None)
        web._correr_fusao(j, memoria.esp)
        assert j["estado"] == "falhou"
        assert "Ab3dEf9h" not in j["mensagem"] and "RuntimeError" not in j["mensagem"], j["mensagem"]

    def test_erro_generico_mascara_o_que_parece_uma_chave(self):
        m = web._erro(ValueError(f"token {TOKEN} recusado"))
        assert m.startswith("ValueError:") and "Ab3dEf9h" not in m and "recusado" in m, m
        assert web._erro(subprocess.CalledProcessError(2, ["x", "texto da nota"])) == "CalledProcessError: o motor saiu com código 2"


class TestRelatorioArrumacao:
    def test_sem_terminal_o_relatorio_nao_mostra_o_conteudo_dos_pendentes(self, memoria, capsys, monkeypatch):
        monkeypatch.setenv("BASALT_GIT", "0")
        p = memoria.real / "privada.md"
        p.write_text(FM.format(nome="privada") + "\n- [ ] ligar ao médico sobre o resultado ZQ-4471\n", encoding="utf-8")
        t = time.time() - (arrumar.ANTIGA_DIAS + 3) * 86400
        os.utime(p, (t, t))
        arrumar.relatorio()
        out = capsys.readouterr().out
        assert "privada" in out and "1 pendente" in out, out
        assert "ZQ-4471" not in out and "médico" not in out, out


class TestErrosDoMCP:
    def test_erro_interno_nao_leva_o_caminho_nem_o_detalhe(self, tmp_path, monkeypatch, capsys):
        u = Utilizador(email="ana@exemplo.pt", espaco="ana", nome="Ana")
        cfg = Config(dados=tmp_path / "dados", escutar="127.0.0.1", auth=AuthConfig(modo="aberto"), utilizadores=[u])
        cfg.validar()
        cfg.espaco_de(u).criar()
        srv = mcp.ServidorMCP(cfg)
        caminho = str(tmp_path / "dados" / "ana" / "real" / "privada.md")

        def rebenta(args, u_):
            raise RuntimeError(f"{caminho}: disco cheio; chave {TOKEN}")

        monkeypatch.setattr(srv, "_t_ler_nota", rebenta)
        r = srv.tratar({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ler_nota", "arguments": {"nome": "x"}}}, u)
        assert r["error"]["code"] == -32603, r
        m = r["error"]["message"]
        assert "RuntimeError" in m and str(tmp_path) not in m and "disco cheio" not in m, m
        err = capsys.readouterr().err
        assert "disco cheio" in err and "Ab3dEf9h" not in err, err    # o detalhe fica no stderr do servidor, sem a chave
