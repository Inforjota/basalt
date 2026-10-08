"""basalt.auth: senhas, tokens, sessões, limite de tentativas e os três modos (senha / cabecalho / aberto), mais o que o servidor
faz com eles (cookie, X-Basalt, Bearer, X-Forwarded-For). O modo `cabecalho` pelo HTTP já está em test_servidor.py."""
import hashlib
import json
import stat
import time

import pytest

from basalt import auth
from basalt.auth import Autenticador, hash_senha, hash_token, verificar_senha
from basalt.config import AuthConfig, Config, Utilizador
from conftest import EMAIL

SENHA = "uma senha comprida"
HASH = hash_senha(SENHA)          # 40 ms: calculado uma vez para o ficheiro todo


def cfg_de(tmp_path, modo="senha", utilizadores=None, **auth_cfg):
    c = Config(dados=tmp_path / "dados")
    c.auth = AuthConfig(modo=modo, **auth_cfg)
    c.utilizadores = utilizadores if utilizadores is not None else [Utilizador(email=EMAIL, nome="Ana", senha_hash=HASH, admin=True)]
    return c


def autenticador(tmp_path, **k):
    return Autenticador(cfg_de(tmp_path, **k), tmp_path / "sessoes.json")


def pedido(**cab):
    """os cabeçalhos de um pedido, como um dict (o `self.headers` do handler tem o mesmo .get)."""
    return dict(cab)


def cookie(token):
    return {"Cookie": f"{auth.COOKIE}={token}"}


# ───────────────────────── senhas ─────────────────────────
class TestSenhas:
    def test_o_hash_tem_o_formato_scrypt_com_sal_proprio(self):
        alg, n, r, p, sal, h = HASH.split("$")
        assert (alg, n, r, p) == ("scrypt", "16384", "8", "1")
        assert len(sal) == 24 and len(h) == 44          # 16 e 32 bytes em base64
        assert SENHA not in HASH

    def test_o_mesmo_texto_da_hashes_diferentes_e_ambos_verificam(self):
        outro = hash_senha(SENHA)
        assert outro != HASH
        assert verificar_senha(HASH, SENHA) and verificar_senha(outro, SENHA)

    def test_senha_errada_nao_verifica(self):
        assert not verificar_senha(HASH, SENHA + "x")
        assert not verificar_senha(HASH, "")
        assert not verificar_senha(HASH, SENHA.upper())

    def test_senha_com_acentos_e_emoji(self):
        h = hash_senha("pão e açúcar ☕ 12")
        assert verificar_senha(h, "pão e açúcar ☕ 12")
        assert not verificar_senha(h, "pao e acucar ☕ 12")

    def test_menos_de_8_caracteres_e_recusado_8_serve(self):
        with pytest.raises(ValueError):
            hash_senha("1234567")
        assert verificar_senha(hash_senha("12345678"), "12345678")

    @pytest.mark.parametrize("guardado", ["", "lixo", "scrypt$1$2$3$4", "scrypt$a$b$c$d$e", "bcrypt$16384$8$1$AAAA$AAAA",
                                          "scrypt$16384$8$1$%%%$%%%", None])
    def test_o_que_esta_guardado_e_invalido_nunca_levanta_so_diz_que_nao(self, guardado):
        assert verificar_senha(guardado, SENHA) is False

    def test_o_custo_vem_do_proprio_hash_guardado(self):
        # um hash feito com parâmetros mais leves (de uma versão antiga) continua a verificar-se
        import base64
        sal = b"0123456789abcdef"
        h = hashlib.scrypt(b"antiga123", salt=sal, dklen=32, n=2 ** 10, r=8, p=1)
        guardado = "scrypt$1024$8$1$%s$%s" % (base64.b64encode(sal).decode(), base64.b64encode(h).decode())
        assert verificar_senha(guardado, "antiga123") and not verificar_senha(guardado, "antiga124")

    def test_compara_em_tempo_constante(self, monkeypatch):
        usadas = []
        real = auth.hmac.compare_digest
        monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: usadas.append(1) or real(a, b))
        verificar_senha(HASH, "errada qualquer")
        assert usadas

    def test_um_hash_de_outro_algoritmo_nao_verifica_mesmo_com_o_resto_certo(self):
        assert verificar_senha(HASH, SENHA)
        assert not verificar_senha(HASH.replace("scrypt$", "outro$", 1), SENHA)


class TestTokens:
    def test_o_token_tem_prefixo_e_o_hash_e_o_sha256(self):
        t, h = auth.gerar_token()
        assert t.startswith("bslt_") and len(t) > 40
        assert h == hashlib.sha256(t.encode()).hexdigest() == hash_token(t)
        assert t not in h

    def test_cada_token_e_novo(self):
        assert auth.gerar_token()[0] != auth.gerar_token()[0]


# ───────────────────────── entrar / sair ─────────────────────────
class TestEntrar:
    def test_entrar_com_a_senha_certa_da_um_token_de_sessao(self, tmp_path):
        a = autenticador(tmp_path)
        ok, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        assert ok and len(token) >= 40
        u = a.utilizador_do_pedido(cookie(token))
        assert u is not None and u.email == EMAIL

    def test_e_mail_com_maiusculas_e_espacos_serve(self, tmp_path):
        a = autenticador(tmp_path)
        ok, token = a.entrar("  GERAL@Exemplo.PT ", SENHA, "1.1.1.1")
        assert ok
        assert a.utilizador_do_pedido(cookie(token)).email == EMAIL         # a sessão guarda o e-mail do toml

    def test_email_inexistente_e_senha_errada_dizem_exactamente_o_mesmo(self, tmp_path):
        a = autenticador(tmp_path)
        r1 = a.entrar("ninguem@exemplo.pt", SENHA, "1.1.1.1")
        r2 = a.entrar(EMAIL, "errada errada", "2.2.2.2")
        assert r1 == r2 == (False, "E-mail ou senha errados.")

    def test_utilizador_sem_senha_nao_entra_por_senha_nenhuma(self, tmp_path):
        a = autenticador(tmp_path, utilizadores=[Utilizador(email=EMAIL)])
        for senha in ("", "x", SENHA, "senha-inexistente-0"):
            assert a.entrar(EMAIL, senha, "1.1.1.1") == (False, "E-mail ou senha errados.")

    def test_o_tempo_nao_denuncia_se_o_email_existe(self, tmp_path, monkeypatch):
        a = autenticador(tmp_path)
        n = []
        real = auth.hashlib.scrypt
        monkeypatch.setattr(auth.hashlib, "scrypt", lambda *x, **k: n.append(1) or real(*x, **k))
        a.entrar("ninguem@exemplo.pt", "qualquer coisa", "1.1.1.1")
        inexistente = len(n)
        n.clear()
        a.entrar(EMAIL, "senha errada", "1.1.1.1")
        assert inexistente == len(n) == 1

    def test_a_sessao_guarda_so_o_hash_do_token_com_permissao_600(self, tmp_path):
        a = autenticador(tmp_path)
        _, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        f = tmp_path / "sessoes.json"
        dados = json.loads(f.read_text())
        assert list(dados) == [hash_token(token)]
        assert token not in f.read_text()
        assert dados[hash_token(token)]["email"] == EMAIL
        assert stat.S_IMODE(f.stat().st_mode) == 0o600

    def test_as_sessoes_sobrevivem_a_um_reinicio(self, tmp_path):
        a = autenticador(tmp_path)
        _, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        b = autenticador(tmp_path)
        assert b.utilizador_do_pedido(cookie(token)).email == EMAIL

    def test_sessoes_expiradas_nao_sao_carregadas(self, tmp_path):
        agora = time.time()
        (tmp_path / "sessoes.json").write_text(json.dumps({
            hash_token("velha"): {"email": EMAIL, "expira": agora - 1, "criada": agora - 100, "cred": auth.impressao_credencial(HASH)},
            hash_token("nova"): {"email": EMAIL, "expira": agora + 100, "criada": agora, "cred": auth.impressao_credencial(HASH)}}))
        a = autenticador(tmp_path)
        assert a.utilizador_do_pedido(cookie("velha")) is None
        assert a.utilizador_do_pedido(cookie("nova")).email == EMAIL

    @pytest.mark.parametrize("conteudo", ["", "{", "[]", '{"x": 5}', '{"x": {"email": "a"}}', "lixo"])
    def test_ficheiro_de_sessoes_estragado_e_ignorado(self, tmp_path, conteudo):
        (tmp_path / "sessoes.json").write_text(conteudo)
        a = autenticador(tmp_path)
        assert a.utilizador_do_pedido(cookie("x")) is None
        ok, _ = a.entrar(EMAIL, SENHA, "1.1.1.1")
        assert ok

    def test_a_sessao_dura_as_horas_do_toml(self, tmp_path):
        a = autenticador(tmp_path, sessao_horas=2)
        antes = time.time()
        _, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        s = json.loads((tmp_path / "sessoes.json").read_text())[hash_token(token)]
        assert antes + 2 * 3600 - 1 <= s["expira"] <= time.time() + 2 * 3600 + 1
        assert s["criada"] <= s["expira"] - 2 * 3600 + 1

    def test_uma_sessao_expirada_deixa_de_valer_e_sai_do_ficheiro(self, tmp_path, monkeypatch):
        a = autenticador(tmp_path, sessao_horas=1)
        _, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        real = time.time
        monkeypatch.setattr(auth.time, "time", lambda: real() + 3601)
        assert a.utilizador_do_pedido(cookie(token)) is None
        assert json.loads((tmp_path / "sessoes.json").read_text()) == {}

    def test_sair_apaga_a_sessao_aqui_e_no_ficheiro(self, tmp_path):
        a = autenticador(tmp_path)
        _, t1 = a.entrar(EMAIL, SENHA, "1.1.1.1")
        _, t2 = a.entrar(EMAIL, SENHA, "1.1.1.1")
        a.sair(t1)
        assert a.utilizador_do_pedido(cookie(t1)) is None
        assert a.utilizador_do_pedido(cookie(t2)) is not None
        assert list(json.loads((tmp_path / "sessoes.json").read_text())) == [hash_token(t2)]
        assert autenticador(tmp_path).utilizador_do_pedido(cookie(t1)) is None

    @pytest.mark.parametrize("token", ["", None, "nunca-existiu"])
    def test_sair_com_token_vazio_ou_desconhecido_nao_faz_mal(self, tmp_path, token):
        a = autenticador(tmp_path)
        _, t = a.entrar(EMAIL, SENHA, "1.1.1.1")
        a.sair(token)
        assert a.utilizador_do_pedido(cookie(t)) is not None

    def test_uma_sessao_de_quem_saiu_do_toml_deixa_de_valer(self, tmp_path):
        a = autenticador(tmp_path)
        _, token = a.entrar(EMAIL, SENHA, "1.1.1.1")
        a.cfg.utilizadores.clear()
        assert a.utilizador_do_pedido(cookie(token)) is None

    def test_ao_arrancar_so_se_carregam_as_sessoes_por_expirar(self, tmp_path):
        agora = time.time()
        (tmp_path / "sessoes.json").write_text(json.dumps({
            "viva": {"email": EMAIL, "expira": agora + 3600, "criada": agora},
            "morta": {"email": EMAIL, "expira": agora - 1, "criada": agora - 7200}}))
        assert set(autenticador(tmp_path)._sessoes) == {"viva"}


class TestLimiteDeTentativas:
    def falhar(self, a, ip, n):
        for _ in range(n):
            assert a.entrar(EMAIL, "errada errada", ip) == (False, "E-mail ou senha errados.")

    def test_depois_de_5_falhas_bloqueia_mesmo_com_a_senha_certa(self, tmp_path):
        a = autenticador(tmp_path)
        self.falhar(a, "1.1.1.1", 5)
        ok, msg = a.entrar(EMAIL, SENHA, "1.1.1.1")
        assert not ok and msg == "Demasiadas tentativas; espera 15 minutos."
        assert not (tmp_path / "sessoes.json").exists()

    def test_4_falhas_ainda_deixam_entrar(self, tmp_path):
        a = autenticador(tmp_path)
        self.falhar(a, "1.1.1.1", 4)
        assert a.entrar(EMAIL, SENHA, "1.1.1.1")[0]

    def test_o_limite_e_por_ip(self, tmp_path):
        a = autenticador(tmp_path)
        self.falhar(a, "1.1.1.1", 5)
        assert a.entrar(EMAIL, SENHA, "2.2.2.2")[0]

    def test_o_limite_e_a_janela_vem_do_toml(self, tmp_path):
        a = autenticador(tmp_path, tentativas=2, janela_minutos=3)
        self.falhar(a, "1.1.1.1", 2)
        assert a.entrar(EMAIL, SENHA, "1.1.1.1") == (False, "Demasiadas tentativas; espera 3 minutos.")

    def test_passada_a_janela_volta_a_deixar_tentar(self, tmp_path, monkeypatch):
        a = autenticador(tmp_path, janela_minutos=15)
        self.falhar(a, "1.1.1.1", 5)
        real = time.time
        monkeypatch.setattr(auth.time, "time", lambda: real() + 15 * 60 - 5)
        assert not a.entrar(EMAIL, SENHA, "1.1.1.1")[0]                    # ainda dentro da janela
        monkeypatch.setattr(auth.time, "time", lambda: real() + 15 * 60 + 5)
        assert a.entrar(EMAIL, SENHA, "1.1.1.1")[0]

    def test_entrar_bem_limpa_as_falhas(self, tmp_path):
        a = autenticador(tmp_path)
        self.falhar(a, "1.1.1.1", 4)
        assert a.entrar(EMAIL, SENHA, "1.1.1.1")[0]
        self.falhar(a, "1.1.1.1", 4)                                        # recomeça do zero: 4 mais não bloqueiam
        assert a.entrar(EMAIL, SENHA, "1.1.1.1")[0]

    def test_pedido_bloqueado_nao_conta_como_nova_falha_nem_chega_a_verificar(self, tmp_path, monkeypatch):
        a = autenticador(tmp_path)
        self.falhar(a, "1.1.1.1", 5)
        chamadas = []
        monkeypatch.setattr(auth, "verificar_senha", lambda *x: chamadas.append(x) or True)
        a.entrar(EMAIL, SENHA, "1.1.1.1")
        assert chamadas == [] and len(a._falhas["1.1.1.1"]) == 5


# ───────────────────────── quem é, por modo ─────────────────────────
class TestQuemE:
    def test_modo_senha_sem_cookie_ou_com_cookie_estranho_nao_e_ninguem(self, tmp_path):
        a = autenticador(tmp_path)
        assert a.utilizador_do_pedido(pedido()) is None
        assert a.utilizador_do_pedido(cookie("nunca-existiu")) is None
        assert a.utilizador_do_pedido({"Cookie": "outra=1"}) is None
        assert a.utilizador_do_pedido({"Cookie": "\x00\x01 isto não é um cookie ;;; ==="}) is None

    def test_modo_senha_ignora_o_cabecalho_de_email(self, tmp_path):
        a = autenticador(tmp_path)
        assert a.utilizador_do_pedido({"X-Basalt-Email": EMAIL}) is None

    def test_modo_senha_o_cookie_certo_entre_outros(self, tmp_path):
        a = autenticador(tmp_path)
        _, t = a.entrar(EMAIL, SENHA, "1.1.1.1")
        assert a.utilizador_do_pedido({"Cookie": f"a=1; {auth.COOKIE}={t}; b=2"}).email == EMAIL

    def test_modo_cabecalho_usa_o_cabecalho_configurado(self, tmp_path):
        a = autenticador(tmp_path, modo="cabecalho", cabecalho="X-Utilizador")
        assert a.utilizador_do_pedido({"X-Utilizador": "  GERAL@exemplo.PT "}).email == EMAIL
        assert a.utilizador_do_pedido({"X-Basalt-Email": EMAIL}) is None
        assert a.utilizador_do_pedido({"X-Utilizador": "outro@exemplo.pt"}) is None
        assert a.utilizador_do_pedido({"X-Utilizador": "   "}) is None
        assert a.utilizador_do_pedido(pedido()) is None

    def test_modo_cabecalho_ignora_cookies_de_sessao(self, tmp_path):
        a = autenticador(tmp_path, modo="cabecalho")
        ok, t = autenticador(tmp_path).entrar(EMAIL, SENHA, "1.1.1.1")      # uma sessão que existe (noutro modo)
        assert ok
        assert a.utilizador_do_pedido(cookie(t)) is None

    def test_modo_aberto_e_o_primeiro_utilizador_ou_local(self, tmp_path):
        a = autenticador(tmp_path, modo="aberto", utilizadores=[Utilizador(email="a@x.pt", espaco="um"), Utilizador(email="b@x.pt")])
        assert a.utilizador_do_pedido(pedido()).email == "a@x.pt"
        sem = autenticador(tmp_path, modo="aberto", utilizadores=[]).utilizador_do_pedido(pedido())
        assert sem.email == "local@basalt" and sem.admin and sem.espaco == "principal"

    def test_bearer_vale_em_todos_os_modos(self, tmp_path):
        token, h = auth.gerar_token()
        for modo in ("senha", "cabecalho", "aberto"):
            a = autenticador(tmp_path, modo=modo, utilizadores=[Utilizador(email="x@x.pt", mcp_token_hash="0" * 64),
                                                              Utilizador(email=EMAIL, mcp_token_hash=h)])
            assert a.utilizador_do_pedido({"Authorization": f"Bearer {token}"}).email == EMAIL, modo

    @pytest.mark.parametrize("cab", ["Bearer {t}", "bearer {t}", "BEARER {t}", "  Bearer   {t}  "])
    def test_bearer_maiusculas_e_espacos(self, tmp_path, cab):
        token, h = auth.gerar_token()
        a = autenticador(tmp_path, utilizadores=[Utilizador(email=EMAIL, mcp_token_hash=h)])
        assert a.utilizador_do_pedido({"Authorization": cab.format(t=token)}).email == EMAIL

    def test_bearer_errado_e_recusado_e_nao_cai_para_o_modo(self, tmp_path):
        # no modo aberto qualquer pedido seria «local» — mas quem diz que é agente com um token errado não entra
        a = autenticador(tmp_path, modo="aberto", utilizadores=[Utilizador(email=EMAIL, mcp_token_hash=hash_token("bslt_certo"))])
        assert a.utilizador_do_pedido({"Authorization": "Bearer bslt_errado"}) is None
        assert a.utilizador_do_pedido({"Authorization": "Bearer bslt_certo", "X-Basalt-Email": "x@x.pt"}).email == EMAIL
        c = autenticador(tmp_path, modo="cabecalho", utilizadores=[Utilizador(email=EMAIL, mcp_token_hash=hash_token("bslt_certo"))])
        assert c.utilizador_do_pedido({"Authorization": "Bearer bslt_errado", "X-Basalt-Email": EMAIL}) is None

    def test_quem_nao_tem_token_configurado_nao_entra_por_bearer(self, tmp_path):
        a = autenticador(tmp_path, modo="aberto")           # o utilizador tem senha mas mcp_token_hash vazio
        assert a.utilizador_do_pedido({"Authorization": "Bearer x"}) is None
        assert a.utilizador_do_pedido({"Authorization": "Bearer " + hash_token("")}) is None

    def test_outro_esquema_de_authorization_nao_e_bearer(self, tmp_path):
        a = autenticador(tmp_path, modo="aberto")
        assert a.utilizador_do_pedido({"Authorization": "Basic Zm9vOmJhcg=="}).email == EMAIL    # ignora e segue o modo
        assert a.utilizador_do_pedido({"Authorization": "Bearertoken"}).email == EMAIL

    def test_token_do_pedido(self, tmp_path):
        a = autenticador(tmp_path)
        assert a.token_do_pedido({"Cookie": f"x=1; {auth.COOKIE}=abc; y=2"}) == "abc"
        assert a.token_do_pedido({"Cookie": "x=1"}) == ""
        assert a.token_do_pedido(pedido()) == ""
        assert a.token_do_pedido({"Cookie": "\x00 ;;; ==="}) == ""


class TestCookies:
    def test_cookie_de_sessao(self, tmp_path):
        a = autenticador(tmp_path, sessao_horas=3)
        c = a.cabecalho_cookie("TOKEN", False)
        assert c == f"{auth.COOKIE}=TOKEN; Path=/; HttpOnly; SameSite=Strict; Max-Age={3 * 3600}"
        assert a.cabecalho_cookie("TOKEN", True) == c + "; Secure"

    def test_cookie_para_apagar(self, tmp_path):
        c = autenticador(tmp_path).cabecalho_apagar_cookie()
        assert c.startswith(f"{auth.COOKIE}=;") and "Max-Age=0" in c and "HttpOnly" in c and "SameSite=Strict" in c


# ───────────────────────── pelo HTTP ─────────────────────────
def entrar(s, email=EMAIL, senha=SENHA):
    return s.pedido("POST", "/api/entrar", {"email": email, "senha": senha}, email=None)


def sessao_de(r):
    c = r.cabecalhos["Set-Cookie"]
    return c.split(";")[0].split("=", 1)[1]


@pytest.fixture
def senha_srv(servidor_local):
    return servidor_local(modo="senha", utilizadores=[{"email": EMAIL, "espaco": "t", "admin": True, "senha_hash": HASH}])


class TestModoSenhaPeloHttp:
    def test_sem_sessao_a_pagina_manda_para_o_formulario_e_a_api_diz_401(self, senha_srv):
        r = senha_srv.get("/", email=None)
        assert r.codigo == 302 and r.cabecalhos["Location"] == "/entrar" and r.cabecalhos["Cache-Control"] == "no-store"
        r = senha_srv.get("/api/notas", email=None)
        assert r.codigo == 401 and r.json["entrar"] == "/entrar"
        assert senha_srv.get("/static/index.html", email=None).codigo == 401
        assert senha_srv.get("/sw.js", email=None).codigo == 302

    def test_o_formulario_e_o_manifesto_abrem_sem_sessao(self, senha_srv):
        r = senha_srv.get("/entrar", email=None)
        assert r.codigo == 200 and r.cabecalhos["Content-Type"].startswith("text/html")
        assert b"/api/entrar" in r.corpo
        for caminho in ("/manifest.webmanifest", "/favicon.ico", "/icon-192.png"):
            assert senha_srv.get(caminho, email=None).codigo == 200, caminho

    def test_entrar_e_usar_a_sessao_e_sair(self, senha_srv):
        r = entrar(senha_srv)
        assert r.codigo == 200 and r.json == {"ok": True}
        c = r.cabecalhos["Set-Cookie"]
        assert "HttpOnly" in c and "SameSite=Strict" in c and "Secure" not in c and f"Max-Age={14 * 24 * 3600}" in c
        sessao = {"Cookie": f"{auth.COOKIE}={sessao_de(r)}"}
        eu = senha_srv.get("/api/eu", email=None, cabecalhos=sessao)
        assert eu.codigo == 200 and eu.json["email"] == EMAIL and eu.json["modo"] == "senha" and eu.json["admin"] is True
        assert senha_srv.get("/", email=None, cabecalhos=sessao).codigo == 200
        out = senha_srv.pedido("POST", "/api/sair", {}, email=None, cabecalhos=sessao)
        assert out.codigo == 200 and "Max-Age=0" in out.cabecalhos["Set-Cookie"]
        assert senha_srv.get("/api/eu", email=None, cabecalhos=sessao).codigo == 401

    def test_o_servidor_nao_deixa_entrar_com_senha_errada_nem_email_desconhecido(self, senha_srv):
        r1 = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": "errada errada"}, email=None)
        r2 = senha_srv.pedido("POST", "/api/entrar", {"email": "x@y.pt", "senha": SENHA}, email=None)
        assert r1.codigo == r2.codigo == 401 and r1.json == r2.json == {"erro": "E-mail ou senha errados."}
        assert "Set-Cookie" not in r1.cabecalhos and "Set-Cookie" not in r2.cabecalhos

    def test_entrar_sem_x_basalt_e_recusado(self, senha_srv):
        r = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None, csrf=False)
        assert r.codigo == 403 and "Set-Cookie" not in r.cabecalhos
        assert senha_srv.pedido("POST", "/api/sair", {}, email=None, csrf=False).codigo == 403

    @pytest.mark.parametrize("bruto", [b"nao e json", b"[1,2]", b'{"email": 5, "senha": null}', b""])
    def test_corpo_estranho_nao_deixa_entrar_nem_rebenta(self, senha_srv, bruto):
        r = senha_srv.pedido("POST", "/api/entrar", email=None, bruto=bruto)
        assert r.codigo in (400, 401) and "Set-Cookie" not in r.cabecalhos

    def test_o_limite_de_tentativas_chega_ao_http_com_429(self, senha_srv):
        for _ in range(5):
            assert senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": "errada errada"}, email=None).codigo == 401
        r = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None)
        assert r.codigo == 429 and "Demasiadas" in r.json["erro"] and "Set-Cookie" not in r.cabecalhos

    def test_atras_de_um_proxy_local_cada_visitante_tem_o_seu_limite(self, senha_srv):
        for i in range(5):                                    # o visitante 198.51.100.1 muda o prefixo inventado a cada tentativa
            senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": "errada errada"}, email=None,
                             cabecalhos={"X-Forwarded-For": f"9.9.9.{i}, 198.51.100.1"})
        mau = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None,
                               cabecalhos={"X-Forwarded-For": "7.7.7.7, 198.51.100.1"})
        outro = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None, cabecalhos={"X-Forwarded-For": "8.8.8.8"})
        assert mau.codigo == 429 and outro.codigo == 200

    def test_um_xff_inventado_nao_foge_ao_limite(self, senha_srv):
        for i in range(5):                                    # cada tentativa com um «IP» diferente mas inválido → cai no loopback
            senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": "errada errada"}, email=None,
                             cabecalhos={"X-Forwarded-For": f"nao-e-ip-{i}"})
        r = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None, cabecalhos={"X-Forwarded-For": "lixo"})
        assert r.codigo == 429

    def test_com_https_no_proxy_o_cookie_leva_secure(self, senha_srv):
        r = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None, cabecalhos={"X-Forwarded-Proto": "https"})
        assert r.cabecalhos["Set-Cookie"].endswith("; Secure")

    def test_o_cabecalho_de_email_nao_serve_de_login_no_modo_senha(self, senha_srv):
        assert senha_srv.get("/api/eu", email=EMAIL).codigo == 401

    def test_escrever_com_sessao_mas_sem_x_basalt_e_recusado(self, senha_srv):
        r = senha_srv.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": SENHA}, email=None)
        sessao = {"Cookie": f"{auth.COOKIE}={sessao_de(r)}"}
        assert senha_srv.pedido("POST", "/api/nota", {"nome": "n", "texto": "x"}, email=None, csrf=False, cabecalhos=sessao).codigo == 403
        assert senha_srv.pedido("POST", "/api/nota", {"nome": "nota_nova", "texto": "x"}, email=None, cabecalhos=sessao).codigo == 201

    def test_sem_sessao_escrever_da_401_antes_de_olhar_ao_x_basalt(self, senha_srv):
        assert senha_srv.pedido("POST", "/api/nota", {"nome": "n", "texto": "x"}, email=None, csrf=False).codigo == 401
        assert senha_srv.pedido("PUT", "/api/nota?nome=n", {"texto": "x"}, email=None).codigo == 401

    def test_dois_utilizadores_nao_veem_as_notas_um_do_outro(self, servidor_local):
        outro_hash = hash_senha("outra senha longa")
        s = servidor_local(modo="senha", utilizadores=[
            {"email": EMAIL, "espaco": "ana", "senha_hash": HASH}, {"email": "rui@exemplo.pt", "espaco": "rui", "senha_hash": outro_hash}])
        a = {"Cookie": f"{auth.COOKIE}={sessao_de(entrar(s))}"}
        r = {"Cookie": f"{auth.COOKIE}={sessao_de(entrar(s, 'rui@exemplo.pt', 'outra senha longa'))}"}
        assert s.pedido("POST", "/api/nota", {"nome": "segredo_da_ana", "texto": "so da ana"}, email=None, cabecalhos=a).codigo == 201
        assert "segredo_da_ana" in {n["nome"] for n in s.get("/api/notas", email=None, cabecalhos=a).json}
        assert "segredo_da_ana" not in {n["nome"] for n in s.get("/api/notas", email=None, cabecalhos=r).json}
        assert s.get(s.nota_url("segredo_da_ana"), email=None, cabecalhos=r).codigo == 404


class TestBearerPeloHttp:
    @pytest.fixture
    def srv(self, servidor_local):
        self.token, h = auth.gerar_token()
        return servidor_local(modo="senha", utilizadores=[{"email": EMAIL, "espaco": "t", "mcp_token_hash": h, "senha_hash": HASH}])

    def test_o_token_dispensa_a_sessao(self, srv):
        r = srv.get("/api/eu", email=None, cabecalhos={"Authorization": f"Bearer {self.token}"})
        assert r.codigo == 200 and r.json["email"] == EMAIL

    def test_token_errado_da_401(self, srv):
        assert srv.get("/api/eu", email=None, cabecalhos={"Authorization": "Bearer bslt_errado"}).codigo == 401

    def test_escrever_com_token_continua_a_pedir_x_basalt(self, srv):
        cab = {"Authorization": f"Bearer {self.token}"}
        assert srv.pedido("POST", "/api/nota", {"nome": "por_token", "texto": "x"}, email=None, csrf=False, cabecalhos=cab).codigo == 403
        assert srv.pedido("POST", "/api/nota", {"nome": "por_token", "texto": "x"}, email=None, cabecalhos=cab).codigo == 201


class TestModoAbertoPeloHttp:
    def test_sem_utilizadores_e_o_utilizador_local(self, servidor_local):
        s = servidor_local(modo="aberto", utilizadores=[])
        r = s.get("/api/eu", email=None)
        assert r.codigo == 200 and r.json["email"] == "local@basalt" and r.json["modo"] == "aberto"
        assert s.get("/", email=None).codigo == 200

    def test_o_formulario_de_entrada_nao_existe_e_entrar_por_senha_recusa(self, servidor_local):
        s = servidor_local(modo="aberto", utilizadores=[])
        r = s.get("/entrar", email=None)
        assert r.codigo == 302 and r.cabecalhos["Location"] == "/"
        assert s.pedido("POST", "/api/entrar", {"email": "a@b.pt", "senha": "x"}, email=None).codigo == 400

    def test_continua_a_exigir_x_basalt_nas_escritas(self, servidor_local):
        s = servidor_local(modo="aberto", utilizadores=[])
        assert s.pedido("POST", "/api/nota", {"nome": "n", "texto": "x"}, email=None, csrf=False).codigo == 403


class TestModoCabecalhoPeloHttp:
    def test_entrar_por_senha_nao_existe_neste_modo_e_o_formulario_volta_a_pagina(self, servidor_local):
        s = servidor_local(modo="cabecalho")
        assert s.pedido("POST", "/api/entrar", {"email": EMAIL, "senha": "x"}).codigo == 400
        assert s.get("/entrar", email=None).codigo == 302

    def test_o_401_sem_cabecalho_nao_oferece_formulario(self, servidor_local):
        s = servidor_local(modo="cabecalho")
        r = s.get("/api/notas", email=None)
        assert r.codigo == 401 and r.json["entrar"] == ""
        assert s.get("/", email=None).codigo == 401                    # sem redirecção para um formulário que não existe

    def test_utilizador_desconhecido_do_proxy_leva_403(self, servidor_local):
        s = servidor_local(modo="cabecalho")
        r = s.get("/api/notas", email="intruso@exemplo.pt")
        assert r.codigo == 403 and "não tem acesso" in r.json["erro"]


class TestIpDoPedido:
    """H._ip() sem rede: só um proxy NA MESMA MÁQUINA pode dizer quem é o visitante (X-Forwarded-For)."""

    @staticmethod
    def ip(cliente, xff=None):
        from types import SimpleNamespace
        from basalt import web
        cab = {"X-Forwarded-For": xff} if xff is not None else {}
        return web.H._ip(SimpleNamespace(client_address=(cliente, 40000), headers=cab))

    @pytest.mark.parametrize("cliente", ["127.0.0.1", "::1"])
    def test_de_loopback_conta_o_ultimo_xff_que_nao_e_proxy(self, cliente):
        # o proxy local ACRESCENTA o IP de quem lhe falou (o último); o que vem antes pode ter sido inventado pelo visitante
        assert self.ip(cliente, "9.9.9.9, 198.51.100.1") == "198.51.100.1"
        assert self.ip(cliente, "198.51.100.1") == "198.51.100.1"
        assert self.ip(cliente, "9.9.9.9, 198.51.100.1, 127.0.0.1") == "198.51.100.1"   # dois proxies locais em cadeia

    @pytest.mark.parametrize("xff", [None, "", "lixo", "9.9.9"])
    def test_de_loopback_sem_xff_valido_fica_o_ip_da_ligacao(self, xff):
        assert self.ip("127.0.0.1", xff) == "127.0.0.1"

    @pytest.mark.parametrize("cliente", ["198.51.100.5", "203.0.113.7", "2001:db8::1"])
    def test_de_fora_o_xff_inventado_e_ignorado(self, cliente):
        assert self.ip(cliente, "9.9.9.9") == cliente
