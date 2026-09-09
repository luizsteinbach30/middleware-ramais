#!/usr/bin/env bash
# tip125i-sondar.sh — lê a configuração SIP de telefones Intelbras TIP 125i (linha
# TIP/platwip) sem alterar nada, para diagnóstico de chamada recusada, INVITE grande
# ou configuração que "some sozinha" (auto-provisionamento).
#
# Uso:  bash tip125i-sondar.sh IP [IP...]            (credencial padrão admin/admin)
#       CRED=usuario:senha bash tip125i-sondar.sh IP [IP...]
# Saída: um arquivo /tmp/tip-<IP>.txt por aparelho (tabelas SIP/codec/NAT/segurança/
#        provisionamento completas, senha SIP mascarada) e um resumo na tela. Com
#        dois IPs, mostra o diff entre eles — compare um que liga com um que não liga.
#
# Como o firmware funciona: GET /db.cgi?<base64(SQL)> executa SQL no SQLite do
# aparelho (o SQL É a API). O Base64 vai percent-encodado (+ / = cru → 401), e o
# SQL não pode ter nada depois do ';' final (senão 200 com corpo vazio).
# O fw 4.3 redireciona HTTP → HTTPS (301) e o certificado é autoassinado: falamos
# HTTPS direto com -k. O firmware devolve 401 esporádico com a credencial certa:
# cada chamada tenta até 3 vezes.
set -uo pipefail
CRED="${CRED:-admin:admin}"
[[ $# -ge 1 ]] || { echo "uso: bash $0 IP [IP...]" >&2; exit 2; }

cgi() {  # cgi IP PATH -> corpo (HTTPS, -k, 3 tentativas no 401)
  local ip="$1" path="$2" out code
  for _ in 1 2 3; do
    out="$(curl -s -k -m 10 -u "$CRED" -w $'\n%{http_code}' "https://$ip$path")"
    code="${out##*$'\n'}"
    [[ "$code" == "401" ]] || { printf '%s' "${out%$'\n'*}"; return 0; }
    sleep 1.5
  done
  printf '%s' "${out%$'\n'*}"
}
db() {  # db IP "SQL;"  -> corpo da resposta (JSON)
  local b64
  b64="$(printf '%s' "$2" | base64 -w0 | sed 's/+/%2B/g; s#/#%2F#g; s/=/%3D/g')"
  cgi "$1" "/db.cgi?$b64"
}
mascarar() { sed -E 's/"(AuthPassword|Password|SECPassword|SYSPhonePin)":"[^"]*"/"\1":"***"/g'; }

ARQS=()
for IP in "$@"; do
  OUT="/tmp/tip-$IP.txt"; ARQS+=("$OUT")
  {
    echo "### $IP  $(date -u +%FT%TZ)"
    echo "## status.cgi"
    cgi "$IP" "/status.cgi"; echo
    echo "## tabelas"
    TABS="$(db "$IP" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)"
    echo "$TABS" | tr '\n' ' '; echo
    for T in $(echo "$TABS" | grep -E -i 'VOIP|SIP|CODEC|RTP|NAT|MEDIA|SECUR|TLS|SRTP|STUN|QOS|AUDIO|DTMF|SESSION|TEL_|SERVICE_CODE|PROVISIONING|DIAL_PLAN'); do
      echo "## $T"
      db "$IP" "SELECT * FROM $T;" | tr -d '\n' | tr -s ' ' | mascarar; echo
    done
  } > "$OUT" 2>&1
  echo "== $IP -> $OUT"
  grep -o '"swMajor":"[^"]*"' "$OUT" | head -1 | sed 's/^/   fw: /'
  grep -o '"user1":"[^"]*"' "$OUT" | head -1 | sed 's/^/   registro conta1: /'
  ACC="$(grep -A1 '^## TAB_VOIP_ACCOUNT' "$OUT" | tail -1 | grep -o '{[^}]*"Account": *0[^}]*}' | head -1)"
  echo "   conta: $(echo "$ACC" | grep -o -E '"(PhoneNumber|ServerAddress|ServerPort|LocalPort|Transport|RegisterTimer)": *[^,}]*' | tr '\n' ' ')"
  PROV="$(grep -A1 '^## TAB_UPDATE_PROVISIONING' "$OUT" | tail -1)"
  echo "   autoprov: $(echo "$PROV" | grep -o -E '"UPDProvisioning(Enable|ServerURL|Path|WhenTurnOn|DHCPEnable|PNPEnable)": *[^,}]*' | tr '\n' ' ')"
  SRV="$(echo "$ACC" | grep -o '"ServerAddress": *"[^"]*"' | cut -d'"' -f4)"
  if [[ -n "$SRV" ]]; then
    if timeout 3 bash -c "</dev/tcp/$SRV/5060" 2>/dev/null; then echo "   PBX $SRV aceita TCP 5060: sim"; else echo "   PBX $SRV aceita TCP 5060: não/filtrado"; fi
  fi
  echo "   tabelas dumpadas: $(grep -c '^## TAB_' "$OUT")"
done

if [[ ${#ARQS[@]} -eq 2 ]]; then
  echo "== diff ${ARQS[0]} ${ARQS[1]} (sem cabeçalhos/datas)"
  diff <(grep -v '^###' "${ARQS[0]}") <(grep -v '^###' "${ARQS[1]}") || true
fi
