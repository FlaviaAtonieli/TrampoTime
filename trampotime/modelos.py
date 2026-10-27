import re
from dataclasses import asdict, dataclass

CHAVE_TICKET = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


_ISSUE_COM_NOME = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]+-\d+)\s*(?:[-–:|]\s*(.*?))?\s*$")


def separar_issue(texto: str | None) -> tuple[str | None, str | None]:
    """'DMANESTOQ-15650 - VacationFlow' -> ('DMANESTOQ-15650', 'VacationFlow'); 'dmanestoq-1' -> ('DMANESTOQ-1', None).
    Texto que não começa com uma chave -> (None, None)."""
    m = _ISSUE_COM_NOME.match(texto or "")
    if not m:
        return None, None
    return m.group(1).upper(), (m.group(2) or None)


def achar_chave(*textos: str | None) -> str | None:
    """Primeira chave de ticket (ex. DMANESTOQ-15684) encontrada nos textos."""
    for texto in textos:
        if texto and (m := CHAVE_TICKET.search(texto)):
            return m.group(1)
    return None


@dataclass
class Reuniao:
    titulo: str
    minutos: int
    chave: str | None = None
    inicio: str | None = None  # HH:MM real da reunião
    origem: str = "agenda"  # agenda ou meet


@dataclass
class TicketDoDia:
    chave: str
    resumo: str
    peso: int  # ações suas no Jira (edições, transições, comentários) ou minutos observados nas janelas
    origem: str = "jira"


@dataclass
class Linha:
    chave: str | None
    acao: str
    minutos: int
    origem: str  # agenda, meet, jira, janelas, completar ou manual
    inicio: str | None = None  # HH:MM fixo (reuniões); as outras linhas preenchem o resto do dia

    def para_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict) -> "Linha":
        """Aceita a linha como vem da API (com campos extras, como periodos)."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
