"""Leitura das reuniões do dia na Google Agenda (opcional)."""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .modelos import Reuniao, achar_chave

ESCOPO = ["https://www.googleapis.com/auth/calendar.readonly"]


def situacao(credenciais: Path, token: Path) -> str:
    """sem_credenciais, desconectada ou conectada."""
    if not credenciais.exists():
        return "sem_credenciais"
    return "conectada" if token.exists() else "desconectada"


def conectar(credenciais: Path, token: Path) -> None:
    """Abre o navegador uma vez para você autorizar a leitura da agenda."""
    _servico(credenciais, token)


def reunioes_do_dia(dia: date, credenciais: Path, token: Path, fuso: str, ignorar: tuple[str, ...]) -> list[Reuniao]:
    if situacao(credenciais, token) != "conectada":
        return []  # sem agenda conectada, as reuniões vêm da aba do Meet
    servico = _servico(credenciais, token)
    tz = ZoneInfo(fuso)
    inicio = datetime.combine(dia, time.min, tz)
    eventos = servico.events().list(
        calendarId="primary",
        timeMin=inicio.isoformat(),
        timeMax=(inicio + timedelta(days=1)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])
    return filtrar_eventos(eventos, ignorar)


def filtrar_eventos(eventos: list[dict], ignorar: tuple[str, ...]) -> list[Reuniao]:
    reunioes = []
    for e in eventos:
        titulo = e.get("summary", "(sem título)")
        if "dateTime" not in e.get("start", {}):
            continue  # dia inteiro
        if any(p in titulo.lower() for p in ignorar):
            continue
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees", [])):
            continue
        ini = datetime.fromisoformat(e["start"]["dateTime"])
        fim = datetime.fromisoformat(e["end"]["dateTime"])
        minutos = int((fim - ini).total_seconds() // 60)
        if minutos > 0:
            reunioes.append(Reuniao(titulo, minutos, achar_chave(titulo, e.get("description")), ini.strftime("%H:%M")))
    return reunioes


def _servico(credenciais: Path, token: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token), ESCOPO) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Abre o navegador uma vez para você autorizar a leitura da agenda.
            creds = InstalledAppFlow.from_client_secrets_file(str(credenciais), ESCOPO).run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
