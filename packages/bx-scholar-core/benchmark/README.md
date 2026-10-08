# Benchmark do verify_citation

Mede o verificador de citações contra obras reais com respostas HTTP congeladas.
Desenho combinado com a revisão do Codex (gpt-6-astra).

| Arquivo | Papel |
|---|---|
| `collect.py` | Sorteia obras por estrato (semente fixa) e congela as respostas completas do Crossref e do OpenAlex em `data/works/*.json.gz`. Único passo que usa rede. |
| `generate.py` | Gera os casos rotulados (`data/cases_dev.jsonl`, `data/cases_holdout.jsonl`) só a partir das respostas congeladas. Não importa o verificador. |
| `run.py` | Roda cada caso pelo caminho real da tool (`_verify_one`) com HTTP simulado, que falha em qualquer requisição não prevista, e conta os erros. |

```bash
uv run python packages/bx-scholar-core/benchmark/collect.py --limit 600   # rede
uv run python packages/bx-scholar-core/benchmark/generate.py
uv run python packages/bx-scholar-core/benchmark/run.py --split dev
```

**Erro:** um por caso. Falso positivo = `verified` quando o esperado não é `verified`, ou com DOI diferente do esperado. Falso negativo = esperado `verified`, veio outro status. **Aprovação:** no máximo 4 erros e zero falso positivo. O split `holdout` só é usado para a avaliação final.

**Limitação declarada:** a busca é sintética. Cada busca simulada devolve os registros congelados da obra-alvo misturados a registros de outras obras do mesmo estrato, em ordem sorteada. O benchmark mede a decisão do verificador, não a qualidade da recuperação do Crossref/OpenAlex.
