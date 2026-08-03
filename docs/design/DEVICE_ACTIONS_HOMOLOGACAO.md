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
| Intelbras V3501/V5501 | ❌     | —      | não reconhecido/não homologado    | —      |
| Intelbras S3002    | ❌        | —      | sem unidade em lab nesta rodada   | —      |

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
  List" (Features → Remote Control). O template do `generate_config` já
  provisiona a permissão; aparelho provisionado por fora precisa liberar o IP
  do middleware manualmente.

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

### Intelbras (V3501 / V5501 / S3002)

- V-series: mecanismo de ação remota não reconhecido nesta rodada → sem
  capability (a UI não mostra nada).
- S3002: **sem unidade em lab** nesta rodada → entra como "não homologado";
  pendência registrada para quando houver aparelho disponível.

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

## Relacionados

- `docs/ADRs/0004-device-actions.md` (decisões de arquitetura)
- Adapters: `src/middleware_monitor/integrations/extension_configurator/vendors/{yealink,flyingvoice,htek}.py`
- Scripts de recon/homologação: `scripts/lab/` (não versionados)
