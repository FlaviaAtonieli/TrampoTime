"""Linha de comando: python -m trampotime [comando] [data]

Sem comando, abre o TrampoTime no navegador (http://127.0.0.1:8765) com o observador ligado.

Comandos:
  abrir               o mesmo que sem comando
  iniciar             liga o TrampoTime em segundo plano, sem janela (não abre o navegador)
  parar               desliga o TrampoTime que está em segundo plano
  instalar-inicio     liga o TrampoTime sozinho quando você entra no Windows
  remover-inicio      desfaz o instalar-inicio
  agenda              conecta a Google Agenda (precisa do credentials.json)
  observar            só o observador, no terminal (não use junto com o app aberto)
  foco [CHAVE|limpar] declara o ticket em que você está (vale até trocar)
  testar              confere se o token funciona (só com USAR_JIRA=sim)
  gerar [data]        cria o rascunho e salva rascunho-AAAA-MM-DD.csv
  ver [data]          mostra o rascunho e os problemas
  revisar [data]      lê de volta rascunho-AAAA-MM-DD.csv depois que você editou
  aprovar [data]      valida e marca o dia como aprovado
  simular [data]      mostra o que seria enviado ao Tempo
  enviar [data]       envia ao Tempo (pede confirmação)

A data é AAAA-MM-DD; sem data, usa hoje.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import agenda, observador, planilha
from .api import servico
from .modelos import Linha
from .servico import ErroDeFluxo


def main(argv: list[str]) -> int:
    if argv and argv[0] in {"-h", "--help", "ajuda"}:
        print(__doc__)
        return 0
    comando = argv[0] if argv else "abrir"
    if EXE:
        os.chdir(PASTA)  # .env, trampotime.db e os logs ficam ao lado do TrampoTime.exe
    if comando in COMANDOS_DE_PROCESSO:
        return COMANDOS_DE_PROCESSO[comando]()
    s = servico()

    if comando in {"abrir", "servidor"}:  # servidor: o que o iniciar roda no .exe, sem abrir o navegador
        import threading
        import webbrowser

        import uvicorn

        from .api import app

        if comando == "abrir":
            print("TrampoTime em http://127.0.0.1:8765  (feche esta janela para parar)", flush=True)
            threading.Timer(1.5, webbrowser.open, ["http://127.0.0.1:8765"]).start()
        uvicorn.run(app, host="127.0.0.1", port=PORTA, log_level="warning")
        return 0
    if comando == "agenda":
        c = s.config
        if not c.google_credentials.exists():
            print(f"Coloque o arquivo {c.google_credentials} nesta pasta primeiro (veja o README).")
            return 1
        agenda.conectar(c.google_credentials, c.google_token)
        print("Google Agenda conectada.")
        return 0

    if comando == "foco":
        if len(argv) > 1:
            s.banco.definir_foco(None if argv[1].lower() == "limpar" else argv[1].upper())
        print(f"Ticket em foco: {s.banco.foco_atual() or '(nenhum)'}")
        return 0
    if comando == "observar":
        c = s.config
        print(f"Observando janelas a cada {c.intervalo_segundos} s. Ticket em foco: {s.banco.foco_atual() or '(nenhum)'}. "
              "Ctrl+C para parar.", flush=True)
        try:
            observador.observar(s.banco, lambda: s.config, mostrar=True)
        except KeyboardInterrupt:
            print("Observador parado.")
        return 0

    dia = date.fromisoformat(argv[1]) if len(argv) > 1 else date.today()
    arquivo = Path(f"rascunho-{dia.isoformat()}.csv")
    try:
        if comando == "testar":
            if s.jira is None:
                print("Modo planilha (USAR_JIRA=nao): o TrampoTime não fala com o Jira, nada para testar.")
                return 0
            eu = s.jira.eu()
            print(f"Token ok: {eu.get('displayName')} ({eu.get('name')})")
        elif comando == "gerar":
            dados = s.gerar(dia)
            linhas = [Linha.de_dict(l) for l in dados["linhas"]]
            arquivo.write_text(planilha.para_csv(linhas, [l["periodos"] for l in dados["linhas"]]), encoding="utf-8-sig")
            _mostrar(dados)
            print(f"\nPlanilha salva em {arquivo}. Edite e rode: python -m trampotime revisar {dia}")
        elif comando == "ver":
            _mostrar(s.ver(dia))
        elif comando == "revisar":
            _mostrar(s.revisar(dia, planilha.de_csv(arquivo.read_text(encoding="utf-8-sig"))))
        elif comando == "aprovar":
            _mostrar(s.aprovar(dia))
            print(f"\nAprovado. Confira com: python -m trampotime simular {dia}")
        elif comando == "simular":
            print(json.dumps(s.publicar(dia, simular=True), ensure_ascii=False, indent=2))
        elif comando == "enviar":
            print(json.dumps(s.publicar(dia, simular=True), ensure_ascii=False, indent=2))
            if input("\nEnviar estes apontamentos ao Tempo? (s/n) ").strip().lower() != "s":
                print("Nada foi enviado.")
                return 1
            print(json.dumps(s.publicar(dia, simular=False), ensure_ascii=False, indent=2))
        else:
            print(f"Comando desconhecido: {comando}\n{__doc__}")
            return 2
    except ErroDeFluxo as e:
        print(f"Erro: {e}")
        return 1
    return 0


def _mostrar(dados: dict) -> None:
    print(f"{dados['data']}  status: {dados['status']}  já no Tempo: {dados['ja_lancado_minutos']} min")
    for l in dados["linhas"]:
        quando = ", ".join(f"{a}-{b}" for a, b in l.get("periodos", []))
        print(f"  {l['chave'] or '(sem ticket)':<18} {l['minutos']:>4} min  {quando:<13} {l['acao']}  [{l['origem']}]")
    total = dados["total_minutos"]
    print(f"  Total do dia: {total // 60}h{total % 60:02d}")
    observado = dados.get("observado_minutos", 0)
    if observado:
        print(f"  Observado ativo no computador: {observado // 60}h{observado % 60:02d}")
    if dados.get("sem_ticket"):
        print("  Tempo sem ticket (para lembrar):", ", ".join(f"{a['app']} {a['minutos']} min" for a in dados["sem_ticket"][:6]))
    for p in dados["problemas"]:
        print(f"  ! {p}")


# Segundo plano e início com o Windows

EXE = getattr(sys, "frozen", False)  # rodando como TrampoTime.exe (PyInstaller), não pelo Python
PASTA = Path(sys.executable).parent if EXE else Path(__file__).resolve().parent.parent  # onde ficam .env, trampotime.db e os logs
PORTA = 8765
ATALHO = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/TrampoTime.lnk"


def _rodando() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORTA}/saude", timeout=2):
            return True
    except OSError:
        return False


def _iniciar() -> int:
    import subprocess
    if _rodando():
        print(f"O TrampoTime já está rodando em http://127.0.0.1:{PORTA}")
        return 0
    python = Path(sys.executable).with_name("python.exe")  # pythonw não tem saída para o log
    comando = ([sys.executable, "servidor"] if EXE else
               [str(python), "-m", "uvicorn", "trampotime.api:app", "--host", "127.0.0.1", "--port", str(PORTA), "--log-level", "warning"])
    with open(PASTA / "trampotime.log", "ab") as log, open(PASTA / "trampotime-erros.log", "ab") as erros:
        subprocess.Popen(
            comando,
            # no .exe, o servidor desempacota a própria cópia; senão usaria a pasta temporária deste processo, apagada ao sair
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"} if EXE else None,
            cwd=PASTA, stdout=log, stderr=erros, stdin=subprocess.DEVNULL,
            creationflags=0x08000000 | 0x00000008 | 0x00000200,  # sem janela, desligado deste terminal, grupo próprio
        )
    print(f"TrampoTime ligado em segundo plano: http://127.0.0.1:{PORTA}")
    return 0


def _parar() -> int:
    import os
    import subprocess
    # Acha o servidor pela linha de comando (iniciar = uvicorn trampotime.api:app; abrir = "-m trampotime" sem comando;
    # no .exe, qualquer TrampoTime.exe), sem pegar este próprio processo nem o lançador do .venv ou do .exe que o abriu.
    eu = {os.getpid(), os.getppid()}
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='TrampoTime.exe'\" | "
              "Where-Object { $_.Name -eq 'TrampoTime.exe' -or $_.CommandLine -like '*trampotime.api:app*' -or $_.CommandLine -match '-m trampotime(\\s+abrir)?\\s*$' } | "
              "ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True)
    alvos = [int(p) for p in r.stdout.split() if p.isdigit() and int(p) not in eu]
    for pid in alvos:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    print("TrampoTime parado." if alvos else "O TrampoTime não estava rodando.")
    return 0


def _instalar_inicio() -> int:
    import subprocess
    alvo, argumentos = (sys.executable, "iniciar") if EXE else (Path(sys.executable).with_name("pythonw.exe"), "-m trampotime iniciar")
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{ATALHO}'); "
              f"$s.TargetPath = '{alvo}'; $s.Arguments = '{argumentos}'; "
              f"$s.WorkingDirectory = '{PASTA}'; $s.Description = 'TrampoTime: registra o dia para o Tempo'; "
              f"$s.WindowStyle = 7; $s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=True)
    print(f"Pronto: o TrampoTime liga sozinho quando você entra no Windows.\nAtalho: {ATALHO}")
    return 0


def _remover_inicio() -> int:
    if ATALHO.exists():
        ATALHO.unlink()
        print("O TrampoTime não liga mais sozinho com o Windows.")
    else:
        print("O TrampoTime não estava configurado para ligar com o Windows.")
    return 0


COMANDOS_DE_PROCESSO = {"iniciar": _iniciar, "parar": _parar, "instalar-inicio": _instalar_inicio,
                        "remover-inicio": _remover_inicio}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
