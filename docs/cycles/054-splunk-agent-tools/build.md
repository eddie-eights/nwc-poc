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

## Round 2

実装モデル: opus-5.5 / effort: high

担当: Round 1 の Should fix 2（S-2）と 5（S-5）、design.md の T-1 / T-10 / T-11、`architecture-managed.deck.md:148` / `:150`。

### 変更したファイル

- `app/agentcore/evidence.py`
  - `splunk_args`: 機器があれば基本の検索に本文の語 `"<device_id>"` を足す（S-2）。絞り込みを `| search device="<id>" OR device="<id>.*"`（完全一致と FQDN）にした（S-5）。
  - `SPLUNK_DEADLINE = 25` と `SPLUNK_CHUNK` を足した。
  - `_splunk_post(url, body, password, deadline)` は `(行, 打ち切りの文, エラー)` を返す。open の timeout は `min(TIMEOUT, 残り)`。読み出しは `read1` のループで、毎回 `_set_read_timeout`（`res.fp.raw._sock.settimeout`）で残りに合わせる。締め切りを過ぎたら、改行で終わった行だけを残す。
  - `search_splunk`: 入口で締め切りを決める。1 件も無ければ `error`、あれば `truncated` を付ける。
  - TOOL_SPECS の description（25 秒で打ち切る、完全一致）。
- `app/gateway/tools.json`: description を同じに。
- `tests/test_agentcore.py`: T-1 の期待を完全一致と機器の語に。T-11 を足した（偽物の `_SlowRes` / `_SlowOpener` と、socketpair の本物の `HTTPResponse`）。
- `docs/architecture/architecture-managed.deck.md:148` / `:150` と `docs/architecture-managed.pptx`（描き直し）: lambda → splunk 8089 の行と、8089 の受信の説明。
- `docs/architecture/resources/agentcore-bedrock.md`: search_splunk の行（完全一致、打ち切り）。未確認の表の「模擬テストだけ」を T-10 の結果に。
- `docs/architecture/resources/splunk.md`: 未確認の表の「模擬テストだけ」を T-10 の結果に。

### 設計から逸脱した点

- 読み出しのソケットの timeout を残りに合わせるために、私有の属性 `res.fp.raw._sock` を使う。無ければ何もしない（open のときの値のまま）。T-11 の socketpair のテストが、名前が変わったら落ちる。
- 締め切りの前に `TIMEOUT` 秒応答が止まったときは、文を「<TIMEOUT> 秒応答が途切れたので打ち切った」にした（締め切りとは言わない）。
- 読んでいる途中で切れたとき（IncompleteRead ほか）は、読めた行を `truncated`（「応答が途中で切れた（…）」）付きで返す。1 行も無ければ「届かない: …」。セルフレビューで足した。
- open で `http.client.HTTPException` を受ける。HTTP のエラーの本文を読む途中で切れても `HTTP <code>: ` を返す。これもセルフレビューで足した（Lambda の例外にしない）。

### 検証

T-1 / T-11（`uv run --group dev --group web python tests/test_agentcore.py`。最後の編集のあと）:

```
ok T-1 SPL: 引数ありは sourcetype=nwc:<topic>・earliest・機器名の語・語の順で始まり、機器名の完全一致か <id>.（FQDN）と head を含む（前方一致にしない）
ok T-1 SPL: 引数が全部空なら sourcetype=nwc:*・60 分・20 件で、device の search も語も無い
ok T-1 SPL: IP の device_id も通る（tags.agent_host で引く機器）。10.0.0.1 は 10.0.0.10 に当たらない形（完全一致か 10.0.0.1. で始まる）
ok T-11 締め切りを過ぎたら読むのをやめ、改行で終わった 2 行だけを truncated 付きで返す（改行で終わっていない 3 行目は捨てる）
ok T-11 接続と読み出しのソケットの timeout は min(TIMEOUT, 締め切りの残り)（最後の読み出しは残りの秒数だけ待つ）
ok T-11 1 件も読めないうちに締め切りを過ぎたら、同じ文の error
ok T-11 少しずつ流れ続ける応答も、読み出しの合間に締め切りを見て打ち切る（Splunk が止まらなくても全体は締め切りまで）
ok T-11 送る前に締め切りを過ぎていれば（パスワードの取得に使い切った）、送らずに error
ok T-11 締め切りの前でも TIMEOUT 秒応答が途切れたら打ち切る（文は「応答が途切れた」で、締め切りとは言わない）
ok T-11 読んでいる途中で切れたら、読めた行（改行で終わった行）を truncated 付きで返す（捨てない）
ok T-11 1 行も読めないうちに切れたら「届かない」の error
ok T-11 応答の頭が壊れている（HTTPException）も Lambda の例外にせず「届かない」の error
ok T-11 HTTP のエラーの本文を読む途中で切れても、状態コードの error を返す
ok T-11 本物の HTTPResponse で fp.raw._sock に読み出しの timeout を設定できる
ok T-11 締め切りは 25 秒で、Runtime → Gateway の mcp_client.TIMEOUT（環境変数 GATEWAY_TIMEOUT、既定 30 秒）より短い
通過 220 / 失敗 0
```

T-10（使い捨ての手元の Splunk。`docker build --platform linux/amd64 --build-arg SPLUNK_VERSION=10.4.4 -t nwc-local-splunk:054 -f docker/images/splunk/Dockerfile app/splunk` で build し、`127.0.0.1:18089/18088` で起動。パスワードと HEC の token はその場の乱数で、環境変数で渡して表示しない。`app/spark/snmp_sinks.py` の `splunk_events` で作った 8 件を HEC で入れ、`evidence.search_splunk` で引いた。スクリプトはスクラッチパッドの `t10.py`。最後の編集のあとに取り直した出力）:

```
docker run rc 0
health ['running', 'healthy'] 100 秒
HEC (200, '{"text":"Success","code":0}')
indexed 8 0 秒
全部: {'minutes': 30, 'limit': 100} -> count=8 devices=['10.0.0.1', '10.0.0.10', 'dc1-a-leaf-01.lab.local', 'hostonly-1', 'spine-1', 'spine-10'] sourcetypes=['nwc:logs', 'nwc:metrics', 'nwc:traps'] error=None truncated=None 0.07 秒
spine-1: {'device_id': 'spine-1', 'minutes': 30, 'limit': 100} -> count=3 devices=['spine-1'] sourcetypes=['nwc:logs', 'nwc:metrics'] error=None truncated=None 0.06 秒
10.0.0.1: {'device_id': '10.0.0.1', 'minutes': 30, 'limit': 100} -> count=1 devices=['10.0.0.1'] sourcetypes=['nwc:traps'] error=None truncated=None 0.07 秒
dc1-a-leaf-01（FQDN）: {'device_id': 'dc1-a-leaf-01', 'minutes': 30, 'limit': 100} -> count=1 devices=['dc1-a-leaf-01.lab.local'] sourcetypes=['nwc:traps'] error=None truncated=None 0.06 秒
大小文字: {'device_id': 'DC1-A-LEAF-01', 'minutes': 30, 'limit': 100} -> count=1 devices=['dc1-a-leaf-01.lab.local'] sourcetypes=['nwc:traps'] error=None truncated=None 0.07 秒
tags.host だけ: {'device_id': 'hostonly-1', 'minutes': 30, 'limit': 100} -> count=1 devices=['hostonly-1'] sourcetypes=['nwc:logs'] error=None truncated=None 0.06 秒
topic=traps: {'topic': 'traps', 'minutes': 30, 'limit': 100} -> count=3 devices=['10.0.0.1', '10.0.0.10', 'dc1-a-leaf-01.lab.local'] sourcetypes=['nwc:traps'] error=None truncated=None 0.07 秒
keyword=BGP_DOWN: {'keyword': 'BGP_DOWN', 'minutes': 30, 'limit': 100} -> count=2 devices=['hostonly-1', 'spine-1'] sourcetypes=['nwc:logs'] error=None truncated=None 0.06 秒
spine-1 + logs + BGP_DOWN: {'device_id': 'spine-1', 'topic': 'logs', 'keyword': 'BGP_DOWN', 'minutes': 30, 'limit': 100} -> count=1 devices=['spine-1'] sourcetypes=['nwc:logs'] error=None truncated=None 0.06 秒
limit=2: {'minutes': 30, 'limit': 2} -> count=2 devices=['spine-1'] sourcetypes=['nwc:logs'] error=None truncated=None 0.06 秒
spine（部分の名前）: {'device_id': 'spine', 'minutes': 30, 'limit': 100} -> count=0 devices=[] sourcetypes=[] error=None truncated=None 0.07 秒
パスワード違い: {'topic': 'traps'} -> count=None devices=[] sourcetypes=[] error='HTTP 401: {"messages":[{"type":"ERROR","text":"Unauthorized"}]}' truncated=None 0.01 秒
ソケットの確かめ: {'minutes': 30, 'limit': 100} -> count=8 devices=['10.0.0.1', '10.0.0.10', 'dc1-a-leaf-01.lab.local', 'hostonly-1', 'spine-1', 'spine-10'] sourcetypes=['nwc:logs', 'nwc:metrics', 'nwc:traps'] error=None truncated=None 0.07 秒
締め切り 0.05 秒: {'minutes': 1440, 'limit': 100} -> count=None devices=[] sourcetypes=[] error='0.05 秒で打ち切った（topic / device_id で絞るか minutes を短く）' truncated=None 0.05 秒
ok spine-1 で spine-10 が出ない
ok 10.0.0.1 で 10.0.0.10 が出ない
ok dc1-a-leaf-01 で FQDN の行が出る
ok 大小文字を区別しない
ok tags.host だけの機器が出る
ok topic で絞れる
ok keyword で絞れる
ok device + topic + keyword
ok limit
ok 部分の名前では当たらない
ok パスワード違いで HTTP 401
ok 本物の応答で fp.raw._sock が見える（読み出しの timeout を合わせられる）
ok 締め切りを過ぎたら error か truncated
docker rm -f -v nwc-054-splunk rc 0
```

T-10 の片付け: コンテナは `docker rm -f -v nwc-054-splunk`（上の最後の行）、イメージは `docker rmi nwc-local-splunk:054`。上流の `splunk/splunk:10.4.4` は始める前に無く、build のあともタグ付きのイメージとしては残っていない。始める前と後で、イメージ 8 本（並べ替えて同じ）、コンテナ 0、volume 30（同じ）、18088 / 18089 の LISTEN 無し。

`architecture-managed.pptx`（`uv run --only-group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`、rc=0。中の文字を数えた）:

```
slides 10
'lambda → splunk 8089' 1
'splunk どうしと lambda 以外' 1
'Splunk の管理 API 8089 の外からの受信' 0
```

`bash ops/check.sh`（最後の編集のあと。rc=0。各テストの集計行と末尾）:

```
通過 220 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 121 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 210 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 461 / 失敗 0
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので effort を切り替えられない）
- 反対弁護人: opus / effort xhigh（文脈を渡した。読み取り専用。返ってきたあと `git status --porcelain -uall` で、増えたファイルが無く変更が自分の 7 ファイルだけなことを確かめた）

#### 指摘と片付け

- **Should fix A1** [runtime bugs] `app/agentcore/evidence.py`（`_splunk_post` の open の except）
  - 破綻: 応答の頭が壊れている（`BadStatusLine` ほか `http.client.HTTPException`）と、open の except に無いので Lambda の例外になる。HTTP のエラーの本文を読む途中で切れても同じ。
  - 再現: テストを足してから受けを外す注入（M10 / M12）で `http.client.BadStatusLine: garbage` / `http.client.IncompleteRead: IncompleteRead(0 bytes read)` で落ちる。
  - 直した: open の except に `HTTPException` を足し、HTTPError の本文の読み出しを try で包んで閉じる。
- **Should fix A2** [data loss] `app/agentcore/evidence.py`（読み出しのループの except）
  - 破綻: N 行読んだあとで接続が切れる（search head の再起動、IncompleteRead）と、読めた行を捨てて「届かない」を返す。
  - 再現: 反対弁護人が socketpair で 3 行を捨てるのを確かめた。注入 M11（直す前の形）で T-11 が落ちる。
  - 直した: 読めた行があれば「応答が途中で切れた（…）」の `truncated` 付きで返す。
- **Should fix A3** [missing tests] `_set_read_timeout` は私有の `fp.raw._sock` に頼るが、テストは自前の偽物だけで、名前が変わっても通っていた。
  - 直した: socketpair の本物の `http.client.HTTPResponse` で timeout が設定されるテストを足した（M13 で落ちる）。TLS のソケットに効くことは T-10 の本物の HTTPS の応答で確かめた。
- **Should fix A4** [correctness] `app/agentcore/evidence.py`（`| search device=…`）。設計側なので直さず PM に報告。
  - 破綻: sysName がある機器を IP で引くと、`device` は sysName になって 0 件になる。error にならないので、エージェントは「ログが無い」と思う。description は「機器名か IP」と書いている。OpenSearch の `search_logs` は sysName / agent_host / source のどれでも当たるので、STORES によって答えが変わる。
  - 確かめたこと: SPL の形（`eval device=coalesce('tags.sysName', 'tags.agent_host', …)` のあとに `search device="<id>"`）から読んだだけ。T-10 には「sysName がある機器を IP で引く」場合が無い。
  - 案: `OR tags.agent_host="<id>" OR tags.source="<id>" OR host="<id>"` を足す。design.md の機器名の項（147 行目あたり）の書き直しが要る。
- **Should fix A5** [correctness] 同じ箇所。設計側なので直さず PM に報告。
  - 破綻: flows（`app/spark/snmp_sinks.py:87` の `FLOW_TAGS`）の tags には sysName / agent_host / source / host が無いので、HEC の host も `device` も `unknown`。`device_id` でフローを引くと必ず 0 件。design.md（145 行目あたり）は「host が unknown のイベントは機器名が無いので困らない」として受け入れているが、フローはトピックごと当たり、`tags.sampler` が送った機器。
  - 確かめたこと: 読んだだけ（`snmp_sinks.py:87`、`:619`）。
  - 案: 絞り込みに `'tags.sampler'` を足すか、description と docs に「flows は keyword で引く」と書く。
- **Should fix A6** [runtime] `app/agentcore/evidence.py`（open と chunk の読み出し）。直さず PM に報告。
  - 破綻: 締め切りはソケットの操作 1 回ごとの timeout で守るので、厳密ではない。open は接続・TLS・送信・応答の頭のそれぞれが `min(20, 残り)` まで待てる。chunk 転送では 1 回の `read1` が CRLF・サイズ行・本文の 3 回待てる。反対弁護人の実験では、締め切り 1.0 秒が 1.91 秒で終わった（CRLF を遅らせた作為の流れ）。応答の頭が 20 秒来ないときは「届かない: timed out」と出るが、Splunk が遅いだけのこともある（design.md 69 行目と T-6 が `_send` と同じ「届かない」を求めるので変えていない）。
  - 確かめたこと: 反対弁護人の socketpair の実験（1.91 秒）。T-10 の各検索は 0.07 秒前後で、本物の Splunk で長く止まる場合は確かめていない。
  - 案: 厳密にするなら別スレッドで締め切りに閉じる形。tools Lambda の timeout は 60 秒なので、超えても Gateway の時間切れの error（Round 1 の Must fix 1 でエージェントに届く）になり、落ちない。
- Nit
  - A7 [docs] 「パスワードの取得を含む」のコメントは、SSM の取得そのものは打ち切れないので言い過ぎ。コメントを直した（SSM の時間そのものは打ち切れないまま）。
  - A8 [docs] `agentcore-bedrock.md:37` に、1 件も無ければ `error`、20 秒止まったとき・途中で切れたときも同じことを足した（直した）。
  - A9 chunk の終わり（`0\r\n`）のあと trailer が遅れると、全部読めていても `truncated` になる（反対弁護人の実験。直さない）。
  - A10 [security] リダイレクトで Authorization を付けたまま追う（Round 1 の Nit 6 と同じ。直さない）。
  - A11 T-11 は経過時間の上限（0.7 秒、1 秒）で見るので、負荷の高いマシンでは揺れうる（直さない）。
  - A12 締め切りは `search_splunk` に入ったときから数えるので、Lambda のコールドスタートは Gateway の 30 秒に食い込む（直さない）。

#### 退行の注入（スクラッチパッドの `mutate.py`。注入のたびに戻し、最後に元どおりを確かめた）

```
M1 前方一致に戻す: rc=1 T-1 SPL: 引数ありは … 前方一致にしない
M2 基本の検索の機器の語を消す: rc=1 T-1 SPL: 引数ありは … 前方一致にしない
M3 読み出しの timeout を締め切りに合わせない: rc=1 T-11 締め切りを過ぎたら読むのをやめ、改行で終わった 2 行だけを truncated 付きで返す
M4 読み出しの前の締め切りの確かめを消す: rc=1 T-11 少しずつ流れ続ける応答も、読み出しの合間に締め切りを見て打ち切る
M5 途中までも無いとき error にしない: rc=1 T-11 1 件も読めないうちに締め切りを過ぎたら、同じ文の error
M6 打ち切りでも改行の無い行を残す: rc=1 T-11 締め切りを過ぎたら読むのをやめ、改行で終わった 2 行だけを truncated 付きで返す
M7 接続の前の締め切りの確かめを消す: rc=1 T-11 送る前に締め切りを過ぎていれば（パスワードの取得に使い切った）、送らずに error
M8 truncated を付けない: rc=1 T-11 締め切りを過ぎたら読むのをやめ、改行で終わった 2 行だけを truncated 付きで返す
M9 SPLUNK_DEADLINE を 30 に: rc=1 T-11 締め切りは 25 秒で、… mcp_client.TIMEOUT … より短い
M10 open の except から HTTPException を外す: rc=1 http.client.BadStatusLine: garbage
M11 途中で切れたら読めた行も捨てる: rc=1 T-11 読んでいる途中で切れたら、読めた行（改行で終わった行）を truncated 付きで返す（捨てない）
M12 HTTP のエラーの本文の読み出しを守らない: rc=1 http.client.IncompleteRead: IncompleteRead(0 bytes read)
M13 私有の属性の名前を変える: rc=1 IndexError: list index out of range
restored True
```

#### 問題なしとした観点

- S-5（前方一致）: T-10 で spine-1 / spine-10、10.0.0.1 / 10.0.0.10、部分の名前 spine が 0 件になることを本物の Splunk で確かめた。M1 で T-1 が落ちる。
- S-2（基本の語と締め切り）: T-10 で tags.host だけの機器と FQDN の行が基本の語で落ちないことを確かめた。M2〜M9 で T-1 / T-11 が落ちる。
- `wait == remaining` の float の等値: `min` は引数のどちらかを返すので等値で正しい（反対弁護人も同じ結論）。
- SPL の注入: device_id の許す文字は Round 1 のまま（`*` も通さない）。反対弁護人が IPv6、`.` で終わる id、keyword の `OR` / `=` / `earliest=` を当て、引用の外に出られないことを確かめた（読んだだけ）。
- `'tags.sysName'` の取り出し: T-10 の本物の Splunk（app/splunk の props.conf 入りのイメージ）で取れた。AWS のクラスターの search head では未確認（docs に書いてある）。
- docs: 反対弁護人が deck 148 / 150、pptx のノート、splunk.md / agentcore-bedrock.md と security_groups.tf の行が合うことを読んだ。

#### ジンテーゼ（反対弁護人の前との差）

- Must fix は 0 のまま。A1〜A3 の 3 件（runtime / data loss / missing tests）を直した。
- 締め切りは「読み出しのループでは守る、open と chunk の枠では 1 回の操作ごとの timeout なので厳密ではない」と範囲を絞って書き直した（A6）。
- 設計側の Should fix 2 件（A4 IP で sysName のある機器を引けない、A5 flows を device_id で引けない）は PM に回す。
