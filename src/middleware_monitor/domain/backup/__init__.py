"""Backup e restauracao.

Dois niveis, deliberadamente separados porque respondem a perguntas
diferentes:

* ``bundle`` — pacote **portavel** de configuracao (``.mwrbak``), cifrado com
  passphrase. Serve para levar a configuracao para OUTRA instalacao: os
  segredos saem recifrados com a passphrase do arquivo, porque a cifra local
  (``SecretBox``, derivada do ``APP_SECRET_KEY`` da maquina) nao vale nada no
  destino.
* ``snapshot`` — copia consistente do banco inteiro (``.db.gz``). Serve para
  recuperar ESTA instalacao: leva tambem o historico (pings, coletas, ledger
  MQTT, chamadas) e mantem os segredos como estao, cifrados pela chave local.

O modulo nao importa modelos no ``__init__`` de proposito: ``core.db`` chama
``snapshot.apply_pending_restore`` antes de abrir o engine, e um import de
``core.models`` aqui fecharia o ciclo.
"""
