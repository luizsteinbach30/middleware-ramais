"""Configurador de Ramais — provisionamento de telefones SIP por modelo.

Modulos:
  vendors/   adapters por fabricante (HTEK, Intelbras V-series)

Os adapters seguem o contrato `VendorAdapter` em `vendors/base.py`:
  fingerprint()    — reconhece o vendor pelo Server header
  discover()       — le modelo/firmware/MAC com auth
  generate_config()— produz bytes do arquivo de provisionamento
  send_config()    — envia para o aparelho (geralmente reinicia)
  backup_config()  — opcional: baixa config atual antes de aplicar

Regra inviolavel: nenhum adapter pode emitir P-codes ou tags de rede
(IP, mascara, gateway, DNS, DHCP, VLAN, VPN, 802.1X, QoS, Wi-Fi etc.).
Aparelhos aplicam configs parciais — o que nao enviamos fica preservado.
"""
