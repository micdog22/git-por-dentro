# Git por Dentro

Um mini Git em Python para entender como o Git funciona: o `mini-git` grava blobs, árvores, commits, o índice e as referências **no formato real do Git**, a ponto de o `git` de verdade ler tudo o que ele cria e de os dois gerarem os mesmos SHA-1 para o mesmo conteúdo. São cerca de 2 mil linhas comentadas em português, só com a biblioteca padrão.

## Recursos

- **Objetos de verdade:** blobs, trees e commits guardados como arquivos zlib em `.git/objects`, com nome igual ao SHA-1 de `<tipo> <tamanho>\0<conteúdo>`.
- **Índice na versão 2** (`.git/index`): cabeçalho `DIRC`, entradas com ctime, mtime, dev, ino, modo, uid, gid, tamanho, SHA-1 e flags, caminho completado até múltiplo de 8 bytes e o SHA-1 final.
- **Comandos:** `init`, `hash-object [-w]`, `cat-file -p|-t|-s`, `add`, `write-tree`, `commit -m`, `log`, `status`, `checkout`, `branch`, `diff`, `ls-tree` e `config`.
- **Commits idênticos aos do Git:** com as mesmas variáveis `GIT_AUTHOR_*` e `GIT_COMMITTER_*`, o mini-git e o git produzem o mesmo hash de commit (os testes conferem).
- **Compatível nos dois sentidos:** o `git fsck --strict` aprova os repositórios do mini-git, e o mini-git lê os objetos soltos, o índice e as referências criados pelo git.
- **`status` como o do Git:** novos, modificados, removidos e não rastreados, com `.gitignore`, `--porcelain` idêntico ao do git e as mesmas aspas em nomes com acento ou espaço.
- **Arquivos executáveis e links simbólicos**, nomes com acentos e espaços, datas em vários formatos e fusos horários.

## Instalação

Precisa de Python 3.9 ou mais novo. Testado no macOS com Python 3.9 e 3.14. Como usa links simbólicos e o bit de execução, que são recursos POSIX, no Windows use o WSL.

```sh
pipx install git+https://github.com/micdog22/git-por-dentro
```

Ou, sem instalar, de dentro da pasta do projeto: `PYTHONPATH=src python3 -m git_por_dentro --help`.

## Como usar

```
$ mini-git init
Repositório Git vazio criado em /home/ana/receitas/.git/

$ printf 'Bolo de fubá\n' > bolo.txt
$ mkdir doces && printf 'Brigadeiro\n' > doces/brigadeiro.txt
$ mini-git hash-object bolo.txt
f3c59565ae4e8eaa692dc8df6ca4af061e932d8a

$ mini-git add .
$ mini-git status
No ramo main

Nenhum commit ainda

Mudanças prontas para o commit:
	novo:        bolo.txt
	novo:        doces/brigadeiro.txt

$ mini-git commit -m "Primeiras receitas"
[main (commit raiz) ceefd34] Primeiras receitas
```

O commit é um objeto de texto que aponta para uma árvore, que aponta para os blobs:

```
$ mini-git cat-file -p HEAD
tree 3babb2170aa725c783db229bfe4a244d84729a10
author Ana Souza <ana@exemplo.com.br> 1791550800 -0300
committer Ana Souza <ana@exemplo.com.br> 1791550800 -0300

Primeiras receitas

$ mini-git cat-file -p 3babb21
100644 blob f3c59565ae4e8eaa692dc8df6ca4af061e932d8a	bolo.txt
040000 tree 9388e4a4bbcfa4b9e70e052605e78b66fdf8e182	doces
```

E o git de verdade concorda com tudo:

```
$ git fsck --strict && git log --oneline
ceefd34 Primeiras receitas

$ printf 'Bolo de fubá\nCom erva-doce\n' > bolo.txt
$ mini-git diff
diff --git a/bolo.txt b/bolo.txt
index f3c5956..e71bad9 100644
--- a/bolo.txt
+++ b/bolo.txt
@@ -1 +1,2 @@
 Bolo de fubá
+Com erva-doce
```

Os outros comandos:

```
mini-git log [--oneline] [-n N] [revisão]     histórico seguindo o primeiro pai
mini-git status [--porcelain]
mini-git checkout <ramo|commit>               recusa se houver mudanças locais
mini-git checkout -b <ramo>                   cria o ramo e muda para ele
mini-git branch [nome [início]] | -d <ramo> | -D <ramo>
mini-git diff [--cached]                      pasta de trabalho contra o índice (ou índice contra o HEAD)
mini-git ls-tree [-r] [--name-only] <árvore> [caminho]
mini-git write-tree
mini-git config [--global] <chave> [valor]    por exemplo, user.name e user.email
```

As revisões aceitam `HEAD`, nomes de ramos e etiquetas, SHA-1 abreviado e os sufixos `~N`, `^` e `^{tree}`. Para o commit, o nome e o email vêm de `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` e companhia, ou de `user.name` e `user.email` na configuração (a mesma do Git). As datas vêm de `GIT_AUTHOR_DATE` e `GIT_COMMITTER_DATE` (formatos `1700000000 -0300`, `@1700000000`, ISO 8601 e RFC 2822) ou do relógio, com o seu fuso horário.

## Testes

```sh
python3 -m unittest discover -s tests
```

São 35 testes, que rodam com Python 3.9 a 3.14. Os de compatibilidade precisam do `git` instalado (sem ele, são pulados), e isolam a configuração do usuário (`GIT_CONFIG_GLOBAL`, `HOME`) para o resultado não depender da máquina:

- **Hashes idênticos:** a mesma sequência de três commits (com subpastas, arquivo executável, link simbólico que vira arquivo, arquivo vazio, binário, nomes com acento e espaço, mensagens com espaços sobrando, datas ISO 8601, RFC 2822 e `@`, autor diferente do committer) é feita pelo git numa pasta e pelo mini-git em outra, e os SHA-1 dos commits precisam ser iguais.
- **O git lê o mini-git:** `git fsck --strict` sem nenhuma reclamação; `git log`, `git log --oneline`, `git branch`, `git ls-tree` e `git cat-file -p|-t|-s` (para todos os objetos) com saída idêntica à do mini-git; `git status --porcelain` igual ao nosso em vários estados (modificado, só o modo, apagado, novo, pasta nova, `AM`, `MD`, tipo mudou, `.gitignore` com negação, padrões ancorados e `.git/info/exclude`).
- **O mini-git lê o git:** log, etiquetas e todos os objetos soltos de um repositório criado pelo git; um commit do mini-git por cima do histórico do git; aviso claro quando os objetos estão num packfile.
- **Checkout:** troca de ramos (arquivos criados, apagados e executáveis, pastas vazias removidas), HEAD destacado, recusa com mudanças locais e com arquivos não rastreados que seriam sobrescritos.
- **Peças isoladas:** formato do índice (preenchimento de 1 a 8 bytes, SHA-1 final, trava `index.lock`), ordem das entradas de uma árvore, limpeza de mensagens, identidades, datas, aspas, `.gitignore` e configuração.

## Como funciona

O Git é, no fundo, um banco de dados de chave e valor: você entrega um conteúdo e ele devolve uma chave (o SHA-1), que serve para recuperar o conteúdo depois. Tudo o resto (ramos, histórico, índice) é construído em cima disso.

### Blob: o conteúdo de um arquivo

Um blob guarda só o conteúdo, sem nome nem permissões. O hash é o SHA-1 de um cabeçalho com o tipo e o tamanho **em bytes**, um byte zero, e o conteúdo:

```
"blob 14\0Bolo de fubá\n"   -->  SHA-1  -->  f3c59565ae4e8eaa692dc8df6ca4af061e932d8a
```

São 14 bytes e não 13, porque o "á" ocupa 2 bytes em UTF-8. Dá para conferir sem Git nenhum:

```sh
$ printf 'blob 14\0Bolo de fubá\n' | shasum
f3c59565ae4e8eaa692dc8df6ca4af061e932d8a  -
```

O objeto vai comprimido com zlib para `.git/objects/f3/c59565ae4e8eaa692dc8df6ca4af061e932d8a`: os dois primeiros caracteres viram a pasta, para nenhuma pasta ficar com milhões de arquivos. Como o nome depende só do conteúdo, dois arquivos iguais viram um único blob, em qualquer pasta e em qualquer commit.

### Tree: uma pasta

Uma árvore lista as entradas de uma pasta: modo, nome e o SHA-1 (em 20 bytes binários) de cada uma.

```
tree 3babb21
 ├── 100644 blob f3c5956  bolo.txt
 └── 040000 tree 9388e4a  doces
                           └── 100644 blob 4267298  brigadeiro.txt
```

Os modos possíveis são `100644` (arquivo comum), `100755` (executável), `120000` (link simbólico; o blob guarda o destino), `40000` (subpasta) e `160000` (submódulo). As entradas ficam ordenadas pelo nome em bytes, com um detalhe que derruba muita implementação caseira: o nome de uma pasta é comparado como se terminasse em `/`. Por isso o arquivo `a.txt` vem **antes** da pasta `a` (o `.` é 0x2E e a `/` é 0x2F). Uma árvore fora de ordem gera outro hash, e o `git fsck` reclama.

### Commit: uma fotografia com história

```
tree 3babb2170aa725c783db229bfe4a244d84729a10      a fotografia da raiz
parent <sha do commit anterior>                     (o primeiro commit não tem)
author Ana Souza <ana@exemplo.com.br> 1791550800 -0300
committer Ana Souza <ana@exemplo.com.br> 1791550800 -0300

Primeiras receitas
```

As datas são segundos desde 1970 mais o fuso de quem fez o commit. O hash cobre tudo isso, inclusive o pai, então mudar qualquer coisa num commit antigo muda o hash dele e de todos os que vêm depois: é isso que torna o histórico à prova de adulteração. Para o mini-git gerar o mesmo hash do Git, ele limpa a mensagem do mesmo jeito que o `git commit -m` (tira espaços no fim das linhas e linhas em branco nas pontas, junta linhas em branco seguidas e termina com uma quebra de linha) e limpa nome e email como o Git.

### Refs e HEAD: nomes para commits

```
.git/HEAD             ref: refs/heads/main          (o ramo atual)
.git/refs/heads/main  ceefd34f0166f3feab6292b9e677856dd960e757
```

Um ramo é só um arquivo com o SHA-1 de um commit. Fazer um commit é gravar o objeto e reescrever esse arquivo. O `HEAD` normalmente aponta para um ramo; com `checkout <sha>` ele passa a guardar o SHA-1 direto (HEAD destacado). Repositórios mais antigos podem ter as referências juntas em `.git/packed-refs`, que o mini-git também lê.

### O índice: o próximo commit em preparação

O `.git/index` é um arquivo binário com uma entrada por arquivo rastreado:

```
DIRC | versão 2 | número de entradas
para cada arquivo:
  ctime, mtime, dev, ino, modo, uid, gid, tamanho    10 números de 32 bits
  SHA-1 do blob                                       20 bytes
  flags (estágio de conflito e tamanho do nome)       16 bits
  caminho, completado com 1 a 8 bytes zero até múltiplo de 8
SHA-1 de tudo o que veio antes
```

Os dados de stat existem para o `status` ser rápido: se a data de modificação, o tamanho e o inode batem com o índice, o arquivo não mudou e nem é lido. Mas há uma armadilha conhecida, o "racy git": se o arquivo foi modificado no mesmo instante em que o índice foi gravado, as datas não bastam. O mini-git faz como o Git e relê o conteúdo de toda entrada com data igual ou posterior à do próprio índice.

### Juntando tudo

```
mini-git add bolo.txt          grava o blob e atualiza o índice
mini-git commit -m "..."        índice -> árvores (uma por pasta) -> commit -> refs/heads/main

refs/heads/main --> commit ceefd34 --> tree 3babb21 --> blob f3c5956 (bolo.txt)
                         |                        \--> tree 9388e4a --> blob 4267298
                         v
                  (commit pai, no próximo commit)
```

E o `status` compara três coisas: a árvore do `HEAD` com o índice ("prontas para o commit") e o índice com a pasta de trabalho ("fora do índice"); o que está na pasta e não está no índice, nem no `.gitignore`, é "não rastreado".

### Exercícios

1. Crie um arquivo com `printf 'oi\n' > a.txt` e calcule o hash dele à mão com `printf 'blob 3\0oi\n' | shasum`. Confira com `mini-git hash-object a.txt` e com `git hash-object a.txt`.
2. Faça um commit, depois abra `.git/objects` e descomprima um objeto com Python: `import zlib; print(zlib.decompress(open(caminho, 'rb').read()))`. Ache o byte zero depois do cabeçalho.
3. Mude só o bit de execução de um arquivo (`chmod +x`). O blob muda? E a árvore? E o commit? Confira com `mini-git ls-tree HEAD` antes e depois do commit.
4. Crie dois arquivos com o mesmo conteúdo em pastas diferentes. Quantos blobs aparecem em `.git/objects`?
5. Rode o mesmo commit duas vezes, com um minuto de diferença, em pastas diferentes. Por que os hashes saem diferentes? Agora defina `GIT_AUTHOR_DATE` e `GIT_COMMITTER_DATE` com o mesmo valor e repita.
6. Apague `.git/index` e rode `mini-git status`: cada arquivo aparece como removido do índice e, ao mesmo tempo, não rastreado. Por quê? Depois rode `mini-git add .` e veja o `status` voltar ao normal.
7. Num repositório de teste, troque uma letra de um objeto: descomprima, edite, comprima de novo e grave por cima (o arquivo é somente leitura, porque o Git trata objetos como imutáveis). O que o `git fsck` diz? E o `mini-git cat-file -p`?

## Limitações

- **Packfiles ficam de fora:** o mini-git só lê objetos soltos. Depois de um `git gc` (ou num repositório clonado), os objetos vão para packfiles e o mini-git avisa que não consegue lê-los.
- Não há merge, rebase, stash, remotos (fetch, push), etiquetas anotadas novas, submódulos nem `.gitattributes`.
- O `status` não detecta renomeações (o Git mostra `R`; o mini-git mostra um arquivo apagado e outro novo).
- O `diff` usa o `difflib` do Python, que pode agrupar as mudanças de um jeito um pouco diferente do Git.
- Os `.gitignore` suportam os padrões comuns (`*`, `?`, `[...]`, `**`, `!`, `/` no começo e no fim), não todos os casos extremos.
- É uma ferramenta de estudo: use o Git de verdade nos seus projetos.

## Contribuindo

Issues e pull requests são bem-vindos.

## Licença

MIT. Veja [LICENSE](LICENSE).
