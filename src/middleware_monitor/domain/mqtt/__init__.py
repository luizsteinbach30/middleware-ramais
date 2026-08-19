"""Ingestão e consulta das mensagens do broker MQTT/EMQX.

* ``address``   — interpretação do endereço digitado e casamento de tópicos.
* ``discovery`` — sonda de rede: descobre porta/transporte/TLS e lista tópicos.
* ``parser``    — reconhecimento do payload de status de ramal (por formato).
* ``repository``— broker, ledger de mensagens e prova de cobertura.
* ``service``   — coletor: fila em memória + gravação em lote.
"""
