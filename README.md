# TrampoTime

Apontamento de horas no Tempo (Jira Data Center), montado a partir do que você realmente fez no dia.

API local que monta o rascunho do dia com 7h30, deixa você revisar numa planilha e só então envia os apontamentos ao Tempo. Roda no seu computador, então precisa estar na rede ou VPN da TOTVS para falar com o Jira.

## Instalação (uma vez)

Precisa do Python 3.10 ou mais novo.

```bash
git clone https://github.com/<seu-usuario>/trampotime.git
cd trampotime
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copie `.env.example` para `.env` e cole o seu token pessoal do Jira em `JIRA_TOKEN`. O token fica só nesse arquivo, no seu computador. Não mande o token por chat nem e-mail.

Confira se funcionou:

```bash
python -m trampotime testar
```

### Google Agenda (opcional)

Sem a agenda, as reuniões vêm da aba do Meet. Para ler a agenda, crie um cliente OAuth do tipo "App para computador" no Google Cloud, com a Google Calendar API ativada, e salve o arquivo baixado como `credentials.json` nesta pasta e clique em **Conectar Google Agenda** na configuração (ou rode `python -m trampotime agenda`). Se o Google Workspace da TOTVS bloquear isso, siga sem agenda e ajuste as reuniões na planilha.

## Modo planilha (padrão, sem Jira)

Com `USAR_JIRA=nao`, o TrampoTime não fala com o Jira: ele observa as janelas do Windows e monta a planilha do dia para você lançar no Tempo à mão.

```bash
python -m trampotime        # abre http://127.0.0.1:8765 e liga o observador; deixe a janela aberta
```

Na primeira vez a página pede a configuração: projetos (ex. DMANESTOQ), jornada, início do dia, arredondamento, ausência e ticket das reuniões. Isso fica no `trampotime.db` e vale por cima do `.env`.

Na página:

- **Contador:** "5h10 de 7h30", dividido em tickets, reuniões e tempo sem ticket.
- **Em que você está?:** o ticket em foco, que vale até você trocar.
- **Planilha do dia:** gere, edite (aceita 90, 1h30 ou 1:30), salve e aprove. Use **Copiar** em cada linha para lançar no Tempo e, no fim, clique em **Marquei no Tempo** para fechar o dia.
- **Dias para lançar:** os últimos dias úteis com o status, para nenhum ficar esquecido.

Como o tempo é atribuído:

- A chave no título da janela ganha do foco, por exemplo a aba do Jira "[DMANESTOQ-15684] ...".
- Ficar mais de alguns minutos sem teclado ou mouse não conta.
- **Reuniões:** com a Google Agenda conectada, as reuniões vêm dela. Sem a agenda, o tempo com a aba do Google Meet em foco vira reunião (não conta como ausência) e vai para o ticket do título ou para o ticket de reuniões.
- Títulos e apps ficam só no `trampotime.db`, no seu computador.

Sem a página, pelo terminal: `python -m trampotime observar`, `foco DMANESTOQ-15684`, `gerar`, `agenda`.

## Uso do dia a dia com Jira (USAR_JIRA=sim)

```bash
python -m trampotime gerar            # cria rascunho-AAAA-MM-DD.csv para hoje
# abra o CSV no Excel, ajuste tickets, ações e minutos (aceita 90, 1h30 ou 1:30), salve
python -m trampotime revisar          # lê a planilha de volta e mostra o total
python -m trampotime aprovar          # valida: todo mundo com ticket e total de 7h30
python -m trampotime simular          # mostra exatamente o que iria para o Tempo
python -m trampotime enviar           # envia, depois de você confirmar com "s"
```

Para outro dia, passe a data: `python -m trampotime gerar 2026-10-20`.

## Como o rascunho é montado

1. Desconta o que já está lançado no Tempo naquele dia.
2. Reuniões com chave de ticket no título ou na descrição (ex. DMANESTOQ-15684) vão para aquele ticket.
3. Reuniões sem chave vão para `TICKET_REUNIOES`, ou ficam sem ticket para você preencher.
4. O tempo restante é dividido entre os tickets em que você mexeu no dia (edições, transições e comentários seus), proporcional ao número de ações.
5. Tudo é arredondado em blocos de 15 minutos, fechando exatamente a jornada.

Proteções: nada é enviado sem aprovação; antes de enviar, o Tempo é consultado de novo e o envio é cancelado se algo mudou; um dia já enviado não é enviado de novo.

## API HTTP

```bash
uvicorn trampotime.api:app --port 8765
```

Documentação interativa em http://localhost:8765/docs.

| Rota | O que faz |
| --- | --- |
| `GET /saude` | Confere o token |
| `POST /dias/{data}/rascunho` | Gera o rascunho |
| `GET /dias/{data}/rascunho?formato=csv` | Rascunho em JSON ou CSV |
| `PUT /dias/{data}/rascunho` | Envia o CSV revisado (corpo `text/csv`) |
| `POST /dias/{data}/aprovar` | Valida e aprova |
| `POST /dias/{data}/publicar?simular=true` | Simula; com `simular=false` envia |
| `GET /dias/{data}/tempo` | Minutos já lançados no Tempo |

## Testes

```bash
pytest
```

Os testes usam um Jira e um Tempo falsos e não fazem nenhuma chamada real.

## Gerar o .exe (piloto)

```powershell
.\empacotar\gerar_exe.ps1
```

Cria `dist\TrampoTime-piloto.zip` com o `TrampoTime.exe`, um `.env` de exemplo (sem token) e o `LEIA-ME.txt`. Quem recebe só descompacta numa pasta fixa e dá dois cliques no `.exe`; o banco, o `.env` e os logs ficam ao lado dele. Os comandos são os mesmos: `TrampoTime.exe iniciar`, `parar`, `instalar-inicio`. O `.exe` não é assinado, então o Windows SmartScreen avisa na primeira vez ("Mais informações" > "Executar assim mesmo").
