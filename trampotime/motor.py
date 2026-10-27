"""Regras que transformam reuniões e tickets do dia num rascunho que fecha a jornada."""
from datetime import date, datetime, time, timedelta

from .modelos import Linha, Reuniao, TicketDoDia


def montar_rascunho(
    reunioes: list[Reuniao],
    tickets: list[TicketDoDia],
    ja_lancado_minutos: int,
    jornada_minutos: int = 450,
    bloco_minutos: int = 15,
    ticket_reunioes: str | None = None,
) -> list[Linha]:
    """Monta as linhas do dia somando exatamente o que falta para fechar a jornada.

    1. Reuniões com chave de ticket vão para aquele ticket.
    2. Reuniões sem chave vão para ticket_reunioes, ou ficam sem ticket para a revisão.
    3. O que sobra é dividido entre os tickets do dia, proporcional ao peso.
    4. Tudo é arredondado em blocos, e o total bate com o que falta.
    """
    alvo = jornada_minutos - ja_lancado_minutos
    if alvo <= 0:
        return []

    brutas: list[tuple[Linha, float]] = []
    for r in reunioes:
        chave = r.chave or ticket_reunioes
        brutas.append((Linha(chave, r.titulo, 0, r.origem, r.inicio), float(r.minutos)))

    em_reuniao = sum(m for _, m in brutas)
    if em_reuniao > alvo:
        # Mais reunião que jornada: encolhe as reuniões proporcionalmente.
        fator = alvo / em_reuniao
        brutas = [(linha, m * fator) for linha, m in brutas]
        em_reuniao = alvo

    resto = alvo - em_reuniao
    if resto > 0:
        peso_total = sum(max(t.peso, 1) for t in tickets)
        for t in tickets:
            brutas.append((Linha(t.chave, t.resumo, 0, t.origem), resto * max(t.peso, 1) / peso_total))
        if not tickets:
            brutas.append((Linha(None, "Completar a jornada", 0, "completar"), float(resto)))

    linhas = _arredondar(brutas, alvo, bloco_minutos)
    return _juntar_iguais(linhas)


def _arredondar(brutas: list[tuple[Linha, float]], alvo: int, bloco: int) -> list[Linha]:
    """Maiores restos: cada linha recebe blocos inteiros e a soma bate com o alvo."""
    if not brutas:
        return []
    blocos_alvo, sobra = divmod(alvo, bloco)
    exatos = [m / bloco for _, m in brutas]
    blocos = [int(e) for e in exatos]
    faltam = blocos_alvo - sum(blocos)
    ordem = sorted(range(len(brutas)), key=lambda i: exatos[i] - blocos[i], reverse=True)
    for i in ordem[:faltam]:
        blocos[i] += 1

    linhas = []
    for (linha, _), b in zip(brutas, blocos):
        linha.minutos = b * bloco
        linhas.append(linha)
    if sobra:
        # Jornada que não fecha em blocos (ex. já havia 20 min lançados): o resto vai para a maior linha.
        max(linhas, key=lambda l: l.minutos).minutos += sobra
    return [l for l in linhas if l.minutos > 0]


def _juntar_iguais(linhas: list[Linha]) -> list[Linha]:
    juntas: dict[tuple, Linha] = {}  # mesma chave, ação e origem viram uma linha
    for l in linhas:
        k = (l.chave, l.acao, l.origem)
        if k in juntas:
            juntas[k].minutos += l.minutos
        else:
            juntas[k] = l
    return list(juntas.values())


def validar(linhas: list[Linha], ja_lancado_minutos: int, jornada_minutos: int) -> list[str]:
    """Problemas que impedem o envio ao Tempo."""
    problemas = []
    for n, l in enumerate(linhas, 1):
        if not l.chave:
            problemas.append(f"Linha {n} ({l.acao}) está sem issue.")
        if l.minutos <= 0:
            problemas.append(f"Linha {n} ({l.acao}) está com tempo zero ou negativo.")
    total = sum(l.minutos for l in linhas) + ja_lancado_minutos
    if total != jornada_minutos:
        problemas.append(
            f"O dia soma {_hhmm(total)} com o que já está no Tempo, e a jornada é {_hhmm(jornada_minutos)}."
        )
    return problemas


def _hhmm(minutos: int) -> str:
    return f"{minutos // 60}h{minutos % 60:02d}"


def horarios(linhas: list[Linha], dia: date, inicio: datetime, pausas: list[tuple[datetime, datetime]],
             bloco_minutos: int = 15) -> list[list[list[str]]]:
    """Agenda do dia para lançar no Tempo: [[início, fim], ...] em HH:MM para cada linha.

    Linhas com início fixo (reuniões) ficam no horário real, arredondado ao bloco. As outras preenchem
    o dia em ordem a partir do início, pulando reuniões e pausas; se cruzarem uma, viram dois trechos."""
    bloco = timedelta(minutes=bloco_minutos)
    ocupado: list[tuple[datetime, datetime]] = []
    resultado: list[list[tuple[datetime, datetime]]] = [[] for _ in linhas]

    for i, l in enumerate(linhas):
        if l.inicio and l.minutos > 0:
            ini = _arredondar_hora(datetime.combine(dia, time.fromisoformat(l.inicio)), bloco)
            resultado[i] = [(ini, ini + timedelta(minutes=l.minutos))]
            ocupado.append(resultado[i][0])
    ocupado += [(_arredondar_hora(a, bloco), _arredondar_hora(b, bloco)) for a, b in pausas]
    ocupado = sorted((a, b) for a, b in ocupado if b > a)

    cursor = inicio
    for i, l in enumerate(linhas):
        if l.inicio or l.minutos <= 0:
            continue
        restante = timedelta(minutes=l.minutos)
        while restante > timedelta(0):
            cursor = _fora_do_ocupado(cursor, ocupado)
            proximo = min((a for a, _ in ocupado if a > cursor), default=None)
            usa = min(restante, proximo - cursor) if proximo else restante
            if resultado[i] and resultado[i][-1][1] == cursor:
                resultado[i][-1] = (resultado[i][-1][0], cursor + usa)
            else:
                resultado[i].append((cursor, cursor + usa))
            cursor += usa
            restante -= usa
    return [[[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in trechos] for trechos in resultado]


def _fora_do_ocupado(cursor: datetime, ocupado: list[tuple[datetime, datetime]]) -> datetime:
    mudou = True
    while mudou:
        mudou = False
        for a, b in ocupado:
            if a <= cursor < b:
                cursor, mudou = b, True
    return cursor


def _arredondar_hora(d: datetime, bloco: timedelta) -> datetime:
    meia_noite = datetime.combine(d.date(), time.min, d.tzinfo)
    return meia_noite + round((d - meia_noite) / bloco) * bloco
