# 直した不具合と 053・054 の残りに docs を揃える（055）の実装

## Round 1

実装モデル: opus-5.5 / effort: high（PM。docs とコメント・docstring だけなので PM が直した）

### 件ごとの結果

| 番号 | 結果 | 確かめた現物 |
|---|---|---|
| E-1 | 直した（`web-ec2.md` のページのタイトルの行: 6ae4501 で直した、AWS では未確認、troubleshooting の「経緯」へ） | `app/dashboard/config.py:49` の `TITLE`、`tests/test_workflow.py:1192` |
| E-2 | 直した（`agentcore-bedrock.md` のツールの回数の上限の行: 上限でもう 1 回だけモデルを呼び、頭に断りを付ける。ツールを外すとは書かない） | `app/agentcore/app.py:64` の `TOOL_LIMIT_NOTE`、`:195` の `ToolLimit`、`tests/test_agentcore.py:373-386` |
| E-3 | 直した（`firehose.md` の resolved の行: e645ecc） | `app/splunk/nwc_alerts/default/savedsearches.conf`、`tests/test_alerts.py` |
| E-4 | 直した（`s3-tables-athena.md`。E-3 と同じ文） | 同上 |
| E-5 | 直した（「既知の不具合」から「直したもの:」と 2 行を消し、「経緯」に 2026-10-05 の 2 項（6ae4501 / e645ecc）と 2026-10-12（053）を日付の順に足した） | 6ae4501 / e645ecc / 053 の `rules.link_anomaly`（`app/temporal/rules.py:278`） |
| E-6 | 直した（`temporal.md` の Neptune Analytics の行に回線の両端のまとめ） | `app/temporal/worker.py:400`、`app/temporal/awsio.py:105` |
| E-7 | 直した（`tests/test_workflow.py` の docstring: PyYAML が要る、実行は `bash ops/check.sh` か `uv run --group dev`） | `app/agentcore/topology.py:42` の `import yaml`、`pyproject.toml:10-11` |
| E-8 | 直した（`ssm-parameter-store.md` の `splunk/admin-password` の受け取る側に tools の Lambda） | `app/agentcore/evidence.py` の docstring、`workflow/gateway.tf:221-222` |
| E-9 | 直した（`app/gateway/handler.py` の docstring の届く先に Splunk の 8089 の検索の API） | `workflow/gateway.tf:222` の `SPLUNK_URL` |
| E-10 | 直した（`deploy.env.example:32`） | `app/agentcore/app.py:113` |
| E-11 | 直した（054 の `design.md` の `:59` / `:62` / `:153`） | `app/spark/snmp_sinks.py:321`（gNMIc の agent_host の列は `tags.source`）、`:619`（HEC の host）、`app/agentcore/evidence.py:187` の `coalesce` |
| E-12 | 直した（`README.md` の mermaid） | 同上 |
| E-13 | 直した（`worker.py:5`、`evidence.py:1` / `:3` / `:4`、`locals.tf:5`、`ops/up.sh:10`） | `evidence.py` の docstring の一覧はすでに 4 つ（search_splunk を含む） |
| E-14 | 直した（`gateway.tf` の `Parameters` の文のコメント） | `gateway.tf` の `Parameters`（`parameter${local.param_prefix}/*`）が `splunk/admin-password` を含む |
| E-15 | 直した（QUEUE の 170 / 171 / 173 に ` → 055-docs-leftovers`。173 の T-10 の項を 172 へ移した） | — |

### 変更ファイル

`docs/architecture/resources/{web-ec2,agentcore-bedrock,firehose,s3-tables-athena,ssm-parameter-store,temporal}.md`、`docs/troubleshooting.md`、`docs/cycles/054-splunk-agent-tools/design.md`、`docs/cycles/QUEUE.md`、`README.md`、`deploy.env.example`、`ops/up.sh`、`tests/test_workflow.py`、`app/gateway/handler.py`、`app/temporal/worker.py`、`app/agentcore/evidence.py`、`IaC/terraform/aws-managed/workflow/{locals,gateway}.tf`

### 設計からの逸脱

- `ops/up.sh:10` を足した。V-4 の grep で `deploy.env.example:32` と同じ文が残っていた。`design.md` の E-13 と V-4 の範囲に書き足した

### 検証（最後の編集のあとに取り直した）

V-1: `grep -rn '既知（2026-10-05' docs README.md --include='*.md' | grep -v 'docs/cycles/\|docs/verification/' | wc -l`

```
       0
```

（「既知の不具合」を指すリンクも `grep -rn '「既知の不具合」' docs README.md app IaC ops tests`（cycles / verification を除く）で 0 行）

V-2: `grep -c '直したもの' docs/troubleshooting.md` → `0`

V-3: `grep -c '依存は無い' tests/test_workflow.py` → `0`

V-4: `grep -rn 'Neptune / OpenSearch / Prometheus\|OpenSearch / Prometheus / S3'`（.md / .py / .tf / .sh / .example / .json。`.venv` と cycles / verification を除き、Splunk を含む行を除く）の残り

```
tests/test_workflow.py:1073:check("tools Lambda は VPC の中（Neptune / OpenSearch / Prometheus に届く）で、OPENSEARCH_ENDPOINT / PROMETHEUS_QUERY_URL を渡す",
```

残した理由: そのチェックが確かめているのは OpenSearch と Prometheus の 2 つの環境変数だけで、チェックの名前がその中身を言っている（調べる先の一覧ではない）。

V-5: `git diff -U0 -- '*.py' '*.tf' '*.sh'` の `+` / `-` の行は、`gateway.tf` / `locals.tf` / `ops/up.sh` の `#` の行と、`evidence.py` / `handler.py` / `worker.py` / `test_workflow.py` の docstring の行だけ（目で見た）

V-6: `bash ops/check.sh`

```
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

（exit 0。最後の 4 行だけ貼った）

V-7: 足したリンクの先 `docs/troubleshooting.md`（`## 経緯` は 505 行目）と `docs/pipeline.md`（`### Splunk のアラート` は 597 行目）は実在する

### セルフレビュー

- 自分: opus-5.5 / effort high（PM）。反対弁護人は呼んでいない（docs とコメントだけで、件ごとに現物を上の表の行で確かめた）

自分で見つけて直したもの

| 分類 | 観点 | 場所 | 中身 | 片付け |
|---|---|---|---|---|
| Should fix | 一貫性 | `ops/up.sh:10` | `deploy.env.example:32` と同じ WORKFLOW の説明に Splunk が無い | 直した（設計の E-13 に足した） |
| Nit | 正確さ | `docs/cycles/054-splunk-agent-tools/design.md:59` | 下書きで「HEC の host は `_raw` に入らない」と足したが、agent_host の列は本文に入るので誤読を招く | 書かなかった |

未解消の指摘: 無し
