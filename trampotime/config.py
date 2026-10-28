import os
from dataclasses import dataclass, field, replace
from datetime import time
from pathlib import Path

from dotenv import load_dotenv


# Apps (.exe) e abas (parte do título) de trabalho cujo título é guardado, para o histórico do dia.
GUARDAR_TITULOS = (
    "POWERPNT.EXE", "WINWORD.EXE", "EXCEL.EXE", "CamtasiaStudio.exe",
    "- Chat", "Google Vids", "Google Docs", "Documentos Google", "Google Sheets", "Planilhas Google",
    "Google Slides", "Apresentações Google",
    "TOTVS",  # qualquer aba ou janela com TOTVS no título (agenda, e-mail, TDN, portais)
    "Copiloto", "Copilot", "Azure",  # copiloto interno e Azure (portal, DevOps, apps de desktop)
    "Google Drive", "Gemini", "Google Workspace", "Formulários Google", "Google Forms",
)


@dataclass(frozen=True)
class Config:
    jira_url: str = ""
    jira_token: str = ""
    usar_jira: bool = False
    jornada_minutos: int = 450
    bloco_minutos: int = 15
    inicio_dia: time = time(9, 0)
    fuso: str = "America/Sao_Paulo"
    ticket_reunioes: str | None = None
    ignorar_eventos: tuple[str, ...] = field(default_factory=tuple)
    projetos: tuple[str, ...] = field(default_factory=tuple)
    intervalo_segundos: int = 5
    ocioso_minutos: int = 5
    manter_dias: int = 5  # detalhe das amostras guardado depois que o dia é lançado
    guardar_titulos: tuple[str, ...] = GUARDAR_TITULOS
    avisos: bool = True  # notificações do Windows
    google_credentials: Path = Path("credentials.json")
    google_token: Path = Path("token.json")
    banco: Path = Path("trampotime.db")


def carregar(env_file: str | None = ".env") -> Config:
    """Lê a configuração do .env e das variáveis de ambiente. O token nunca é impresso."""
    if env_file:
        load_dotenv(env_file)

    jira_url = os.environ.get("JIRA_URL", "").rstrip("/")
    jira_token = os.environ.get("JIRA_TOKEN", "")
    usar_jira = os.environ.get("USAR_JIRA", "nao").strip().lower() in {"sim", "s", "1", "true"}
    if usar_jira and (not jira_url or not jira_token):
        raise SystemExit("Com USAR_JIRA=sim, configure JIRA_URL e JIRA_TOKEN no arquivo .env (veja .env.example).")

    hora, minuto = os.environ.get("INICIO_DIA", "09:00").split(":")
    credenciais = os.environ.get("GOOGLE_CREDENTIALS", "credentials.json")
    ignorar = os.environ.get("IGNORAR_EVENTOS", "")
    projetos = os.environ.get("PROJETOS", "")

    return Config(
        jira_url=jira_url,
        jira_token=jira_token,
        usar_jira=usar_jira,
        jornada_minutos=int(os.environ.get("JORNADA_MINUTOS", "450")),
        bloco_minutos=int(os.environ.get("BLOCO_MINUTOS", "15")),
        inicio_dia=time(int(hora), int(minuto)),
        fuso=os.environ.get("FUSO", "America/Sao_Paulo"),
        ticket_reunioes=os.environ.get("TICKET_REUNIOES") or None,
        ignorar_eventos=tuple(p.strip().lower() for p in ignorar.split(",") if p.strip()),
        projetos=tuple(p.strip().upper() for p in projetos.split(",") if p.strip()),
        intervalo_segundos=int(os.environ.get("INTERVALO_SEGUNDOS", "5")),
        ocioso_minutos=int(os.environ.get("OCIOSO_MINUTOS", "5")),
        google_credentials=Path(credenciais),
        google_token=Path(os.environ.get("GOOGLE_TOKEN", "token.json")),
        banco=Path(os.environ.get("BANCO", "trampotime.db")),
    )


# Preferências editáveis na página (guardadas no trampotime.db, valem por cima do .env)

def preferencias(c: Config) -> dict:
    return {
        "projetos": list(c.projetos),
        "jornada_minutos": c.jornada_minutos,
        "bloco_minutos": c.bloco_minutos,
        "inicio_dia": c.inicio_dia.strftime("%H:%M"),
        "ocioso_minutos": c.ocioso_minutos,
        "ticket_reunioes": c.ticket_reunioes or "",
        "ignorar_eventos": list(c.ignorar_eventos),
        "manter_dias": c.manter_dias,
        "guardar_titulos": list(c.guardar_titulos),
        "avisos": c.avisos,
    }


def aplicar_preferencias(c: Config, p: dict) -> Config:
    """Valida e aplica as preferências. Erros viram ValueError com mensagem para a tela."""
    mudancas = {}
    if "projetos" in p:
        projetos = tuple(x.strip().upper() for x in _lista(p["projetos"]) if x.strip())
        if any(not x.replace("_", "").isalnum() or not x[0].isalpha() for x in projetos):
            raise ValueError("Projetos são prefixos de chave, como DMANESTOQ.")
        mudancas["projetos"] = projetos
    for campo, minimo in (("jornada_minutos", 15), ("bloco_minutos", 1), ("ocioso_minutos", 1), ("manter_dias", 3)):
        if campo in p:
            valor = int(p[campo])
            if valor < minimo:
                raise ValueError(f"{campo} precisa ser pelo menos {minimo}.")
            mudancas[campo] = valor
    if "inicio_dia" in p:
        hora, minuto = str(p["inicio_dia"]).split(":")
        mudancas["inicio_dia"] = time(int(hora), int(minuto))
    if "ticket_reunioes" in p:
        mudancas["ticket_reunioes"] = str(p["ticket_reunioes"] or "").strip().upper() or None
    if "ignorar_eventos" in p:
        mudancas["ignorar_eventos"] = tuple(x.strip().lower() for x in _lista(p["ignorar_eventos"]) if x.strip())
    if "avisos" in p:
        mudancas["avisos"] = bool(p["avisos"])
    if "guardar_titulos" in p:
        mudancas["guardar_titulos"] = tuple(x.strip() for x in _lista(p["guardar_titulos"]) if x.strip())
    return replace(c, **mudancas)


def _lista(valor) -> list[str]:
    return valor.split(",") if isinstance(valor, str) else [str(x) for x in valor]
