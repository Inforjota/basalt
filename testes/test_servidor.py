"""O servidor do Basalt arrancado a sério (outro processo, porta local), com a memória em pastas temporárias.

Modo `cabecalho`: o utilizador vem do cabeçalho X-Basalt-Email, posto pelo proxy de login à frente do servidor; as escritas
exigem também X-Basalt: 1 (CSRF). A pesquisa passa por `basalt.sig`, que fala com o fornecedor de IA simulado.
"""
import os
import socket
import subprocess
import sys
import time

import pytest

from conftest import AQUI, EMAIL, FM, SEGREDO_PROXY, Memoria, Servidor, sha
from ollama_falso import OllamaFalso

SEGREDOS = {
    "chave Anthropic": "a chave é sk-ant-" + "a1B2" * 8,
    "chave AWS": "acesso AKIA" + "ABCDEFGHIJKLMNOP",
    "token GitHub": "token ghp_" + "x" * 36,
    "token de bot": "bot 123456789:" + "A" * 35,
    "chave privada": "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE...",
}

BARCOS = FM.format(nome="barcos") + "\nOs barcos do porto precisam de velas novas antes da maré de Outubro.\n"
IMPRESSORA = FM.format(nome="impressora") + "\nA impressora 3D entope o bico com filamento PLA húmido.\n"
RETIDA = FM.format(nome="retida") + "\nVeleiro no porto da ilha: nota retida pelo guarda de segredos.\n"


@pytest.fixture(scope="module")
def ollama():
    o = OllamaFalso()
    yield o
    o.parar()


@pytest.fixture(scope="module")
def srv(tmp_path_factory, ollama):
    mem = Memoria(tmp_path_factory.mktemp("servidor"))
    mem.nota("barcos", BARCOS)
    mem.nota("impressora", IMPRESSORA)
    mem.nota("retida", RETIDA, copia=False)          # na real mas não na cópia = retida pelo 2vias → excluída da web
    s = Servidor(mem, ollama, indexar=True)
    yield s
    s.parar()


def nova(srv, nome, texto=None):
    """nota nova só na cópia (como se tivesse sido criada pela web e ainda não levada para a real)."""
    texto = texto if texto is not None else FM.format(nome=nome) + "\nlinha 1\nlinha 2\n"
    (srv.mem.copia / f"{nome}.md").write_text(texto, encoding="utf-8")
    return texto


# ───────────────────────── autenticação ─────────────────────────
class TestAutenticacao:
    @pytest.mark.parametrize("caminho", ["/api/notas", "/api/nota?nome=barcos", "/api/estado", "/api/procura?q=barcos",
                                         "/api/pacote", "/", "/sw.js", "/static/sw.js"])
    def test_leitura_sem_cabecalho_de_email_e_recusada(self, srv, caminho):
        r = srv.get(caminho, email=None)
        assert r.codigo == 401
        assert "erro" in r.json
        assert b"velas novas" not in r.corpo

    @pytest.mark.parametrize("email", ["outro@exemplo.pt", "geral@exemplo.pt.evil.pt", "geral@exemplo.co", "x-geral@exemplo.pt"])
    def test_email_diferente_e_recusado(self, srv, email):
        assert srv.get("/api/notas", email=email).codigo == 403

    def test_cabecalho_vazio_conta_como_sem_cabecalho(self, srv):
        assert srv.get("/api/notas", email="").codigo == 401

    def test_email_certo_com_maiusculas_e_espacos_e_aceite(self, srv):
        assert srv.get("/api/notas", email="  Geral@Exemplo.PT ").codigo == 200

    def test_manifesto_e_icones_sao_publicos_mas_sem_dados(self, srv):
        for caminho in ("/manifest.webmanifest", "/favicon.ico", "/icon-192.png"):
            assert srv.get(caminho, email=None).codigo == 200, caminho

    def test_gravar_sem_email_e_recusado_e_nao_mexe_na_nota(self, srv):
        texto = nova(srv, "auth_put")
        r = srv.pedido("PUT", srv.nota_url("auth_put"), {"texto": "estragada", "base_hash": sha(texto)}, email=None)
        assert r.codigo == 401
        assert srv.mem.ler("copia", "auth_put") == texto

    def test_criar_sem_email_e_recusado(self, srv):
        r = srv.pedido("POST", "/api/nota", {"nome": "auth_post", "texto": "x"}, email=None)
        assert r.codigo == 401
        assert not (srv.mem.copia / "auth_post.md").exists()

    def test_escrita_sem_cabecalho_csrf_e_recusada(self, srv):
        texto = nova(srv, "auth_csrf")
        r = srv.pedido("PUT", srv.nota_url("auth_csrf"), {"texto": "x", "base_hash": sha(texto)}, csrf=False)
        assert r.codigo == 403 and "X-Basalt" in r.json["erro"]
        r = srv.pedido("POST", "/api/nota", {"nome": "auth_csrf2", "texto": "x"}, csrf=False)
        assert r.codigo == 403
        assert srv.mem.ler("copia", "auth_csrf") == texto and not (srv.mem.copia / "auth_csrf2.md").exists()

    def test_modo_aberto_so_arranca_em_127_0_0_1(self, tmp_path):
        toml = tmp_path / "basalt.toml"
        toml.write_text('[basalt]\ndados = "%s"\nporta = 1\nescutar = "0.0.0.0"\n[auth]\nmodo = "aberto"\n' % (tmp_path / "d"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(AQUI / "lanca_servidor.py"), str(toml)], capture_output=True, text=True, timeout=20,
                           env=dict(os.environ, PYTHONPATH=str(AQUI.parent), HOME=str(tmp_path)))
        assert r.returncode != 0
        assert "modo aberto só com escutar = 127.0.0.1" in r.stderr


# ───────────────────────── ler ─────────────────────────
class TestLer:
    def test_ler_nota_devolve_texto_completo_e_hash(self, srv):
        r = srv.get(srv.nota_url("barcos"))
        assert r.codigo == 200
        d = r.json
        assert d["texto"] == BARCOS and d["hash"] == sha(BARCOS) and d["editavel"] is True
        assert d["meta"]["name"] == "barcos"

    def test_lista_de_notas_tem_as_da_copia_e_nao_as_excluidas(self, srv):
        nomes = {n["nome"] for n in srv.get("/api/notas").json}
        assert {"barcos", "impressora"} <= nomes
        assert "retida" not in nomes

    def test_nota_excluida_nao_se_le(self, srv):
        assert srv.get(srv.nota_url("retida")).codigo == 404

    def test_nota_inexistente_da_404(self, srv):
        assert srv.get(srv.nota_url("nao_existe")).codigo == 404

    def test_pacote_offline_traz_texto_e_hash_de_cada_nota(self, srv):
        notas = {n["nome"]: n for n in srv.get("/api/pacote").json["notas"]}
        assert notas["barcos"]["hash"] == sha(BARCOS) and "retida" not in notas


# ───────────────────────── gravar (PUT com hash) ─────────────────────────
class TestGravar:
    def test_gravar_com_o_hash_certo_grava_e_devolve_o_hash_novo(self, srv):
        texto = nova(srv, "g_ok")
        novo = texto + "linha 3\n"
        r = srv.pedido("PUT", srv.nota_url("g_ok"), {"texto": novo, "base_hash": sha(texto)})
        assert r.codigo == 200 and r.json == {"ok": True, "hash": sha(novo)}
        assert srv.mem.ler("copia", "g_ok") == novo
        assert srv.get(srv.nota_url("g_ok")).json["hash"] == sha(novo)

    def test_gravar_com_hash_antigo_da_conflito_e_nao_sobrepoe(self, srv):
        antes = nova(srv, "g_conf")
        actual = antes + "mudança feita noutro sítio\n"
        (srv.mem.copia / "g_conf.md").write_text(actual, encoding="utf-8")
        r = srv.pedido("PUT", srv.nota_url("g_conf"), {"texto": antes + "a minha\n", "base_hash": sha(antes)})
        assert r.codigo == 409
        assert r.json["hash_actual"] == sha(actual)
        assert srv.mem.ler("copia", "g_conf") == actual

    def test_conflito_ao_gravar_devolve_o_texto_actual(self, srv):
        antes = nova(srv, "g_conf_texto")
        actual = antes + "mudança feita noutro sítio\n"
        (srv.mem.copia / "g_conf_texto.md").write_text(actual, encoding="utf-8")
        r = srv.pedido("PUT", srv.nota_url("g_conf_texto"), {"texto": "x", "base_hash": sha(antes)})
        assert r.codigo == 409
        assert r.json.get("actual", r.json.get("texto")) == actual

    def test_nao_grava_por_cima_de_mudanca_da_memoria_real_ainda_por_trazer(self, srv):
        texto = FM.format(nome="g_pend") + "\nv0\n"
        srv.mem.nota("g_pend", texto)
        (srv.mem.real / "g_pend.md").write_text(texto + "do agente\n", encoding="utf-8")
        r = srv.pedido("PUT", srv.nota_url("g_pend"), {"texto": texto + "da web\n", "base_hash": sha(texto)})
        assert r.codigo == 409 and r.json.get("pendente") is True
        assert srv.mem.ler("copia", "g_pend") == texto

    def test_gravar_nota_que_nao_existe_nao_a_cria(self, srv):
        r = srv.pedido("PUT", srv.nota_url("g_nao_existe"), {"texto": "x", "base_hash": sha("")})
        assert r.codigo == 404
        assert not (srv.mem.copia / "g_nao_existe.md").exists()

    def test_corpo_que_nao_e_json_da_400(self, srv):
        texto = nova(srv, "g_json")
        r = srv.pedido("PUT", srv.nota_url("g_json"), bruto=b"isto nao e json")
        assert r.codigo == 400
        assert srv.mem.ler("copia", "g_json") == texto

    @pytest.mark.parametrize("corpo", [
        pytest.param(lambda h: {"base_hash": h}, id="sem-texto"),
        pytest.param(lambda h: {"base_hash": h, "texto": None}, id="texto-null"),
    ])
    def test_gravar_sem_texto_valido_nao_estraga_a_nota(self, srv, corpo):
        nome = f"g_sem_texto_{int(time.time() * 1e6)}"
        texto = nova(srv, nome)
        r = srv.pedido("PUT", srv.nota_url(nome), corpo(sha(texto)))
        assert r.codigo == 400
        assert srv.mem.ler("copia", nome) == texto


# ───────────────────────── criar ─────────────────────────
class TestCriar:
    def test_criar_nota_nova(self, srv):
        r = srv.pedido("POST", "/api/nota", {"nome": "c_nova", "texto": "ideia nova"})
        assert r.codigo == 201 and r.json == {"ok": True, "nome": "c_nova"}
        assert srv.mem.ler("copia", "c_nova") == "ideia nova"
        assert srv.get(srv.nota_url("c_nova")).json["texto"] == "ideia nova"

    def test_criar_com_nome_que_ja_existe_na_copia_da_409_e_nao_sobrepoe(self, srv):
        nova(srv, "c_dup", "original")
        r = srv.pedido("POST", "/api/nota", {"nome": "c_dup", "texto": "por cima"})
        assert r.codigo == 409 and srv.mem.ler("copia", "c_dup") == "original"

    def test_criar_com_nome_que_ja_existe_na_memoria_real_da_409(self, srv):
        (srv.mem.real / "c_dup_real.md").write_text("da real", encoding="utf-8")
        r = srv.pedido("POST", "/api/nota", {"nome": "c_dup_real", "texto": "x"})
        assert r.codigo == 409 and not (srv.mem.copia / "c_dup_real.md").exists()

    @pytest.mark.parametrize("corpo", [{"nome": "c_sem_texto"}, {"nome": "c_sem_texto", "texto": None}])
    def test_criar_sem_texto_e_recusado(self, srv, corpo):
        assert srv.pedido("POST", "/api/nota", corpo).codigo == 400
        assert not (srv.mem.copia / "c_sem_texto.md").exists()

    def test_criar_com_nome_reservado_e_recusado(self, srv):
        r = srv.pedido("POST", "/api/nota", {"nome": "index", "texto": "a minha nota"})
        assert r.codigo == 400, f"respondeu {r.codigo}; e depois a leitura dá {srv.get(srv.nota_url('index')).codigo}"


# ───────────────────────── procurar ─────────────────────────
class TestProcurar:
    def test_procura_por_significado_encontra_a_nota_sem_palavras_em_comum(self, srv):
        r = srv.get("/api/procura?q=" + "navio%20mar")
        assert r.codigo == 200
        nomes = [x["nome"] for x in r.json]
        assert nomes and nomes[0] == "barcos"
        assert all(set(x) >= {"fonte", "ref", "nome", "titulo", "sim", "trecho"} for x in r.json)

    def test_procura_nao_devolve_notas_excluidas(self, srv):
        nomes = [x["nome"] for x in srv.get("/api/procura?q=veleiro%20porto%20ilha").json]
        assert "barcos" in nomes and "retida" not in nomes

    def test_procura_sem_nada_de_confianca_devolve_lista_vazia(self, srv):
        assert srv.get("/api/procura?q=zebra%20quantica%20xpto").json == []

    def test_procura_vazia_devolve_lista_vazia(self, srv):
        assert srv.get("/api/procura?q=%20%20").json == []


# ───────────────────────── apagar ─────────────────────────
class TestApagar:
    def test_nao_ha_forma_de_apagar_uma_nota_pela_web(self, srv):
        texto = nova(srv, "a_nao_apagar")
        r = srv.pedido("DELETE", srv.nota_url("a_nao_apagar"))
        assert r.codigo in (404, 405, 501)
        assert srv.mem.ler("copia", "a_nao_apagar") == texto

    def test_apagar_ideia_da_entrada_guarda_registo_para_desfazer_engano(self, srv):
        inbox = srv.mem.partilhada / "entrada.jsonl"
        inbox.write_text('{"t": "2026-09-27T10:00:00", "de": "web", "texto": "ideia a apagar", "arquivado": false}\n'
                         '{"t": "2026-09-27T11:00:00", "de": "web", "texto": "ideia a ficar", "arquivado": false}\n',
                         encoding="utf-8")
        r = srv.pedido("POST", "/api/entrada/apagar", {"t": "2026-09-27T10:00:00"})
        assert r.codigo == 200
        assert "ideia a apagar" not in inbox.read_text() and "ideia a ficar" in inbox.read_text()
        assert "ideia a apagar" in (srv.mem.partilhada / "entrada-apagadas.jsonl").read_text()
        assert srv.pedido("POST", "/api/entrada/apagar", {"t": "2026-09-27T10:00:00"}).codigo == 404


# ───────────────────────── nomes de ficheiro maliciosos ─────────────────────────
MALICIOSOS = ["../fora", "..%2Ffora", "../../etc/passwd", "/etc/passwd", "barcos.md", "barcos.txt", "fora/../barcos",
              "a/b", "outro:../../fora", "root:/etc/passwd", "desconhecido:barcos", "MEMORY", "index", "CONFIG",
              ".oculta", "barcos\x00", "x" * 121, "", "..", "~/fora"]


class TestNomesMaliciosos:
    @pytest.fixture(autouse=True)
    def ficheiro_fora(self, srv):
        (srv.mem.base / "fora.md").write_text("FORA DA COPIA", encoding="utf-8")
        (srv.mem.copia / "MEMORY.md").write_text("indice", encoding="utf-8")

    def ficheiros(self, srv):
        return sorted(str(p.relative_to(srv.mem.base)) for p in srv.mem.base.rglob("*")
                      if p.is_file() and p.suffix != ".log" and "offline" not in p.parts[-1])

    @pytest.mark.parametrize("nome", MALICIOSOS)
    def test_ler_com_nome_malicioso_da_404(self, srv, nome):
        r = srv.get(srv.nota_url(nome))
        assert r.codigo == 404
        assert b"FORA DA COPIA" not in r.corpo and b"root:" not in r.corpo

    @pytest.mark.parametrize("nome", MALICIOSOS)
    def test_gravar_com_nome_malicioso_e_recusado_e_nao_escreve_nada(self, srv, nome):
        antes = self.ficheiros(srv)
        r = srv.pedido("PUT", srv.nota_url(nome), {"texto": "ESCRITO", "base_hash": sha("FORA DA COPIA")})
        assert r.codigo in (400, 403, 404)
        assert self.ficheiros(srv) == antes
        assert (srv.mem.base / "fora.md").read_text() == "FORA DA COPIA"

    @pytest.mark.parametrize("nome", [n for n in MALICIOSOS if n != "index"] + ["Maiusculas", "com espaco", "com-hifen", "a", "nota.md.md", "x" * 101])
    def test_criar_com_nome_malicioso_ou_invalido_da_400(self, srv, nome):
        antes = self.ficheiros(srv)
        r = srv.pedido("POST", "/api/nota", {"nome": nome, "texto": "ESCRITO"})
        assert r.codigo == 400
        assert self.ficheiros(srv) == antes

    @pytest.mark.parametrize("caminho", ["/static/../basalt/web.py", "/static/%2e%2e/basalt/web.py",
                                         "/static/../../fora.md", "/static//etc/passwd", "/basalt/web.py", "/pytest.ini"])
    def test_ficheiros_estaticos_nao_saem_da_pasta_static(self, srv, caminho):
        r = srv.get(caminho)
        assert r.codigo == 404
        assert b"BaseHTTPRequestHandler" not in r.corpo and b"root:" not in r.corpo


# ───────────────────────── segredos ─────────────────────────
class TestSegredos:
    @pytest.mark.parametrize("segredo", list(SEGREDOS.values()), ids=list(SEGREDOS))
    def test_gravar_nota_com_segredo_e_recusado(self, srv, segredo):
        nome = f"s_put_{abs(hash(segredo)) % 10 ** 8}"
        texto = nova(srv, nome)
        r = srv.pedido("PUT", srv.nota_url(nome), {"texto": texto + segredo + "\n", "base_hash": sha(texto)})
        assert r.codigo == 400 and "password ou chave" in r.json["erro"]
        assert srv.mem.ler("copia", nome) == texto

    @pytest.mark.parametrize("segredo", list(SEGREDOS.values()), ids=list(SEGREDOS))
    def test_criar_nota_com_segredo_e_recusado(self, srv, segredo):
        nome = f"s_post_{abs(hash(segredo)) % 10 ** 8}"
        r = srv.pedido("POST", "/api/nota", {"nome": nome, "texto": "nota\n" + segredo})
        assert r.codigo == 400 and not (srv.mem.copia / f"{nome}.md").exists()

    def test_guardar_ideia_com_segredo_na_entrada_e_recusado(self, srv):
        r = srv.pedido("POST", "/api/guarda", {"texto": SEGREDOS["chave AWS"]})
        assert r.codigo == 400
        inbox = srv.mem.partilhada / "entrada.jsonl"
        assert not inbox.exists() or "AKIA" not in inbox.read_text()

    def test_sincronizar_edicao_offline_com_segredo_e_recusado(self, srv):
        texto = nova(srv, "s_sinc")
        r = srv.pedido("POST", "/api/sincronizar", {"nome": "s_sinc", "base_texto": texto,
                                                    "texto": texto + SEGREDOS["token GitHub"]})
        assert r.codigo == 400 and srv.mem.ler("copia", "s_sinc") == texto

    @pytest.mark.parametrize("linha", ["senha do router: está no cofre", "a password: pede ao administrador",
                                       "palavra-passe = guardada no cofre", "mudar a password todos os meses",
                                       "pwd: /home/utilizador/basalt", "password: /root/.config/cofre",
                                       "senha do NAS: https://cofre.exemplo.pt/cofre", "password: ver_no_cofre"])
    def test_falar_de_passwords_sem_as_escrever_continua_a_gravar(self, srv, linha):
        nome = f"s_sem_pw_{abs(hash(linha)) % 10 ** 8}"
        texto = nova(srv, nome)
        r = srv.pedido("PUT", srv.nota_url(nome), {"texto": texto + linha + "\n", "base_hash": sha(texto)})
        assert r.codigo == 200

    @pytest.mark.parametrize("linha, e_segredo", [
        ("Validade da senha: 2026-10-06", False), ("a senha expira: 06/10/2026", False), ("password: 09h30 de amanhã", False),
        ("senha: Tr0ub4dor&3", True), ("password = hunter2!", True), ("senha: 2026-10-06x7", True)])
    def test_uma_data_ou_hora_depois_de_senha_nao_e_a_password(self, linha, e_segredo):
        from basalt import web
        assert bool(web.tem_segredo_texto(linha)) is e_segredo

    def test_password_em_claro_escrita_a_mao_nao_e_gravada(self, srv):
        texto = nova(srv, "s_password")
        r = srv.pedido("PUT", srv.nota_url("s_password"),
                       {"texto": texto + "password do router: Tr0ub4dor&3xyz\n", "base_hash": sha(texto)})
        assert r.codigo == 400


# ───────────────────────── sincronizar (edição feita sem internet) ─────────────────────────
class TestSincronizar:
    BASE = FM.format(nome="n") + "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5\n"

    def test_mudancas_em_sitios_diferentes_dao_uma_proposta_de_juncao_que_so_se_grava_com_o_clique(self, srv):
        # a junção linha a linha (git merge-file, sem IA), mesmo limpa, NUNCA se grava
        # sozinha — volta como 409 com proposta_tipo «linhas»; a página grava-a com PUT base_hash = proposta_hash
        actual = self.BASE + "linha 6 dos agentes\n"
        nova(srv, "y_junta", actual)
        teu = self.BASE.replace("linha 1", "linha 1 corrigida")
        r = srv.pedido("POST", "/api/sincronizar", {"nome": "y_junta", "base_texto": self.BASE, "texto": teu})
        assert r.codigo == 409
        d = r.json
        assert d["estado"] == "conflito" and d["proposta_tipo"] == "linhas" and d["actual"] == actual and d["teu"] == teu
        assert "linha 1 corrigida" in d["proposta"] and "linha 6 dos agentes" in d["proposta"]
        # proposta_hash é o hash do texto ACTUAL sobre o qual a proposta foi feita (= base_hash do PUT que a grava): a página
        # só mostra a proposta enquanto proposta_hash === hash da cópia (index.html «temProposta»)
        assert d["proposta_hash"] == d["hash_actual"] == sha(actual)
        assert srv.mem.ler("copia", "y_junta") == actual                      # nada se gravou
        r2 = srv.pedido("PUT", srv.nota_url("y_junta"), {"texto": d["proposta"], "base_hash": d["proposta_hash"]})
        assert r2.codigo == 200, r2.corpo
        final = srv.mem.ler("copia", "y_junta")
        assert "linha 1 corrigida" in final and "linha 6 dos agentes" in final

    def test_mesmas_linhas_dao_conflito_com_o_texto_actual_e_o_hash_dele(self, srv):
        actual = self.BASE.replace("linha 3", "linha 3 dos agentes")
        nova(srv, "y_conf", actual)
        teu = self.BASE.replace("linha 3", "linha 3 da pessoa")
        r = srv.pedido("POST", "/api/sincronizar", {"nome": "y_conf", "base_texto": self.BASE, "texto": teu})
        assert r.codigo == 409
        d = r.json
        assert d["estado"] == "conflito" and d["actual"] == actual and d["hash_actual"] == sha(actual) and d["teu"] == teu
        assert srv.mem.ler("copia", "y_conf") == actual
        guardada = srv.mem.partilhada / "offline-versoes" / d["copia"]
        assert guardada.read_text(encoding="utf-8") == teu


# ───────────────────────── robustez do HTTP ─────────────────────────
class TestPedidosEstranhos:
    def test_corpo_demasiado_grande_e_recusado(self, srv):
        r = srv.pedido("POST", "/api/nota", cabecalhos={"Content-Length": "3000000"}, bruto=b"{}")
        assert r.codigo == 400

    def test_content_length_negativo_responde_depressa(self, srv):
        s = socket.create_connection(("127.0.0.1", srv.porta), timeout=3)
        s.sendall(b"POST /api/nota HTTP/1.1\r\nHost: x\r\nX-Basalt-Email: " + EMAIL.encode() +
                  b"\r\nX-Basalt-Proxy-Segredo: " + SEGREDO_PROXY.encode() +      # r16: como o proxy (sem ele era um 401 logo)
                  b"\r\nX-Basalt: 1\r\nContent-Length: -1\r\n\r\n{}")
        try:
            assert s.recv(100).startswith(b"HTTP/1.")
        except socket.timeout:
            pytest.fail("sem resposta em 3 s")
        finally:
            s.close()
