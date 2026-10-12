# 054 のレビュー（Splunk だけの構成でもエージェントが Splunk からログ・trap・メトリクスを引けるようにする）

## Round 1

- 実行したモデル: cold reviewer は opus（effort xhigh）。PM の確認は opus-5.5
- cold reviewer に依頼した（初回のビルドのあと）。対象は 4f29031 → aca0c69（`build.md` の Round 1 / Round 2）
- 結果は下の「054 cold review Round 1」をそのまま連結した

# 054 cold review Round 1（Splunk だけの構成でもエージェントが Splunk からログ・trap・メトリクスを引けるようにする）

- 対象: base 4f29031 → HEAD aca0c69（worktree pm-054）
- 根拠: design.md と実際のコード。build.md は検証の記録（T-9 / T-10 の出力）を確かめるためだけに読んだ
- レビュー担当は直していない。書いたのはこのファイルだけ

## サマリ

- Must fix 0 / Should fix 2 / Nit 6
- Must fix は無い。SPL の注入の防ぎ方（拒否と完全一致の正規表現）、SG・IAM・Terraform のつなぎ方、締め切りと途中までの結果の扱いは design.md のとおりで、テストも付いている
- Should fix の 1 件目は、design.md に書かれていない振る舞いの変更（`app.run_tool` の `GATEWAY_ONLY`）。中身は正しいが、仕様の正本に無い
- Should fix の 2 件目は、`device_id` に IP を渡したとき、sysName がある機器には黙って 0 件を返すこと。ツールの説明は「機器名か IP」と書いている

自分で走らせたテスト（cwd は pm-054。pm-054 に `__pycache__` を作らないように `PYTHONDONTWRITEBYTECODE=1`、Python は pm-042 の `.venv`）:

```
python tests/test_agentcore.py   → 通過 220 / 失敗 0（exit 0。途中の EventLoopException の traceback はテストが意図して起こすもの）
python tests/test_workflow.py    → 通過 461 / 失敗 0（exit 0）
python tests/test_analytics.py   → 通過 549 / 失敗 0（exit 0）
terraform fmt -check -diff <変更した .tf の 5 ファイル>   → rc=0（差分なし）
```

`ops/check.sh` の全体と `terraform validate` は走らせていない（pm-054 に `.terraform` とロックファイルを作るため）。build.md の Round 2 には、最後の編集のあとに取り直した `ops/check.sh` の出力（rc=0、「すべて通過」）が貼ってある。

### 見た観点 / 見ていない観点

- 見た観点
  - design.md との整合
    - 変更対象ファイル、SPL の形、引数の検証、戻りの形、エラーの文、締め切り、Terraform の output・locals・環境変数・SG の行、ツールの登録（14 個）、2 つのプロンプトの 1 文、docs を突き合わせた
  - correctness
    - SPL の組み立て（`splunk_args`）
    - 応答の読み方（`search_splunk` の JSON の行の解釈）
    - Spark の `splunk_events` が作るイベントの形と、`coalesce` で決まる機器名の対応。`python -c` で再現した
  - security
    - SPL の注入（`keyword` で拒む文字、`device_id` の正規表現の fullmatch）
    - 資格情報の置き場所（SSM の SecureString を `toolkit.Param` で読む。環境変数にも Terraform の state にも値を置かない）
    - tools の Lambda のロールの SSM の範囲（`parameter/<prefix>/*`。Deny は Nautobot のパスだけ）
    - SG の行（lambda → splunk 8089 の 1 本だけ）
    - TLS を検証しないこと（design.md で受け入れ済み）
    - リダイレクトのときの Authorization ヘッダーの扱い
  - runtime bugs
    - 締め切りとソケットの timeout
    - 読んでいる途中で切れたときと、HTTP のエラーの本文を読む途中で切れたときの例外の受け方
    - 私有の属性 `fp.raw._sock` に頼るところ
  - data loss
    - 途中で切れたときに、読めた行を捨てないこと
    - 書き込みはしないツールであること
  - API compatibility
    - 既存のツール（`search_logs` ほか）の振る舞いと、`run_tool` が Gateway の失敗のあとコンテナの関数に戻す動きが変わっていないこと
    - `tools.json` と `TOOL_SPECS` が一致すること
  - type safety
    - `inputSchema` の型が string / integer だけであること
    - `minutes` / `limit` の `int()` が `ValueError` を出したとき、`toolkit.runner` が `error` にすること
  - missing tests
    - T-1〜T-8 と T-11 があることをテストのコードで確かめ、3 本を走らせた
- 見ていない観点
  - AWS の上での動き。tools の Lambda から Splunk の 8089 に届くか、クラスターのとき Cloud Map の `splunk` が search head を指して検索できるか、tools のロールで SSM の `splunk/admin-password` を復号して読めるか。design.md も「AWS では確かめない」としている
  - `terraform validate`（T-9）。`fmt -check` だけ自分で走らせた。validate は build.md の `ops/check.sh` の出力を読んだだけ
  - T-10（使い捨ての手元の Splunk）は自分では走らせていない。build.md の出力を読んだだけ
  - `docs/architecture-managed.pptx` の中身は開いていない（build.md の文字数の数え方の出力だけ）
  - データ量が多い本物の Splunk での所要時間

## Must fix

None

## Should fix

- [design.md との整合] `app/agentcore/app.py:81-85` と `app/agentcore/evidence.py:160-162`（`GATEWAY_ONLY`）の振る舞いの変更が design.md に無い
  - 場面
    - `app.run_tool` は、Gateway のツールが `error` を返したら同じ名前のコンテナの関数に戻す。この変更で、モジュールの `GATEWAY_ONLY` にあるツール（いまは `search_splunk` だけ）は戻さず、Gateway のエラーをそのまま返すようになった
    - design.md の変更対象（`design.md:88`）は `app/agentcore/app.py` を「system prompt の 1 文」としか書いていない。方針・検証方法（T-1〜T-11）・未確定事項とリスクにも `GATEWAY_ONLY` と `run_tool` の振る舞いの変更が無い。design-log.md にも無い
    - 中身は正しい。Runtime のコンテナには `SPLUNK_URL` が無いので、戻すと 401 や時間切れが「まだ配備されていない」に化ける。`tests/test_agentcore.py:696-711` が、`search_splunk` は Gateway のエラーをそのまま返し、`search_logs` はいまどおりコンテナに戻すことを確かめている
    - ただ、モジュールと `app.py` のあいだに新しい約束（`GATEWAY_ONLY` という属性）ができた。仕様の正本に無いと、次にツールを足す人がこの約束を知らないまま設計する
  - 分類の理由: 動きは正しくテストもあるので merge しても壊れないが、仕様の正本（design.md）が実際の振る舞いと食い違っているので、後で直すべき。
  - 再現

    ```
    $ grep -n "GATEWAY_ONLY\|run_tool" docs/cycles/054-splunk-agent-tools/design.md docs/cycles/054-splunk-agent-tools/design-log.md
    docs/cycles/054-splunk-agent-tools/design.md:19:  - `TOOL_SPECS`（`:181-207`。Bedrock の `toolSpec` の形で `inputSchema.json`）、`TOOLS`（`:208`）、…（既存の run_tool の説明だけ）
    $ sed -n 88p docs/cycles/054-splunk-agent-tools/design.md
    - `app/agentcore/app.py` — system prompt の 1 文
    $ git diff 4f29031..HEAD -- app/agentcore/app.py | grep "^[-+] *if"
    -        if "error" not in out or local is None:
    +        if "error" not in out or local is None or name in getattr(local, "GATEWAY_ONLY", ()):
    ```

  - 直し方の案: design.md の方針（ツールの登録とプロンプトの節あたり）と変更対象に、`GATEWAY_ONLY` とその理由、`run_tool` の振る舞いの変更、それを見るテストを足す。

- [correctness] `app/agentcore/evidence.py:187-190`（`| search device=…`）と、ツールの説明 `app/agentcore/evidence.py:385` / `app/gateway/tools.json:210`。sysName がある機器を IP で引くと、エラーにならずに 0 件を返す
  - 場面
    - ツールの説明は `device_id` を「機器名か IP」と書いている
    - 機器名は `coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host)` の値で、その値との完全一致（または `<id>.` で始まる）で絞る
    - trap と gNMI は、Spark が device map で `tags.sysName` を足す（`app/spark/snmp_sinks.py:461` の `with_sysname`、`splunk_events` の `:626`）。そのため device map にある機器の trap は、`device` が sysName（例 `spine-1`）になる。`device_id="10.0.0.1"`（その機器の管理 IP）で引くと、1 段目の本文の語 `"10.0.0.1"` では当たるのに、2 段目の `| search device="10.0.0.1"` で落ちて 0 件になる
    - `search_logs`（`evidence.py:115`）は `tags.sysName` / `tags.agent_host` / `tags.source` のどれでも当たる。そのため同じ問いでも、`STORES` に grafana があるかどうかで答えが変わる
    - `error` にならないので、エージェントは「その機器にはログも trap も無い」と読み、原因の分析を間違える
    - design.md:147 の「`device_id` に IP を渡せば引ける」は、sysName が付かない機器の話。説明文ではその条件が落ちている
  - 分類の理由: アラートと `list_devices` の機器名（sysName）で引く普通の使い方では当たるので、merge しても壊れない。ただ、説明どおりに IP で引くと、もっともらしい 0 件を返すので直すべき。
  - 再現（Spark の `splunk_events` が作るイベントの形と、SPL の `coalesce` の順番を Python で写して確かめた。本物の Splunk には送っていない）

    ```
    $ cd pm-054 && PYTHONDONTWRITEBYTECODE=1 python -c '…evidence.splunk_args("10.0.0.1","traps","",30,5) と、
        snmp_sinks.splunk_events([{"topic":"traps","tags":{"source":"10.0.0.1"},…}], devmap={"10.0.0.1":"spine-1"}) を表示…'
    SPL: search index=* sourcetype="nwc:traps" earliest=-30m "10.0.0.1" | eval device=coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host) | search device="10.0.0.1" OR device="10.0.0.1.*" | head 5 | fields _time device sourcetype source _raw
    host: unknown tags: {'source': '10.0.0.1', 'sysName': 'spine-1'}
    coalesce device: spine-1 | matches 10.0.0.1: False
    ```

    本物の Splunk での再現手順: T-10 と同じ使い捨ての Splunk に、`devmap={"10.0.0.1": "spine-1"}` で組んだ trap を HEC で入れ、`search_splunk(device_id="10.0.0.1", topic="traps")` を呼ぶ。`count == 0` になるはず。T-10 の入力は「`agent_host` だけの `10.0.0.1`」で、sysName と IP の両方を持つイベントが無い。
  - 直し方の案（どちらかを PM が選ぶ）
    - 説明を「IP で当たるのは device map に無い（sysName が付かない）機器だけ。sysName のある機器は機器名で引く」に直す（`evidence.py:385` と `tools.json:210`）
    - 2 段目の絞り込みに `OR 'tags.source'="<id>" OR 'tags.agent_host'="<id>"` 相当を足して、`search_logs` と同じく IP でも当たるようにする（design.md の SPL の形と T-1 / T-10 の変更が要る）

## Nit

- [runtime bugs] `app/agentcore/evidence.py:220` と読み出しのループ `:233-255`。25 秒の締め切りは厳密ではない
  - 場面
    - open の timeout は `min(TIMEOUT, 残り)` だが、ソケットの操作 1 回ごとの timeout なので、接続・TLS・送信・応答の頭のそれぞれが最大でその秒数まで待てる
    - Splunk が応答の頭を遅らせると、全体が Gateway の 30 秒を超えうる
    - 超えても、`GATEWAY_ONLY` のおかげで Gateway の時間切れの `error` がエージェントに届く。tools の Lambda の timeout は 60 秒なので落ちない
    - design.md:68 も「ソケットの timeout は `min(TIMEOUT, 残り)`」としか決めていないので、design.md どおり
  - 分類の理由: 超えたときの結果が正しいエラーになるので好みの範囲。
  - 読んだだけ。再現手順: 接続のあと、応答の頭を 20 秒近く遅らせてから少しずつ流す偽物のサーバーを socketpair か `127.0.0.1` で立て、`SPLUNK_DEADLINE` を短くして `search_splunk` の経過時間を測る。

- [correctness] `app/agentcore/evidence.py:187-190` と `:385`。flows は `device_id` で引くと必ず 0 件
  - 場面
    - flows のタグ（`FLOW_TAGS`、`app/spark/snmp_sinks.py:87`）には sysName / agent_host / source / host が無いので、HEC の `host` も `device` も `unknown` になる
    - `search_splunk(device_id="dc1-a-leaf-01", topic="flows")` は、エラーにならずに 0 件を返す
    - design.md:145 は「host が unknown のイベントは機器名が無いので困らない」として受け入れている。ただ、flows はトピックごとそうなるのに、ツールの説明は `topic` に flows を並べるだけで、そのことを言っていない
  - 分類の理由: design.md で受け入れ済みで、flows は前からエージェントが引けなかったので退行ではない。
  - 再現（上の Should fix 2 件目と同じ `python -c` で、flows のレコードを `splunk_events` に通した）

    ```
    flow host: unknown | flow device: unknown
    ```

  - 案: 説明に「flows は機器名では絞れない。keyword（送り元の IP など）で引く」と足す。

- [docs] `.env.example:105-109`。ツールの節に `SPLUNK_URL` / `SPLUNK_USER` / `SPLUNK_PASSWORD` が無い
  - 場面: 節の頭は「ここだけのものは 3 つ」で、`OPENSEARCH_ENDPOINT` / `OPENSEARCH_INDEX` / `PROMETHEUS_QUERY_URL` だけを並べている。evidence.py は `SPLUNK_URL`（tools の Lambda の環境変数）と `SPLUNK_USER` / `SPLUNK_PASSWORD`（手元で渡すもの）を新しく読む（`evidence.py:58-60`）。`.env.example` は design.md の変更対象ファイルに無い。
  - 分類の理由: 手元の動かし方の参考の一覧なので、無くても壊れない。
  - 読んだだけ。再現手順: `sed -n 105,109p .env.example` と `grep -n "SPLUNK_" app/agentcore/evidence.py | head -5` を比べる。

- [docs] `app/agentcore/evidence.py:149`（`SPLUNK_NOT_DEPLOYED`）。Splunk を立てていても「配備されていない」の案内が直し方を間違える場面がある
  - 場面
    - Runtime のコンテナには `SPLUNK_URL` が渡されない（`IaC/terraform/aws-managed/agent/` に無い）
    - `WORKFLOW=0`（Gateway が無い）で `STORES=s3,splunk` だと、エージェントの `search_splunk` はコンテナの関数で動き、「analytics を STORES に splunk を入れて apply すると使える」を返す。Splunk はもう立っていて、本当に要るのは `WORKFLOW=1`（tools の Lambda）
    - `search_logs` の文も同じ形で、文言は design.md:71 の指定どおり
  - 分類の理由: 既存のツールと同じ形で、design.md の指定どおりなので好みの範囲。
  - 読んだだけ。再現手順: `SPLUNK_URL` を空にして `evidence.search_splunk()` を呼び、`error` の文に WORKFLOW が出てこないことを見る。

- [correctness] `app/agentcore/evidence.py:302`。`minutes` の丸めを `splunk_args`（`:169`）と 2 回書いている
  - 場面: 戻りの `minutes` を、`splunk_args` が丸めた値を受け取らずに同じ式で計算し直している。片方だけ直すと、SPL の `earliest` と戻りの `minutes` がずれる。
  - 分類の理由: いまは同じ式なので値は合っており、好みの範囲。
  - 読んだだけ。再現手順: `grep -n "max(1, min(int(minutes), 24 \* 60))" app/agentcore/evidence.py` で `:169` と `:302` の 2 か所が出る。

- [security] `app/agentcore/evidence.py:210-220`。Splunk がリダイレクト（301 / 302 / 303）を返すと、urllib は Authorization（admin の Basic 認証）を付けたまま GET で追う
  - 場面
    - Python 3.13 の `HTTPRedirectHandler.redirect_request` は、`content-length` / `content-type` 以外のヘッダーを新しい Request にそのまま写す。`build_opener` は既定で `HTTPRedirectHandler` を入れる
    - 宛先は VPC の中の Cloud Map の名前（信頼している Splunk）で、SG の lambda の送信は表の行だけなので、外へは出られない
  - 分類の理由: Splunk そのものがリダイレクトを返さない限り起きず、起きても VPC の外へ出ないので好みの範囲。
  - 再現（urllib のソースを表示した）

    ```
    $ python -c 'import inspect, urllib.request as u; …getsource(u.HTTPRedirectHandler.redirect_request)…'
    3.13.11
            or code in (301, 302, 303) and m == "POST")):
            CONTENT_HEADERS = ("content-length", "content-type")
            newheaders = {k: v for k, v in req.headers.items()
                          if k.lower() not in CONTENT_HEADERS}
    ```

  - 案: opener に、リダイレクトで例外を出すハンドラーを入れる（`redirect_request` が `None` を返す `HTTPRedirectHandler` の派生）。読み取り専用のユーザーへの切り替え（QUEUE）と一緒でもよい。

## 良かった点

- SPL の注入の防ぎ方
  - `keyword` はエスケープせずに拒む（`"` `\` `|` `[` `]` `` ` `` と、表示できない文字）
  - `device_id` は `[A-Za-z0-9._:-]{1,64}` の fullmatch で、`*` も通さない
  - どちらも二重引用符の中にしか入らない。引用を抜けてパイプ・サブサーチ・マクロを作れない
  - T-2 が、拒んだときに opener を作らない（送らない）ことまで見ている
- `GATEWAY_ONLY` で、Gateway の 401 や時間切れがコンテナの「配備されていない」に化けるのを防いだ。同じテストで `search_logs` がいまどおりコンテナに戻ることも見ていて、既存のツールの振る舞いを変えていない
- 途中で切れたときの扱い
  - 締め切りや途中の切断では、読めた行（改行で終わった行だけ）を `truncated` 付きで返す。1 行も無ければ `error`
  - 応答の頭が壊れているとき（`HTTPException`）や、HTTP のエラーの本文を読む途中で切れたときも、Lambda の例外にしない
- 私有の属性 `fp.raw._sock` に頼るところは、無ければ何もしない形にしてある。socketpair の本物の `http.client.HTTPResponse` で設定できることをテストしているので、Python の版が変わって名前が変わればテストで気づける
- SG の表のテスト（`tests/test_analytics.py`）は、`lambda` から出る行が 8089 の 1 本だけで、`runtime → splunk` が無いことまで見る。OSS 版は base/core と workflow がリンクで、SG の表も `sg_flows` を `concat` するので、同じ行が入る
- パスワードは環境変数にも Terraform の state にも置かず、SSM の SecureString を `toolkit.Param` で読む。output の `splunk_search_url` は、Splunk を ECS で動かさない構成では空なので、「配備されていない」の案内に落ちる
- docs の更新範囲がそろっている
  - SG の表: `core.md`、`vpc-perimeter.md`
  - Splunk のポートとつながり: `splunk.md`
  - ツールの表とつながり: `agentcore-bedrock.md`
  - スライド: `architecture-managed.deck.md` の 148 / 150 行と pptx
  - `deploy.md`、`deploy.env.example` の `STORES` の説明（`WORKFLOW=1` の条件つき）
  - 未確認の行に「AWS では未確認」と「admin の資格情報を使う」を書いている

## ユーザーへの質問

None

### PM の確認（Round 1）

- cold reviewer が書いたのは scratchpad の `054-review-r01.md` だけ。pm-054 と pm-042 の `git status --porcelain` は空のまま（新しいファイル無し）
- Should fix 2 件目（sysName のある機器を IP で引くと 0 件）を再現した。エンジニアの A4 / A5 と同じ原因（2 段目の絞り込みを `coalesce` した 1 つの機器名だけで書いた）。aca0c69 で:

  ```
  traps host: unknown tags: {'source': '10.0.0.1', 'name': 'linkDown', 'sysName': 'spine-1'} | device: spine-1 | filter device==10.0.0.1: False
  flows host: unknown tags: {'sampler': '10.0.0.1', 'src': '10.1.1.1', 'dst': '10.2.2.2'} | device: unknown | filter device==10.0.0.1: False
  search index=* sourcetype="nwc:traps" earliest=-30m "10.0.0.1" | eval device=coalesce('tags.sysName', 'tags.agent_host', 'tags.source', host) | search device="10.0.0.1" OR device="10.0.0.1.*" | head 5 | fields _time device sourcetype source _raw
  ```

  → Round 3 で設計を直す（2 段目に `tags.agent_host` / `tags.source` / `tags.sampler` / `host` の完全一致を足す。`design-log.md` の Round 3）
- Should fix 1 件目（`GATEWAY_ONLY` が design.md に無い）は、指摘どおり design.md の方針・変更対象・T-12 に足した。コードは変えない（`tests/test_agentcore.py` の該当のテストは cold reviewer が走らせて通過）
- エンジニアの A6（締め切りが厳密でない）と cold の Nit 1 件目は同じ件。受け入れて design.md の「未確定事項とリスク」に書いた。超えたときの結果の根拠:

  ```
  IaC/terraform/aws-managed/workflow/gateway.tf:202:  timeout          = 60
  app/agentcore/mcp_client.py:26:TIMEOUT = int(os.environ.get("GATEWAY_TIMEOUT", "30"))
  app/agentcore/app.py:85: if "error" not in out or local is None or name in getattr(local, "GATEWAY_ONLY", ()):
  ```

- Nit 6 件は直さない（flows の Nit は A5 と一緒に Round 3 で片付く）

## Round 2

- 実行したモデル: PM の確認は opus-5.5
- cold reviewer に依頼しなかった（中間ラウンド）。`build.md` の Round 3 のセルフレビュー（反対弁護人 opus / effort xhigh）に依った
- 前ラウンドの Should fix（sysName のある機器を IP で引くと 0 件、flows を device_id で引けない）: `build.md` の Round 3 の T-10 で、10.0.0.2 → spine-2、192.0.2.3 → spine-3、flows の 10.0.0.3 と 10.0.0.30 が当たり合わないことを本物の Splunk で確かめている（エンジニアの実行。完了の前に PM が取り直す）

### 未解消の指摘

- **Should fix B1** [correctness] `app/agentcore/evidence.py:193`（`OR host="<id>"`）。`device_id="unknown"` で、本文に unknown の語を含む他の機器の行が出る。PM が 0da5558 で再現した:

  ```
  HEC host: unknown | device: leaf-9 | raw has 'unknown': True
  device clause: False | ip clauses: False | host clause: True
  ```

  原因: Round 3 の設計で HEC の `host`（`tags.host` か agent_host の写し。元が無いと `unknown`）を IP の欄として足した → 設計 Round 4 で句を外す（`design-log.md` の Round 4）

### 最終報告に回したもの

- Nit B3（`device="<id>.*"` が IP の一部を通す）: 直さない。完了時に QUEUE へ
- Nit B4（docs が `tags.host` / `host` を欄に挙げる）: Round 4 の docs で B1 と一緒に直す
- Nit B2: エンジニアが T-10 に rec12 を足して片付けた
