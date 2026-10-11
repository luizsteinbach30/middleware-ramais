"""CFTV por adaptador de fabricante — só leitura (AGENTE-NOC item 23, CONTRATO §13.9).

- ``base``      — o contrato (``CftvAdapter.ler``) e a forma da leitura.
- ``intelbras`` — Intelbras e Dahua (CGI, digest).
- ``hikvision`` — Hikvision (ISAPI, digest, XML).
- ``unifi``     — UniFi Protect (login no console, cookie + token CSRF, JSON).
- ``registry``  — os três, por ``adaptador_id``.
"""
