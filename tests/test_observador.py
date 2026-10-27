"""Observador de janelas e modo planilha (sem Jira, sem chamar o Windows)."""
from datetime import date, datetime

import pytest

from trampotime import observador
from trampotime.armazenamento import Banco
from trampotime.config import Config
from trampotime.servico import ErroDeFluxo, Servico

DIA = date(2026, 10, 21)
JIRA_ABA = "[DMANESTOQ-15684] Ajuste no saldo - Jira - Google Chrome"


def anotar(banco, app, titulo, n, ocioso=0, projetos=("DMANESTOQ",), hora=10):
    for i in range(n):
        observador.anotar(banco, app, titulo, ocioso, 60, 300, projetos, datetime(2026, 10, 21, hora, i % 60))


def test_chave_do_titulo_respeita_projetos():
    assert observador.chave_no_titulo("Arquivo UTF-8 - DMANESTOQ-1 - Code", ("DMANESTOQ",)) == "DMANESTOQ-1"
    assert observador.chave_no_titulo("Arquivo UTF-8 - Notepad", ("DMANESTOQ",)) is None
    assert observador.chave_no_titulo("Arquivo UTF-8 - Notepad") == "UTF-8"  # sem PROJETOS, aceita qualquer chave


def test_foco_vale_quando_o_titulo_nao_tem_chave(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "Code.exe", "servico.py - estoque - Visual Studio Code", 2, hora=9)
    banco.definir_foco("DMANESTOQ-15700")
    anotar(banco, "Code.exe", "servico.py - estoque - Visual Studio Code", 3, hora=10)
    anotar(banco, "chrome.exe", JIRA_ABA, 1, hora=11)  # chave no título ganha do foco
    chaves = [a["chave"] for a in banco.amostras(DIA)]
    assert chaves == [None, None, "DMANESTOQ-15700", "DMANESTOQ-15700", "DMANESTOQ-15700", "DMANESTOQ-15684"]


def test_ocioso_nao_conta(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA_ABA, 5, ocioso=600)
    assert banco.amostras(DIA) == []


def test_tickets_observados_e_tempo_sem_ticket(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA_ABA, 30)
    anotar(banco, "chrome.exe", "[DMANESTOQ-999] Só uma olhada - Jira", 1)
    anotar(banco, "ms-teams.exe", "Chat | Microsoft Teams", 20, hora=11)
    amostras = banco.amostras(DIA)
    tickets = observador.tickets_observados(amostras)
    assert [(t.chave, t.resumo, t.peso, t.origem) for t in tickets] == [("DMANESTOQ-15684", "Ajuste no saldo", 30, "janelas")]
    assert observador.sem_ticket(amostras) == [{"app": "Teams", "minutos": 20}]


def test_modo_planilha_gera_sem_jira_e_nao_envia(tmp_path):
    banco = Banco(tmp_path / "t.db")
    banco.definir_foco("DMANESTOQ-15700")
    anotar(banco, "Code.exe", "servico.py - estoque - Visual Studio Code", 30)
    anotar(banco, "chrome.exe", JIRA_ABA, 10, hora=11)
    s = Servico(Config(banco=tmp_path / "t.db"), None, banco, lambda *a: [])
    dados = s.gerar(DIA)
    por_chave = {l["chave"]: l["minutos"] for l in dados["linhas"]}
    assert sum(por_chave.values()) == 450
    assert por_chave["DMANESTOQ-15700"] > por_chave["DMANESTOQ-15684"]
    assert dados["observado_minutos"] == 40
    s.aprovar(DIA)
    with pytest.raises(ErroDeFluxo, match="Modo planilha"):
        s.publicar(DIA, simular=True)


def test_meet_vira_reuniao_e_nao_conta_como_ocioso(tmp_path):
    banco = Banco(tmp_path / "t.db")
    banco.definir_foco("DMANESTOQ-15700")
    anotar(banco, "chrome.exe", "Meet - Daily DMANESTOQ-15684 - Google Chrome", 15, ocioso=900, hora=9)
    anotar(banco, "chrome.exe", "Meet – abc-defg-hij - Google Chrome", 30, ocioso=900, hora=10)
    anotar(banco, "Code.exe", "Meet - notas.md - Visual Studio Code", 5, hora=11)  # não é navegador
    amostras = banco.amostras(DIA)
    reunioes = observador.reunioes_observadas(amostras)
    assert [(r.titulo, r.minutos, r.chave) for r in reunioes] == [
        ("Meet: abc-defg-hij", 30, None), ("Meet: Daily DMANESTOQ-15684", 15, "DMANESTOQ-15684")]
    assert [(t.chave, t.peso) for t in observador.tickets_observados(amostras)] == [("DMANESTOQ-15700", 5)]  # reunião não usa o foco
    assert observador.sem_ticket(amostras) == []

    s = Servico(Config(banco=tmp_path / "t.db", ticket_reunioes="DMANESTOQ-1"), None, banco, lambda *a: [])
    por_chave = {}
    for l in s.gerar(DIA)["linhas"]:
        por_chave[l["chave"]] = por_chave.get(l["chave"], 0) + l["minutos"]
    assert por_chave["DMANESTOQ-1"] == 30 and por_chave["DMANESTOQ-15684"] == 15
    assert sum(por_chave.values()) == 450


def test_agenda_conectada_ganha_do_meet_mas_soma_instantanea(tmp_path):
    from trampotime.modelos import Reuniao
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", "Meet - Daily - Google Chrome", 15, hora=9)
    anotar(banco, "chrome.exe", "Meet – abc-defg-hij - Google Chrome", 20, hora=10)  # instantânea
    s = Servico(Config(banco=tmp_path / "t.db"), None, banco, lambda *a: [Reuniao("Daily", 15, "DMANESTOQ-9")])
    acoes = {l["acao"] for l in s.gerar(DIA)["linhas"]}
    assert "Daily" in acoes and "Meet: Daily" not in acoes
    assert "Meet: abc-defg-hij" in acoes


def test_agenda_com_erro_nao_derruba_o_rascunho(tmp_path):
    def quebrada(*a):
        raise RuntimeError("bloqueado")
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", "Meet - Daily - Google Chrome", 15)
    dados = Servico(Config(banco=tmp_path / "t.db"), None, banco, quebrada).gerar(DIA)
    assert "Meet: Daily" in {l["acao"] for l in dados["linhas"]}
    assert dados["avisos"]


def test_nome_da_reuniao():
    assert observador.nome_da_reuniao("Meet: Avaliação Paulo - Google Chrome") == "Meet: Avaliação Paulo"
    assert observador.nome_da_reuniao("Meet - Google Chrome") == "Reunião no Meet"
    assert observador.nome_da_reuniao("Meet – abc-defg-hij - Perfil 1 - Microsoft Edge") == "Meet: abc-defg-hij"
    assert observador.nome_da_reuniao("Chamada no Teams") == "Chamada no Teams"


def test_mesma_atividade_vira_uma_linha_so(tmp_path):
    """O caso da tela: 5 registros, 2 atividades."""
    banco = Banco(tmp_path / "t.db")
    meet = ("chrome.exe", "Meet: Avaliação Paulo - Google Chrome")

    def em(h, m, s=0):
        return datetime(2026, 10, 21, h, m, s)

    observador.anotar(banco, "chrome.exe", "TrampoTime - Google Chrome", 0, 60, 300, (), em(9, 0))  # antes do 1º ticket
    for m in range(1, 11):
        observador.anotar(banco, "chrome.exe", JIRA_ABA, 0, 60, 300, (), em(9, m))
    for m in range(11, 13):  # olhada na agenda, sem ticket
        observador.anotar(banco, "chrome.exe", "TOTVS SA: agenda - Google Chrome", 0, 60, 300, (), em(9, m))
    for m in range(13, 16):
        observador.anotar(banco, "chrome.exe", JIRA_ABA, 0, 60, 300, (), em(9, m))
    observador.anotar(banco, "chrome.exe", "Meet - Google Chrome", 0, 60, 300, (), em(9, 16))  # sala de espera
    for m in range(17, 20):  # Meet em segundo plano, nome ainda não visto
        observador.anotar(banco, "Code.exe", "x", 0, 60, 300, (), em(9, m), chamada=("chrome.exe", "Chamada no navegador"))
    for m in range(20, 30):
        observador.anotar(banco, "Code.exe", "x", 0, 60, 300, (), em(9, m), chamada=meet)
    for m in range(0, 30):  # muito depois, longe de tudo: continua sem ticket
        observador.anotar(banco, "EXCEL.EXE", "Planilha", 0, 60, 300, (), em(14, m))

    amostras = banco.amostras(DIA)
    assert [(t.chave, t.peso) for t in observador.tickets_observados(amostras, 0)] == [("DMANESTOQ-15684", 16)]
    assert [(r.titulo, r.minutos) for r in observador.reunioes_observadas(amostras, 0)] == [("Meet: Avaliação Paulo", 14)]
    assert observador.sem_ticket(amostras) == [{"app": "Excel", "minutos": 30}]

    p = observador.periodos(amostras)
    assert (p["inicio"], p["fim"]) == ("09:00", "14:30")
    assert p["atividades"]["ticket:DMANESTOQ-15684"] == [["09:00", "09:16"]]
    assert p["atividades"]["reuniao:Meet: Avaliação Paulo"] == [["09:16", "09:30"]]
    assert p["atividades"]["sem:Excel"] == [["14:00", "14:30"]]


def test_vm_aparece_pelo_nome():
    assert observador.rotulo_do_app("mstsc.exe", "VM-EST01 - Conexão de Área de Trabalho Remota") == "VM VM-EST01"
    assert observador.rotulo_do_app("mstsc.exe", "Conexão de Área de Trabalho Remota") == "VM (área de trabalho remota)"
    assert observador.rotulo_do_app("Code.exe", "") == "VS Code"


def test_branch_do_vscode_em_minusculas():
    titulo = "servico.py - estoque - feature/dmanestoq-15770 - Visual Studio Code"
    assert observador.chave_no_titulo(titulo, ("DMANESTOQ",)) == "DMANESTOQ-15770"


def test_chamada_em_segundo_plano_pelo_microfone(tmp_path):
    meet = ("chrome.exe", "Meet: Avaliação Paulo - Google Chrome")
    jira = ("chrome.exe", JIRA_ABA)
    # Meet numa janela visível
    assert observador.chamada_em_andamento({"chrome.exe"}, [jira, meet], None) == meet
    # Meet numa aba em segundo plano: usa a última aba do Meet vista
    assert observador.chamada_em_andamento({"chrome.exe"}, [jira], meet[1]) == meet
    assert observador.chamada_em_andamento({"chrome.exe"}, [jira], None) == ("chrome.exe", "Chamada no navegador")
    assert observador.chamada_em_andamento({"ms-teams.exe"}, [jira], None) == ("ms-teams.exe", "Chamada no Teams")
    assert observador.chamada_em_andamento({"camtasiastudio.exe"}, [meet], None) is None
    assert observador.chamada_em_andamento(set(), [meet], None) is None

    banco = Banco(tmp_path / "t.db")
    for i in range(20):  # você no Jira, ociosa, mas com a chamada rolando
        observador.anotar(banco, *jira, 900, 60, 300, ("DMANESTOQ",), datetime(2026, 10, 21, 10, i), chamada=meet)
    amostras = banco.amostras(DIA)
    assert [(r.titulo, r.minutos) for r in observador.reunioes_observadas(amostras)] == [("Meet: Avaliação Paulo", 20)]
    assert observador.tickets_observados(amostras) == []
