"""basalt.unir — fusão de duas notas parecidas: validar (é isto que decide se se aplica), aplicar e desfazer.
Sem IA: a «fusão da IA» é um texto escrito à mão em cada teste. Memória de rascunho num repositório git temporário."""
import subprocess

import pytest

from basalt import ia, unir, web
from basalt.config import IAConfig

FM_A = '---\nname: nota_a\ndescription: "A"\nmetadata:\n  type: project\n---\n\n'
FM_B = '---\nname: nota_b\ndescription: "B"\nmetadata:\n  type: project\n---\n\n'
A = FM_A + "Barragem com 9 comportas desde 24/09. Ver [[vigia]] e `dados-barragem.py` em /srv/dados/barragem.\n"
B = FM_B + "Risco 83% por mar. Ligado a [[nota_a]] e [[barcos]]. https://exemplo.org\n"
BOA = ('---\nname: nota_a\ndescription: "A e B"\nmetadata:\n  type: project\n---\n\nBarragem com 9 comportas desde 24/09, '
       "risco 83% por mar. Ver [[vigia]] e [[barcos]]; `dados-barragem.py` em /srv/dados/barragem. https://exemplo.org\n")


def validar(m, a=A, b=B):
    return unir.validar(a, b, m, "nota_a", "nota_b")


# ───────────────────────── validar: junções válidas ─────────────────────────
class TestJuncoesValidas:
    def test_fusao_com_todos_os_factos_e_ligacoes_e_aceite(self):
        ok, motivo, est = validar(BOA)
        assert ok and motivo == "ok"
        assert est["em_falta"] == 0 and est["cobertura"] == 1.0

    def test_ligacao_para_a_nota_fundida_pode_desaparecer(self):
        a = A + "Ver também [[nota_b]].\n"
        assert validar(BOA, a=a)[0]

    @pytest.mark.parametrize("escrito", ["Dose 20 mg.", "Dose: 20, por dia", "(20)", "dose=20"])
    def test_facto_com_pontuacao_a_volta_conta_como_presente(self, escrito):
        assert unir.tem_facto("20", escrito)

    @pytest.mark.parametrize("escrito", ["200", "120", "20.5", "3,20", "x20"])
    def test_facto_dentro_de_outro_numero_nao_conta(self, escrito):
        assert not unir.tem_facto("20", escrito)

    def test_ip_e_versao_sao_um_facto_cada_nao_pedacos(self):
        a = FM_A + "Servidor em 192.0.2.104.\n"
        b = FM_B + "A versão é v1.20.\n"
        bom = FM_A + "Servidor em 192.0.2.104.\nA versão é v1.20.\n"
        assert validar(bom, a=a, b=b)[0]
        ok, motivo, _ = validar(bom.replace("192.0.2.104", "192.0.2.105"), a=a, b=b)
        assert not ok and "192.0.2.104" in motivo

    def test_name_entre_aspas_no_frontmatter_e_aceite(self):
        assert validar(BOA.replace("name: nota_a", 'name: "nota_a"'))[0]


# ───────────────────────── validar: recusas ─────────────────────────
class TestRecusas:
    @pytest.mark.parametrize("segredo", ["sk-ant-" + "a" * 30, "AKIA" + "ABCDEFGHIJKLMNOP", "ghp_" + "x" * 36,
                                         "-----BEGIN OPENSSH " + "PRIVATE KEY-----"])
    def test_resultado_com_segredo_e_recusado(self, segredo):
        ok, motivo, _ = validar(BOA + segredo + "\n")
        assert not ok and "segredo" in motivo

    def test_sem_frontmatter_e_recusado(self):
        assert validar(BOA.split("---\n", 2)[2])[:2] == (False, "a nota fundida não começa pelo frontmatter")

    def test_frontmatter_com_outro_nome_e_recusado(self):
        ok, motivo, _ = validar(BOA.replace("name: nota_a", "name: nota_b"))
        assert not ok and "name: nota_a" in motivo

    def test_perder_uma_ligacao_e_recusado(self):
        ok, motivo, _ = validar(BOA.replace("[[barcos]]", "barcos"))
        assert not ok and "barcos" in motivo

    def test_perder_factos_e_recusado(self):
        m = BOA.replace("/srv/dados/barragem", "a pasta").replace("https://exemplo.org", "").replace("24/09", "Setembro")
        ok, motivo, est = validar(m)
        assert not ok and "perdeu" in motivo and est["em_falta"] >= 3

    def test_resultado_muito_mais_curto_e_recusado(self):
        a = A + ("Parágrafo comprido sem números que também conta para o tamanho da nota. " * 20) + "\n"
        ok, motivo, _ = validar(BOA, a=a)
        assert not ok and "mais curta" in motivo

    def test_numero_mudado_e_recusado(self):
        a = FM_A + "Dose 20 mg por dia.\n"
        m = FM_A.replace('"A"', '"A e B"') + "Dose 200 mg por dia.\n" + B.split("---\n", 2)[2]
        assert not validar(m, a=a)[0]

    @pytest.mark.xfail(reason="só números, datas, caminhos, `código`, URLs e [[ligações]] contam como factos "
                              "(basalt.unir.factos): apagar uma regra inteira ou trocar «proibido» por «permitido» "
                              "passa, desde que a nota não fique com menos de metade do tamanho")
    def test_regra_apagada_ou_invertida_e_recusada(self):
        a = FM_A + "Acesso proibido aos agentes.\nNunca apagar notas da memória sem fotografia git.\n"
        m = FM_A + "Acesso permitido aos agentes.\n" + B.split("---\n", 2)[2]
        assert not validar(m, a=a)[0]

    def test_perder_um_facto_em_trinta_e_recusado(self):
        corpo = " ".join(f"valor {i}" for i in range(10, 40))
        a = FM_A + corpo + "\n"
        m = FM_A + corpo.replace("valor 39", "valor") + "\n" + B.split("---\n", 2)[2] + "[[barcos]] https://exemplo.org 83\n"
        ok, _, est = validar(m, a=a)
        assert est["em_falta"] == 1
        assert not ok


# ───────────────────────── aplicar e desfazer (git de rascunho) ─────────────────────────
def git(real, *args):
    return subprocess.run(["git", "-C", str(real), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def real(memoria):
    r = memoria.real
    r.mkdir(parents=True, exist_ok=True)
    (r / "nota_a.md").write_text(A)
    (r / "nota_b.md").write_text(B)
    (r / "outra.md").write_text("vê [[nota_b]] e [[nota_b|a B]] e [[nota_b#secção]] e [x](nota_b.md)\n")
    (r / "nao_toca.md").write_text("fala de [[nota_bb]] e [[vigia]]\n")
    (r / "MEMORY.md").write_text("- [A](nota_a.md) — a\n- [B](nota_b.md) — b\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "inicio")
    return r


class TestAplicarEDesfazer:
    def test_aplicar_grava_troca_ligacoes_apaga_a_fundida_e_faz_commit(self, real):
        h, tocadas, feito = unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        assert feito and h == git(real, "rev-parse", "HEAD").strip()
        assert (real / "nota_a.md").read_text() == BOA
        assert not (real / "nota_b.md").exists()
        outra = (real / "outra.md").read_text()
        assert "nota_b" not in outra and outra.count("nota_a") == 4
        assert (real / "nao_toca.md").read_text() == "fala de [[nota_bb]] e [[vigia]]\n"
        assert sorted(tocadas) == ["MEMORY.md", "outra.md"]
        assert "nota_b.md" not in (real / "MEMORY.md").read_text()
        assert git(real, "status", "--porcelain") == ""

    def test_aplicar_nao_grava_nada_se_uma_nota_mudou_entretanto(self, real):
        (real / "nota_b.md").write_text(B + "acrescento de uma sessão durante a fusão\n")
        with pytest.raises(RuntimeError, match="mudou"):
            unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        assert (real / "nota_a.md").read_text() == A
        assert "acrescento" in (real / "nota_b.md").read_text()
        assert git(real, "log", "--format=%s").splitlines() == ["inicio"]

    def test_desfazer_repoe_as_duas_notas_as_ligacoes_e_o_indice(self, real):
        antes = {p.name: p.read_text() for p in real.glob("*.md")}
        h, _, _ = unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        ok, msg = unir.desfazer(real, h)
        assert ok and "Desfeito" in msg
        assert {p.name: p.read_text() for p in real.glob("*.md")} == antes

    def test_desfazer_depois_de_mudancas_em_conflito_nao_estraga_nada(self, real):
        h, _, _ = unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        (real / "nota_a.md").write_text(BOA + "editada depois da fusão\n")
        git(real, "commit", "-q", "-am", "edição posterior")
        ok, msg = unir.desfazer(real, h)
        assert not ok and "Não consegui" in msg
        assert (real / "nota_a.md").read_text() == BOA + "editada depois da fusão\n"
        assert git(real, "status", "--porcelain") == ""

    def test_o_autor_do_commit_e_o_configurado(self, real, monkeypatch):
        monkeypatch.setenv("BASALT_GIT_NOME", "Ana Teste")
        monkeypatch.setenv("BASALT_GIT_EMAIL", "ana@exemplo.pt")
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        assert git(real, "log", "-1", "--format=%an <%ae>").strip() == "Ana Teste <ana@exemplo.pt>"

    def test_sem_autor_configurado_assina_como_basalt(self, real):
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        assert git(real, "log", "-1", "--format=%an <%ae>").strip() == "Basalt <basalt@localhost>"

    def test_gravacao_feita_depois_da_verificacao_nao_se_perde_e_a_fusao_recua_inteira(self, real, monkeypatch):
        original = unir._git
        outra = (real / "outra.md").read_text()

        def git_espiao(r, *args):
            res = original(r, *args)
            if args[:2] == ("commit", "-q") and "antes de unir" in args[-1]:     # já depois da verificação e da fotografia…
                (real / "nota_a.md").write_text(A + "AGENTE escreveu aqui\n", encoding="utf-8")       # …um agente grava na nota A
            return res
        monkeypatch.setattr(unir, "_git", git_espiao)
        with pytest.raises(RuntimeError, match="recuei tudo"):
            unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
        assert (real / "nota_a.md").read_text() == A + "AGENTE escreveu aqui\n"      # a escrita do agente ganha
        assert (real / "nota_b.md").read_text() == B                                   # a «fundida» não foi apagada
        assert (real / "outra.md").read_text() == outra                                # nem as ligações trocadas
        assert not list(real.glob(".*.tmp"))


# ───────────────────────── o botão «Unir» da web (basalt.web._correr_fusao) ─────────────────────────
@pytest.fixture
def reindexou(monkeypatch):
    """a reindexação (em segundo plano) não corre aqui: só se regista que foi pedida."""
    pedidos = []
    monkeypatch.setattr(web, "_reindexar", lambda esp=None: pedidos.append(esp))
    return pedidos


@pytest.fixture
def ia_ligada():
    """há «chat» configurado (o fornecedor nunca é contactado: a fusão da IA é substituída em cada teste)."""
    ia.definir(ia.IA(IAConfig(fornecedor="openai", url="http://127.0.0.1:9/v1", modelo_chat="modelo-de-teste")))


def fusao(memoria, monkeypatch, resultado):
    monkeypatch.setattr(unir, "fundir_llm", lambda a, b, manter, fundir: resultado)
    j = {"id": "abcdef012345", "manter": "nota_a", "fundir": "nota_b", "estado": "na_fila", "passo": "", "mensagem": ""}
    web._correr_fusao(j, memoria.esp)
    return web._job_le(j["id"])


class TestBotaoUnir:
    def test_fusao_valida_fica_feita_e_pode_desfazer_se(self, memoria, real, reindexou, monkeypatch):
        j = fusao(memoria, monkeypatch, BOA)
        assert j["estado"] == "feita" and not (real / "nota_b.md").exists()
        assert len(reindexou) == 1                                   # o par fundido sai das «parecidas»
        cod, r = web.desfazer_fusao(j["id"])
        assert cod == 200 and r["ok"]
        assert (real / "nota_b.md").read_text() == B and web._job_le(j["id"])["estado"] == "desfeita"
        assert len(reindexou) == 2

    def test_fusao_que_perde_conteudo_e_recusada_e_as_notas_ficam(self, memoria, real, reindexou, monkeypatch):
        j = fusao(memoria, monkeypatch, BOA.replace("[[barcos]]", "barcos"))
        assert j["estado"] == "recusada" and "Não apliquei" in j["mensagem"]
        assert (real / "nota_a.md").read_text() == A and (real / "nota_b.md").read_text() == B
        assert reindexou == []

    def test_so_se_desfaz_uma_fusao_feita(self, memoria, real, reindexou, monkeypatch):
        j = fusao(memoria, monkeypatch, "sem frontmatter")
        assert web.desfazer_fusao(j["id"])[0] == 400

    @pytest.mark.parametrize("manter,fundir", [("nota_a", "nota_a"), ("../nota_a", "nota_b"), ("outro:x", "nota_b"),
                                               ("nota_a", "nao_existe")])
    def test_pedidos_de_fusao_invalidos_sao_recusados(self, memoria, real, ia_ligada, manter, fundir):
        assert web.iniciar_fusao(manter, fundir)[0] == 400

    def test_sem_ia_a_fusao_automatica_diz_que_esta_desligada(self, memoria, real):
        ia.definir(ia.IA(IAConfig(fornecedor="nenhum")))
        cod, r = web.iniciar_fusao("nota_a", "nota_b")
        assert cod == 503 and r["ia"] is False and "[ia]" in r["erro"]
        assert (real / "nota_b.md").read_text() == B

    def test_fusao_que_perde_um_facto_em_trinta_e_recusada_e_diz_qual(self, memoria, real, reindexou, monkeypatch):
        corpo = " ".join(f"valor {i}" for i in range(10, 40))
        a = FM_A + corpo + "\n"
        (real / "nota_a.md").write_text(a)
        git(real, "commit", "-q", "-am", "mais factos")
        m = FM_A + corpo.replace("valor 39", "valor") + "\n" + B.split("---\n", 2)[2] + "[[barcos]] https://exemplo.org 83\n"
        j = fusao(memoria, monkeypatch, m)
        assert j["estado"] == "recusada" and j["verificacao"]["em_falta"] == 1
        assert "perdeu 1 de" in j["mensagem"] and "39" in j["mensagem"]
        assert (real / "nota_a.md").read_text() == a and (real / "nota_b.md").read_text() == B

    def test_mensagem_da_fusao_feita_diz_os_factos_e_zero_perdidos(self, memoria, real, reindexou, monkeypatch):
        j = fusao(memoria, monkeypatch, BOA)
        assert j["estado"] == "feita"
        assert f"({j['verificacao']['factos']} factos, 0 perdidos)" in j["mensagem"]
