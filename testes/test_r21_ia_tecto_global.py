"""r21 (auditoria externa à r14, ponto 5) — `[ia] pedidos_por_hora` é o tecto do SERVIDOR (todas as contas juntas), mas só o
/api/ia o aplicava: a pergunta (/api/pergunta) e a fusão (/api/unir) só viam o tecto por conta, e várias contas juntas (ou uma
só, num servidor sem tecto por conta) chamavam a IA sem limite. Agora (IA_GLOBAL_v1) as três rotas gastam o mesmo balde
global, pela mesma ordem do /api/ia (primeiro o da conta, depois o do servidor), com a mesma mensagem 429.
Cada teste falha no 68ad69d (r20) pela razão certa."""
import pytest

from conftest import usar_ia

ANA, BEA, RUI = "ana@exemplo.pt", "bea@exemplo.pt", "rui@exemplo.pt"
PERGUNTA = {"q": "quando sai o barco?"}
EDITOR = {"acao": "resumir", "texto": "O barco sai às oito da manhã."}


@pytest.fixture
def s(servidor_local):
    return servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"},
                                                         {"email": RUI, "espaco": "r"}])


@pytest.fixture
def falsos(monkeypatch):
    from basalt import web
    feitos = {"pergunta": 0, "unir": 0}

    def pergunta(q):
        feitos["pergunta"] += 1
        return {"resposta": "falsa", "fontes": []}

    def fusao(manter, fundir):
        feitos["unir"] += 1
        return 200, {"job": "j-falso"}
    monkeypatch.setattr(web, "pergunta", pergunta)
    monkeypatch.setattr(web, "iniciar_fusao", fusao)
    return feitos


def test_a_pergunta_respeita_o_tecto_do_servidor(s, falsos):
    usar_ia("falsa", pedidos_por_hora=2, pedidos_por_hora_por_conta=10)
    assert s.pedido("POST", "/api/pergunta", PERGUNTA, email=ANA).codigo == 200
    assert s.pedido("POST", "/api/pergunta", PERGUNTA, email=BEA).codigo == 200
    r = s.pedido("POST", "/api/pergunta", PERGUNTA, email=RUI)       # a 3.ª da hora, de uma conta que ainda não pediu nada
    assert r.codigo == 429, (r.codigo, r.corpo)                      # antes: 200 (só se via o tecto da conta)
    assert "2" in r.json["erro"] and falsos["pergunta"] == 2


def test_a_fusao_respeita_o_tecto_do_servidor(s, falsos):
    usar_ia("falsa", pedidos_por_hora=2, pedidos_por_hora_por_conta=10)
    assert s.pedido("POST", "/api/unir", {"manter": "a", "fundir": "b"}, email=ANA).codigo == 200
    assert s.pedido("POST", "/api/unir", {"manter": "a", "fundir": "b"}, email=BEA).codigo == 200
    r = s.pedido("POST", "/api/unir", {"manter": "a", "fundir": "b"}, email=RUI)
    assert r.codigo == 429, (r.codigo, r.corpo)                      # antes: 200
    assert falsos["unir"] == 2


def test_editor_pergunta_e_fusao_gastam_o_mesmo_balde_do_servidor(s, falsos):
    usar_ia("resumo falso", pedidos_por_hora=3, pedidos_por_hora_por_conta=10)
    assert s.pedido("POST", "/api/ia", EDITOR, email=ANA).codigo == 200
    assert s.pedido("POST", "/api/pergunta", PERGUNTA, email=BEA).codigo == 200
    assert s.pedido("POST", "/api/unir", {"manter": "a", "fundir": "b"}, email=RUI).codigo == 200
    for metodo, rota, corpo in (("POST", "/api/ia", EDITOR), ("POST", "/api/pergunta", PERGUNTA),
                                ("POST", "/api/unir", {"manter": "a", "fundir": "b"})):
        r = s.pedido(metodo, rota, corpo, email=ANA)
        assert r.codigo == 429, (rota, r.codigo, r.corpo)


def test_servidor_de_uma_so_conta_sem_tecto_por_conta_tambem_tem_o_do_servidor(servidor_local, falsos):
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}])
    usar_ia("falsa", pedidos_por_hora=2)                              # uma conta, sem criar_contas → sem tecto por conta (r16)
    for _ in range(2):
        assert s.pedido("POST", "/api/pergunta", PERGUNTA, email=ANA).codigo == 200
    r = s.pedido("POST", "/api/pergunta", PERGUNTA, email=ANA)
    assert r.codigo == 429, (r.codigo, r.corpo)                      # antes: 200 — sem tecto nenhum


def test_ia_desligada_na_fusao_nao_conta(s, falsos):
    usar_ia("falsa", fornecedor="nenhum", pedidos_por_hora=1)
    for _ in range(3):                                                # a fusão sem IA (só junta as notas) não é um pedido à IA
        assert s.pedido("POST", "/api/unir", {"manter": "a", "fundir": "b"}, email=ANA).codigo == 200


def test_pedidos_por_hora_zero_nao_tira_a_pergunta_a_quem_o_pos(s, falsos):
    # controlo (passa no 68ad69d): com 0 o editor já ficava sem IA, mas a pergunta funcionava — e continua
    usar_ia("falsa", pedidos_por_hora=0, pedidos_por_hora_por_conta=10)
    for _ in range(3):
        assert s.pedido("POST", "/api/pergunta", PERGUNTA, email=ANA).codigo == 200
