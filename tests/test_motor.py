from trampotime.motor import montar_rascunho, validar
from trampotime.modelos import Reuniao, TicketDoDia, achar_chave


def total(linhas):
    return sum(l.minutos for l in linhas)


def test_fecha_7h30_em_blocos_de_15():
    linhas = montar_rascunho(
        [Reuniao("Daily DMANESTOQ-15684", 15, "DMANESTOQ-15684"), Reuniao("Refinamento", 50)],
        [TicketDoDia("DMANESTOQ-15684", "Ajuste no saldo", 3), TicketDoDia("DMANESTOQ-15700", "Erro no inventário", 1)],
        ja_lancado_minutos=0,
        ticket_reunioes="DMANESTOQ-1",
    )
    assert total(linhas) == 450
    assert all(l.minutos % 15 == 0 for l in linhas)
    assert {l.chave for l in linhas} == {"DMANESTOQ-15684", "DMANESTOQ-1", "DMANESTOQ-15700"}


def test_peso_maior_recebe_mais_tempo():
    linhas = montar_rascunho([], [TicketDoDia("A-1", "a", 3), TicketDoDia("B-2", "b", 1)], 0)
    por_chave = {l.chave: l.minutos for l in linhas}
    assert por_chave["A-1"] > por_chave["B-2"]
    assert total(linhas) == 450


def test_desconta_o_que_ja_esta_no_tempo():
    linhas = montar_rascunho([], [TicketDoDia("A-1", "a", 1)], ja_lancado_minutos=200)
    assert total(linhas) == 250  # 16 blocos de 15 + 10 min de resto


def test_dia_ja_fechado_nao_gera_nada():
    assert montar_rascunho([], [TicketDoDia("A-1", "a", 1)], ja_lancado_minutos=450) == []


def test_reuniao_sem_ticket_fica_para_revisao():
    linhas = montar_rascunho([Reuniao("1:1 com gestor", 30)], [TicketDoDia("A-1", "a", 1)], 0)
    sem = [l for l in linhas if l.chave is None]
    assert sem and sem[0].acao == "1:1 com gestor"
    assert any("sem issue" in p for p in validar(linhas, 0, 450))


def test_sem_tickets_gera_linha_para_completar():
    linhas = montar_rascunho([Reuniao("Daily X-1", 15, "X-1")], [], 0)
    assert total(linhas) == 450
    assert any(l.origem == "completar" for l in linhas)


def test_reuniao_demais_encolhe_para_a_jornada():
    linhas = montar_rascunho([Reuniao("Workshop X-1", 600, "X-1")], [TicketDoDia("A-1", "a", 1)], 0)
    assert total(linhas) == 450


def test_validar_aceita_dia_certo():
    linhas = montar_rascunho([], [TicketDoDia("A-1", "a", 1)], 0)
    assert validar(linhas, 0, 450) == []


def test_achar_chave():
    assert achar_chave("Revisão do DMANESTOQ-15684 com o time") == "DMANESTOQ-15684"
    assert achar_chave("Daily", None, "ver PROJ2-7") == "PROJ2-7"
    assert achar_chave("Almoço") is None
