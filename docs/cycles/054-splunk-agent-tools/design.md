# Splunk だけの構成でもエージェントが Splunk からログ・trap・メトリクスを引けるようにする（054）

設計: PM(opus-5.5) / effort: high。QUEUE 168。2026-10-12。Round 2 で機器の絞り方・時間の上限・T-10 を直した（`design-log.md` の Round 2）。Round 3 で IP と flows の絞り方、Gateway だけのツール（`GATEWAY_ONLY`）、締め切りの範囲を足した（`design-log.md` の Round 3）。

## 背景

- `STORES` から `grafana` を外す（`s3,splunk` / `splunk` / `grafana` 無しの構成）と、エージェントの `search_logs`（OpenSearch）と `query_metrics`（Prometheus）は「まだ配備されていない」を返す。Splunk には全トピックが入っているのに、エージェントはそれを引けない（`deploy.env.example:81` / `:86`）
- 2026-10-12 にユーザーが QUEUE に積んで進めることを了承し、形を決めた（`design-log.md` の Round 0）
  - **決めた検索の新しいツール `search_splunk` を足す。** SPL は固定で、引数は検証してから埋める。`search_logs` / `query_metrics` はいまのまま（格納先が無ければ「配備されていない」）
  - **資格情報は既存の Splunk の admin のパスワード**（SSM の `/<prefix>/splunk/admin-password`。`ops/up.sh` が作る）。読み取り専用のユーザーは QUEUE に積んで後で
- AWS は使わない。手元のテストと、使い捨てで立てる手元の Splunk（T-10）で確かめる

## 現物（PM が 2026-10-12 に読んで確かめたもの）

- ツールの置き場（`app/agentcore/evidence.py`）
  - モジュールの docstring（`:1-22`）に環境変数と OSS 版の認証の説明。`OPENSEARCH_ENDPOINT` / `PROMETHEUS_QUERY_URL`（`:38-40`）、`OPENSEARCH_PASSWORD = toolkit.Param("OPENSEARCH_PASSWORD", "opensearch-password", decrypt=True)`（`:49`）、`TIMEOUT = 20`（`:50`）
  - `_send`（`:83-91`）は `urllib.request.urlopen(..., timeout=TIMEOUT)` で、失敗は `{"error": "HTTP …"}` / `{"error": "届かない: …"}`
  - `search_logs`（`:94-110`）と `query_metrics`（`:113-135`）は、宛先が空なら `{"error": "…まだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics を sinks に … を入れて apply すると使える）", "hits": []}`
  - `TOOL_SPECS`（`:181-207`。Bedrock の `toolSpec` の形で `inputSchema.json`）、`TOOLS`（`:208`）、`run_tool = toolkit.runner(TOOLS)`（`:209`）
- `toolkit.Param(env, param, ttl, decrypt)`（`app/agentcore/toolkit.py:90-117`）は「環境変数が先、無ければ SSM の `<PARAM_PREFIX>/<param>`」
- ツールの登録
  - `app/gateway/tools.json`（Gateway のツール。`inputSchema` は `json` の包みが無い形）。`tests/test_workflow.py:463-465` が「13 個は topology / evidence / proposals の `TOOL_SPECS` と同じ名前」を見る
  - `tests/test_agentcore.py:350-352` は「system prompt に出てくるツール名は全部 `TOOL_SPECS` にある」を見る
  - エージェントの system prompt（`app/agentcore/app.py:100` 付近）と修復のプロンプト（`app/temporal/rules.py:37-38` の `build_prompt`）が `search_logs` / `query_metrics` / `query_history` を名指ししている
- Splunk の REST の検索（手元で動いている形。`docker/compose/check.sh:90-97`）: `POST https://<host>:8089/services/search/jobs/export`、`search=search …` と `output_mode=json`、Basic 認証。応答は JSON の行（1 行 1 件 `{"preview":false,"offset":N,"result":{…}}`）。パスワード違いは 401 で `{"messages":[{"type":"ERROR","text":"Unauthorized"}]}`、知らないコマンドは 400 で `messages` の FATAL
- Splunk の管理 API への出し方（`app/splunk/peers_check.py:54-60`）: Basic 認証、`ssl.CERT_NONE`（自己署名の証明書）、`urllib.request.ProxyHandler({})`（プロキシの環境変数を見ない）
- Splunk のイベント（`app/splunk/nwc_alerts/default/savedsearches.conf:7-8` / `:21` / `:67`）
  - Spark（`app/spark/snmp_sinks.py` の `splunk_events`。`:609-633`）が HEC へ書く。`source = telegraf:<measurement>`、本文は `{"topic", "measurement", "agent_host", "tags": {…}, "fields": {…}}`
  - HEC の `host` は `r.host or r.agent_host or "unknown"`（`:619`）。`r.host` は `tags.host`、`r.agent_host` は `tags.source`（gNMI）か `tags.agent_host`（`:321`、`:334`）で、どれも本文の `tags` に入る。そのため `coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host)` の値は、`host` が `unknown` のときを除いて `_raw` の文字にも出る
  - 機器名は `coalesce('tags.sysName', 'tags.agent_host', 'tags.source')`（保存済みサーチがこの形で引いている）
  - `sourcetype` は `nwc:<topic>`（`check.sh:96` の `sourcetype=nwc:*`）。トピックは metrics / gnmi / traps / logs / flows
- Splunk の居場所（`IaC/terraform/aws-managed/pipeline/analytics/`）
  - `local.splunk_on_ecs = local.sink_splunk`（`locals.tf:136`）、`local.service_namespace = "${local.name_prefix}.internal"`（`:153`）
  - Cloud Map のサービス `splunk`（`splunk.tf:76-79`）は 1 台のときのその 1 台、クラスターのときは search head（`SPLUNK_ROLE = splunk_search_head`。`:133`）。保存済みサーチも search head で動く（`:17`）。`splunk-cm` / `splunk-idx` は別名
  - `outputs.tf` に HEC の URL（`splunk_hec_url`。`:131`）はあるが、検索の URL の output は無い
  - **OSS 版の `IaC/terraform/oss/pipeline/analytics/outputs.tf` はリンクではない別のファイル**（`splunk.tf` はリンク。`splunk_on_ecs` / `service_namespace` は OSS の `network.tf:105` / `:118`）。output は両方に足す
- tools Lambda（`IaC/terraform/aws-managed/workflow/gateway.tf:213-231`）の環境変数は `merge(...)` で、`OPENSEARCH_ENDPOINT` / `OPENSEARCH_INDEX` / `PROMETHEUS_QUERY_URL` ほか。OSS 版は `local.analytics_oss` のとき `OPENSEARCH_AUTH` / `PROMETHEUS_AUTH` を足す。OSS 版の `workflow/gateway.tf` と `locals.tf` はリンク（同じファイル）
- `IaC/terraform/aws-managed/workflow/locals.tf:190-198` が analytics の state を `try(data.terraform_remote_state.analytics.outputs.X, "")` で読む
- tools Lambda のロールは `ssm:GetParameter` を `parameter${local.param_prefix}/*` に持つので、`splunk/admin-password` も読める（2026-10-12 に PM が `gateway.tf` の IAM を読んで確かめた。AWS では未確認）
- SG の表（`IaC/terraform/aws-managed/base/core/security_groups.tf`。OSS 版はリンク）
  - 行は `{ from = "...", to = "...", protocol = "tcp", port = N, why = "..." }`。`splunk → splunk` の 8089 / 9887 / 9997 は `:103-105`
  - 頭のコメント `:11-12` は「Splunk の管理 API 8089（開けるのはクラスターの splunk どうしだけ。外から使わない）」
  - `tests/test_analytics.py:160-161` が「`splunk` 以外から `splunk` の 8089 を開けない」を見る（`:128` に期待の行の一覧）

## 設計方針

- **新しいツール `search_splunk(device_id="", topic="", keyword="", minutes=60, limit=20)` を `evidence.py` に足す。** SPL は固定で、エージェントは SPL を書けない
  - 組み立てる SPL（値はすべて検証してから `"…"` の中に入れる）

    ```
    search index=* sourcetype="nwc:<topic か *>" earliest=-<minutes>m ["<device_id>"] ["<keyword>"]
    | eval device=coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host)
    [| search device="<device_id>" OR device="<device_id>.*" OR tags.agent_host="<device_id>" OR tags.source="<device_id>" OR tags.sampler="<device_id>" OR host="<device_id>"]
    | head <limit>
    | fields _time device sourcetype source _raw
    ```

    `[...]` は引数が空なら付けない。Splunk の `search` は値の大小文字を区別しない
    - 1 行目の `"<device_id>"` は本文の語で先に絞る（索引で引けるので、`earliest` の範囲を全部読んでから `eval` で絞らない。Round 1 のセルフレビューの S-2）。機器名は `_raw` の `tags` に出る（上の「現物」）ので、これで落ちるイベントは無い（`host` が `unknown` のものだけは落ちる）
    - `| search` は、機器名 `device` の完全一致か、そのあとに `.` が続く名前（sysName が FQDN の機器）か、機器の IP を持つ欄（`tags.agent_host` / `tags.source` / `tags.sampler` / `host`）の完全一致。前方一致にしない（`10.0.0.1` が `10.0.0.10` に、`spine-1` が `spine-10` に当たる。S-5）。欄の名前は `search` の中なので引用符で包まない（`eval` の `'tags.sysName'` とは書き方が違う。T-10 で本物の Splunk に通す）
    - IP の欄も見る理由（Round 3）: device map で sysName が付いた機器（trap と gNMI。`snmp_sinks.py` の `with_sysname`）は `device` が sysName になり、IP（`tags.source` / `tags.agent_host`）で引くと 0 件になっていた。flows は sysName / agent_host / source / host のどれも持たず（`FLOW_TAGS`、`snmp_sinks.py:87`）、送り元の機器は `tags.sampler`（sampler_address）にしか出ない。`search_logs` も `tags.sysName` / `tags.agent_host` / `tags.source` のどれでも当たる（`evidence.py` の `search_logs`）。戻りの `device` は `coalesce` のまま（IP で引いても sysName のある機器は sysName が返る）
  - 戻り: `{"count": N, "minutes": m, "topic": t, "events": [{"time": _time, "device": …, "sourcetype": …, "source": …, "raw": _raw を 1000 文字に切ったもの}]}`。`messages` に ERROR / FATAL があれば `{"error": "Splunk: <type> <text>", "events": []}`
  - **引数の検証（通らなければ Splunk に送らずに `{"error": "…", "events": []}`）**
    - `device_id`: 空か `^[A-Za-z0-9._:-]{1,64}$`
    - `topic`: 空か `metrics` / `gnmi` / `traps` / `logs` / `flows` のどれか
    - `keyword`: 空か 100 文字まで。`"` と `\` と制御文字と `|` `[` `]` `` ` `` を含んだら拒む（SPL の引用を抜け出す・サブサーチ・マクロを作れないようにする。エスケープではなく拒否にして、SPL の規則の読み違いに頼らない）
    - `minutes`: 整数に直して 1〜1440 に丸める、`limit`: 1〜100 に丸める（`search_logs` と同じ扱い。いまの丸め方を `evidence.py` で読んで合わせる）
  - 送り方: `POST {SPLUNK_URL}/services/search/jobs/export`、本文は `urlencode({"search": spl, "output_mode": "json"})`、Basic 認証、`ssl.CERT_NONE` の context と `ProxyHandler({})`（`peers_check.py` と同じ。自己署名で、宛先は VPC の中）
  - **時間の上限（S-2）。** `search_splunk` の全体（パスワードの取得・接続・読み出し）に締め切り `SPLUNK_DEADLINE = 25` 秒を持つ（Runtime → Gateway の `mcp_client.GATEWAY_TIMEOUT` 30 秒より短く）。ソケットの timeout は `min(TIMEOUT, 残り)`、応答は塊で読んで締め切りを見る。過ぎたら読むのをやめ、読めた行（改行で終わった行だけ）の結果を `"truncated": "25 秒で打ち切った（topic / device_id で絞るか minutes を短く）"` を付けて返す。1 件も無ければ同じ文の `error`
  - 失敗は `_send` と同じ形（`{"error": "HTTP 401 …"}` / `{"error": "届かない: …"}`）にそろえる。応答の行のうち JSON でないものは数えて捨て、全部読めなければ `{"error": "Splunk の応答が読めない", …}`
- **宛先と資格情報**
  - 宛先は環境変数 `SPLUNK_URL`（例 `https://splunk.<prefix>.internal:8089`）。空なら `{"error": "Splunk の検索はまだ配備されていない（IaC/terraform/aws-managed/pipeline/analytics を STORES に splunk を入れて apply すると使える）", "events": []}`
  - ユーザーは `SPLUNK_USER`（既定 `admin`）、パスワードは `toolkit.Param("SPLUNK_PASSWORD", "splunk/admin-password", decrypt=True)`。無ければ `{"error": "Splunk のパスワードが無い（環境変数 SPLUNK_PASSWORD か SSM の splunk/admin-password）", …}`
- **Terraform**
  - analytics の output `splunk_search_url` = `local.splunk_on_ecs ? "https://splunk.${local.service_namespace}:8089" : ""`（マネージド版と OSS 版の `outputs.tf` の両方）
  - workflow の `locals.tf` に `splunk_url = try(data.terraform_remote_state.analytics.outputs.splunk_search_url, "")`、`gateway.tf` の tools Lambda の環境変数に `SPLUNK_URL = local.splunk_url`
  - SG の表に `{ from = "lambda", to = "splunk", protocol = "tcp", port = 8089, why = "Splunk search API - tools Lambda search_splunk" }` を 1 行足し、頭のコメント `:11-12` を「splunk どうしと、tools Lambda の search_splunk（054）だけ」に直す。runtime（AgentCore Runtime）からは開けない（ツールは Gateway → tools Lambda で動く）
  - `tests/test_analytics.py:128` の期待の一覧と `:160-161` の検査を、`lambda` からの 8089 も許す形に直す（ほかの送り元はいまどおり拒む）
- **ツールの登録とプロンプト**
  - `evidence.TOOL_SPECS` / `TOOLS` と `app/gateway/tools.json` に足す（14 個）。`tests/test_workflow.py:465` の「13 個」を 14 に
  - `TOOL_SPECS` の description に「Splunk に入っている全トピック（metrics / gnmi / traps / logs / flows）を機器名・トピック・語で引く。STORES に splunk が無い構成では案内だけ返す。search_logs / query_metrics が配備されていないときに使う」と書く
  - `device_id` の description は「機器名（例 dc1-a-leaf-01）か機器の IP。完全一致（sysName が FQDN の機器は <id>. で始まるものも当たる）。flows は機器名を持たないので、送り元の機器の IP（sampler）で引く。空なら全機器」（Round 3。`evidence.py` と `tools.json` の両方）
  - エージェントの system prompt（`app/agentcore/app.py:100` 付近）と修復のプロンプト（`rules.build_prompt` の `:37-38`）に「search_logs / query_metrics が配備されていないと返したら search_splunk で Splunk を引く」を足す。`rules.py` は純粋なまま（文言だけ）
  - **Gateway だけのツール（Round 2 で実装済み。Round 3 で正本に足した）。** `evidence.GATEWAY_ONLY = frozenset({"search_splunk"})`。`app.run_tool` は Gateway のツールが `error` を返すと同じ名前のコンテナの関数に戻すが、モジュールの `GATEWAY_ONLY` にあるツールは戻さず、Gateway の `error` をそのまま返す。Runtime のコンテナには `SPLUNK_URL` が無く 8089 も開いていないので、戻すと 401 や時間切れが「配備されていない」に化けるため。ほかのツールはいまどおり戻す。次に Gateway でしか宛先に届かないツールを足すときは、そのモジュールの `GATEWAY_ONLY` に名前を入れる
- 却下した案は `design-log.md` の Round 0

## 変更対象ファイル

- `app/agentcore/evidence.py` — `search_splunk`、検証、`SPLUNK_URL` / `SPLUNK_USER` / `SPLUNK_PASSWORD`、`GATEWAY_ONLY`、`TOOL_SPECS` / `TOOLS`、モジュールの docstring。Round 3 は `splunk_args` の 2 段目の絞り込みと `device_id` の description
- `app/gateway/tools.json` — `search_splunk` を足す。Round 3 は `device_id` の description
- `app/agentcore/app.py` — system prompt の 1 文と、`run_tool` が `GATEWAY_ONLY` のツールをコンテナに戻さないこと
- `app/temporal/rules.py` — `build_prompt` の 1 文
- `IaC/terraform/aws-managed/pipeline/analytics/outputs.tf` と `IaC/terraform/oss/pipeline/analytics/outputs.tf` — `splunk_search_url`
- `IaC/terraform/aws-managed/workflow/locals.tf`（OSS 版はリンク）— `splunk_url`
- `IaC/terraform/aws-managed/workflow/gateway.tf`（OSS 版はリンク）— `SPLUNK_URL`
- `IaC/terraform/aws-managed/base/core/security_groups.tf`（OSS 版はリンク）— `lambda → splunk` 8089 の行と頭のコメント
- `tests/test_agentcore.py`（`evidence` のテストがあるならそこ。無ければ `tests/test_workflow.py`）、`tests/test_workflow.py`、`tests/test_analytics.py`
- `docs/architecture/architecture-managed.deck.md:148` / `:150` — 「Splunk の管理 API 8089 の外からの受信は開けていない」を、lambda → splunk の 8089（search_splunk）を足した形に直す
- docs: `deploy.env.example:81` / `:86`（「search_logs / query_metrics は配備されていないを返す。search_splunk で Splunk を引く」）、`docs/deploy.md:123` 付近、`docs/architecture/resources/splunk.md` と `agentcore-bedrock.md`（ツールの一覧があれば）、`docs/architecture/resources/` の SG の表の文書（vpc-perimeter / core の SG の表に `lambda → splunk 8089` を足す）。`grep -rn "search_logs" docs README.md` で拾った「ツールの一覧」の箇所すべて
- Round 3 の docs: `search_splunk` の機器の絞り方を書いている箇所（`docs/architecture/resources/agentcore-bedrock.md:37` / `:127`、`docs/architecture/resources/splunk.md:61` / `:170` ほか。`grep -rn search_splunk docs deploy.env.example` で探す）に、IP でも引けることと flows は sampler の IP で引くことを足す
- 全体設計 HTML（`~/Documents/repo/artifacts/projects/nwc-poc-architecture.html`）は PM がサイクル完了時に直す（エンジニアは触らない）

## 再利用するもの

- `evidence._send` の失敗の形と `TIMEOUT`、`toolkit.Param`
- `app/splunk/peers_check.py:54-60` の Basic 認証・`CERT_NONE`・`ProxyHandler({})` の組み方
- `docker/compose/check.sh:90-97` の `jobs/export` の呼び方と、応答の `messages` の読み方
- `savedsearches.conf:67` の機器名の `coalesce`
- 既存のテストの `urlopen` の差し替え方（`evidence` の `search_logs` のテストがあればその形）

## 実装ステップ

1. `evidence.py` に検証の関数（`splunk_args(device_id, topic, keyword, minutes, limit) -> (spl, error)`）と `search_splunk` を書く。SPL の組み立ては純粋な関数にしてテストで文字列を見る
2. `TOOL_SPECS` / `TOOLS` / `tools.json` に足す
3. system prompt と `build_prompt` に 1 文
4. Terraform（output 2 か所、workflow の locals と gateway.tf、SG の表）
5. テスト（下の T-1〜T-9）
6. docs
7. `bash ops/check.sh` を通す。T-10 を手元の使い捨ての Splunk で確かめ、片付ける

## 検証方法

`bash ops/check.sh` の最後の行が「すべて通過」で終了コード 0。追加のテストは次のとおり。

- T-1（SPL の組み立て）: `splunk_args("dc1-a-leaf-01", "traps", "linkDown", 30, 5)` の SPL が `search index=* sourcetype="nwc:traps" earliest=-30m "dc1-a-leaf-01" "linkDown" | eval device=` で始まり、`| search device="dc1-a-leaf-01" OR device="dc1-a-leaf-01.*" OR tags.agent_host="dc1-a-leaf-01" OR tags.source="dc1-a-leaf-01" OR tags.sampler="dc1-a-leaf-01" OR host="dc1-a-leaf-01"` と `| head 5` を含み、`device="dc1-a-leaf-01*"` を含まない。引数が全部空なら `sourcetype="nwc:*"`、`| search device=` も keyword の語も無い、`earliest=-60m`、`| head 20`
- T-2（拒否）: `device_id="a b"`、`"x\"|delete"`、65 文字、`topic="foo"`、`keyword` に `"` / `|` / `[` / `` ` `` / `\` / 改行 / 101 文字、のそれぞれで `error` を返し、`urlopen` は呼ばれない
- T-3（丸め）: `minutes=0` → 1、`minutes=99999` → 1440、`limit=1000` → 100、`minutes="abc"` → いまの `search_logs` と同じ扱い
- T-4（配備されていない）: `SPLUNK_URL` が空なら `error` に「Splunk の検索はまだ配備されていない」を含み、`urlopen` は呼ばれない
- T-5（送り方）: `urlopen` を差し替えて、URL が `<SPLUNK_URL>/services/search/jobs/export`、メソッド POST、本文に `output_mode=json` と URL エンコードした `search=search index=*…`、`Authorization: Basic <admin:pw の base64>`、`timeout=20` を確かめる。パスワードは `SPLUNK_PASSWORD` の環境変数で渡す（SSM は呼ばない）
- T-6（応答の読み方）: 3 行の JSON の行（`result` に `_time` / `device` / `sourcetype` / `source` / `_raw`）→ `count == 3`、`raw` は 1000 文字に切る。`{"messages":[{"type":"ERROR","text":"Unauthorized"}]}` → `error` が `Splunk: ERROR Unauthorized`。HTTP 401 / 届かない → `_send` と同じ形
- T-7（登録）: `tools.json` が 14 個で `TOOL_SPECS` の名前と同じ（`tests/test_workflow.py:465` を直す）。`search_splunk` の `inputSchema` の型は string / integer だけ（`:479` の検査が通る）。system prompt と `build_prompt` に `search_splunk` が出る（`tests/test_agentcore.py:350-352` が通る）
- T-8（Terraform の表）: `tests/test_analytics.py` が `lambda → splunk 8089` を期待の行に持ち、`lambda` / `splunk` 以外からの 8089 を引き続き拒む。`runtime → splunk` が無いこと
- T-9（Terraform の静的な確かめ）: `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics validate` と `workflow`、`base/core`（`init -backend=false` 済みのものだけ。`ops/check.sh` がやっているならそれに任せる）。`SPLUNK_URL` が gateway.tf の tools Lambda の環境変数にあり、値が `local.splunk_url`
- T-10（手元の Splunk。必須）: 使い捨ての Splunk を立てて、本物の SPL と応答を確かめる
  - 立て方: `docker/images/splunk/Dockerfile` を別タグ（例 `nwc-local-splunk:054`）でビルドし、別名のコンテナ（例 `nwc-054-splunk`）を `127.0.0.1` の空いたポートで立てる。パスワードと HEC の token は使い捨ての値をスクラッチパッドのファイルに作り、表示しない。手元の `.env` と compose の Splunk は使わない
  - 入れるイベント: `app/spark/snmp_sinks.py` の `splunk_events` で組んだ行を HEC へ送る（本物と同じ形）。機器は `spine-1` と `spine-10`（sysName）、`dc1-a-leaf-01.lab.local`（FQDN の sysName）、`10.0.0.1` と `10.0.0.10`（agent_host だけ）、sysName / agent_host / source が無く `tags.host` だけのもの。Round 3 で足す: `10.0.0.2`（trap。`tags.source` だけで、device map で sysName `spine-2` を付ける）、flows（`tags.sampler` が `10.0.0.3` と `10.0.0.30`）
  - 確かめること（`SPLUNK_URL=https://127.0.0.1:<port>` で `search_splunk` を呼び、結果の `device` の集合を build.md に貼る）: `spine-1` で `spine-10` が出ない、`10.0.0.1` で `10.0.0.10` が出ない、`dc1-a-leaf-01` で FQDN の行が出る、`tags.host` だけの機器が出る、`topic` と `keyword` で絞れる、パスワード違いで `HTTP 401` の `error`。Round 3 で足す: `10.0.0.2` で `device` が `spine-2` の行が出る（`spine-2` でも同じ行が出る）、`topic=flows` の `10.0.0.3` で sampler が `10.0.0.3` の行だけが出て `10.0.0.30` は出ない。所要時間も貼る
  - 片付け: `docker rm -f -v` でコンテナと volume を消し、自分がビルドしたタグと、このために pull した上流のイメージ（始める前に無かったものだけ）を `docker rmi` する。`docker ps -a` / `docker volume ls` / `docker images` で残っていないことを確かめて貼る。ほかのイメージ・コンテナには触らない
- T-12（Gateway だけのツール）: `tests/test_agentcore.py` で、Gateway が `error` を返したとき `app.run_tool("search_splunk")` はその `error` をそのまま返してコンテナの関数を呼ばず、`search_logs` はいまどおりコンテナに戻す（Round 2 で足したテスト。Round 3 で正本に足した）
- T-11（時間の上限）: 応答を少しずつ返して止まる偽物の opener で、締め切りを過ぎると読めた行だけを `truncated` 付きで返すこと、1 件も無ければ `error` になること、改行で終わっていない行を捨てることを確かめる（締め切りは引数か定数の差し替えで短くして、テストを待たせない）

## 未確定事項とリスク

- **admin の資格情報を tools Lambda が使う。** admin は検索以外（削除・設定変更）もできる。SPL を固定して引数を拒否で絞るので、エージェントからは検索しか出せない。読み取り専用のユーザーは QUEUE に積む（ユーザーの決定）
- **SPL の注入。** `keyword` の拒否の文字の選び方に穴があると、`| delete` などを出せる。T-2 で代表の文字を確かめる。拒否なので、穴は「通るべき語が通らない」側に倒れる
- **TLS を検証しない。** Splunk の 8089 は自己署名の証明書（`peers_check.py` と同じ）。宛先は VPC の中の Cloud Map の名前で、SG で `lambda` からだけ開ける
- **クラスターのとき `splunk` の名前が search head を指すか。** `splunk.tf:76-79` と `:133` を読んだ限り search head。AWS では未確認
- **応答の時間。** `jobs/export` は検索が終わるまで流し続ける。機器名は本文の語で先に絞り、全体は 25 秒の締め切りで打ち切る（途中までの結果を返す）。`keyword` も `device_id` も無く `minutes` が長いと `head` に届くまで読むので、description に「まず topic と device_id で絞る」と書く。tools Lambda の timeout は 60 秒（`gateway.tf:202`）で締め切りより長い
  - 締め切りは読み出しのループでは守るが、open（接続・TLS・送信・応答の頭）と chunk の枠はソケットの操作 1 回ごとの timeout（`min(TIMEOUT, 残り)`）なので厳密ではない（Round 2 の反対弁護人の実験で、締め切り 1.0 秒が 1.91 秒）。Splunk が応答の頭を遅らせると Gateway の 30 秒を超えうる。超えたときは Gateway の時間切れの `error` がそのままエージェントに届き（`GATEWAY_ONLY`）、tools Lambda（60 秒）は落ちない。別スレッドで締め切りにソケットを閉じる形は取らない（Round 3 で受け入れた）
- **lambda の SG は 3 つの Lambda が付ける。** tools の Lambda（`workflow/gateway.tf`）のほか、graph の status の Lambda（`pipeline/graph/sync.tf`）と KB の索引の Lambda（`agent/kb.tf`）も同じ SG なので、8089 に届く。ただし SSM の `/<prefix>/*` を読めるのは tools の Lambda のロールだけで（status と KB の索引のロールに `ssm:GetParameter` は無い。2026-10-12 に PM が IAM を読んだ）、2 つは admin のパスワードを持たない。専用の SG に分けるのは読み取り専用のユーザーと一緒に後で（QUEUE）
- **`host` が `unknown` のイベント。** `tags` に sysName / agent_host / source / host のどれも無いイベントは、機器名 `unknown` で引いても本文の語の絞り込みで落ちる。機器名が無いイベントなので、機器で引く用途では困らない。flows はトピックごとこれに当たる（`device` は `unknown`）ので、機器では `tags.sampler`（送り元の機器の IP）で引く（Round 3）
- **tools Lambda のサブネットから Splunk のサブネットへ届くか。** 同じ VPC で、SG の行を足せば届くはず。AWS では未確認（次の AWS の動作確認で確かめる）
- **イベントの機器名。** gNMI と trap は Spark の device map で `tags.sysName` が付く前提（`savedsearches.conf:21`）。付いた機器は `device` が sysName、付かない機器は IP になる。どちらも IP の欄（`tags.source` / `tags.agent_host`）で当たるので、`device_id` は機器名でも IP でもよい（Round 3）。device map に無い機器を sysName で引くと出ない（イベントに sysName が無いため。IP で引く）
- **AWS では確かめない。** 手元のテストと、手元の使い捨ての Splunk（T-10）だけ
