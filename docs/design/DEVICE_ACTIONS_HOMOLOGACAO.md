# Device actions — homologação ao vivo por vendor

Data: 2026-07-31 · Homologado por: Luiz (lab de homologação) · Release: v2.7.0

Método: probes/scripts descartáveis em `scripts/lab/` (não versionados) contra
os aparelhos de lab, com credencial `admin/admin`. Cada mecanismo só entrou na
matriz depois de atender ao **critério de homologação**:

1. executa via HTTP com as credenciais da chain (as mesmas do apply);
2. efeito confirmado **fisicamente** no aparelho;
3. repetível;
4. sem reboot — ou com o reboot documentado;
5. não degrada o registro SIP.

O que não fechou os 5 pontos ficou como "não suportado" (fora da capability).

## Matriz vendor × ação

| Vendor / modelo    | normalize | set_ip | Mecanismo                         | Reboot |
|--------------------|-----------|--------|-----------------------------------|--------|
| Yealink T31G       | ✅        | —      | Action URI (`GET /servlet?key=…`) | não    |
| FlyingVoice P10    | ✅        | —      | form-replay `preference` → `/goform/setSip` | **sim, ao mudar DND** |
| HTEK UC902G        | ✅ (volume + DND) | — | `hl_provision` parcial (P-codes) | **sim, sempre** |
| Intelbras V3501/V5501 | ✅ (volume + DND + campainha) | — | Action URI `DNDOff` + `sysConf` parcial | não |
| Intelbras S3002    | ❌        | —      | sem unidade em lab para homologar | —      |
| Intelbras TIP 125i | ✅ (volume + DND) | — | `UPDATE` no banco (`db.cgi`) + `notify.cgi` | não |

Cobertura: **5 dos 6 adapters** do sistema (HTEK, Yealink, FlyingVoice,
Intelbras V-series e Intelbras TIP 125i). Só o S3002 segue pendente, por
falta de hardware.

`set_ip` está no catálogo (`vendors/base.py`) com guard de confirmação na
API/UI, mas **nenhum vendor homologou** até agora.

## Detalhe por vendor

### Yealink T31G — Action URI

- `GET https://{ip}/servlet?key=<KEY>` com **Basic Auth** (não usa a sessão
  web do login RSA — sem Basic devolve 401).
- Normalize = `DNDOff` → `MUTE` → `VOLUME_UP` ×10.
  - `MUTE` é **toggle**: em idle o efeito líquido é destravar o mute.
  - Volume é **relativo** (1 passo por request); a escala vai a ~15, então 10
    presses a partir de qualquer ponto garantem o teto.
- **Gate**: o aparelho só aceita Action URI de IPs na "Action URI Allow IP
  List" (Features → Remote Control). O middleware **não** provisiona essa
  lista — o `yealink_template.cfg` não emite chave `features.*` nenhuma. O IP
  do middleware precisa estar liberado no aparelho, à mão ou pelo sistema que
  provisionou o telefone; sem isso o `normalize` responde 403 e a mensagem de
  erro diz exatamente onde liberar.

### FlyingVoice P10 — form-replay

- Login de sessão (cookie `ASPSSIONID`), `GET /phone/Phone_Preference.asp`,
  replay do form `preference` (~214 campos preservados) com overrides, POST
  HTTP/1.0 em `/goform/setSip`.
- Overrides do normalize: `DBID_DND_ENABLE=0` + volumes no máximo da escala
  **0-9**: `DBID_HF_OUT_VOL`, `DBID_HF_IN_VOL`, `DBID_HEADSET_OUT_VOL`,
  `DBID_HEADSET_IN_VOL`, `DBID_RING_VOL`.
- **Reinicia ao mudar o DND** (o campo não é ImmeEffect); volume aplica na
  hora. `ActionResult.rebooted=True`.
- Regra inviolável do adapter vale aqui também: **replay nunca emite valor
  próprio para chaves de rede** (whitelist).

### HTEK UC902G — P-codes via provisionamento parcial

- Upload de `hl_provision` XML **parcial** (o aparelho preserva o que não foi
  listado): `P8503` (`RingVolume`, escala 0-14) = `14` e `P1305`
  (`DND_Enable`, 0/1) = `0`.
- O HTEK **reinicia ao aceitar qualquer config** — inclusive o normalize.
  `ActionResult.rebooted=True`.
- **DND (achado em 2026-08-03, na homologação da release):** o P-code
  **não aparece em nenhuma página web** (lá só há refs cosméticas —
  `P24877`/`P25104` — e códigos de sync XSI/FAC em `features.htm`). Foi
  localizado no **export completo da config**: `GET /download_xml_cfg`
  (~188 KB, ~3600 P-codes, com atributo `para="Label"` por código) →
  `<P1305 para="DND_Enable">1</P1305>` com o DND fisicamente ligado.
- **Credencial**: o firmware desafia com `Basic realm="IP Phone"`; se o
  ambiente já aplicou `nova_web_password`, o admin/admin de fábrica deixa de
  valer — a chain de credenciais do middleware cobre isso, mas probes manuais
  precisam usar a senha atual.
- ⚠️ **CUIDADO no recon**: `api-sys_operation?type=reboot` reinicia o aparelho
  imediatamente (aconteceu sem querer durante a exploração).

### Intelbras V-series (V3001 / V3101 / V3501 / V5501) — sysConf parcial

Homologado em **2026-08-03** (segunda rodada, durante a homologação da
release). Na primeira rodada o mecanismo não tinha sido reconhecido; o
caminho foi o **export nativo da config**:
`GET /default_user_config.xml` (autenticado, ~80 KB no V5501) — os campos
vivem no **mesmo `<sysConf>`** que o adapter já envia no provisionamento,
então o normalize é uma **config parcial** pelo `POST /config.htm` de sempre
(o aparelho preserva tudo que não foi listado, rede inclusive).

Campos sobrescritos:

| Caminho no `sysConf`             | Valor | Efeito                       |
|----------------------------------|-------|------------------------------|
| `call/port[1]/EnableDND`         | `0`   | desliga o DND                |
| `call/port[1]/MuteRinging`       | `0`   | destrava a campainha         |
| `phone/volume/HandsetVol`        | `9`   | volume do monofone           |
| `phone/volume/HeadsetVol`        | `9`   | volume do headset            |
| `phone/volume/HeadsetRingVol`    | `9`   | campainha no headset         |
| `phone/volume/HandFreeVol`       | `9`   | volume do viva-voz           |
| `phone/volume/HandFreeRingVol`   | `9`   | campainha no viva-voz        |

- Escala de volume **0-9** (confirmada em `/media.htm`: `maxlength="1"` com
  a dica `(0~9)`).
- **Ganho de microfone (`*MicVol`) NÃO é tocado** de propósito: é entrada de
  áudio, e forçá-lo ao máximo tende a gerar eco/microfonia.
- O aparelho **NÃO reinicia** no upload (`rebooted=False`) — ver abaixo.
- Achado relevante: o V5501 de lab estava com `EnableDND=1` **e**
  `MuteRinging=1` — exatamente o sintoma "o telefone não toca" que o cliente
  reporta. `MuteRinging` é um segundo jeito de silenciar o aparelho, separado
  do DND, e por isso entrou no normalize.

#### ⚠️ A config sozinha NÃO desliga o DND (corrigido em 2026-08-03)

Primeira versão do normalize só gravava a config — e o telefone **continuava
com o DND ligado**. O diagnóstico:

1. a config **era** aplicada: o export passou a mostrar `EnableDND=0`,
   `MuteRinging=0` e volumes `9`;
2. mas o aparelho **não reinicia** no upload — `UP_TIME` em `/information.htm`
   seguia em **75 h** depois dele;
3. o DND ligado pela tecla é **estado de runtime**: gravar a config não o
   derruba, e o ícone continua na tela.

Correção: além da config, o normalize dispara o **Action URI**
`GET /cgi-bin/ConfigManApp.com?key=DNDOff` (Basic Auth, sem a sessão web).

- `DNDOff` é **idempotente** — desliga quando ligado, não faz nada quando já
  está desligado. `F_DND` também funciona, mas é **toggle**: ligaria o DND num
  telefone que estava normal. Ambos verificados contra a tela do aparelho.
- **A ordem importa**: o Action URI vem **antes** do upload. Depois de um
  upload o firmware passa ~10 s digerindo e **engole comandos** — o `DNDOff`
  colado no `send_config` falhava em silêncio, respondendo `HTTP 200`.

#### Tela do aparelho como oráculo de homologação

O V-series expõe **`GET /cgi-bin/scnShot?type=main`** (Basic Auth): um BMP
320×240 com a tela real do telefone. Isso permite confirmar por **diff de
pixels** se o ícone de DND (barra de status, canto direito) sumiu — sem
depender de alguém olhar o aparelho. Foi assim que `DNDOff` foi eleito e a
idempotência, comprovada. Recomendado para qualquer homologação futura deste
vendor. (`cgi-bin/WebCapture` e `cgi-bin/syslog` também existem.)

#### Sessão web: gargalo real

Leituras (`information.htm`, `default_user_config.xml`, `scnShot`) e o Action
URI aceitam **Basic Auth** e são estáveis. Já o **upload de config** usa a
sessão (`GET /` → `/key==nonce` → POST) e o firmware aceita **uma sessão por
vez**: com uma presa, `/key==nonce` volta vazio e **nenhum** upload passa até
expirar por tempo. Não há endpoint de logout (`/key==logout`, `/logout.htm`
etc. → 404; o `clearLogFlag` do S3002 não existe aqui). Provas manuais em
sequência esgotam o aparelho — espere entre elas.
- ⚠️ O firmware Rapid Logic é **flaky com sessões concorrentes**: logins em
  sequência curta derrubam o serviço HTTP temporariamente (o adapter já trata
  com retry + delay). Em probes manuais, espere alguns segundos entre logins.

### Intelbras S3002 (linha S / firmware GoAhead)

- **Sem unidade em lab acessível** (o IP conhecido `192.168.0.48` não
  responde). O adapter é **outro** (form-replay em páginas `.asp`, não
  `sysConf`), então o mecanismo do V-series **não se aplica**.
- Fica sem capability (ações ocultas na UI). Para homologar é preciso um
  aparelho na rede: o caminho provável é achar o form de preferências/áudio
  e replicar o padrão `/goform/Save*` já usado no adapter.

### Intelbras TIP 125i (linha TIP / platwip) — `UPDATE` + `notify`

Homologado em **2026-08-31**, contra um aparelho de campo em **fw 4.3.17**
(`192.168.0.220`) — não a bancada 5.0.2 do adapter original, o que também
confirma o mecanismo na versão que o parque roda.

**O achado que destravou:** nesta plataforma a **tecla física escreve no
banco**. Medido, com o dono no aparelho:

| Ação no telefone | Efeito no banco |
|---|---|
| DND ligado pela tecla | `TAB_SERVICE_CODE.DND` = 1 nas **quatro** contas (0..3) |
| campainha abaixada pela tecla | `CurVolumeRing` 10 → **0** |

Ou seja, aqui o banco **é** o estado de runtime — o oposto do V-series, onde o
DND da tecla não aparece na config e exige o Action URI `DNDOff`. Por isso o
normalize do TIP é só `UPDATE` + `notify.cgi`, sem segunda camada.

**A premissa anterior estava errada.** O adapter dizia que a plataforma "não tem
tela web de volume" e que o máximo seria chute. A tela existe: fieldset
*Controle de Ganho* em `views/system/phone.html`, e ela **declara a escala** nos
próprios `<select>`:

| Campo | Escala |
|---|---|
| `CurVolumeHandPhone` / `CurVolMicHandPhone` | 1..10 |
| `CurVolumeHeadPhone` / `CurVolMicHeadPhone` | 1..10 |
| `CurVolumeSpeaker` / `CurVolMicSpeaker` | 1..10 (só `tip125`/`tip425`) |
| `CurVolumeRing` | **0**..10 (0 = mudo) |

Procurar por "volume" no `app.js` não acha nada — os campos só existem no HTML
da view; o `app.js` os carrega pelo schema (`schemaPhoneSoftCurrentConfig`). Foi
o que fez a primeira leitura concluir que a tela não existia.

- **Mecanismo:** um `db.cgi` com dois statements
  (`UPDATE TAB_SOFT_CURRENTCONFIG SET CurVolume* = 10 WHERE PK = 1;` +
  `UPDATE TAB_SERVICE_CODE SET DND = 0;`), depois
  `notify.cgi?tables=tab_soft_currentConfig,TAB_SERVICE_CODE` — os mesmos nomes
  que a web UI envia (a primeira em camelCase, como no `app.js`).
- **Microfones (`CurVolMic*`) não são tocados**, mesma regra do V-series: é
  ganho de entrada, e forçá-lo ao máximo tende a gerar eco.
- **DND sem `WHERE Account`**: a tecla liga nas quatro contas, então zerar só a
  provisionada deixaria o telefone mudo nas outras.
- **Sem reboot.** O `notify` basta para o volume e o DND — diferente do
  `send_config`, que precisa do reinício para a conta SIP entrar em vigor. No
  fw 4.3.x isso importa muito: lá o único reinício disponível é o do aparelho
  inteiro (~1 min fora do ar), e o normalize é disparado com o telefone em uso.
- **401 intermitente confirmado também no 4.3.17:** um `SELECT` de leitura
  devolveu 401 com `admin/admin` correto e passou na repetição imediata. O
  retry de `_executar_com_retry` cobre o normalize pelo mesmo caminho.

## Limitações gerais

- **MUTE é estado de runtime** no FlyingVoice e no HTEK — não há controle
  HTTP; só o Yealink destrava mute remotamente (via toggle).
- **Volume de chamada/viva-voz do HTEK**: o normalize cobre o volume do
  toque (`P8503`); os volumes de áudio em chamada não foram mapeados.
- Ações não homologadas ficam **ocultas por capability** e não bloqueiam a
  release (escopo mínimo v2.7.0 = normalize onde possível).

## Aparelhos de lab usados

| Modelo             | IP               |
|--------------------|------------------|
| Intelbras V5501    | 172.16.250.132   |
| FlyingVoice P10    | 172.16.250.130   |
| HTEK UC902G        | 172.16.250.131   |
| Intelbras V3501    | 192.168.0.179    |
| Yealink T31G       | 172.16.250.133   |
| Intelbras TIP 125i | 192.168.0.220 (fw 4.3.17) |

Senhas web: os ambientes que já aplicaram `nova_web_password` deixam de
aceitar `admin/admin` (ex.: o UC902G de lab está em `w0rk151234`). O
middleware resolve sozinho pela chain de credenciais; probes manuais
precisam da senha atual.

## Relacionados

- `docs/ADRs/0004-device-actions.md` (decisões de arquitetura)
- Adapters: `src/middleware_monitor/integrations/extension_configurator/vendors/{yealink,flyingvoice,htek}.py`
- Scripts de recon/homologação: `scripts/lab/` (não versionados)
