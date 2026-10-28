"""Perguntas do TrampoTime: o dia é analisado e o que parece ser a mesma atividade vira pergunta de sim ou não.

- Juntar: dois trechos do mesmo ticket com um intervalo curto entre eles (sem reunião nem outro ticket
  relevante no meio). Sim = o intervalo inteiro vai para o ticket, inclusive o tempo sem atividade.
- Associar: uma atividade identificável (planilha, Azure, VM, pasta do VS Code...) que tomou tempo sem ter
  ticket próprio e parece fazer parte de uma demanda, pelo texto parecido com o resumo da issue e por ser
  usada perto dos trechos dela. Sim = o tempo de hoje vai para a demanda e vira regra para os próximos dias.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import observador, sugestao
from .modelos import achar_chave

JUNTAR_ATE_MINUTOS = 30
ASSOCIAR_A_PARTIR_MINUTOS = 10
PERTO_MINUTOS = 10  # "usada junto": até 10 min de um trecho em que a issue estava aberta
NOTA_MINIMA = 0.3


def _hhmm(minutos: int) -> str:
    return f"{minutos} min" if minutos < 60 else f"{minutos // 60}h{minutos % 60:02d}"


def gerar(amostras: list[dict], pausas: list[dict], associacoes: dict[str, str], respondidas: set[str],
          rotinas: list[dict] = (), uso_anterior: dict[tuple[str, str], int] | None = None,
          agora: datetime | None = None, nomes: dict[str, str] | None = None) -> list[dict]:
    itens = observador.resolver(amostras)
    nomes = nomes or {}
    tickets = {t.chave: nomes.get(t.chave, t.resumo) for t in observador.tickets_observados(amostras, 0)}
    perguntas = (_rotinas(rotinas, itens, associacoes, agora) + _juntar(itens, pausas, tickets)
                 + _associar(itens, associacoes, tickets, uso_anterior or {}))
    return [p for p in perguntas if p["id"] not in respondidas]


def uso_do_dia(amostras: list[dict]) -> dict[tuple[str, str], int]:
    """(atividade, ticket) -> minutos, para o histórico compacto. Chat fica de fora (tem nome de pessoas)."""
    uso: Counter = Counter()
    for a in observador.resolver(amostras):
        if a["chave"] and a["fonte"] != "reuniao" and _identificavel(a):
            rotulo = observador.rotulo_do_app(a["app"], a["titulo"])
            if "- chat" not in rotulo.lower():
                uso[(rotulo, a["chave"])] += a["segundos"]
    return {k: round(s / 60) for k, s in uso.items()}


def _rotinas(rotinas: list[dict], itens: list[dict], associacoes: dict[str, str], agora: datetime | None) -> list[dict]:
    perguntas = []
    vistas_hoje = {a["reuniao"].removeprefix("Meet: ").lower() for a in itens if a["fonte"] == "reuniao"}
    for r in rotinas:
        nome = r["nome"]
        frequencia = f"{r['dias']} dos últimos {r['de']} dias confirmados"
        if r["chave_constante"] and nome not in associacoes:
            perguntas.append({
                "id": f"rotina-fixar:{nome}:{r['chave']}", "tipo": "rotina", "chave": r["chave"], "rotulo": nome,
                "pergunta": f"\"{nome}\" é uma rotina ({frequencia}), sempre em {r['chave']}. Lançar sempre nessa issue?",
                "detalhe": "Com sim, a reunião já entra com essa issue na planilha, sem você preencher.",
            })
        if agora and r["inicio"] and agora.weekday() < 5:
            h, m = map(int, r["inicio"].split(":"))
            inicio = agora.replace(hour=h, minute=m, second=0, microsecond=0)
            if agora >= inicio + timedelta(minutes=r["minutos"] + 15) and nome.removeprefix("Meet: ").lower() not in vistas_hoje:
                perguntas.append({
                    "id": f"rotina-faltou:{agora.date().isoformat()}:{nome}", "tipo": "rotina",
                    "chave": associacoes.get(nome) or r["chave"] or achar_chave(nome), "rotulo": nome,
                    "inicio": inicio.isoformat(timespec="seconds"), "minutos": r["minutos"],
                    "pergunta": f"Não vi a \"{nome}\" hoje (costuma ser às {r['inicio']}, {_hhmm(r['minutos'])}). Você participou?",
                    "detalhe": f"Rotina em {frequencia}. Com sim, ela entra no seu dia nesse horário.",
                })
    return perguntas


def _blocos(itens: list[dict]) -> list[dict]:
    """Trechos seguidos: (tipo, nome, início, fim, segundos), com o tempo sem amostras como buraco."""
    blocos: list[dict] = []
    for a in itens:
        inicio = datetime.fromisoformat(a["instante"])
        fim = inicio + timedelta(seconds=a["segundos"])
        tipo = "reuniao" if a["fonte"] == "reuniao" else "ticket" if a["chave"] else "sem"
        nome = a["reuniao"] if tipo == "reuniao" else a["chave"]
        ultimo = blocos[-1] if blocos else None
        if ultimo and (ultimo["tipo"], ultimo["nome"]) == (tipo, nome) and (inicio - ultimo["fim"]).total_seconds() <= 60:
            ultimo["fim"] = fim
            ultimo["segundos"] += a["segundos"]
            ultimo["apps"][observador.rotulo_do_app(a["app"], a["titulo"])] += a["segundos"]
        else:
            blocos.append({"tipo": tipo, "nome": nome, "inicio": inicio, "fim": fim, "segundos": a["segundos"],
                           "apps": Counter({observador.rotulo_do_app(a["app"], a["titulo"]): a["segundos"]})})
    return blocos


def _juntar(itens: list[dict], pausas: list[dict], tickets: dict[str, str]) -> list[dict]:
    blocos = _blocos(itens)
    perguntas = []
    relevantes = [i for i, b in enumerate(blocos) if b["tipo"] == "reuniao" or (b["tipo"] == "ticket" and b["segundos"] >= 5 * 60)]
    for x, y in zip(relevantes, relevantes[1:]):
        a, b = blocos[x], blocos[y]
        if a["tipo"] != "ticket" or b["tipo"] != "ticket" or a["nome"] != b["nome"]:
            continue
        buraco = b["inicio"] - a["fim"]
        if not timedelta(minutes=1) <= buraco <= timedelta(minutes=JUNTAR_ATE_MINUTOS):
            continue
        meio = Counter()
        for m in blocos[x + 1:y]:
            meio.update(m["apps"] if m["tipo"] == "sem" else {m["nome"]: m["segundos"]})
        parado = buraco.total_seconds() - sum(meio.values())
        partes = [f"{nome} {round(s / 60)} min" for nome, s in meio.most_common(3) if round(s / 60) >= 1]
        if parado >= 60:
            pausa = next((p for p in pausas if p["inicio"] < b["inicio"].isoformat() and p["fim"] > a["fim"].isoformat()), None)
            partes.append(f"{'pausa' if pausa else 'sem atividade'} {round(parado / 60)} min")
        chave = a["nome"]
        resumo = tickets.get(chave, chave)
        perguntas.append({
            "id": f"juntar:{chave}:{a['fim']:%H%M}-{b['inicio']:%H%M}",
            "tipo": "juntar",
            "chave": chave,
            "pergunta": f"Juntar os dois trechos de {chave} ({a['inicio']:%H:%M}–{a['fim']:%H:%M} e "
                        f"{b['inicio']:%H:%M}–{b['fim']:%H:%M})? Os {_hhmm(round(buraco.total_seconds() / 60))} do meio passam para ela.",
            "detalhe": (f"{resumo} · " if resumo != chave else "") + ("No meio: " + ", ".join(partes) if partes else ""),
            "inicio": a["fim"].isoformat(timespec="seconds"),
            "fim": b["inicio"].isoformat(timespec="seconds"),
        })
    return perguntas


def _associar(itens: list[dict], associacoes: dict[str, str], tickets: dict[str, str],
              uso_anterior: dict[tuple[str, str], int]) -> list[dict]:
    if not tickets:
        return []
    # onde cada issue estava aberta de verdade (chave no título), para medir o "usada junto"
    evidencias: dict[str, list[datetime]] = defaultdict(list)
    por_rotulo: dict[str, list[dict]] = defaultdict(list)
    for a in itens:
        instante = datetime.fromisoformat(a["instante"])
        if a["fonte"] == "titulo" and a["chave"]:
            evidencias[a["chave"]].append(instante)
        elif a["fonte"] not in {"reuniao", "titulo", "associado", "juntado", "manual"} and _identificavel(a):
            por_rotulo[observador.rotulo_do_app(a["app"], a["titulo"])].append({**a, "_t": instante})

    perguntas = []
    for rotulo, amostras in por_rotulo.items():
        minutos = round(sum(a["segundos"] for a in amostras) / 60)
        if minutos < ASSOCIAR_A_PARTIR_MINUTOS or rotulo in associacoes:
            continue
        melhor = None
        for chave, resumo in tickets.items():
            texto = sugestao.similaridade(_texto_do_rotulo(rotulo), resumo if resumo != chave else "")
            junto = _perto(amostras, sorted(evidencias.get(chave, [])))
            antes = uso_anterior.get((rotulo, chave), 0)  # padrão: já andou junto em outros dias
            nota = min(0.6 * texto + 0.4 * junto + 0.15 * min(antes, 2), 1.0)
            if not melhor or nota > melhor[0]:
                melhor = (nota, chave, resumo, texto, junto, antes)
        if not melhor or melhor[0] < NOTA_MINIMA:
            continue
        nota, chave, resumo, texto, junto, antes = melhor
        motivos = [m for m, v in (("texto parecido", texto >= 0.25), ("usada junto hoje", junto >= 0.5),
                                  (f"usada junto em {antes} dia{'s' if antes > 1 else ''} anterior{'es' if antes > 1 else ''}", antes))
                   if v]
        perguntas.append({
            "id": f"associar:{rotulo}:{chave}",
            "tipo": "associar",
            "chave": chave,
            "rotulo": rotulo,
            "confianca": round(nota * 100),
            "pergunta": f"\"{rotulo}\" ({_hhmm(minutos)} hoje) faz parte de {chave}"
                        + (f", {resumo}?" if resumo != chave else "?"),
            "detalhe": f"Confiança {round(nota * 100)}%" + (f" · {' · '.join(motivos)}" if motivos else "")
                       + ". Com sim, passa a contar sozinha para essa demanda.",
        })
    return sorted(perguntas, key=lambda p: p["confianca"], reverse=True)


def _identificavel(a: dict) -> bool:
    """Só vale perguntar sobre o que tem nome: documento, aba guardada, VM ou pasta do VS Code."""
    return bool(a["titulo"]) or a["app"].lower() == "mstsc.exe"


def _texto_do_rotulo(rotulo: str) -> str:
    """'Excel: Validação menus' -> 'Validação menus'; 'VS Code: estoque (main)' -> 'estoque main'."""
    return rotulo.split(":", 1)[-1]


def _perto(amostras: list[dict], evidencias: list[datetime]) -> float:
    """Fração do tempo da atividade a até PERTO_MINUTOS de um trecho em que a issue estava aberta."""
    if not evidencias:
        return 0.0
    janela = timedelta(minutes=PERTO_MINUTOS)
    perto = 0
    for a in amostras:
        i = bisect_left(evidencias, a["_t"])
        vizinhos = evidencias[max(i - 1, 0):i + 1]
        if any(abs(a["_t"] - v) <= janela for v in vizinhos):
            perto += a["segundos"]
    return perto / max(sum(a["segundos"] for a in amostras), 1)
