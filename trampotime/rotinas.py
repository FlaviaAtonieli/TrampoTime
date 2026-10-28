"""Rotinas: padrões que se repetem nos dias confirmados (planilhas aprovadas ou lançadas).

A fonte são as planilhas, que ficam guardadas mesmo depois que o detalhe do dia é apagado, e o histórico
compacto de uso (atividade -> ticket -> minutos por dia), gravado quando o dia é marcado como lançado.

- Reunião recorrente: o mesmo nome em pelo menos MINIMO_DIAS dos dias confirmados recentes (e em 60% deles),
  com o horário típico (mediana), a duração típica e o ticket mais usado.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

MINIMO_DIAS = 3
OLHAR_DIAS = 15  # dias corridos para trás
ORIGENS_REUNIAO = {"agenda", "meet"}


def reunioes_recorrentes(planilhas: list[tuple[str, list[dict]]], hoje: date) -> list[dict]:
    """planilhas: [(data ISO, linhas)] dos dias confirmados. Devolve as reuniões que viraram rotina."""
    desde = (hoje - timedelta(days=OLHAR_DIAS)).isoformat()
    dias = [(d, linhas) for d, linhas in planilhas if desde <= d < hoje.isoformat()]
    if len(dias) < MINIMO_DIAS:
        return []
    por_nome: dict[str, dict] = defaultdict(lambda: {"dias": set(), "inicios": [], "minutos": [], "chaves": Counter()})
    for d, linhas in dias:
        for l in linhas:
            if l.get("origem") not in ORIGENS_REUNIAO or not l.get("acao"):
                continue
            r = por_nome[l["acao"]]
            r["dias"].add(d)
            r["minutos"].append(l["minutos"])
            if l.get("inicio"):
                h, m = l["inicio"].split(":")
                r["inicios"].append(int(h) * 60 + int(m))
            if l.get("chave"):
                r["chaves"][l["chave"]] += 1
    rotinas = []
    for nome, r in por_nome.items():
        n = len(r["dias"])
        if n < MINIMO_DIAS or n / len(dias) < 0.6:
            continue
        chave, vezes = r["chaves"].most_common(1)[0] if r["chaves"] else (None, 0)
        inicio = round(median(r["inicios"])) if r["inicios"] else None
        rotinas.append({
            "nome": nome, "dias": n, "de": len(dias),
            "inicio": f"{inicio // 60:02d}:{inicio % 60:02d}" if inicio is not None else None,
            "minutos": round(median(r["minutos"])),
            "chave": chave, "chave_constante": bool(chave) and vezes / n >= 0.8,
        })
    return sorted(rotinas, key=lambda r: (r["inicio"] or "99:99", r["nome"]))
