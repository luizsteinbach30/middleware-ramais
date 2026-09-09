#!/usr/bin/env bash
# tip125i-sondar.sh — lê a configuração SIP de telefones Intelbras TIP 125i (linha
# TIP/platwip) sem alterar nada, para diagnóstico de chamada recusada / INVITE grande.
#
# Uso:  bash tip125i-sondar.sh IP [IP...]            (credencial padrão admin/admin)
#       CRED=usuario:senha bash tip125i-sondar.sh IP [IP...]
# Saída: um arquivo /tmp/tip-<IP>.txt por aparelho (tabelas SIP/codec/NAT/segurança
#        completas, senha SIP mascarada) e um resumo na tela. Com dois IPs, mostra
#        o diff entre eles — compare um aparelho que liga com um que não liga.
#
# Como o firmware funciona: GET /db.cgi?<base64(SQL)> executa SQL no SQLite do
# aparelho (o SQL É a API). O Base64 vai percent-encodado (+ / = cru → 401), e o
# SQL não pode ter nada depois do ';' final (senão 200 com corpo vazio).
set -uo pipefail
CRED="${CRED:-admin:admin}"
[[ $# -ge 1 ]] || { echo "uso: bash $0 IP [IP...]" >&2; exit 2; }

db() {  # db IP "SQL;"  -> corpo da resposta (JSON)
  local ip="$1" sql="$2" b64
  b64="$(printf '%s' "$sql" | base64 -w0 | sed 's/+/%2B/g; s#/#%2F#g; s/=/%3D/g')"
  curl -s -m 8 -u "$CRED" "http://$ip/db.cgi?$b64"
}
mascarar() { sed -E 's/"(AuthPassword|Password|SYSPhonePin)":"[^"]*"/"\1":"***"/g'; }

ARQS=()
for IP in "$@"; do
  OUT="/tmp/tip-$IP.txt"; ARQS+=("$OUT")
  {
    echo "### $IP  $(date -u +%FT%TZ)"
    echo "## status.cgi"
    curl -s -m 8 -u "$CRED" "http://$IP/status.cgi"; echo
    echo "## tabelas"
    TABS="$(db "$IP" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)"
    echo "$TABS" | tr '\n' ' '; echo
    for T in $(echo "$TABS" | grep -E -i 'VOIP|SIP|CODEC|RTP|NAT|CALL|SECUR|TLS|SRTP|STUN|QOS|AUDIO|DTMF|SESSION|TEL_'); do
      echo "## $T"
      db "$IP" "SELECT * FROM $T;" | mascarar; echo
    done
  } > "$OUT" 2>&1
  echo "== $IP -> $OUT"
  grep -o '"swMajor[^}]*' "$OUT" | head -1 | sed 's/^/   fw: /'
  # conta 0: servidor, porta, transporte (0=UDP 1=TCP 2=TLS)
  ACC="$(grep -A1 '^## TAB_VOIP_ACCOUNT' "$OUT" | tail -1 | grep -o '{[^}]*"Account":0[^}]*}' | head -1)"
  [[ -z "$ACC" ]] && ACC="$(grep -A1 '^## TAB_VOIP_ACCOUNT' "$OUT" | tail -1 | grep -o '{[^}]*}' | head -1)"
  echo "   conta: $(echo "$ACC" | grep -o -E '"(PhoneNumber|ServerAddress|ServerPort|Transport|SendRegister)":[^,}]*' | tr '\n' ' ')"
  SRV="$(echo "$ACC" | grep -o '"ServerAddress":"[^"]*"' | cut -d'"' -f4)"
  if [[ -n "$SRV" ]]; then
    if timeout 3 bash -c "</dev/tcp/$SRV/5060" 2>/dev/null; then echo "   PBX $SRV aceita TCP 5060: sim"; else echo "   PBX $SRV aceita TCP 5060: não/filtrado"; fi
  fi
  echo "   tabelas SIP/codec dumpadas: $(grep -c '^## TAB_' "$OUT")"
done

if [[ ${#ARQS[@]} -eq 2 ]]; then
  echo "== diff ${ARQS[0]} ${ARQS[1]} (sem cabeçalhos/datas)"
  diff <(grep -v '^###' "${ARQS[0]}") <(grep -v '^###' "${ARQS[1]}") || true
fi
