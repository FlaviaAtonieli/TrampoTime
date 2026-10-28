"""Card Agora (motivo, 'Não é isso', foco fixado), segunda tela e avisos do Windows."""
from datetime import date, datetime, time, timedelta

from trampotime import observador
from trampotime.armazenamento import Banco
from trampotime.avisos import Avisos
from trampotime.config import Config
from trampotime.servico import Servico

DIA = date(2026, 10, 22)
P = ("DMANESTOQ",)


def em(h, m, dia=DIA):
    return datetime.combine(dia, time(h, m))


def test_motivo_de_cada_atribuicao(tmp_path):
    banco = Banco(tmp_path / "t.db")
    a = lambda app, titulo, chamada=None: observador.atribuir(banco, app, titulo, P, chamada)
    _, _, chave, fonte = a("Code.exe", "x.p - estoque - feature/dmanestoq-1 - Visual Studio Code")
    assert (chave, observador.explicar("Code.exe", chave, fonte, False, False)) == ("DMANESTOQ-1", "pela branch do VS Code")
    _, _, chave, fonte = a("chrome.exe", "[DMANESTOQ-2] X - JIRA - Google Chrome")
    assert observador.explicar("chrome.exe", chave, fonte, False, False) == "pela aba do Jira"
    app, _, _, fonte = a("Code.exe", "x", chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    assert observador.explicar(app, None, fonte, False, True) == "pelo microfone do Chrome"
    _, _, chave, fonte = a("EXCEL.EXE", "rascunho - Excel")
    assert (chave, fonte) == (None, None)
    banco.definir_foco("DMANESTOQ-3")
    assert a("EXCEL.EXE", "rascunho - Excel")[2:] == ("DMANESTOQ-3", "foco")


def test_foco_fixado_ganha_da_branch_mas_nao_da_reuniao(tmp_path):
    banco = Banco(tmp_path / "t.db")
    branch = "x.p - estoque - feature/dmanestoq-1 - Visual Studio Code"
    banco.definir_foco("DMANESTOQ-9")
    assert observador.atribuir(banco, "Code.exe", branch, P)[2] == "DMANESTOQ-1"  # foco comum: a branch ganha
    banco.definir_foco("DMANESTOQ-9", fixo=True)
    assert observador.atribuir(banco, "Code.exe", branch, P)[2:] == ("DMANESTOQ-9", "foco")
    assert observador.atribuir(banco, "Code.exe", branch, P, ("chrome.exe", "Meet: X"))[3] == "reuniao"
    banco.definir_foco(None)  # limpar desfaz o fixado
    assert not banco.foco_fixo()


def test_nao_e_isso_corrige_o_trecho_atual_e_fixa(tmp_path):
    banco = Banco(tmp_path / "t.db")
    for m in range(0, 10):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-5] Outro - JIRA - Google Chrome", 0, 60, 300, P, em(9, m))
    for m in range(10, 30):  # branch errada desde 09:10
        observador.anotar(banco, "Code.exe", "x.p - estoque - feature/dmanestoq-1 - Visual Studio Code", 0, 60, 300, P, em(9, m))
    s = Servico(Config(banco=tmp_path / "t.db", intervalo_segundos=60), None, banco, lambda *a: [])
    r = s.corrigir_agora("DMANESTOQ-7", em(9, 30))
    assert (r["desde"], r["minutos"]) == ("09:10", 20)
    chaves = [a["chave"] for a in banco.amostras(DIA)]
    assert chaves[:10] == ["DMANESTOQ-5"] * 10 and chaves[10:] == ["DMANESTOQ-7"] * 20
    assert banco.foco_atual() == "DMANESTOQ-7" and banco.foco_fixo()


def test_segunda_tela_mouse_mexendo_em_outra_janela():
    t = observador.SegundaTela()
    code, planilha = ("Code.exe", "x - Visual Studio Code"), ("chrome.exe", "Custos - Planilhas Google")
    assert t.escolher(code, planilha, (100, 100), 0, 5) == (code, False)  # primeira volta: sem histórico do mouse
    assert t.escolher(code, planilha, (2500, 300), 2, 5) == (planilha, True)  # mexeu sobre a outra tela
    assert t.escolher(code, planilha, (2500, 300), 40, 5) == (planilha, True)  # parado lendo: continua lá
    assert t.escolher(code, planilha, (2500, 300), 1, 5) == (code, False)  # digitou: volta para a janela em foco
    assert t.escolher(code, code, (300, 300), 0, 5) == (code, False)  # mouse sobre a própria janela em foco


def test_avisos(tmp_path):
    banco = Banco(tmp_path / "t.db")
    enviados = []
    av = Avisos(banco, lambda titulo, texto: enviados.append(titulo))
    c = Config(banco=tmp_path / "t.db")
    ontem = DIA - timedelta(days=1)
    banco.registrar_amostra(em(9, 0, ontem), "x.exe", "", 5, "A-1", "titulo")  # ontem teve atividade e não foi lançado

    av.passo(em(9, 0), c, em(8, 50))  # 10 min sem ticket: ainda não
    assert enviados == ["Dia sem lançar no Tempo"]
    av.passo(em(9, 6), c, em(8, 50))  # 16 min sem ticket
    assert enviados.count("Em que você está?") == 1
    av.passo(em(9, 20), c, em(8, 50))  # não repete antes de 30 min
    assert enviados.count("Em que você está?") == 1
    av.passo(em(9, 40), c, em(8, 50))
    assert enviados.count("Em que você está?") == 2
    assert enviados.count("Dia sem lançar no Tempo") == 1  # uma vez por dia

    for m in range(0, 450):
        banco.registrar_amostra(em(10, 0) + timedelta(minutes=m), "x.exe", "", 60, "A-1", "titulo")
    av.passo(em(18, 0), c, None)
    assert enviados.count("Jornada completa") == 1
    av.passo(em(18, 5), c, None)
    assert enviados.count("Jornada completa") == 1

    desligados = []
    Avisos(banco, lambda *a: desligados.append(a)).passo(em(19, 0), Config(avisos=False), em(18, 0))
    assert desligados == []


def test_dias_pendentes_ignora_lancados(tmp_path):
    banco = Banco(tmp_path / "t.db")
    for d in (date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)):
        banco.registrar_amostra(em(9, 0, d), "x.exe", "", 5, "A-1", "titulo")
    banco.salvar(date(2026, 10, 20), "lancado", 0, [])
    assert banco.dias_pendentes(DIA, date(2026, 10, 15)) == ["2026-10-21", "2026-10-19"]
