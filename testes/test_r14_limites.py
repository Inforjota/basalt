"""r14 — dois tectos novos por CONTA, além dos tectos do servidor todo:
  · [ia] pedidos_por_hora_por_conta — a mesma conta não pode esgotar a IA de toda a gente;
  · [auth] espaco_max_mb — uma conta não pode enchter o disco (notas + anexos).
Cada regra tem o caso bom (dentro do tecto) e o mau (passa o tecto → recusa), e uma conta vizinha não afectada."""
import pytest

from conftest import ServidorLocal, usar_ia

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def s(servidor_local):
    return servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])


@pytest.fixture
def com_espaco_max_mb(tmp_path):
    feitos = []

    def fazer(limite_mb, utilizadores):
        s = _servidor_com_espaco_max_mb(tmp_path, limite_mb, utilizadores)
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


def _servidor_com_espaco_max_mb(tmp_path, limite_mb, utilizadores):
    """um `ServidorLocal` com `[auth] espaco_max_mb` — a `toml_de` já abre a secção [auth] para o `modo`, por isso a
    linha entra a seguir a ela em vez de reabrir a secção (o TOML recusa declará-la duas vezes)."""
    import threading as _th

    from basalt import config, web
    from conftest import toml_de

    s = object.__new__(ServidorLocal)
    s.dados = tmp_path / "dados-do-servidor"
    s.toml = tmp_path / "basalt.toml"
    texto = toml_de(0, s.dados, "cabecalho", None, utilizadores, "")
    texto = texto.replace('modo = "cabecalho"\n', f'modo = "cabecalho"\nespaco_max_mb = {limite_mb}\n', 1)
    s.toml.write_text(texto, encoding="utf-8")
    s.cfg = config.carregar(s.toml)
    s.srv = web.criar_servidor(s.cfg, host="127.0.0.1", porta=0)
    s.porta = s.srv.server_address[1]
    s.fio = _th.Thread(target=s.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    s.fio.start()
    return s


def test_limite_de_ia_por_conta(s):
    usar_ia("resumo falso", pedidos_por_hora_por_conta=2)
    pedido = {"acao": "resumir", "texto": "O barco sai às oito da manhã."}
    assert s.pedido("POST", "/api/ia", pedido, email=ANA).codigo == 200
    assert s.pedido("POST", "/api/ia", pedido, email=ANA).codigo == 200
    r = s.pedido("POST", "/api/ia", pedido, email=ANA)
    assert r.codigo == 429, r.corpo
    assert "2" in r.json["erro"]
    # outra conta não é afectada pelo tecto da Ana
    assert s.pedido("POST", "/api/ia", pedido, email=BEA).codigo == 200


def test_limite_de_ia_por_conta_desligado_por_omissao(s):
    usar_ia("resumo falso")                                     # pedidos_por_hora_por_conta = 0 (omissão)
    pedido = {"acao": "resumir", "texto": "O barco sai às oito da manhã."}
    for _ in range(5):
        assert s.pedido("POST", "/api/ia", pedido, email=ANA).codigo == 200


def test_espaco_max_mb_recusa_nota_grande(com_espaco_max_mb):
    s = com_espaco_max_mb(1, [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    texto_700k = "x" * (700 * 1024)                               # < 2 MB (tecto do corpo do pedido), mas 2x > 1 MB (o tecto)
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": texto_700k}, email=ANA).codigo == 201
    r = s.pedido("POST", "/api/nota", {"nome": "n2", "texto": texto_700k}, email=ANA)
    assert r.codigo == 413, r.corpo
    assert "1" in r.json["erro"]
    # a Bea não tem nada gravado: não é afectada pelo tecto da Ana
    assert s.pedido("POST", "/api/nota", {"nome": "n3", "texto": texto_700k}, email=BEA).codigo == 201


def test_espaco_max_mb_recusa_segundo_anexo(com_espaco_max_mb):
    s = com_espaco_max_mb(1, [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    anexo = b"a" * (600 * 1024)                                   # 600 KB
    r1 = s.pedido("POST", "/api/anexos?nome=foto1.png", bruto=anexo, email=ANA, cabecalhos={"Content-Type": "image/png"})
    assert r1.codigo == 201, r1.corpo
    r2 = s.pedido("POST", "/api/anexos?nome=foto2.png", bruto=anexo, email=ANA, cabecalhos={"Content-Type": "image/png"})
    assert r2.codigo == 413, r2.corpo
    assert "1" in r2.json["erro"]
    esp = s.espaco("a")
    assert not (esp.anexos / "foto2.png").exists()
    # a Bea tem o espaço todo livre
    r3 = s.pedido("POST", "/api/anexos?nome=foto1.png", bruto=anexo, email=BEA, cabecalhos={"Content-Type": "image/png"})
    assert r3.codigo == 201, r3.corpo


def test_espaco_max_mb_zero_e_sem_tecto(servidor_local):
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}])
    texto_700k = "x" * (700 * 1024)                                # sem [auth] espaco_max_mb → 0 → sem tecto
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": texto_700k}, email=ANA).codigo == 201
    assert s.pedido("POST", "/api/nota", {"nome": "n2", "texto": texto_700k}, email=ANA).codigo == 201


# ───────────── revisão Fable (20/10): o tecto de disco vale por TODAS as portas, não só pelo criar/anexos ─────────────
def test_espaco_max_mb_recusa_crescer_uma_nota_mas_deixa_encolher(com_espaco_max_mb):
    s = com_espaco_max_mb(1, [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])
    texto_700k = "x" * (700 * 1024)
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": texto_700k}, email=ANA).codigo == 201
    h = s.get("/api/nota?nome=n1", email=ANA).json["hash"]
    # crescer para 1,3 MB por PUT (a mesma nota) passava o tecto sem ninguém dar por isso
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": texto_700k * 2, "base_hash": h}, email=ANA)
    assert r.codigo == 413, r.corpo
    assert s.get("/api/nota?nome=n1", email=ANA).json["hash"] == h                  # nada gravado
    # encolher passa sempre (quem está no tecto tem de conseguir apagar texto)
    r = s.pedido("PUT", "/api/nota?nome=n1", {"texto": "curta\n", "base_hash": h}, email=ANA)
    assert r.codigo == 200, r.corpo
    # e com o espaço libertado volta a caber uma nota grande
    assert s.pedido("POST", "/api/nota", {"nome": "n2", "texto": texto_700k}, email=ANA).codigo == 201


def test_espaco_max_mb_cobre_sincronizar_entrada_e_guarda(com_espaco_max_mb, monkeypatch):
    from basalt import web
    s = com_espaco_max_mb(1, [{"email": ANA, "espaco": "a"}])
    assert s.pedido("POST", "/api/nota", {"nome": "n1", "texto": "pequena\n"}, email=ANA).codigo == 201
    grande = "y" * 900_000                                        # cabe sozinha; a dobrar (1,8 MB < 2 MB de corpo) passa o tecto
    # /api/sincronizar (edição offline) = o mesmo gravar → o mesmo tecto, pelo caminho simples e pelo da cópia offline
    r = s.pedido("POST", "/api/sincronizar", {"nome": "n1", "base_texto": "pequena\n", "texto": grande + grande}, email=ANA)
    assert r.codigo == 413, r.corpo
    r = s.pedido("POST", "/api/sincronizar", {"nome": "n1", "base_texto": "outra base\n", "texto": grande + grande}, email=ANA)
    assert r.codigo == 413, r.corpo
    esp = s.espaco("a")
    assert not list(esp.raiz.rglob("n1.*.md"))                    # nenhuma cópia offline ficou gravada
    r = s.pedido("POST", "/api/sincronizar", {"nome": "n1", "base_texto": "pequena\n", "texto": grande}, email=ANA)
    assert r.codigo == 200, r.corpo
    assert s.pedido("POST", "/api/nota", {"nome": "n2", "texto": "x" * 144_000}, email=ANA).codigo == 201   # sobram ~4,5 KB
    # /api/guarda (Entrada) com o espaço quase cheio: 4000 «é» = 8000 bytes não cabem; «cabe» cabe
    chamadas = []
    monkeypatch.setattr(web, "motor", lambda args, timeout=60, espaco=None: chamadas.append(args) or
                        type("R", (), {"returncode": 0, "stdout": "guardado", "stderr": ""})())
    r = s.pedido("POST", "/api/guarda", {"texto": "é" * 4000}, email=ANA)
    assert r.codigo == 413 and not chamadas, r.corpo
    r = s.pedido("POST", "/api/guarda", {"texto": "cabe"}, email=ANA)
    assert r.codigo == 200 and len(chamadas) == 1, r.corpo


def test_limite_de_ia_por_conta_cobre_a_pergunta(s, monkeypatch):
    from basalt import web
    usar_ia("resumo falso", pedidos_por_hora_por_conta=2)
    monkeypatch.setattr(web, "pergunta", lambda q: {"resposta": "falsa", "fontes": []})
    assert s.pedido("POST", "/api/ia", {"acao": "resumir", "texto": "O barco sai às oito."}, email=ANA).codigo == 200
    assert s.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=ANA).codigo == 200
    r = s.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=ANA)      # o mesmo balde do editor
    assert r.codigo == 429 and "2" in r.json["erro"], r.corpo
    assert s.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=BEA).codigo == 200
