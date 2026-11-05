# ADR-0011 — O middleware como serviço do Windows

Data: 2026-11-05 · Status: aceito · Revê: "No Windows service" da v2.1.0 e a consequência "sem a janela aberta numa
sessão logada não há middleware rodando" do ADR 0008

## Contexto

No Windows, o middleware só roda com a janela aberta numa sessão logada. Se a máquina reinicia, ele fica fora até alguém
entrar e abrir o `.exe`, e com ele somem o heartbeat e a atualização pedida pelo NOC. O dono pediu (05/11) que ele vire
serviço e inicie junto com o Windows.

## Decisões

1. **Serviço de verdade, com `ctypes`.** Sem pywin32 nem NSSM: nada novo para empacotar. O mesmo `.exe`, com `--servico`,
   fala com o gerenciador de serviços (`servico_windows.rodar`).
2. **`LocalSystem`, início automático.** Rodar como o usuário exigiria guardar a senha dele.
3. **Programa em `%ProgramFiles%\MiddlewareMonitor\MiddlewareMonitor.exe`.** O serviço precisa de um caminho fixo; o
   `.exe` antigo fica onde o operador o deixou.
4. **Dados em `%ProgramData%\MiddlewareMonitor`, só SYSTEM e administradores.** O `%LOCALAPPDATA%` do SYSTEM é outro, e a
   pasta guarda `secret.key` e o banco. A permissão é aplicada por SID (em português o grupo é "Administradores") antes
   da cópia, para nenhum segredo nascer legível por todos.
5. **Copiar, não mover.** A pasta antiga fica intacta, inclusive `secret.key`: é o que permite voltar atrás.
6. **Ativação pela janela, com UAC.** A atualização automática acontece sem ninguém na tela e não tem como pedir
   administrador. A janela pergunta a cada abertura, com o servidor rodando atrás da pergunta; quem recusa segue na janela.
7. **Com o serviço instalado, o `.exe` só abre o painel.** Uma janela subindo servidor próprio teria outro banco, divergindo
   em silêncio.
8. **Instalação só conta quando o `healthz` responde com a versão certa, em até 150 s.** Antes disso, qualquer falha apaga
   o serviço, a regra do firewall e a cópia feita agora. Dados que já eram do serviço nunca são apagados nem sobrescritos.
9. **Regra de firewall própria,** só de entrada, para a porta e o programa. Na sessão 0 não existe o aviso "permitir
   acesso", e sem a regra o painel só abriria na própria máquina.
10. **RODANDO é informado antes do boot.** Migration longa não pode estourar os 30 s de partida (erro 1053). Falha de boot
    sai com código diferente de zero, e o Windows reinicia após 1 min (`sc failure` + `failureflag`). Parada pedida sai com
    zero: é assim que o ajudante da atualização consegue trocar o `.exe` sem disputar com a reinicialização.
11. **O ajudante da atualização sobe com `Start-Service`.** O `Start-Process` abriria o `.exe` fora do gerenciador, na sessão
    0, tentando abrir janela. A verificação de saúde e a volta do ADR 0008 valem iguais.
12. **Log com rotação no serviço** (10 MB × 5): a janela fecha, o serviço não.

## Consequências

- Clientes existentes só viram serviço quando alguém abrir a janela e aceitar. Em máquina sem ninguém, a atualização
  automática instala a versão nova e ela segue na janela até lá.
- Durante a instalação, o painel fica fora do ar: a janela fecha, os dados são copiados e o serviço sobe.
- A pasta antiga continua com uma cópia de `secret.key` e do banco, acessível ao usuário dele.
- Tudo o que depende do usuário logado deixa de existir para o serviço: `LocalSystem` não vê unidades de rede mapeadas nem
  VPN iniciada só na sessão do usuário. Rotas de VPN da máquina continuam valendo.

## Como desfazer

Como administrador: `MiddlewareMonitor.exe --remover-servico` para e apaga o serviço e a regra do firewall, sem apagar dados.
Para seguir na janela com as alterações feitas pelo serviço, copie `%ProgramData%\MiddlewareMonitor` por cima de
`%LOCALAPPDATA%\MiddlewareMonitor` antes de abrir o `.exe`. Sem a cópia, a janela abre com os dados de quando virou serviço.
