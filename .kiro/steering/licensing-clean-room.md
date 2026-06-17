# Licensing & Clean-Room — MANDATORY

Regra **mandatória** e inegociável: **nunca copiar código de repositórios de
terceiros** para o nosso source. Aprender com eles é livre; copiar a expressão
deles não é.

Esta regra existe porque o projeto estuda ativamente outros projetos open-source
(ver `docs/COMPETITIVE-ANALYSIS.md`) com licenças variadas e **incompatíveis
entre si para mistura** (Apache-2.0, MIT, SigmaHQ/DRL, etc.). Copiar contaminaria
o repo com obrigações de atribuição/licença que não queremos carregar.

---

## CRITICAL: a linha que não se cruza

**Copyright protege a EXPRESSÃO (o código literal), não a IDEIA.**

| Permitido (livre) | Proibido (mandatório não fazer) |
|-------------------|----------------------------------|
| Ler e entender como um projeto faz algo | Copiar/colar arquivo ou trecho de código de terceiro |
| Descrever o padrão/conceito com atribuição | Traduzir o código deles linha-a-linha pro nosso estilo |
| **Implementar do zero** a partir do entendimento | Copiar estrutura literal de arquivo (mesma ordem, mesmos nomes, mesma lógica copiada) |
| Citar o projeto como inspiração numa spec/ADR | Copiar configs/regras/datasets sob licença própria |

Inspiração conceitual ("usar context-spill-to-disk", "provider plugável",
"HITL por-tool") é **livre** — são ideias. A implementação é **nossa, do zero**.

## Dependências de terceiros (o caminho limpo para reúso)

Quando reusar código de terceiro for genuinamente a melhor opção, fazer via
**dependência declarada** (package manager), NUNCA colando source:

- Adicionar como dependência pinada (`requirements.txt`/`pyproject.toml`).
- **Verificar e declarar a licença ANTES de adotar.** Flagar explicitamente ao
  usuário (ex: "litellm é MIT — OK adicionar?").
- Preferir licenças permissivas (MIT, Apache-2.0, BSD). Sinalizar copyleft
  (GPL/AGPL) como decisão consciente — pode contaminar.
- Nomes incomuns / possível typosquatting → flagar (ver `code-quality`/segurança).

## Datasets, regras e conteúdo (não só código)

A regra vale além de `.py`: regras SigmaHQ, datasets de benchmark, prompts,
templates, configs sob licença própria — mesma disciplina. Reusar só com a
licença respeitada e declarada; preferir reimplementar/recriar do zero.

## Atribuição quando inspirado

Ao implementar algo inspirado num projeto estudado, **citar a fonte** na spec/
ADR/comentário ("padrão inspirado em X") — honestidade intelectual, e deixa
claro que é reimplementação, não cópia.

## Anti-patterns

- ❌ Copiar trecho "só pra começar, depois eu mudo"
- ❌ Traduzir arquivo de terceiro pro nosso estilo e tratar como nosso
- ❌ Adotar dependência sem verificar/declarar a licença
- ❌ Copiar regras/datasets/prompts sob licença sem respeitar os termos
- ❌ Misturar código de licenças incompatíveis no mesmo repo

## Em caso de dúvida

Se não está claro se algo é "ideia" (livre) ou "expressão" (protegida):
**parar e perguntar ao usuário**. Nunca assumir que pode copiar.
