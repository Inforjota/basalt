"""r16 (auditoria de isolamento 21/10, M1) — o tecto de pedidos à IA por CONTA deixa de estar desligado por omissão quando o
servidor tem (ou pode ter) várias contas. A regra (web.tecto_ia_conta, calculada à hora de cada pedido):
  · [ia] pedidos_por_hora_por_conta > 0 → esse;
  · omisso ou 0, com mais de uma conta ou [auth] criar_contas = true → min(20, pedidos_por_hora) (20 se esse for 0);
  · omisso ou 0, com uma só conta e sem criar contas → sem tecto por conta (a produção do dono não muda).
Cada regra com o caso que passa e o que é recusado."""
import pytest

from basalt import web
from basalt.config import AuthConfig, Config, IAConfig, Utilizador
from conftest import usar_ia

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
PEDIDO = {"acao": "resumir", "texto": "O barco sai às oito da manhã."}


def _ia(s, email, n=1):
    return [s.pedido("POST", "/api/ia", PEDIDO, email=email).codigo for _ in range(n)]


@pytest.fixture
def duas(servidor_local):
    return servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}])


# ───────────────────────────── a regra, sem servidor ─────────────────────────────
def _cfg(tmp_path, contas=1, criar_contas=False):
    us = [Utilizador(email=f"c{i}@exemplo.pt", espaco=f"c{i}") for i in range(contas)]
    return Config(dados=tmp_path / "dados", auth=AuthConfig(modo="cabecalho", criar_contas=criar_contas), utilizadores=us)


@pytest.mark.parametrize("contas,criar,total,explicito,esperado", [
    (1, False, 40, 0, 0),           # o servidor de uma pessoa (a produção do dono): sem tecto por conta
    (2, False, 40, 0, 20),          # várias contas: automático
    (1, True, 40, 0, 20),           # uma conta mas pode haver mais (criar_contas): automático
    (2, False, 10, 0, 10),          # nunca mais do que o tecto do servidor todo
    (2, False, 0, 0, 20),           # pedidos_por_hora = 0 → 20
    (1, False, 40, 5, 5),           # o valor do toml manda sempre
    (2, False, 40, 30, 30),
])
def test_tecto_ia_conta_regra(tmp_path, contas, criar, total, explicito, esperado):
    cfg_ia = IAConfig(pedidos_por_hora=total, pedidos_por_hora_por_conta=explicito)
    assert web.tecto_ia_conta(_cfg(tmp_path, contas, criar), cfg_ia) == esperado


def test_tecto_ia_conta_fora_do_servidor_so_conta_o_explicito():
    assert web.tecto_ia_conta(None, IAConfig()) == 0
    assert web.tecto_ia_conta(None, IAConfig(pedidos_por_hora_por_conta=3)) == 3


# ───────────────────────────── pelo HTTP ─────────────────────────────
def test_duas_contas_sem_valor_o_21o_pedido_leva_429_e_a_outra_conta_passa(duas):
    usar_ia("resumo falso")                                    # pedidos_por_hora = 40, sem pedidos_por_hora_por_conta
    assert _ia(duas, ANA, 20) == [200] * 20
    r = duas.pedido("POST", "/api/ia", PEDIDO, email=ANA)
    assert r.codigo == 429, r.corpo
    assert "20" in r.json["erro"]
    assert _ia(duas, BEA) == [200]                             # a Bea tem o balde dela


def test_tecto_automatico_vale_tambem_na_pergunta(duas, monkeypatch):
    usar_ia("resumo falso")
    monkeypatch.setattr(web, "pergunta", lambda q: {"resposta": "falsa", "fontes": []})
    assert _ia(duas, ANA, 19) == [200] * 19
    assert duas.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=ANA).codigo == 200     # o 20.º
    r = duas.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=ANA)
    assert r.codigo == 429 and "20" in r.json["erro"], r.corpo
    assert duas.pedido("POST", "/api/pergunta", {"q": "quando sai o barco?"}, email=BEA).codigo == 200


def test_valor_explicito_e_respeitado_acima_do_automatico(duas):
    usar_ia("resumo falso", pedidos_por_hora_por_conta=25)
    assert _ia(duas, ANA, 25) == [200] * 25                    # passa dos 20 automáticos
    assert _ia(duas, ANA) == [429]


def test_producao_do_dono_uma_conta_sem_valor_nao_tem_tecto_por_conta(servidor_local):
    """1 conta, modo cabecalho, pedidos_por_hora = 40, sem pedidos_por_hora_por_conta: como antes da r16 — só o tecto do
    servidor (o 41.º é recusado por ele, não por um tecto de conta)."""
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a", "admin": True}])
    usar_ia("resumo falso")                                    # (passa igual no código da r15: o comportamento não muda)
    assert _ia(s, ANA, 40) == [200] * 40                       # bem mais do que os 20 de uma conta entre várias
    r = s.pedido("POST", "/api/ia", PEDIDO, email=ANA)
    assert r.codigo == 429 and "Já foram 40" in r.json["erro"], r.corpo      # o do servidor todo


def test_tecto_automatico_segue_as_contas_relidas_do_toml(servidor_local):
    """o tecto calcula-se em cada pedido: juntar uma 2.ª conta ao toml (relido sem reiniciar, r15) liga-o logo."""
    s = servidor_local(modo="cabecalho", utilizadores=[{"email": ANA, "espaco": "a"}])
    usar_ia("resumo falso", pedidos_por_hora=100)
    assert _ia(s, ANA, 21) == [200] * 21                       # uma conta: sem tecto por conta
    texto = s.toml.read_text(encoding="utf-8")
    s.toml.write_text(texto + '\n[[utilizadores]]\nemail = "bea@exemplo.pt"\nespaco = "b"\n', encoding="utf-8")
    assert _ia(s, ANA, 20) == [200] * 20                       # a partir daqui conta: 20 por hora
    assert _ia(s, ANA) == [429]
    assert _ia(s, BEA) == [200]
