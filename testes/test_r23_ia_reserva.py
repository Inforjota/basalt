"""Ronda 23: a IA por uma ponte/API com o Ollama local como reserva.

SEM_TEMPERATURE_v1 — o pedido à Messages API não leva `temperature` (os modelos 5.5 dão HTTP 400 a todos os pedidos com ela).
IA_RECUSA_v1 — texto vazio com stop_reason refusal/content_filter é recusa: IARecusa com mensagem neutra, sem tentar a reserva.
IA_RESERVA_v1 — [ia.reserva]: se o principal não responde (tempo esgotado, sem ligação, HTTP 5xx ou 429) a mesma pergunta vai à
reserva; um 4xx (configuração) não se esconde. A chave do principal nunca vai para a reserva. Nunca há rede: `_post_json` simulado."""
import pytest

from basalt import config, ia
from basalt.config import ConfigInvalida, IAConfig
from basalt.ia import IA, IAIndisponivel, IARecusa, IASemResposta


class Rede:
    """substitui o ia._post_json: regista (url, corpo, cabeçalhos) e responde pela lista `respostas` (excepção ou dict)."""

    def __init__(self, *respostas):
        self.respostas, self.pedidos = list(respostas), []

    def __call__(self, url, corpo, cab, tempo):
        self.pedidos.append((url, corpo, dict(cab), tempo))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def http(codigo):
    e = IAIndisponivel(f"http://ponte-falsa:8140: HTTP {codigo}")
    e.codigo = codigo
    return e


MSG_OK = {"content": [{"type": "text", "text": "resposta da ponte"}], "stop_reason": "end_turn"}
OLLAMA_OK = {"choices": [{"message": {"content": "resposta do ollama"}}]}
RESERVA = {"fornecedor": "openai", "url": "http://ollama-falso:11434/v1", "modelo_chat": "qwen-falso", "tempo_limite": 77}


def ia_ponte(monkeypatch, reserva=None, **extra):
    monkeypatch.setenv("CHAVE_PONTE_TESTE", "ponte-teste-nao-e-segredo")
    return IA(IAConfig(fornecedor="anthropic", url="http://ponte-falsa:8140", chave_env="CHAVE_PONTE_TESTE",
                       modelo_chat="claude-sonnet-5-5", tempo_limite=30, reserva=dict(reserva or {}), **extra))


# ── SEM_TEMPERATURE_v1 ──
def test_pedido_anthropic_nao_leva_temperature(monkeypatch):
    rede = Rede(MSG_OK); monkeypatch.setattr(ia, "_post_json", rede)
    assert ia_ponte(monkeypatch).chat("olá") == "resposta da ponte"
    url, corpo, cab, _ = rede.pedidos[0]
    assert url == "http://ponte-falsa:8140/v1/messages"
    assert "temperature" not in corpo and corpo["model"] == "claude-sonnet-5-5"
    assert cab["x-api-key"] == "ponte-teste-nao-e-segredo"


# ── IA_RECUSA_v1 ──
@pytest.mark.parametrize("motivo", ["refusal", "content_filter"])
def test_recusa_vazia_e_iarecusa_neutra_e_nao_vai_a_reserva(monkeypatch, motivo):
    rede = Rede({"content": [], "stop_reason": motivo}); monkeypatch.setattr(ia, "_post_json", rede)
    with pytest.raises(IARecusa) as e:
        ia_ponte(monkeypatch, RESERVA).chat("texto da nota secreta")
    assert "recusou" in str(e.value) and "secreta" not in str(e.value)
    assert len(rede.pedidos) == 1                       # a reserva não se tentou


def test_texto_vazio_sem_recusa_continua_a_ser_texto_vazio(monkeypatch):
    monkeypatch.setattr(ia, "_post_json", Rede({"content": [], "stop_reason": "end_turn"}))
    assert ia_ponte(monkeypatch).chat("olá") == ""


# ── IA_RESERVA_v1 ──
@pytest.mark.parametrize("falha", [IASemResposta("sem resposta de http://ponte-falsa:8140: timed out"), http(502), http(503), http(429)])
def test_principal_em_baixo_vai_a_reserva_sem_a_chave_do_principal(monkeypatch, falha):
    rede = Rede(falha, OLLAMA_OK); monkeypatch.setattr(ia, "_post_json", rede)
    assert ia_ponte(monkeypatch, RESERVA).chat("olá", sistema="sê breve") == "resposta do ollama"
    url, corpo, cab, tempo = rede.pedidos[1]
    assert url == "http://ollama-falso:11434/v1/chat/completions" and corpo["model"] == "qwen-falso"
    assert "ponte-teste-nao-e-segredo" not in repr(cab) and tempo == 77
    assert corpo["messages"][0] == {"role": "system", "content": "sê breve"}


@pytest.mark.parametrize("codigo", [400, 401, 404])
def test_erro_4xx_nao_se_esconde_na_reserva(monkeypatch, codigo):
    rede = Rede(http(codigo), OLLAMA_OK); monkeypatch.setattr(ia, "_post_json", rede)
    with pytest.raises(IAIndisponivel) as e:
        ia_ponte(monkeypatch, RESERVA).chat("olá")
    assert str(codigo) in str(e.value) and len(rede.pedidos) == 1


def test_sem_reserva_o_erro_e_o_de_sempre(monkeypatch):
    rede = Rede(http(502)); monkeypatch.setattr(ia, "_post_json", rede)
    with pytest.raises(IAIndisponivel) as e:
        ia_ponte(monkeypatch).chat("olá")
    assert "502" in str(e.value) and "reserva" not in str(e.value)


def test_reserva_tambem_em_baixo_diz_as_duas(monkeypatch):
    rede = Rede(http(503), IASemResposta("sem resposta de http://ollama-falso:11434/v1/chat/completions: recusada"))
    monkeypatch.setattr(ia, "_post_json", rede)
    with pytest.raises(IAIndisponivel) as e:
        ia_ponte(monkeypatch, RESERVA).chat("olá")
    assert "503" in str(e.value) and "reserva" in str(e.value) and "recusada" in str(e.value)


def test_a_reserva_vale_para_as_tarefas(monkeypatch):
    """[ia.entrada] na ponte (o vigia da Entrada) também cai para a reserva."""
    rede = Rede(IASemResposta("sem resposta"), OLLAMA_OK); monkeypatch.setattr(ia, "_post_json", rede)
    x = ia_ponte(monkeypatch, RESERVA, tarefas={"entrada": {"tempo_limite": 20}}).para("entrada")
    assert x.cfg.seccao == "ia.entrada" and x.chat("classifica") == "resposta do ollama"
    assert x.descricao()["reserva"] == {"chat": "openai", "modelo": "qwen-falso"}


def test_chat_json_tambem_usa_a_reserva(monkeypatch):
    rede = Rede(http(500), {"choices": [{"message": {"content": '{"ok": true}'}}]}); monkeypatch.setattr(ia, "_post_json", rede)
    assert ia_ponte(monkeypatch, RESERVA).chat_json("diz ok") == {"ok": True}
    assert rede.pedidos[1][1]["response_format"] == {"type": "json_object"}


# ── configuração ──
def toml(tmp_path, texto):
    p = tmp_path / "basalt.toml"
    p.write_text(f'[basalt]\ndados = "{tmp_path}"\n[auth]\nmodo = "aberto"\n' + texto, encoding="utf-8")
    return p


def test_toml_reserva_le_se_e_nao_e_uma_tarefa(tmp_path, monkeypatch):
    p = toml(tmp_path, '[ia]\nfornecedor = "anthropic"\nurl = "http://ponte-falsa:8140"\nchave_env = "X"\n'
                       '[ia.reserva]\nfornecedor = "openai"\nurl = "http://ollama-falso:11434/v1"\nmodelo_chat = "qwen"\n')
    monkeypatch.setenv("BASALT_CONFIG", str(p))
    c = config.carregar()
    assert c.ia.tarefas == {} and c.ia.reserva["modelo_chat"] == "qwen"
    r = c.ia.para_reserva()
    assert r.fornecedor == "openai" and r.chave_env == "" and r.seccao == "ia.reserva"


@pytest.mark.parametrize("linhas,erro", [
    ('fornecedor = "openai"\n', "precisa de url"),
    ('fornecedor = "magia"\n', "fornecedor tem de ser"),
    ('fornecedor = "openai"\nurl = "http://x/v1"\nlimite = 3\n', "só se pode pôr"),
    ('fornecedor = "openai"\nurl = "http://x/v1"\ntempo_limite = 0\n', "tempo_limite"),
])
def test_toml_reserva_invalida_e_recusada(tmp_path, monkeypatch, linhas, erro):
    p = toml(tmp_path, '[ia]\nfornecedor = "nenhum"\n[ia.reserva]\n' + linhas)
    monkeypatch.setenv("BASALT_CONFIG", str(p))
    with pytest.raises(ConfigInvalida, match=erro):
        config.carregar()
