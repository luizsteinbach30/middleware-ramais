# ADR-0004 — Device actions (ações remotas nos telefones)

Data: 2026-08-03 · Status: aceito · Release: v2.7.0

## Contexto

O problema operacional nº 1 do cliente: operador abaixa o volume, ativa DND ou
muta o telefone; o ramal "para de tocar" e vira chamado técnico com visita
presencial. O pedido foi gerenciar remotamente os telefones homologados a
partir do middleware, com prioridade em **desfazer MUTE/DND**.

A homologação foi feita **ao vivo** por vendor em 2026-07-31 (matriz e
mecanismos em `docs/design/DEVICE_ACTIONS_HOMOLOGACAO.md`). Os mecanismos são
completamente heterogêneos: Action URI no Yealink, form-replay no FlyingVoice,
provisionamento parcial por P-code no HTEK.

## Decisões

### 1. Catálogo fechado + capabilities por adapter

`vendors/base.py` define o catálogo (`normalize`, `set_ip`); cada adapter
declara em `capabilities()` o subconjunto **homologado** (default: vazio).
A UI só mostra o que vem em `GET .../capabilities` — vendor sem homologação
(Intelbras hoje) simplesmente não exibe ações. Ação fora da capability é
rejeitada no service com 422, mesmo se chamada direto pela API.

### 2. "Normalizar" é UMA ação semântica, não N ações finas

Em vez de expor `unmute`/`dnd_off`/`set_volume` separados, a ação de negócio é
**normalize** = "devolver o telefone ao estado operacional" (volume no máximo +
DND desligado + unmute onde possível). Motivos: (a) os mecanismos por vendor
não são uniformes — volume relativo em passos no Yealink, absoluto 0-9 no
FlyingVoice, P-code 0-14 no HTEK — então ações finas teriam semântica diferente
por aparelho; (b) o operador de NOC não quer escolher entre 4 botões, quer o
telefone tocando de novo. Cada adapter implementa o **máximo homologado** do
seu vendor (ex.: HTEK cobre volume do toque + DND via P-codes `P8503`/`P1305`;
o mute do HTEK é runtime, sem controle HTTP).

### 3. Auditoria flat em `device_action_events` (migration 0008)

Tabela própria (timestamp, environment/line/device, ip, vendor, action,
params_json, status, erro, operador), **1 evento por telefone por ação**,
gravado **sempre** — sucesso e falha, ação unitária e bulk. Não acopla ao
padrão `ExtensionApplyRun`: ações são pontuais, não têm snapshot antes/depois
nem estados intermediários persistidos. Auditoria é a fonte de verdade
persistente; o tracking ao vivo é descartável.

### 4. Bulk "normalizar" = run in-memory + polling (espelho do apply)

`POST .../actions/normalize` dispara worker em background (semáforo 5 — não
saturar rede/servidor) e devolve `{run_id, total}`; o progresso sai em
`GET /action-runs/{run_id}/live` via `action_state.py`, espelho do
`run_state.py` do apply (1 worker uvicorn → dict módulo-level é seguro).
Mesmo contrato de 404 do apply: run expirado da memória → UI encerra o
acompanhamento; o resultado permanece na auditoria (decisão 3).

### 5. Chain de credenciais reusada do apply

`build_creds_chain` (credencial atual → nova) é a mesma do apply: telefone
com senha já trocada e telefone ainda na senha antiga funcionam sem config
extra. `VendorAuthError` de um adapter → tenta a próxima credencial da chain.

### 6. `set_ip` no catálogo, com guard de confirmação

Trocar IP pode tirar o aparelho da rede. O endpoint exige `confirm_ip` igual
ao IP atual da linha (400 `confirm_ip_mismatch`); a UI força digitar o IP
atual num modal destrutivo. Nenhum vendor homologou `set_ip` ainda — o
catálogo e o guard ficam prontos para quando houver homologação.

## Consequências

- **Reboots documentados**: FlyingVoice reinicia ao mudar DND; HTEK reinicia
  ao aceitar qualquer config (inclusive o normalize). A UI avisa; o
  `ActionResult.rebooted` propaga até o toast e o painel.
- Yealink Action URI é gated por "Action URI Allow IP List" no aparelho — o
  template do `generate_config` já provisiona a permissão; instalações com
  telefones provisionados fora do middleware precisam liberar o IP.
- MUTE não tem controle HTTP no FlyingVoice/HTEK (estado de runtime); no
  Yealink a key MUTE é um toggle — em idle o efeito líquido é destravar.
- Intelbras V-series homologado numa segunda rodada (2026-08-03) via export
  nativo `sysConf`; o **S3002** (adapter distinto, form-replay `.asp`) segue
  sem unidade de lab → fica oculto por capability e **não bloqueia a
  release**. Cobertura atual: 4 dos 5 adapters.
- **Silenciar o telefone tem mais de um caminho por vendor**: no Intelbras,
  além do DND existe `MuteRinging`. Ampliar o normalize é sempre "achar todos
  os jeitos de silenciar daquele firmware", não só o DND — por isso o
  mecanismo por vendor é derivado do **export real da config**, não das
  páginas web (que escondem campos).

## Relacionados

- `docs/design/DEVICE_ACTIONS_HOMOLOGACAO.md` (matriz vendor×ação + mecanismos)
- ADR-0002 (extension configurator) · ADR-0003 (multi-USCall)
- Migration `0008_device_action_events`
