"""Agente do NOC WorkConnect (v2.13.0) — ver ``docs/AGENTE-NOC.md``.

O middleware passa a se identificar a uma base central e a mandar sinal de vida
para ela. **Toda conexão sai daqui**: o NOC nunca chama o middleware, e a
interface web continua ouvindo só na LAN.

- ``estado``    — o que fica guardado (KV ``noc.*``, credencial cifrada).
- ``cliente``   — o HTTP de saída para ``/agente/v1``.
- ``manifesto`` — o que este agente declara saber fazer.
"""
