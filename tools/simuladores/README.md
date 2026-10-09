# Simuladores de gravador (CFTV)

Três gravadores de mentira para testar o módulo de CFTV **sem equipamento**. Cada um responde no formato da
documentação do fabricante e deixa você mudar o cenário na hora (canal sem vídeo, disco com defeito, hora
errada…). **Só para laboratório** — não suba isto na rede de um cliente.

| Simulador | Porta | Usuário | Senha | Credencial no NOC |
|---|---|---|---|---|
| Intelbras / Dahua | 18081 | `admin` | `simulador123` | tipo `HTTP` |
| Hikvision | 18082 | `admin` | `simulador123` | tipo `HTTP` |
| UniFi Protect | 18083 | `admin` | `simulador123` | tipo `API` |

Precisa só de Python 3 (a biblioteca padrão; nada para instalar). Estes arquivos não vão no instalador do
middleware.

## 1. Subir

Na pasta `tools/simuladores`, um por janela:

- **Windows:** dê dois cliques em `subir-intelbras.bat`, `subir-hikvision.bat` ou `subir-unifi.bat`.
- **Linux:** `./subir-intelbras.sh`, `./subir-hikvision.sh` ou `./subir-unifi.sh`.

A janela mostra o endereço, o usuário e a senha. Para outra porta ou outra senha:
`subir-intelbras.bat 19081 admin outrasenha`. Para parar: `Ctrl+C`.

## 2. Ligar o módulo

O módulo de CFTV vem **desligado**. No middleware, abra **Sistema → NOC**, desça até **Módulos** e ligue
`CFTV Intelbras / Dahua` (ou Hikvision, ou UniFi). Também dá para ligar pelo NOC.

## 3. Cadastrar no NOC

Cadastre um equipamento no cliente do agente que roda **na mesma máquina** dos simuladores:

1. **Tipo:** gravador. **Endereço:** `127.0.0.1`.
2. **Verificação:** CFTV, com o **adaptador** do simulador (`intelbras`, `hikvision` ou `unifi`), **esquema**
   `http` e a **porta** da tabela (18081, 18082 ou 18083). Pode deixar também um ping.
3. **Credencial:** tipo `HTTP` (Intelbras e Hikvision) ou `API` (UniFi), usuário `admin`, senha `simulador123`.

Em até um minuto o equipamento aparece com canais, discos e a hora do gravador.

## 4. Mudar o cenário

Tudo em `http://127.0.0.1:<porta>/_sim/estado` — abrir no navegador mostra o cenário atual e os últimos pedidos
que o agente fez (só `GET`; no UniFi, mais o `POST` do login). Para mudar, mande só o que muda:

```sh
# canal 2 sem vídeo
curl -X POST http://127.0.0.1:18081/_sim/estado -d '{"semVideo": [2]}'

# canal 3 parou de gravar
curl -X POST http://127.0.0.1:18081/_sim/estado -d '{"naoGravando": [3]}'

# disco com defeito (estado: ok | defeito | cheio | ausente)
curl -X POST http://127.0.0.1:18082/_sim/estado -d '{"discos": [{"estado": "defeito", "totalGb": 2000, "livreGb": 800}]}'

# relógio do gravador 10 minutos adiantado (negativo = atrasado)
curl -X POST http://127.0.0.1:18083/_sim/estado -d '{"desvioS": 600}'

# 16 canais
curl -X POST http://127.0.0.1:18081/_sim/estado -d '{"canais": 16}'

# o gravador responde 200 vazio (ou "malformada": 200 com lixo) — o agente tem de acusar falha
curl -X POST http://127.0.0.1:18081/_sim/estado -d '{"resposta": "vazia"}'

# Hikvision como DVR analógico (canais por resDesc = NO VIDEO)
curl -X POST http://127.0.0.1:18082/_sim/estado -d '{"analogico": true}'

# tudo de volta ao começo
curl -X POST http://127.0.0.1:18081/_sim/reiniciar
```

No Windows, no **cmd**, troque as aspas: `curl -X POST http://127.0.0.1:18081/_sim/estado -d "{\"semVideo\": [2]}"`.

Para testar **senha recusada**, cadastre a credencial com outra senha. O agente espera 30 minutos antes de
tentar de novo com a mesma senha errada (gravador de verdade bloqueia o usuário); trocar a senha no cadastro tenta
na hora.

## O que o agente mostra

- `ok` quando leu e não achou problema; senão o motivo ("canal 2 sem vídeo", "disco 1 com defeito", "hora do
  gravador 600 s adiantada", "senha recusada", "sem credencial").
- Depois de 3 falhas seguidas (o padrão), o gravador fica **degradado** se ainda responde ao ping.
- Eventos `cftv.*` na hora em que o cenário muda: `canal_sem_video`, `canal_voltou`, `disco_com_defeito`,
  `disco_normalizou`, `parou_de_gravar`, `voltou_a_gravar`, `hora_errada` (mais de 120 s), `hora_certa`.
- No UniFi não existe "disco cheio": o console grava por cima do mais antigo.

## 6. DVR falso do acesso pelo navegador (NOC, ADR 0043)

O simulador Intelbras também faz o papel de DVR para o laboratório do acesso ao DVR do NOC
(`noc-workconnect/scripts/laboratorio-cftv`):

```sh
python tools/simuladores/intelbras.py --host 0.0.0.0 --porta 80 --pagina legado-4 \
  --rtsp-porta 554 --rtsp-destino 127.0.0.1:8554
```

- `GET /plugin.data`, `GET /` e `GET /plugin/webplugin.exe` respondem **sem senha**, como nos DVRs de verdade. A
  variante da página vem de `--pagina` ou de `estado.pagina`: `legado-4` (pede `WebActiveX.Plugin.4.1.511.0`),
  `legado-5`, `moderno` (sem plugin) ou `desconhecido` (plugin fora do catálogo do NOC). O instalador é um
  executável mínimo, sem assinatura.
- **Bloqueio**: depois de `bloqueioAposFalhas` senhas erradas (5), por `bloqueioS` segundos — HTTP e RTSP no mesmo
  contador. `falhas` e `tentativasRtsp` aparecem em `/_sim/estado`: é por eles que se prova que o NOC tenta uma vez.
- **RTSP** (`rtsp.py`): digest no DESCRIBE e os caminhos Intelbras/Hikvision traduzidos para um servidor de vídeo
  (mediamtx): `/cam/realmonitor?channel=N&subtype=S` → `/chNsS`, `/cam/playback?channel=N&…` → `/pbN`,
  `/Streaming/Channels/N01|N02` e `/Streaming/tracks/N01`. Caminho desconhecido é 404.
