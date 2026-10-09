# docs を読みやすくする（020）の実装

## Round 1

実装モデル: opus-5.5 / effort: 既定のまま（自分のセッションの effort は変えられない）。エンジニア1。ブランチ `docs/020-docs-readability`（origin/main 9fb0616 から）。

### ステップ 0: 着手前の実測（9fb0616）

```
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs awk 'length>300 && !/^\|/' | wc -l
     350
$ ls README.md docs/*.md docs/architecture/*.md docs/architecture/resources/*.md docker/compose/README.md | grep -v faq-fukuda | xargs grep -hoE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | wc -l
     421
$ grep -c '\$' README.md
8
$ grep -o '\$' README.md | wc -l
      18
$ grep -c '^### ' docs/faq-fukuda-nwc-poc.md; grep -c '^- \[' docs/faq-fukuda-nwc-poc.md
80
12
$ uv run --group dev --group web python tests/test_analytics.py 2>&1 | tail -1
通過 513 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py 2>&1 | tail -1
通過 173 / 失敗 0
$ uv run --group dev --group web python tests/test_lab_debug.py 2>&1 | tail -1
通過 97 / 失敗 0
```

- `grep -c '\$' README.md` は行数を数えるので 8（design.md の「17」は `$` の個数に近い。個数は 18）。検証 3 は行数（`grep -c`）で 5 以下を見る。
- 検証 1・2 の対象には、変えないファイル（`docs/ai-dev-flow.md` の長い行 2、`docs/hearing.md` の日付 2、`docs/GLOSSARY.md`）も入っている。
