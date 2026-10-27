"""Rascunho em CSV (abre no Excel ou no Google Planilhas) para revisão."""
import csv
import io

from .modelos import Linha

COLUNAS = ["ticket", "acao", "minutos", "origem", "inicio", "fim"]
ORIGENS_REUNIAO = {"agenda", "meet"}


def para_csv(linhas: list[Linha], periodos: list[list[list[str]]] | None = None) -> str:
    """Uma linha por ticket. inicio e fim vêm da agenda do dia (primeiro início, último fim)."""
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(COLUNAS)
    for n, l in enumerate(linhas):
        trechos = periodos[n] if periodos else []
        inicio = trechos[0][0] if trechos else (l.inicio or "")
        fim = trechos[-1][1] if trechos else ""
        w.writerow([l.chave or "", l.acao, l.minutos, l.origem, inicio, fim])
    return saida.getvalue()


def de_csv(texto: str) -> list[Linha]:
    texto = texto.lstrip("﻿")
    delimitador = ";" if texto.splitlines()[0].count(";") >= texto.splitlines()[0].count(",") else ","
    linhas = []
    for row in csv.DictReader(io.StringIO(texto), delimiter=delimitador):
        if not any((v or "").strip() for v in row.values()):
            continue
        tempo = minutos((row.get("minutos") or "0").strip())
        origem = (row.get("origem") or "manual").strip() or "manual"
        inicio = (row.get("inicio") or "").strip() if origem in ORIGENS_REUNIAO else ""  # só reunião tem horário fixo
        linhas.append(Linha((row.get("ticket") or "").strip().upper() or None, (row.get("acao") or "").strip(), tempo,
                            origem, inicio or None))
    return linhas


def minutos(valor: str) -> int:
    """Aceita 90, 1h30 ou 1:30."""
    v = valor.lower().replace(" ", "")
    if "h" in v:
        h, _, m = v.partition("h")
        return int(h or 0) * 60 + int(m or 0)
    if ":" in v:
        h, m = v.split(":")
        return int(h) * 60 + int(m)
    return int(float(v.replace(",", ".")))
