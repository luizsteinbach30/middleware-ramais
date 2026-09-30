"""Equipamentos e disponibilidade — a expansão do NOC, Plano 0 (``docs/AGENTE-NOC.md``, itens 18 a 21).

Câmera, gravador, switch e servidor não têm ramal. Ganham cadastro próprio, que
chega do NOC pelo verbo ``definir_equipamentos`` e fica guardado aqui, para a
monitoria continuar com o NOC fora do ar.

- ``lista``   — o cadastro: validação do ``para``, conferência do ``de``, gravação e retrato.
- ``estados`` — a máquina de estados: quando uma verificação falha, quando o equipamento cai.
- ``sondas``  — roda uma verificação (icmp, tcp, http) contra o equipamento.

**Monitoria é só leitura.** Nada aqui escreve em equipamento, e nenhum
parâmetro de rede é emitido: a lista é gravada no agente, nunca no equipamento.
"""
