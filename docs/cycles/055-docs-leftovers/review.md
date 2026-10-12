# 055 のレビュー（直した不具合と 053・054 の残りに docs を揃える）

## Round 1

- 実行したモデル: cold reviewer は opus（effort xhigh）。PM の確認は opus-5.5
- cold reviewer に依頼した（初回のビルドのあと）。対象は adbaafa → 452ca7c（`build.md` の Round 1）
- 結果は下の「直した不具合と 053・054 の残りに docs を揃える（055）のレビュー Round 1」をそのまま連結した

# 直した不具合と 053・054 の残りに docs を揃える（055）のレビュー Round 1

対象は `adbaafa..452ca7c`（ブランチ `docs/170-docs-leftovers`）。設計は `docs/cycles/055-docs-leftovers/design.md`。レビューした人は cold reviewer で、前の文脈を持っていない。

## サマリ

件数は Must fix 0、Should fix 1、Nit 3。

設計の E-1〜E-15 は、すべて差分に入っていた。直したと書いた 4 件は次のとおり。

- ページのタイトルの文字化け
- `ToolLimit`
- trap のサブインターフェースの `link_down`
- `nwc_gnmi` の起動直後の `resolved` のまとめ送り

どれにもコミット、確かめたテストのファイル、「AWS では未確認」が書いてあり、コードの中身とも合っていた。`.py` / `.tf` / `.sh` の変更はコメントと docstring だけだった（V-5）。

Should fix の 1 件は、E-13（エージェントの調べる先の列挙に Splunk を足す）の取りこぼしで、2 か所残っている。V-4 の grep が `Neptune / OpenSearch / Prometheus` という 1 通りの書き方しか探さないので、言い回しの違う列挙を拾えなかった。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  E-1〜E-15 と V-1〜V-7 を、`git diff adbaafa 452ca7c` の 20 ファイルと 1 つずつ照らした。V-1〜V-3 は自分で grep して 0 件だった。build.md の逸脱（`ops/up.sh:10` を E-13 に足した）も見た。
- **正確さ**
  「直した」と書いた 4 件をコードとテストで確かめた。
  コードは `app/agentcore/app.py` の `ToolLimit` と `TOOL_LIMIT_NOTE` / `TOOL_LIMIT_PREFIX`、`app/dashboard/config.py` の `TITLE`、`savedsearches.conf` の `nwc_gnmi` と `nwc_trap` の `match(target, "^(lo|mgmt)|[.]")`、`app/temporal/worker.py` の `handle_message` と `rules.link_anomaly` を見た。
  テストは `tests/test_agentcore.py`、`tests/test_workflow.py` のタイトルの検査、`tests/test_alerts.py` を見た。
- **054 の Splunk のツールの説明**
  `evidence.py` の `search_splunk`（`coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host)`）、`handler.py`、`gateway.tf` の ssm:GetParameter の範囲（`parameter${local.param_prefix}/*`）、`locals.tf:195` の `splunk_url`、`snmp_sinks.py:321` / `:619` と照らした。
- **リンク先の有無**
  troubleshooting.md の「経緯」と、pipeline.md の「Splunk のアラート」（`### Splunk のアラート`）があることを確かめた。
- **実行時の不具合**
  .py の差分は docstring とコメントの行だけだった（差分を読んだ）。`terraform fmt -check IaC/terraform/aws-managed/workflow` は差分なし、`bash -n ops/up.sh` は構文エラーなしだった。
- **テスト**
  自分で走らせた。
  `uv run --isolated --group dev python tests/test_workflow.py` は「通過 476 / 失敗 0」で rc=0。docstring に新しく書いた実行方法が、dev のグループだけで動くことも確かめた。
  `.venv/bin/python tests/test_*.py` は、全ファイルが rc=0 だった。結果は下の表のとおり。

| テスト | 結果 |
|---|---|
| test_agentcore | 220 / 0 |
| test_alerts | 168 / 0 |
| test_analytics | 549 / 0 |
| test_collectors | 79 / 0 |
| test_dashboard_config | 3 / 0 |
| test_graph | 78 / 0 |
| test_kb_index | 7 / 0 |
| test_lab_debug | 121 / 0 |
| test_local_compose | 145 / 0 |
| test_nautobot | 「68 項目すべて通過」 |
| test_oss | 177 / 0 |
| test_oss_ops | 210 / 0 |
| test_oss_roll | 66 / 0 |
| test_stream | 119 / 0 |
| test_sync | 104 / 0 |
| test_workflow | 476 / 0 |

見ていない観点:

- **`bash ops/check.sh` の全体**
  走らせていない。2 段目の `terraform init` / `validate`（10 の root と 2 つの base）が lock ファイルを書き換えるおそれがあるため。build.md の V-6（「すべて通過」）のうち、terraform validate と 5 段目の netops の grep は自分では見ていない。
- **AWS での動作**
  走らせていない。docs の「AWS では未確認」が正しいかは、`docs/verification/` の記録で確かめた（下の Nit の補足）。
- **README の mermaid の描画**
  描画していない。差分を読んだだけ。

## Must fix

None

## Should fix

- [design.md との整合性] E-13（エージェントの調べる先の列挙に Splunk を足す）の取りこぼしが 2 か所ある。
  1 つ目は `IaC/terraform/aws-managed/workflow/locals.tf:8-9`。「so the runtime can read Neptune, the logs collection and the metrics workspace over MCP」とあり、Splunk が無い。このサイクルが `:5` に Splunk を足した同じファイルの、すぐ下のコメント。
  2 つ目は `docs/data-stores.md:263` の `agent` イメージの行。「Neptune のトポロジ、OpenSearch / Prometheus の証拠を集めて答え」とあり、Splunk が無い。
  どちらも、054 で足した `search_splunk` を数えていない。V-4 の grep が `Neptune / OpenSearch / Prometheus` の書き方しか探さないので、build.md の「残りは test_workflow.py:1073 だけ」に出てこなかった。
  Should fix にした理由: 動作は壊れていないが、このサイクルの目的の 1 つ（QUEUE 173 の「Splunk のツールを docs に足す」）が届いていない。ここを読んだ人は、Splunk だけの構成ではエージェントが証拠を引けないと受け取る。
  再現したコマンドと出力（`--include` は .md / .tf / .py / .sh / .example。出力は該当の 2 行だけ残し、長い行は切った）:
  ```
  $ grep -rn 'OpenSearch / Prometheus\|OpenSearch・Prometheus\|OpenSearch と Prometheus\|Neptune / OpenSearch\|Neptune / S3\|Neptune, the logs\|Neptune / Prometheus' --include='*.md' --include='*.tf' --include='*.py' --include='*.sh' --include='*.example' . | grep -v '^./.venv\|docs/cycles/\|docs/verification/' | grep -v Splunk
  ./docs/data-stores.md:263:| `agent` | [app/agentcore/](../app/agentcore/)（自前ビルド） | AgentCore Runtime | チャットの本体。Bedrock のモデルを呼び、Neptune のトポロジ、OpenSearch / Prometheus の証拠を集めて答え、承認待ちの修復案を作る
  ./IaC/terraform/aws-managed/workflow/locals.tf:8:# The AgentCore Gateway (MCP) exposes the agent tools through a Lambda in the VPC so the runtime can read Neptune, the logs
  ```
  同じ grep のほかの行は、エージェントの調べる先の列挙ではなかった。たとえば `worker.py:41` の awsio の一覧、`deploy.env.example:104`、`docker/compose/README.md:262`、`pipeline.md:667`、`ops/up.sh:181/210`、`locals.tf:28`（下の Nit）、`tests/test_workflow.py:1073`（build.md に理由あり）。

## Nit

- [正確さ] `IaC/terraform/aws-managed/workflow/locals.tf:28` のコメントに「修復案の S3 Tables と OpenSearch / Prometheus は IaC/terraform/aws-managed/pipeline/analytics の state から読む」とあるが、`locals.tf:195` の `splunk_url = try(data.terraform_remote_state.analytics.outputs.splunk_search_url, "")` も analytics の state から読んでいる。Splunk を足すと、コメントがコードと揃う。
  Nit にした理由: 読む先の state の名前は合っていて、漏れているのは列挙の 1 項目だけだから。V-4 の書き方には当たらないので、設計の範囲の外とも読める。
- [design.md との整合性] `docs/cycles/054-splunk-agent-tools/design.md:62` の書き直しで、コードの行番号 `snmp_sinks.py:321` を新しく書いた。055 の設計方針 4 は「コードの行番号を新しく書かない。関数名・ファイル名・節の見出しで指す」。同じ行の `snmp_sinks.py:619` と `:61` の `snmp_sinks.py:87` は前からあったもの（`git diff adbaafa 452ca7c -- docs/cycles/054-splunk-agent-tools/design.md` で、`+` の行にだけ `:321` がある）。
  Nit にした理由: 書いた事実は正しく（gNMIc は agent_host の列が `tags.source`）、行がずれても読み違いは小さいから。
- [正確さ] `docs/architecture/resources/agentcore-bedrock.md:55` の Gateway の行に「ツールは最大 5 往復」とあるが、`app/agentcore/app.py` の `MAX_TOOL_ROUNDS` は「1 回の質問でツールを呼べる回数の上限」で、1 往復で複数のツールを呼んでも 1 回ずつ数える。同じファイルの `:125`（このサイクルで直した行）は「ツールの回数の上限」と正しく書いている。
  Nit にした理由: `git blame` では b6abab26（2026-10-05）からある行で、このサイクルの差分の外だから。直すかどうかは QUEUE に積むかで決めればよい。
  補足（指摘ではない）: AWS に直接の確かめは無い。ただ、`docs/verification/20261008-oss-aws.md`（「Splunk の link_down を 2 件」「修復案 2 件 pending」）と `20261011-aws-managed-045.md:101`（「InvestigateAnomaly 2 本 / pending 2 行」）では、1 本の回線断の修復案が 4 件から 2 件に減っている。trap のサブインターフェースの修正が AWS で効いていることの間接的な証拠になる。それだけを切り分けた記録ではないので、docs の「AWS では未確認」は正しい。

## 良かった点

- 「直した」と書いた行はどれも、直したコミット（6ae4501 / e645ecc / 053）、確かめたテストのファイル、「AWS では未確認」、troubleshooting.md の「経緯」へのリンクの 4 つが揃っていた。設計方針 2 のとおり、どこまで確かめたかが行ごとに読める。
- troubleshooting.md の「既知の不具合」を「いまは無い」にして、直した件を日付つきで「経緯」へ移した。設計方針 3 のとおり、節の役割が分かれた。
- `.py` / `.tf` / `.sh` の変更はコメントと docstring だけで、動作は変わらない（設計方針 1）。`terraform fmt -check` と `bash -n` も通った。
- `tests/test_workflow.py` の docstring に書いた実行方法（`uv run --group dev python tests/test_workflow.py`）が、`--isolated` の新しい環境で dev のグループだけでも通ることを確かめた（476 / 0）。
- 054 の design.md:62 の書き直しは事実として正しい。gNMIc の HEC の host は IP（`tags.source`）なので、`host` の句が新しく当てていた値は `unknown` だけだった。言い回しが QUEUE 173 の指摘に答えている。
- QUEUE は 170 / 171 / 173 に `→ 055-docs-leftovers` を付け、範囲から外した T-10 の gNMIc の件を 172 へ移していた。運用の決まりに沿っている。

## ユーザーへの質問

None

### PM の確認（Round 1）

**Should fix（E-13 の取りこぼし 2 か所）: 再現した。直した。**

- 再現: cold reviewer と同じ grep（`OpenSearch / Prometheus\|…\|Neptune, the logs\|…` で .md / .tf / .py / .sh / .example、`.venv` と cycles / verification を除き、Splunk を含む行を除く）を 452ca7c で打ち、`docs/data-stores.md:263` と `IaC/terraform/aws-managed/workflow/locals.tf:8` が出た
- 原因: V-4 の grep が `Neptune / OpenSearch / Prometheus` と `OpenSearch / Prometheus / S3` の 2 通りの書き方しか探さず、「Neptune, the logs collection and the metrics workspace」（英語のコメント）と「Neptune のトポロジ、OpenSearch / Prometheus の証拠」（`Neptune /` で続かない）を拾えなかった
- 直し:
  - `locals.tf:9` を「the logs collection, the metrics workspace and Splunk (search_splunk, cycle 054) over MCP」にした。`terraform fmt -check IaC/terraform/aws-managed/workflow` → exit 0
  - `docs/data-stores.md:263` を「OpenSearch / Prometheus / Splunk の証拠を集めて答え」にした。`grep -c 'OpenSearch / Prometheus / Splunk の証拠' docs/data-stores.md` → `1`
- 同じ grep の残り（直した 2 か所のほか）は、cold reviewer の挙げたものと同じで、エージェントの調べる先の列挙ではない（格納先の作り分け、FAQ、awsio の一覧、`locals.tf:28` は下の Nit、`tests/test_workflow.py:1073` は build.md の V-4 に残した理由）

**Nit 3 件: 直さない。**

QUEUE に `- [ ] 055 の cold review の Nit を片付ける` で積んだ（`locals.tf` の remote_state の上のコメントに `splunk_url` が無い、054 の design.md に行番号 `snmp_sinks.py:321` を新しく書いた、`agentcore-bedrock.md` の「ツールは最大 5 往復」）。

**検証（直したあと）**

- `bash ops/check.sh` → exit 0、最後の行は「すべて通過」。`grep -cE '^NG|失敗 [1-9]'` は `0`。作業ツリーの変更は直した 3 ファイルと review.md だけ（lock ファイルは変わっていない）
- cold reviewer の Should fix の元の再現（上の grep）は、`docs/data-stores.md:263` が「OpenSearch / Prometheus / Splunk」になり、`locals.tf:8` は同じ文の続きの `:9` に Splunk がある（grep の 1 行単位では `:8` が残るが、文としては直った）

**2 回目の cold reviewer: 依頼しない。**

Must fix は 0 で、Round 1 の直しはコメント 1 行と docs の表の 1 行だけ（`git diff --stat` は locals.tf 1 行、data-stores.md 1 行、QUEUE.md）。052 と同じ扱い。

**見た観点**

design 整合性 / 正確さ（cold reviewer）、runtime bugs（.py / .tf / .sh はコメントと docstring だけ。`terraform fmt` と ops/check.sh）、missing tests（docs のサイクルなので新しいテストは無し。既存のテストは全部通過）。

**見ていない観点**

security / data loss / API compatibility / type safety（コードの動作を変えていない）、AWS での動作（このサイクルでは AWS を使っていない）、README の mermaid の描画。
