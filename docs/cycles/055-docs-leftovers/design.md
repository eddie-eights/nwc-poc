# 直した不具合と 053・054 の残りに docs を揃える（055）

設計: PM(opus-5.5) / effort: high。QUEUE 170 / 171 / 173。2026-10-13。

## 背景

- 053（1 本の回線断で修復案が 2 件できるのを 1 件にまとめる）と 054（Splunk だけの構成でもエージェントが Splunk からログ・trap・メトリクスを引けるようにする）のエンジニアと cold reviewer が、直さずに QUEUE に積んだ docs の件が 3 行ある（170 / 171 / 173）
- 2026-10-13 にユーザーが「docs はなおして」と言った。この 3 行を 1 つのサイクルで直す
- QUEUE 173 の終わりの「T-10 に gNMIc の形のレコードを足す」はテストの件なので、このサイクルでは扱わず QUEUE 172（`search_splunk` の Nit）へ移す

## 現物（PM が 2026-10-12〜13 に読んで確かめたもの）

### 170: 直した不具合を「既知」と書いている 4 行

- 6ae4501（2026-10-05「AWS の動作確認（2026-10-05）で見つかった小さな不具合 3 件を直した」）で直したもの
  - Web のページのタイトルの文字化け: タイトルを user_data から外した。いまは `app/dashboard/config.py:49` の `TITLE = os.environ.get("TITLE", "運用管理ダッシュボード")`。`tests/test_workflow.py:1192` が見る
  - `MAX_TOOL_ROUNDS` で何も返さずに終わる: いまは Strands の `ToolLimit` のフックが、上限で TOOL_LIMIT_NOTE を足してもう 1 回だけ呼び、答えの頭に TOOL_LIMIT_PREFIX を付ける（`app/agentcore/app.py:61-66`、`:199-266`）。`tests/test_agentcore.py:373-386` が見る
- e645ecc（2026-10-05「AWS の動作確認（2026-10-05）の後片付け」）で直したもの
  - `nwc_gnmi` が起動の直後に `resolved` をまとめて送る: 前の値が無いときは、異常なら `firing` だけ出す。`tests/test_alerts.py:534`、`:551` が見る。`docs/pipeline.md:628-632` と `docs/architecture/resources/splunk.md:167` はすでに「直した（手元で確認、AWS では未確認）」
  - `nwc_trap` がサブインターフェースの `link_down` も出す（同上）
- f9a9498（2026-10-05）が `docs/troubleshooting.md` の「既知の不具合」の表から、タイトルと `MAX_TOOL_ROUNDS` の 2 行を消した。resolved のまとめ送りの行も、その後の修正で表から消えている
- `docs/verification/` に、これらの修正を AWS で確かめた記録は無い
- いまも「既知」と書いて troubleshooting の「既知の不具合」を指している行（QUEUE の `agentcore-bedrock.md:123` はいまは `:125`）
  - `docs/architecture/resources/web-ec2.md:92`: `| ページのタイトル | 文字化けする。既知（2026-10-05。…「既知の不具合」） |`
  - `docs/architecture/resources/agentcore-bedrock.md:125`: `| ツールの回数の上限に当たったとき | MAX_TOOL_ROUNDS（既定 5）に当たると、何も返さずに終わる。既知（…） |`
  - `docs/architecture/resources/firehose.md:76`: `| Splunk が起動の直後に resolved をまとめて送る | 既知（2026-10-05）。alert_events の行が増える（…） |`
  - `docs/architecture/resources/s3-tables-athena.md:94`: 上と同じ

### 171: 053 の cold review で直さなかった 3 か所

- `docs/troubleshooting.md:499-506` の `## 既知の不具合`: 節の頭は「…まだ直していないもの。直したら、その行を消す。」なのに、「いまは無い（…）。直したもの:」のあとに直した件を 2 つ（053 の回線の両端のまとめ、`nwc_trap` のサブインターフェース）残している。すぐ下の `## 経緯`（`:508`〜）は日付つきの箇条書きで、2026-10-08（012）から始まる
- `docs/architecture/resources/temporal.md:35`: worker が読み書きするものの表の `| Neptune Analytics | トポロジ（事前チェックと、保守中の機器の判定に使う）。修復案は置かない | 読むだけ |`。053 で starter が回線の両端を `a` の端にまとめるのに Neptune を読む（`app/temporal/awsio.py:6`、`:105`、`app/temporal/worker.py:11`）が、表に無い
- `tests/test_workflow.py:5` の docstring の終わり「実行は python3 tests/test_workflow.py（依存は無い）。」: `app/agentcore/topology.py:42` の `load_static` が `import yaml` する。`.venv/bin/python3 -S tests/test_workflow.py` は `ModuleNotFoundError: No module named 'yaml'` で rc=1（2026-10-12 に PM が実行）。`ops/check.sh` は `uv run --group dev --group web python "$t"` で回し、`pyproject.toml` の dev グループに pyyaml がある。test_analytics / test_dashboard_config / test_kb_index は `-S` でも通るので、そちらの「依存は無い」は正しい

### 173: 054 で直さなかった docs と design の言い回し

- `docs/architecture/resources/ssm-parameter-store.md:45`: `/<prefix>/splunk/admin-password`、`hec-token` の受け取る側が「Splunk のタスク」だけ。054 から tools の Lambda も `admin-password` を読む（`app/agentcore/evidence.py:15`、`IaC/terraform/aws-managed/workflow/gateway.tf:220-221`。`ssm:GetParameter` は `gateway.tf:84-88` の `${param_prefix}/*`）
- `app/gateway/handler.py:7-9` の docstring: 届く先が Neptune・OpenSearch・Prometheus・Athena で、Splunk の検索の API（`SPLUNK_URL`）が無い
- `deploy.env.example:32` の WORKFLOW の説明「エージェントが Neptune / OpenSearch / Prometheus を見て…」に Splunk が無い
- `docs/cycles/054-splunk-agent-tools/design.md`
  - `:59`「（`host` が `unknown` のものだけは落ちる）」: 1 段目の本文の語で落ちるのは、`_raw` の tags に機器名が無いイベント = `device` が `unknown`（tags に sysName / agent_host / source / host のどれも無い）もの。HEC の `host` は `_raw` に無い
  - `:62`「`host="<device_id>"` が当てる値は実際には `unknown` だけだった」: gNMIc の HEC の `host` は IP（`app/spark/snmp_sinks.py:321` で agent_host の列 = `tags.source`、`:619` で `host` = `host` か `agent_host`）。IP は `tags.source` の句ですでに当たるので、`host` の句が新たに当てていたのは `unknown` だけ、という意味になるよう直す
  - `:153` の見出し「**`host` が `unknown` のイベント。**」: 本文は `device` が `unknown` の話
- 同じ原因（054 で Splunk を足したとき、エージェントの調べる先の列挙を直していない）のコメント・docstring・README（QUEUE には無い。PM が 2026-10-12 に grep で見つけた）
  - `README.md:15` の mermaid `TOOLS["ツール<br/>Neptune / OpenSearch / Prometheus"]`
  - `app/temporal/worker.py:5`「エージェントに Neptune / S3 / OpenSearch / Prometheus を見させて」
  - `app/agentcore/evidence.py:1`、`:3`（調べる先に Splunk が無い）、`:4`「ここは残りの 3 つ:」（下に 4 つ並ぶ）
  - `IaC/terraform/aws-managed/workflow/locals.tf:5`（英語のコメント「Neptune / OpenSearch / Prometheus through the MCP tools」）
  - `IaC/terraform/aws-managed/workflow/gateway.tf:82-83` の `Parameters` の文のコメントが Neptune の ID と OSS 版のパスワードだけで、`splunk/admin-password` が無い

## 設計方針

1. **直すのは docs とコメント・docstring だけ。** コードの動きは変えない（`.py` / `.tf` は `#` の行と docstring だけ）。`docs/cycles/054-splunk-agent-tools/design.md` は当時の記録だが、QUEUE 173 で直すと決めた言い回しの 3 か所だけ直す（事実の誤りなので）
2. **「直した」と書くときは、どこで確かめたかを添える。** 手元のテスト（ファイル名）で確かめた、AWS では未確認、と分けて書く。確かめた範囲より広く言わない
3. **troubleshooting の「既知の不具合」は節の頭の決まりどおりにする。** 直した件は消し、`## 経緯` に日付つきで移す。resources の 4 行は「直した（commit、手元のテスト、AWS では未確認）」にし、troubleshooting の「経緯」を指す
4. **コードの行番号を新しく書かない。** 関数名・ファイル名・節の見出しで指す（052 の方針 4 と同じ）

## 変更対象ファイル

| 番号 | ファイル:行（目安） | 直し方 |
|---|---|---|
| E-1 | `docs/architecture/resources/web-ec2.md:92` | 「文字化けする。既知」→ 直した（6ae4501。タイトルを user_data から外し、既定は `app/dashboard/config.py` の `TITLE`。`tests/test_workflow.py` で確認。AWS では未確認） |
| E-2 | `docs/architecture/resources/agentcore-bedrock.md:125` | 「何も返さずに終わる。既知」→ 上限に当たると、もう 1 回だけモデルを呼んで答えさせ、頭に注意書きを付ける（`app/agentcore/app.py` の `ToolLimit`。6ae4501。`tests/test_agentcore.py` で確認。AWS では未確認） |
| E-3 | `docs/architecture/resources/firehose.md:76` | 「既知」→ 直した（e645ecc。前の値が無いときは `firing` だけ。`tests/test_alerts.py` と手元の Splunk で確認。AWS では未確認） |
| E-4 | `docs/architecture/resources/s3-tables-athena.md:94` | E-3 と同じ |
| E-5 | `docs/troubleshooting.md` の `## 既知の不具合` と `## 経緯` | 既知から「直したもの:」の 2 つを消し、節を「いまは無い（…）。」だけにする。経緯に 3 行足す（日付の順）: 2026-10-05 にタイトル・ツールの上限（6ae4501）、2026-10-05 に trap のサブインターフェース・`nwc_gnmi` の resolved のまとめ送り（e645ecc）、2026-10-12（053）に回線の両端のまとめ。どれも「手元で確認、AWS では未確認」 |
| E-6 | `docs/architecture/resources/temporal.md:35` | Neptune Analytics の中身に「starter が回線の両端の異常を 1 つにまとめるときの回線の端（cycle 053）」を足す |
| E-7 | `tests/test_workflow.py:5`（docstring） | 「（依存は無い）」→ PyYAML が要る（`topology.load_static`）。`bash ops/check.sh` か `uv run --group dev python tests/test_workflow.py` で回す |
| E-8 | `docs/architecture/resources/ssm-parameter-store.md:45` | 受け取る側に「tools の Lambda（`admin-password` だけ。`search_splunk` が 8089 の検索の API に Basic 認証で使う。cycle 054）」を足す |
| E-9 | `app/gateway/handler.py:7-9`（docstring） | 届く先に Splunk の検索の API（`SPLUNK_URL`。8089。`search_splunk`）を足す |
| E-10 | `deploy.env.example:32`（コメント） | 「Neptune / OpenSearch / Prometheus」→「Neptune / OpenSearch / Prometheus / Splunk」 |
| E-11 | `docs/cycles/054-splunk-agent-tools/design.md:59`、`:62`、`:153` | 現物の 173 の項のとおりに直す（`host` → `device`。`:62` は gNMIc の host が IP で `tags.source` の句ですでに当たるので、`host` の句が新たに当てていたのは `unknown` だけ） |
| E-12 | `README.md:15`（mermaid） | `Neptune / OpenSearch / Prometheus` → `Neptune / OpenSearch / Prometheus / Splunk` |
| E-13 | `app/temporal/worker.py:5`、`app/agentcore/evidence.py:1`、`:3`、`:4`（docstring）、`IaC/terraform/aws-managed/workflow/locals.tf:5`、`ops/up.sh:10`（コメント。`deploy.env.example:32` と同じ文） | 調べる先に Splunk を足す。evidence.py の「残りの 3 つ」→「残りの 4 つ」 |
| E-14 | `IaC/terraform/aws-managed/workflow/gateway.tf:82-83`（コメント） | `Parameters` の文で `splunk/admin-password`（`search_splunk`。cycle 054）も読むことを足す |
| E-15 | `docs/cycles/QUEUE.md` | 170 / 171 / 173 に ` → 055-docs-leftovers`。173 の「T-10 に gNMIc の形のレコードを足す」を 172 へ移す |

## 再利用するもの

- `docs/architecture/resources/splunk.md:167` の「直した（手元で確認、AWS では未確認）」の書き方
- `docs/pipeline.md:628-632` の e645ecc の 2 件の書き方
- `docs/cycles/052-docs-sync/` の進め方（docs だけのサイクル、cold reviewer 1 回）

## 実装ステップ

1. E-1〜E-14 を直す（PM。docs とコメントだけなのでエンジニアを起こさない）
2. E-15 の QUEUE を直す
3. 下の検証を打って `build.md` に貼る。セルフレビュー（反対弁護人は呼ばない。docs だけ）
4. commit、push、cold reviewer 1 回

## 検証方法

- V-1: `grep -rn '既知（2026-10-05' docs README.md`（`docs/cycles/` と `docs/verification/` を除く）が 0 行。いまは 4 行
- V-2: `grep -n '直したもの' docs/troubleshooting.md` が 0 行。いまは 1 行
- V-3: `grep -n '依存は無い' tests/test_workflow.py` が 0 行。いまは 1 行
- V-4: `grep -rn 'Neptune / OpenSearch / Prometheus'`（README.md、deploy.env.example、app、IaC、ops、tests、docs。`docs/cycles/` を除く）の残りが、エージェントの調べる先の列挙でない行だけ。残った行と理由を build.md に貼る
- V-5: `.py` / `.tf` の変更が docstring と `#` の行だけ（`git diff -U0 -- '*.py' '*.tf'` の `+` / `-` の行を目で見る）
- V-6: `bash ops/check.sh` の最後の行が「すべて通過」
- V-7: 足した相対リンクの先が実在する

## 未確定事項とリスク

- **AWS では確かめない。** 直した 4 件（タイトル、ツールの上限、resolved のまとめ送り、trap のサブインターフェース）と 053 の回線の両端のまとめは、AWS では未確認のまま。docs にもそう書く。次の AWS の動作確認で見る
- **054 の design.md を直すこと。** サイクルの記録は当時のまま残すのが原則（052 の方針 1）。ここは QUEUE 173 で「言い回しを直す」と決めた事実の誤りなので直す。言い回し以外は触らない
- **E-12〜E-14 は QUEUE に無い。** 173 と同じ原因（054 で調べる先の列挙を直していない）で、同じ種類の直しなので入れる。範囲を広げすぎないよう、エージェントの調べる先の列挙だけにする
