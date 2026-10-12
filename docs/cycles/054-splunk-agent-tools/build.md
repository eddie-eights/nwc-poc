# Splunk だけの構成でもエージェントが Splunk からログ・trap・メトリクスを引けるようにする（054）の実装

## Round 1

実装モデル: opus-5.5 / effort: high

### 変更したファイル

- `app/agentcore/evidence.py`
  - `SPLUNK_URL` / `SPLUNK_USER`（既定 admin）/ `SPLUNK_PASSWORD`（`toolkit.Param("SPLUNK_PASSWORD", "splunk/admin-password", decrypt=True)`）を足した。
  - `splunk_args`（検証と SPL の組み立て。純粋な関数）、`_splunk_post`、`search_splunk` を足した。
  - `TOOL_SPECS` / `TOOLS` に登録し、docstring も直した。
- `app/gateway/tools.json`
  - `search_splunk` を `query_history` の後に足した（14 個）。description は Python と同じ文。
- `app/agentcore/app.py` の `run_tool`（セルフレビューの Must fix 1）
  - モジュールの `GATEWAY_ONLY`（evidence は `{"search_splunk"}`）にあるツールは、Gateway のエラーをコンテナの関数で上書きしない。
- `app/agentcore/app.py` の SYSTEM_PROMPT と `app/temporal/rules.py` の `build_prompt`
  - 「search_logs / query_metrics が配備されていないと返したら、search_splunk で引く」の 1 文を足した。
- Terraform の output
  - `IaC/terraform/aws-managed/pipeline/analytics/outputs.tf` と `IaC/terraform/oss/pipeline/analytics/outputs.tf` に `output "splunk_search_url"` を足した。
  - 値は `local.splunk_on_ecs ? "https://splunk.${local.service_namespace}:8089" : ""`。
- workflow の Terraform
  - `IaC/terraform/aws-managed/workflow/locals.tf` に `splunk_url` を足した（`try(...outputs.splunk_search_url, "")`）。
  - `gateway.tf` で、tools の Lambda の環境変数に `SPLUNK_URL = local.splunk_url` を渡す。
- `IaC/terraform/aws-managed/base/core/security_groups.tf`
  - 表に `lambda → splunk 8089/tcp` の行を足し、頭のコメントも直した。
  - OSS 版は `oss.tf` が `msk` だけを外すので、この行はそのまま入る。
- テスト
  - `tests/test_agentcore.py`
    - Converse のツールの一覧と、system prompt の 1 文を見る。
    - search_splunk の T-1〜T-6 と、`app.run_tool` からの振り分けを見る。
  - `tests/test_workflow.py`
    - T-7: 14 個、evidence の 4 ツール、`build_prompt` の 1 文を見る。
    - T-9: `SPLUNK_URL = local.splunk_url`、locals の `splunk_url`、2 つの outputs.tf の `splunk_search_url` の値を見る。Lambda の timeout が `evidence.TIMEOUT` より長いことも見る。
  - `tests/test_analytics.py`（T-8）
    - `EXPECTED_FLOWS` に `lambda → splunk 8089` を足した。
    - 8089 を開けてよい送り元を `splunk` と `lambda` だけにした。`lambda` からの行が 8089 の 1 本だけであることも見る。
- docs
  - `deploy.env.example` の grafana と splunk の説明、`s3,splunk` / `splunk` の選択肢を直した。
  - `docs/deploy.md` の `grafana` の「外すと」を直した。
  - `docs/architecture/agent.md` の図のツールの一覧と「ツールが読むもの」に足した。
  - `docs/architecture/resources/agentcore-bedrock.md` のツールの表、つながり、未確認に足した。
  - `docs/architecture/resources/splunk.md` のポートの行、つながり、未確認を直した。
  - `docs/architecture/core.md` と `docs/architecture/resources/vpc-perimeter.md` の SG の表に行を足した。core.md の「開けていないもの」も直した。

### 設計から逸脱した点

- T-5 の「`urlopen` を差し替えて」
  - 送り方は `peers_check.py` と同じ `urllib.request.build_opener(ProxyHandler({}), HTTPSHandler(context=ctx)).open(...)` にした。
  - そのため、テストは `urllib.request.build_opener` を差し替えて Request・timeout・ハンドラを見る（T-2 と T-4 の「送らない」も、opener が作られないことで見る）。
- 返り値に `skipped`（JSON として読めなかった行の数）を足した。0 のときは付けない。
- `_splunk_post` は `URLError` / `TimeoutError` / `ValueError` に加えて `OSError` も「届かない: …」にする。TLS の途中で切れたときに Lambda を落とさないため。
- docs で直さなかったもの: `docs/architecture/architecture-managed.deck.md`
  - :148 と :150 にある「Splunk の管理 API 8089 の外からの受信は開けていない」が、この変更で古くなる。
  - スライドの原稿で、pptx の描き直しを伴うので触っていない（変更対象ファイルに無い）。PM に報告する。

### 検証

T-1〜T-6 と app.run_tool（`tests/test_agentcore.py`。search_splunk の行）:

```
ok Converse にトポロジの 9 ツール + 証拠の 4 ツール（search_splunk は cycle 054）+ 修復案の履歴（異常一覧 list_anomalies は 2026-10-02 にやめた）
ok system prompt は search_logs / query_metrics が未配備なら search_splunk を使えと言う（cycle 054）
ok T-2 拒否: {'device_id': 'a b'} は error で、Splunk に送らない
ok T-2 拒否: {'device_id': 'x"|delete'} は error で、Splunk に送らない
ok T-2 拒否: {'keyword': 'a | delete'} は error で、Splunk に送らない
ok T-2 拒否: {'keyword': '[search x]'} は error で、Splunk に送らない
ok T-2 拒否: {'keyword': '`m`'} は error で、Splunk に送らない
ok T-2 拒否: {'keyword': 'a\\b'} は error で、Splunk に送らない
ok T-2 拒否: {'keyword': 'a\nb'} は error で、Splunk に送らない
ok T-3 minutes="abc" は search_logs と同じく run_tool が error にする（Splunk に送らない）
ok T-4 SPLUNK_URL が空なら「Splunk の検索はまだ配備されていない」で、送らない
ok T-6 messages の ERROR → error が「Splunk: ERROR Unauthorized」
ok T-6 全部読めなければ「Splunk の応答が読めない」
ok app.run_tool は search_splunk を evidence に振る（仕様に無い引数は落とす）
ok app.run_tool: Gateway の search_splunk のエラーはそのまま返す（コンテナの「配備されていない」で上書きしない）。search_logs はいまどおりコンテナに戻す
通過 208 / 失敗 0
```

（T-1、T-2 の残り 10 件、T-3 の丸め、T-4 のパスワード無し、T-5、T-6 の残りは、同じログの `ok T-` の行。上は抜粋）

T-7 / T-9（`tests/test_workflow.py`）:

```
ok プロンプトは search_logs / query_metrics が未配備なら search_splunk で引かせる（cycle 054）
ok tools.json の 14 個は topology / evidence / proposals の TOOL_SPECS と同じ名前（list_anomalies は 2026-10-02 にやめた。search_splunk は cycle 054）
ok evidence のツールは search_logs / query_metrics / query_history / search_splunk
ok search_splunk の引数と必須が Python と同じ
ok search_splunk の説明が Python と同じ
ok tools Lambda に SPLUNK_URL = local.splunk_url を渡し、splunk_url は analytics の splunk_search_url を try で読む。splunk_search_url はマネージド版と OSS 版の analytics に同じ値（Splunk を ECS で動かすときだけ https://splunk.<namespace>:8089）で出る。Lambda の timeout は evidence.TIMEOUT より長い（cycle 054）
通過 461 / 失敗 0
```

T-8（`tests/test_analytics.py`）: `通過 168 / 失敗 0`。通信の表と 8089 の検査の行は「セルフレビュー」の R5 の注入で落ちることを確かめた。

T-9 の validate と、`bash ops/check.sh`（セルフレビューの直しを含む最後の編集のあとに取り直した。validate は 20 ルート）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/workflow  OK
（ほかの 14 ルートも OK）

== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし

== 4. 模擬テスト
通過 208 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 121 / 失敗 0
通過 145 / 失敗 0
通過 177 / 失敗 0
通過 210 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 461 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

T-10（手元の Splunk）: 未実行。`nc -z -G 2 127.0.0.1 8089` が終了コード 1 で、手元の compose の Splunk は動いていなかった（立てていない）。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので effort を切り替えられない）
- 反対弁護人: opus / effort xhigh（文脈を渡した。読み取り専用。返ってきたあと `git status --porcelain -uall` で増えたファイルが無いことを確かめた）

#### 指摘と片付け

- **Must fix 1** [correctness] `app/agentcore/app.py:79-88`（`run_tool`）
  - 破綻: AWS では Gateway が search_splunk を持ち、Runtime のコンテナには `SPLUNK_URL` が無い（8089 も lambda → splunk だけ）。Gateway の答えが error（401、`Splunk: ERROR …`、届かない、時間切れ、引数の拒否）だと、コンテナの関数に戻して「Splunk の検索はまだ配備されていない」に化ける。エージェントは原因を見られない。
  - 再現: Gateway の `call` を偽物にして `Splunk: ERROR Unauthorized` を返すテストを先に足し、直す前に落ちた（`AssertionError: app.run_tool: Gateway の search_splunk のエラーはそのまま返す…`、rc=1）。
  - 直した: `evidence.GATEWAY_ONLY = {"search_splunk"}` を足し、`run_tool` はそこにあるツールの Gateway のエラーをそのまま返す。ほかのツール（search_logs ほか）はいまどおりコンテナに戻す（同じテストで確かめる）。直したあと 208 / 0。
- **Should fix 2** [runtime / performance] `app/agentcore/evidence.py`（`splunk_args` の SPL）。設計側なので差し戻し候補として PM に報告。
  - 破綻: 機器名の絞り込みは `eval` のあとの `| search` なので、Splunk は `earliest` の範囲を全部読んでから絞る。`minutes` が長いと遅い。Splunk の `TIMEOUT = 20` はソケットの 1 回ごとで、全体の上限ではない。Runtime → Gateway の上限（`mcp_client.GATEWAY_TIMEOUT` 既定 30 秒）を超えうる。
  - 確かめたこと: Must fix 1 を直したので、時間切れは「gateway call failed: …」の error としてエージェントに届く（落ちない）。本物の Splunk の所要時間は測っていない（T-10 未実行）。
  - 片付け: SPL の形は design.md の指定どおりなので直さない。基本の検索に機器名の語を足す案は、`_raw` に機器名が無いイベント（host だけ）を落とすおそれがあるので、本物の Splunk で確かめてから決める。
- **Should fix 3** [docs] `docs/architecture/resources/agentcore-bedrock.md`、`docs/architecture/resources/splunk.md` の未確認の行
  - 破綻: 「手元のテストだけ」は、手元の Splunk で確かめたように読める。T-10 は未実行。
  - 直した: 「模擬テストだけ（本物の Splunk には送っていない）」にした。
- **Should fix 4** [docs / security] `IaC/terraform/aws-managed/base/core/security_groups.tf:11-13`、`docs/architecture/core.md`、`docs/architecture/resources/vpc-perimeter.md`、`docs/architecture/resources/splunk.md`
  - 破綻: SG `lambda` は tools の Lambda（`workflow/gateway.tf:210`）だけでなく、graph の status の Lambda（`pipeline/graph/sync.tf:144`、OSS 版 `:167`）と KB の索引の Lambda（`agent/kb.tf:216`）も付ける。「tools Lambda だけ」は誤りで、3 つから 8089 に届く。
  - 確かめたこと: `grep` で `security_group_ids = [local.lambda_sg_id]` の 4 か所と、それぞれの `lambda_sg_id` が `security_group_ids["lambda"]` であることを見た。
  - 直した: コメントと docs の 4 か所を「lambda の SG を付ける 3 つの Lambda から届く」に直した。専用の SG に分けるのは設計の変更なので PM に報告。
- **Should fix 5** [correctness] `app/agentcore/evidence.py`（`| search device="<id>*"`）。設計側なので差し戻し候補として PM に報告。
  - 破綻: 前方一致なので `10.0.0.1` が `10.0.0.10`〜`10.0.0.19` に、`spine-1` が `spine-10` に当たる。`head` の件数をほかの機器が食う。各イベントに `device` があるので、エージェントは見分けられる。
  - 確かめたこと: Splunk のワイルドカードの規則から読んだだけ（本物の Splunk では確かめていない）。
  - 片付け: design.md が `device="<device_id>*"` と T-1 の期待を決めているので直さない。直すなら `(device="<id>" OR device="<id>.*")` の形。
- Nit（直さない）
  - 6 [security] 8089 の応答がリダイレクトなら urllib は Authorization を付けたまま追う。宛先は VPC の中の Cloud Map の名前で、SG の送信の表は lambda → splunk の 8089 だけなので、外へは出られない。
  - 7 [correctness] messages に ERROR があると、届いた結果の行も捨てる（設計の指定どおり）。`preview: true` の行は見ていない（export は preview を返さない前提）。
  - 8 [security] パスワードはプロセスの間キャッシュする（`toolkit.Param`）。admin のロックアウトと監査ログは、読み取り専用のユーザー（QUEUE に積み済み）で片付ける。
  - 9 [docs] `docs/architecture/architecture-managed.deck.md:148` と `:150` の「Splunk の管理 API 8089 の外からの受信は開けていない」が古い（スライドは変更対象ファイルに無いので PM に報告）。`.env.example` のツールの節に SPLUNK_* が無い（手元は `SPLUNK_URL` を環境変数で渡す）。deploy.env.example と deploy.md の「search_splunk で引く」に WORKFLOW=1 の条件を足した（反対弁護人の指摘の一部）。
  - 10 [correctness] flows は機器名が host に戻ることがある（Spark の device map しだい）。設計の「未確定事項」の機器名の項と同じ。
  - 11 [security] `_raw` の中の文字がプロンプトに入る。search_logs と同じで、この設計の範囲外。

#### 退行の注入（`inject.py`。注入のたびに戻し、最後に `git status` で元どおりを確かめた）

```
R11 run_tool の GATEWAY_ONLY を外す: test_agentcore.py rc=1 AssertionError: app.run_tool: Gateway の search_splunk のエラーはそのまま返す（コンテナの「配備されていない」で上書きしない）。search_logs はいまどおりコンテナに戻す
R12 GATEWAY_ONLY から search_splunk を外す: test_agentcore.py rc=1 AssertionError: app.run_tool: Gateway の search_splunk のエラーはそのまま返す（コンテナの「配備されていない」で上書きしない）。search_logs はいまどおりコンテナに戻す
R1 keyword の拒否から | を外す: test_agentcore.py rc=1 AssertionError: T-2 拒否: {'keyword': 'a | delete'} は error で、Splunk に送らない
R2 ProxyHandler({}) を外す: test_agentcore.py rc=1 AssertionError: T-5 送り方: ProxyHandler({})（プロキシの環境変数を見ない）と、証明書を検証しない HTTPSHandler（peers_check.py と同じ）
R3 device の正規表現に空白を許す: test_agentcore.py rc=1 AssertionError: T-2 拒否: {'device_id': 'a b'} は error で、Splunk に送らない
R4 制御文字の拒否（isprintable）を外す: test_agentcore.py rc=1 AssertionError: T-2 拒否: {'keyword': 'a\nb'} は error で、Splunk に送らない
R5 SG の lambda → splunk 8089 を runtime → splunk に: test_analytics.py rc=1 AssertionError: 通信の表は決めた流れだけ（多い: [('runtime', 'splunk', 'tcp', 8089, 8089, '')] 足りない: [('lambda', 'splunk', 'tcp', 8089, 8089, '')]）
R6 tools Lambda の SPLUNK_URL を外す: test_workflow.py rc=1 AssertionError: tools Lambda に SPLUNK_URL = local.splunk_url を渡し、…（cycle 054）
R7 OSS 版の output splunk_search_url の値を空に: test_workflow.py rc=1 AssertionError: tools Lambda に SPLUNK_URL = local.splunk_url を渡し、…（cycle 054）
R8 raw を切らない: test_agentcore.py rc=1 AssertionError: T-6 3 行の JSON → count 3、各フィールドを写し、raw は 1000 文字に切る
R9 FATAL を拾わない: test_agentcore.py rc=1 AssertionError: T-6 FATAL も error（INFO は拾わない。結果の行があっても error を優先）
R10 build_prompt の 1 文を消す: test_workflow.py rc=1 AssertionError: プロンプトは search_logs / query_metrics が未配備なら search_splunk で引かせる（cycle 054）
```

#### 問題なしとした観点

- SPL の注入: T-2 の 17 件と R1 / R3 / R4 の注入で、拒否の文字を外すと落ちることを確かめた。拒否なので、穴は通るべき語が通らない側に倒れる。
- 送り方（プロキシを見ない、TLS を検証しない、Basic 認証、timeout 20）: T-5 と R2 で確かめた。
- Terraform: `ops/check.sh` の fmt と 20 ルートの validate が通り、R5〜R7 の注入で表と output と環境変数の検査が落ちる。
- 既存のツールの振る舞い: search_logs が Gateway のエラーのあとコンテナに戻ることを、Must fix 1 のテストで一緒に確かめた。`ops/check.sh` の模擬テスト 15 本が通る（上の検証の生ログ）。
- 未実行: T-10（本物の Splunk）。Should fix 2 と 5 の本物の Splunk での挙動は確かめていない。
