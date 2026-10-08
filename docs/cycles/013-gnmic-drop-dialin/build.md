# build: gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）

## Round 1

実装モデル: opus-5.5 / effort: xhigh（get_session の値。自分のセッションの effort は set_session_effort で変えられないので、既定の high に下げていない）

### 第 1 段（44c7ccd・bd10786 と、この commit）

- 実装: gnmic の設定・入口・イメージ（`app/gnmic/`、`docker/images/gnmic/Dockerfile`）、Spark の gnmic の読み替え（`gnmic_message` と read_rows の分岐、`GNMI_MEASUREMENTS` / `GNMI_TAGS`、`STATE_FIELDS` の `oper_state` / `admin_state`、splunk の devmap）、Grafana の `link_down`（`snmp_interface_oper_up` の 24h `last_over_time` と admin disable の除外、`lt 0.5`）とダッシュボード、Splunk の `netops_poll` を消して `netops_gnmi` に link_down、evidence / tools.json の例、kafka_load と TRex の README、テスト
- event の形は**ソースから組んだもの（実物ではない）**。gnmic v0.49.0 のソース（`formatters/event.go`・`outputs/kafka_output`）から組んだ。手元の SR Linux は動かないので、実物の照合は AWS の検証 7（PM）。design.md の未確定 1
- 012 のマージ（bd10786）: 衝突は `snmp_sinks.py` の read_rows だけで、`F.when(flows).when(gnmic).otherwise(telegraf)` に解いた
- 設計から足したもの（design.md に先に書き足し、design-log に 1 行）: splunk のジョブにも `--device-map`（Terraform 3 本）、ダッシュボードの `last_over_time` / `[5m]`、`docker/compose/{check.sh,README.md}` の系列名を第 1 段へ前倒し、`test_oss.py` の偽の pyspark に `|` / `&`

#### 検証（第 1 段の終わり。最後の編集のあとに取り直した出力）

1. `bash ops/check.sh`

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（aws-managed 9・oss 9 の全ルートが OK）
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
通過 157 / 失敗 0      (test_alerts)
通過 504 / 失敗 0      (test_analytics)
通過 161 / 失敗 0
通過 78 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0      (test_local_compose)
69 項目すべて通過
通過 172 / 失敗 0      (test_oss)
通過 181 / 失敗 0
通過 66 / 失敗 0
通過 115 / 失敗 0      (test_stream)
通過 103 / 失敗 0
通過 327 / 失敗 0

すべて通過
exit=0
```

1 回目は `tests/test_oss.py` が落ちた（原因: read_rows の gnmic の条件 `msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())` を、test_oss の偽の列 `AnyObj` が `|` を持たずに受けられない。`AnyObj` に `__or__` / `__and__` を足した）。

```
  File ".../app/spark/snmp_sinks.py", line 275, in read_rows
    gnmic = msg["fields"].isNull() & (msg["values"].isNotNull() | msg["deletes"].isNotNull())
TypeError: unsupported operand type(s) for |: 'AnyObj' and 'AnyObj'
!! tests/test_oss.py が失敗した
```

2. `tests/test_stream.py`: `通過 115 / 失敗 0`
3. `tests/test_analytics.py`: `通過 504 / 失敗 0`
4. `tests/test_alerts.py`: `通過 157 / 失敗 0`。`grep -rn "ifOperStatus\|netops_poll" app/grafana app/splunk` → 出力なし、`exit=1`（0 件）
5. gnmic のイメージ: `docker build -q -t nwc-gnmic-check:013 -f docker/images/gnmic/Dockerfile app/gnmic` → `sha256:cad1fb0d013a…`。`docker run`（`KAFKA_AUTH=none`、`KAFKA_BROKERS=127.0.0.1:9092`、target 2 台 `192.0.2.11` / `192.0.2.12`、偽の資格情報）で 20 秒:

```
/tmp/gnmic.yaml を作った（gnmi: 2 台 "192.0.2.11:57400", "192.0.2.12:57400" / brokers: 127.0.0.1:9092 / topics: gnmi, metrics / kafka auth: none）
level=INFO msg="gnmic version" version=0.49.0 commit=ce0d4173
level=INFO msg="using config file" path=/tmp/gnmic.yaml
level=INFO msg="starting output" type=kafka   (2 回)
level=INFO msg="queuing target" target=192.0.2.11 / 192.0.2.12
level=INFO msg="initialized kafka producer" output=kafka name=metrics … "Format":"event" … "SplitEvents":true … "Topic":"metrics"
level=INFO msg="initialized kafka producer" output=kafka name=gnmi … "Format":"event" … "SplitEvents":true … "Topic":"gnmi"
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … interface/statistics mode:SAMPLE sample_interval:60000000000
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … bgp/neighbor[peer-address=*]/session-state mode:ON_CHANGE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … platform/control[slot=*]/cpu[index=all]/total, …/memory mode:SAMPLE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … interface[name=*]/oper-state, …/admin-state mode:ON_CHANGE
level=INFO msg="sending gNMI SubscribeRequest" target=192.0.2.11 … isis/instance[name=main]/interface[interface-name=*]/oper-state mode:ON_CHANGE
（192.0.2.12 も同じ 5 つ）
level=ERROR msg="subscription receive error" target=192.0.2.11 subscription=bgp_neighbor err="failed to create a subscribe client, target='192.0.2.11', retry in 10s. err=rpc error: code = Unavailable …
（ERROR は全部この形で、target に届かないもの。設定の誤りのエラーは無い）
running exit=0
```

6. （第 2 段）未実行
7. / 8. AWS（PM）: 未実行
- `tests/check_splunk_image.py`（ops/check.sh に入らない実機の検査。HEC の event を gnmic の形に、サーチを `netops_gnmi` に替えた）: 未実行。セルフレビューで走らせる

### 第 2 段（この commit。ae03dcb で docs/cycle-006-design を取り込んだあと）

- 実装: telegraf-dialin（タスク定義・サービス・SSM 3 つ・SG `telegraf_dialin`・`telegraf_dialin_*` の output）、SNMP のポーリング（`inputs.snmp`・`SNMP_POLL`・`snmp_poll` / `snmp_agents`・`lab_topology.py --snmp-agents`・`lab.sh forward` の udp 161）、`TELEGRAF_ROLE` と `# >>> role` の区間、`lab_gnmi.star` / `lab_circuits.star`（Dockerfile の COPY も）、Telegraf の `outputs.kafka` の `metrics` / `gnmi` を外した。Telegraf は trap（`traps`）だけ
- gnmic を足した: `IaC/terraform/aws-managed/pipeline/stream/gnmic.tf`（012 の `collectors.tf` の形。arm64・1 タスク・SCRAM 9096・`<prefix>-gnmic-exec` / `-task`・SSM `/<prefix>/gnmic/<lab|nautobot>/gnmi-targets` と `gnmi-username` / `gnmi-password`・output `gnmic_exec_command` / `gnmic_list_tasks_command`）、`IaC/terraform/oss/pipeline/stream/gnmic.tf`（認証なし 9092）、SG `gnmic`（何も受けない）、ECR `<prefix>-gnmic`、`app/gnmic/gnmic.sh get`（ECS Exec で 1 回取る）、`build_gnmic`（`ops/up-common.sh` の `GNMIC_VERSION`）
- Nautobot: `nb_map.TARGET_KEYS = ("gnmi-targets",)`、`nb_sync` の作り直し先を gnmic に、`pipeline/nautobot` の IAM / locals、Job の名前「gnmic とグラフ DB に同期」
- ops: `up.sh` / `oss/ops/up.sh` は `SNMP_POLL` が残っていれば注意だけ、base/core に `telegraf_dialin` の SG が残り stream が使っていれば何も作る前に止まる、`link_down` の Grafana の送り手は `GRAFANA` と `SINK_PROMETHEUS` だけで決まる。`down.sh` は `/<prefix>/telegraf-dialin/` の前の回の分も ManagedBy で消す
- lab / compose: `tg test` / `tg gnmi`・`lab.sh telegraf test|gnmi` は案内して exit 1、`LAB_SNMP_COMMUNITY` を消した、compose に `gnmic` を 14 番目のサービスとして足した（第 1 段の時点で compose に `telegraf-dialin` は無く、`telegraf` 1 つが `TELEGRAF_ROLE=all` だった）
- docs: design.md 4. の docs の全部（collection / pipeline / architecture / data-stores / deploy / troubleshooting / nautobot / workflow / FAQ / README / alert-comparison / hearing / docker/compose/README.md）。`troubleshooting.md` の `-var 'snmp_agents=…'` は変数が無くなって打つと落ちるので消した
- 設計から足したこと（design.md に先に書き、design-log に記録）: 設計方針 1・4 の事実の直し（1 回取るのは `lab.sh gnmic` ではなく `gnmic_exec_command` の `gn get`）と 4. の「第 2 段の実装で決めた細目」
- **012 Round 2 の ACL が入るまで、マネージドの gnmic は Kafka に書けない見込み**（SCRAM のユーザーに `gnmi` / `metrics` の Write・Describe が無い。design.md の未確定 7。AWS 未確認。OSS は認証なしなので影響しない） → セルフレビュー F1 で 012 Round 2 を取り込み、`ensure_acls` に `gnmi` / `metrics` を足した（282ea83。下の「セルフレビュー」）
- 変更ファイル: 85（`git diff --stat`: 1078 insertions / 1523 deletions）＋新規 2（2 つの `gnmic.tf`）。削除 2（`app/telegraf/lab_gnmi.star`・`lab_circuits.star`）

#### テストの期待値を変えたもの（元の期待値が誤りになった理由）

- `tests/test_stream.py`（115 → 104）: dialin の役割・`SNMP_POLL`・`inputs.snmp` / `inputs.gnmi`・Starlark（`lab_cpu` / `lab_memory` / `lab_if_counters` / MAC / circuits / `_num`）・`tg test` の検査は、検査の対象（設定の区間・.star）を消したので外した。「trap だけ」「古い環境変数を読まない」「`tg test` / `gnmi` は案内して 1」を足した
- `tests/test_lab_debug.py`（104 → 97）: ポーリング先・inputs.gnmi の 3 つ・.star・udp 161 の検査を、「ポーリング先を作らない」「gnmic の購読先は SSM から」「161/udp は通さない」「`gn get` で 1 回取る」に替えた。Telegraf の出力は 3 → 1、SG の入れ替えの守りに `telegraf_dialin` を足した、`dir_tag` の呼び元は 10 → 12（gnmic の `ops/up.sh` と `oss/ops/up.sh`）
- `tests/test_nautobot.py`（69 → 68）: `snmp-agents` のキーを消した（`nb_map.TARGET_KEYS` が 1 つ）
- `tests/test_local_compose.py`（132 → 137）: compose の gnmic のサービス（host ネットワーク・`GNMIC_VERSION`・render）を足し、Telegraf の出力は 1 つ・`SNMP_AGENTS` を渡さないに替えた
- `tests/test_oss_ops.py`（181 → 182）: gnmic のイメージのビルドを足し、OSS の SecureString は 14 → 13（`/telegraf-dialin/snmp-community` が無くなり、gnmic の 2 つは dialin の 2 つの置き換え）
- `tests/test_sync.py`・`test_oss.py`・`test_analytics.py`: 名前（dialin → gnmic）、`--snmp-agents` が無いこと、`SNMP_POLL` で送り手が変わらないこと

#### 検証（第 2 段の終わり。最後の編集のあとに取り直した出力）

1. `bash ops/check.sh`（検証方法 1 と 6。`terraform fmt` / `validate` は 1・2）

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（aws-managed 9・oss 9 の全ルートが OK）
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
通過 157 / 失敗 0      (test_alerts)
通過 504 / 失敗 0      (test_analytics)
通過 161 / 失敗 0      (test_app)
通過 78 / 失敗 0       (test_collectors)
通過 3 / 失敗 0        (test_dashboard_config)
通過 78 / 失敗 0       (test_graph)
通過 7 / 失敗 0        (test_kb_index)
通過 97 / 失敗 0       (test_lab_debug)
通過 137 / 失敗 0      (test_local_compose)
68 項目すべて通過       (test_nautobot)
通過 172 / 失敗 0      (test_oss)
通過 182 / 失敗 0      (test_oss_ops)
通過 66 / 失敗 0       (test_oss_roll)
通過 104 / 失敗 0      (test_stream)
通過 103 / 失敗 0      (test_sync)
通過 327 / 失敗 0      (test_workflow)

すべて通過
```

テストを 1 本ずつ（`uv run --group dev --group web python tests/<t>.py`）:

```
---- test_stream rc=0
---- test_lab_debug rc=0
---- test_local_compose rc=0
---- test_sync rc=0
---- test_oss_ops rc=0
---- test_oss rc=0
---- test_oss_roll rc=0
---- test_analytics rc=0
---- test_nautobot rc=0
---- test_alerts rc=0
---- test_app rc=0
```

2. / 3. / 4. `test_stream` / `test_analytics` / `test_alerts`: 上の 1 に入っている（`通過 104` / `504` / `157`）
5. gnmic のイメージ（第 2 段の `gnmic.sh get` を足したあとに作り直した）: `docker build -q -t nwc-gnmic-check:013 -f docker/images/gnmic/Dockerfile app/gnmic` → `sha256:76d51024fc4e…`、`build rc=0`

```
---- render (scram)
rc=0
/tmp/gnmic.yaml を作った（gnmi: 1 台 "192.0.2.10:57400" / brokers: 127.0.0.1:9096 / topics: gnmi, metrics / kafka auth: SASL/SCRAM-SHA-512）
---- get (target 203.0.113.1 に届かない)
rc=1
level=INFO msg="sending gNMI GetRequest" target=203.0.113.1 … interface[name=*]/oper-state … admin-state … network-…
"203.0.113.1:57400" GetRequest failed: rpc error: code = DeadlineExceeded desc = context deadline exceeded while waiting for connections to become ready
Error: one or more requests failed
---- run 35s (target 203.0.113.1 に届かない)
running restarts=0 exit=0
15        (subscription receive error の行数。5 つの購読 × 10 秒ごと)
level=ERROR msg="subscription receive error" target=203.0.113.1 subscription=bgp_neighbor err="failed to create a subscribe client, target='203.0.113.1', retry in 10s. err=rpc error: code = Unavailable …
level=INFO msg="initialized kafka producer" output=kafka name=gnmi …
level=INFO msg="initialized kafka producer" output=kafka name=metrics …
```

`SKIP_LAB=1` の案内（届かない target に 10 秒ごとに繋ぎ直し、プロセスは落ちない）は、step 3 のときに同じ形で 80 秒見た（`running restarts=0 exit=0`）。ECS では未確認

6. `terraform fmt` / `validate`: 上の 1（18 ルートが OK）
7. / 8. AWS（PM）: 未実行
- `tests/check_splunk_image.py`: 57c1b0d で走らせた。そのあと `app/splunk`・`docker/images/splunk`・`tests/check_splunk_image.py` は変わっていない（`git diff --stat 57c1b0d HEAD -- …` が空）

```
-- docker/images/splunk/Dockerfile と app/splunk/ を linux/amd64 でビルドして nwc-splunk-check:local にする
-- nwc-splunk-check:local を nwc-splunk-check-73036 で起こした。healthy になるのを待つ（数分）
ok Splunk が入口（app/splunk/entrypoint.sh）から起きて healthy になる（170 秒）
ok splunkd が読む設定でも python.required = 3.13（btool）
ok 偽の認証情報の口と偽の SNS がコンテナの中で動く
ok 直に: Splunk の Python 3.13 で boto3 / botocore が読め、SNS のクライアントを作れる（app に同梱していない = Splunk の site-packages のもの）
   python 3.13.11（/opt/splunk/bin/python3.13）boto3 1.37.14 botocore 1.37.14 /opt/splunk/lib/python3.13/site-packages/boto3/__init__.py
ok 直に: netops_sns.send で偽の SNS へ 1 通届く（認証情報は偽の口から、署名つき、Query API の Publish）
-- HEC に link down（dc1-a-leaf-01 ethernet-1/1）を入れた。netops_gnmi（毎分）が送るのを待つ
ok 本物の流れ: アラートアクションが偽の SNS へ 1 通だけ送る（次の回で重ねて送らない）
ok 本物の流れ: 本文は Grafana と同じ形の JSON（source=splunk、link_down の firing 1 件）、件名は netops alert、form は Publish
ok 本物の流れ: splunkd は Python 3.13 で起こし、Splunk の boto3 で送っている（User-Agent: Python 3.13.11、boto3 1.37.14）
ok 本物の流れ: splunkd.log に件数（published=1/1）と exit code=0 が残る
-- bin/boto3.py を置いて、HEC に link down（ethernet-1/2）を入れた。splunkd.log に理由が出るのを待つ
ok boto3 が読めないとき: splunkd.log に理由（boto3 を読めない・Splunk の Python の版・確かめ方）が 1 行で出て、exit code=3。送らない
   etops_sns STDERR -  boto3 を読めない（ModuleNotFoundError: No module named boto3 (check_splunk_image が置いた偽物)）。Splunk の Python 3.13.11（/opt/splunk/bin/python3.13）に boto3 が無い。Splunk の版を変えたなら tests/check_splunk_image.py で確かめ、無ければ boto3 を app の lib/ に同梱する（git の ffba169）
ok 見えた版が CHECKED と同じ（{'splunk': '10.4.4', 'python': '3.13.11', 'boto3': '1.37.14'}）。違うなら、上が通っているので CHECKED を書き換える（Splunk の版を変えたら Dockerfile・ops/up.sh も）
すべて通過（11 件）

[exited with code 0]
```

### セルフレビュー

- 自分: opus-5.5 / xhigh。入力は design.md と bf6860b（ae03dcb..bf6860b）のコード
- 反対弁護人: opus（Agent の general-purpose、読み取り専用）。渡したのは design.md / build.md のパス、変更ファイルの一覧、選んだ方針と迷った点。返ってきたあとの `git status --porcelain` は自分の未コミットの 2 本（test_oss_ops / test_stream）だけで、増えたものは無い
- 反対弁護人の指摘は Must 1（F1）/ Should 3（F1b・F3・F5）/ Nit 2（F2・F7）。全部を自分で確かめてから片付けた
- 直しは 57c1b0d（自分の退行注入で見つけたテストの穴）、282ea83・e8a50ee（F1）、このあとの commit（F5・F2）

#### 自分の退行注入

1 か所ずつ壊してテストを回し、元に戻した。1 回目（M1〜M4・M6・M8）は壊し方（置き換えた文字列が元の文字列を含む）か見るテストの選び方が外れていたので、b で取り直した。b でも落ちなかったものが穴。

| # | 壊したもの | 見たテスト | 結果 |
|---|---|---|---|
| M1b | ops/up.sh の `telegraf_dialin` の SG の守りを丸ごと消す | test_stream | 落ちる |
| M2b | oss/ops/up.sh の同じ守りを丸ごと消す | test_oss_ops | 落ちない → テストを足した（57c1b0d）。足したあと落ちる |
| M3b | stream の remote_state の postcondition のキーを `telegraf_dialin` に戻す | test_stream | 落ちない → テストを足した（57c1b0d。文字列の検査。F7）。足したあと落ちる |
| M4b | SG の gnmic → lab_mgmt 57400 を消す | test_analytics | 落ちる（通信の表） |
| M5 | `nb_map.TARGET_KEYS` に `snmp-agents` を戻す | test_nautobot | 落ちる |
| M6b | lab.sh forward に udp 161 を戻す | test_stream | 落ちる |
| M7 | `tg test` / `tg gnmi` の exit 1 を消す | test_lab_debug | 落ちる |
| M8b | ops/down.sh の stream の destroy に `snmp_agents` を戻す | test_stream | 落ちる |
| M9 | lab_topology.py に `--snmp-agents` を戻す | test_sync | 落ちる |
| M10 | compose の gnmic の `KAFKA_AUTH: none` を消す（既定の scram になる） | test_local_compose | 落ちる |
| G1〜G3 | gnmic.sh get: `shift "$n"` を消す / 既定のパスから admin-state を落とす / render を標準出力へ（57c1b0d の commit メッセージでは M7 / M7b / M7c） | test_stream | 検査が無かった → テストを足した（57c1b0d）。足したあと 3 つとも落ちる |
| M11 | compose の check.sh から gnmi の件数の判定を消す | test_local_compose | 落ちる（15 項目が ok の検査） |
| M12 | check.sh の gnmi の判定を 0 件でも ok にする | test_local_compose | 落ちる（F5 で足した検査） |
| M13 | `SCRAM_TOPICS` を logs / flows に戻す（012 のまま） | test_analytics | 落ちる |
| M14 | `SCRAM_TOPICS` から metrics を落とす（e70f59e の案） | test_analytics | 落ちる |
| M15 | `SCRAM_TOPICS` に traps を足す | test_analytics | 落ちる |

M11〜M15 の出力（mut_final.py。最後の編集のあと）:

```
M11 check.sh から gnmi の件数の判定を消す: rc=1 落ちた | AssertionError: check.sh: 応答が全部そろえば 15 項目とも ok で「すべて ok」、終了コード 0（メモリが 20 GB 以上なら注意を出さない）
M12 check.sh の gnmi の判定を 0 件でも ok にする: rc=1 落ちた | AssertionError: check.sh: Kafka の gnmi のメッセージ数が 0 なら、metrics が届いていても NG で logs gnmic を案内する（on-change の購読だけが断られた。cycle 013 のセルフレビュー F5）
M13 SCRAM_TOPICS を logs / flows に戻す: rc=1 落ちた | AssertionError: ensure_acls: User:collectors に logs / flows / gnmi / metrics の WRITE と DESCRIBE（TOPIC・LITERAL・host *・ALLOW）の 8 つを 1 回の createAcls で入れ、入れたものを返す
M14 SCRAM_TOPICS から metrics だけ落とす（e70f59e の案）: rc=1 落ちた | AssertionError: ensure_acls: User:collectors に logs / flows / gnmi / metrics の WRITE と DESCRIBE（TOPIC・LITERAL・host *・ALLOW）の 8 つを 1 回の createAcls で入れ、入れたものを返す
M15 SCRAM_TOPICS に traps も足す: rc=1 落ちた | AssertionError: ensure_acls: User:collectors に logs / flows / gnmi / metrics の WRITE と DESCRIBE（TOPIC・LITERAL・host *・ALLOW）の 8 つを 1 回の createAcls で入れ、入れたものを返す
restored
```

#### F1（Must）[設計整合性 / correctness]: 012 Round 2 の ACL がこのブランチに無く、入っても gnmi / metrics に付かない

- 場所: `app/spark/snmp_sinks.py`（docs/cycle-006-design 側の `SCRAM_TOPICS = ("logs", "flows")`）、design.md の未確定 7、`IaC/terraform/aws-managed/pipeline/stream/gnmic.tf:10`、上の第 2 段の 94 行目
- 破綻: マネージドの SCRAM のユーザーに gnmi / metrics の ACL が無いと（MSK が `allow.everyone.if.no.acl.found` を SCRAM の主体に効かせないとき。012 の前提 (a)）、gnmic は Kafka に書けない。Grafana / Splunk の link_down と WORKFLOW が止まる。dialin は IAM で書けていたので、既定ブランチより後退する
- 確かめた: `git merge-base --is-ancestor 5944704 HEAD` → 1（祖先でない）。docs/cycle-006-design の `SCRAM_TOPICS` は logs / flows だけ。PM の前提（metrics は IAM の Telegraf が書くので ACL は要らない）は誤りで、`gnmic.yaml.in:70` が gnmi、`:85` が metrics、`telegraf.conf.in:43` が traps（PM も確かめて了承）
- 片付け: 直した。282ea83 で df42f51 を、e8a50ee で e70f59e を取り込み、`SCRAM_TOPICS = ("logs", "flows", "gnmi", "metrics")`（ACL は 8 つ）と test_analytics の検査（M13〜M15 で落ちる）。design.md の設計方針 1 の SCRAM の行と未確定 7 を事実に合わせた。MSK の上で効くかは未確認（検証 7 / 8）
- 残り: ACL は Spark のジョブの起動（`ensure_acls`）で入るので、それより前にマネージドの gnmic が出した値は落ちる。on-change の最初の同期はそこで失われ、次に状態が変わるまで系列が無い（未確定 7。AWS 未確認。OSS・手元は認証なしなので影響しない）

#### F1b（Should）[security]: SCRAM のユーザー `User:collectors` を syslog-ng / GoFlow2 と共有する

- 場所: `app/spark/snmp_sinks.py:97`、design.md の設計方針 1
- 破綻: NLB 経由で外から UDP を受ける syslog-ng / GoFlow2 のどちらかが乗っ取られると、同じ資格情報で gnmi に偽の oper-state down を書ける。そこから link_down が鳴り、WORKFLOW が起きる
- 確かめた: 読んだだけ（3 つのタスクが同じ `local.kafka_collector_secrets` を ECS の secrets で受ける。`collectors.tf:77,169`、`gnmic.tf:92`）
- 片付け: PM の判断で共有のまま（e70f59e）。ユーザーを分けるのは BACKLOG

#### F3（Should）[runtime / 運用]: up.sh は gnmic が Kafka に書けていなくても「動いている」と出す

- 場所: `ops/up.sh:963-964`、`oss/ops/up.sh:387-388`
- 破綻: `aws ecs wait services-stable` はタスクが安定しているかだけを見る。gnmic が Kafka に拒まれても落ちずに繋ぎ直すなら、up.sh は成功と表示して先へ進む。up.sh が待ち続けて止まることは無い（10 分で黄色の警告だけ）
- 確かめた: 読んだだけ。手元では、届かない target / broker でも gnmic は落ちなかった（上の第 1 段の検証 5 の `running exit=0`、第 2 段の `running restarts=0 exit=0`）ので、表示が成功のまま残る見込みは高い。Kafka の認可で拒まれたときは未確認
- 片付け: 最終報告に回した（PM と合意）。up.sh から Kafka のトピックを読む手段が design に無く、012 の syslog-ng / GoFlow2（`ops/up.sh:970`）も同じ見方をしている。gnmi / metrics に届くかは AWS の検証 8 で見る

#### F5（Should）[missing tests]: compose の check.sh が gnmi の件数を見ない

- 場所: `docker/compose/check.sh:62-72`
- 破綻: on-change の 3 つ（interface_state / bgp_neighbor / isis_interface）のパスが機器に拒まれても、metrics > 0 で全部 ok になる
- 確かめた: check.sh を読んだ（件数を見るのは metrics だけだった）
- 片付け: 直した（このあとの commit）。design.md 4. の「第 2 段の実装で決めた細目」に先に書き、design-log に 1 行。`gnmi のメッセージ数 > 0` を足し、0 なら NG で `docker compose logs gnmic` を案内する。test_local_compose に `FAKE_GNMI` と 0 件の検査を足し、項目の数（14 → 15 / 13 → 14 / 12 → 13）を直した。README も。M11・M12 で落ちる。SR Linux の入った compose では未確認（手元の SR Linux が起きない。未確定 1）

#### F2（Nit）[docs]: `telegraf.tf:219` のコメントが消した output `telegraf_exec_command` を指す

- 確かめた: `git grep -n telegraf_exec_command` でこのコメントだけが出た。直したあとは `-- IaC ops app tests docs/*.md` で 0 件
- 片付け: 直した（`output telegraf_dialout_list_tasks_command` で探し、`--container telegraf`）

#### F7（Nit）[missing tests]: postcondition の検査は locals.tf の文字列を探すだけ

- 場所: `tests/test_stream.py:280`
- 破綻: その行をコメント（`#`）にしても `in` で一致して通る
- 片付け: 最終報告に回した。postcondition は state が無いと評価されず（`terraform validate` では走らない）、tf の検査はこのリポジトリでは文字列で見る形

#### 問題なしとした観点と根拠

- **ECS Exec の `gn get` で資格情報が見えるか:** 同じ SCRAM の secret を持つ syslog-ng / GoFlow2 も ECS Exec を有効にしている（`collectors.tf:105,196` の `enable_execute_command = true`。読んだだけ）ので、新しく見えるものは無い。`gn get` の標準出力に資格情報の値が出ないことは test_stream の `_gnmic_get`（偽の gnmic で実行。G1〜G3 で落ちる）
- **compose の depends_on:** syslog-ng と同じ形（`compose.yaml:118-119`。反対弁護人が読んだだけ）
- **`telegraf_source_cidr` の名前:** 変えると lab.sh と lab の IAM まで変わる。description だけ直した（読んだだけ）
- **古い state からの移り方:** up.sh / oss/ops/up.sh の守り（M1b・M2b）、stream の postcondition（M3b）、down.sh の destroy の変数（M8b）、`/telegraf-dialin/` の SecureString を down-common.sh が ManagedBy で消すこと（読んだだけ）
- **SG / IAM の範囲:** udp 161 を通さない（M6b）、gnmic から出るのは lab_mgmt の 57400 だけ（M4b）。Nautobot に足した IAM は 1 つの SSM パラメータと 1 つの ECS サービス（読んだだけ）

#### 検証（セルフレビューの直しのあと。build.md 以外の最後の編集のあと、base（e70f59e）に新しい commit が無いのを `git fetch` で確かめてから取り直した）

`bash ops/check.sh`（rc=0、2881 行。下は 1〜3 と、4 の各テストの最後の行。右の括弧は `tests/test_*.py` の glob の順で付けた名前）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし

== 4. 模擬テスト
通過 168 / 失敗 0          (test_alerts)
通過 513 / 失敗 0          (test_analytics)
通過 161 / 失敗 0          (test_app)
通過 79 / 失敗 0           (test_collectors)
通過 3 / 失敗 0            (test_dashboard_config)
通過 78 / 失敗 0           (test_graph)
通過 7 / 失敗 0            (test_kb_index)
通過 97 / 失敗 0           (test_lab_debug)
通過 138 / 失敗 0          (test_local_compose)
68 項目すべて通過             (test_nautobot)
通過 173 / 失敗 0          (test_oss)
通過 196 / 失敗 0          (test_oss_ops)
通過 66 / 失敗 0           (test_oss_roll)
通過 106 / 失敗 0          (test_stream)
通過 103 / 失敗 0          (test_sync)
通過 327 / 失敗 0          (test_workflow)

すべて通過
```

検証方法 7 / 8（AWS）は PM。未実行

### マージのラウンド（fcae1d2。根の片付け（017）を取り込む）

base の docs/cycle-006-design に 017（PR #5、fcae1d2）が入ったので `git merge fcae1d2` した。16 ファイルが衝突。初回は解消せずに `git merge --abort` して PM に報告し、PM の判断（案 A: 013 側の文を取り、パスだけ 017 の新しい名前にする）で diff3 で取り直した。

#### 衝突と解き方

23 塊のうち 21 塊は、017 側の変更がパスの付け替えだけだった。base の塊に下の付け替えを当てると 017 の塊と一字一句同じになることをスクリプトで確かめてから、013 の塊に同じ付け替えを当てた。

- `oss/ops/` → `ops/oss/`、`("oss", "ops",` → `("ops", "oss",`
- `tools/netflow_send.py` → `ops/netflow_send.py`、`tools/{handler.py,tools.json}` → `app/gateway/`

| ファイル | 塊 | 解き方 |
| :--- | ---: | :--- |
| docker/compose/check.sh | 1 | パスだけ |
| docs/architecture/README.md | 1 | 手で: 017 の `app/gateway/` の行と 013 の Dockerfile の行（gnmic 入り）を両方残す |
| docs/architecture/resources/lab-ec2.md | 1 | パスだけ |
| docs/architecture/resources/nautobot.md | 1 | パスだけ |
| docs/collection.md | 1 | パスだけ |
| docs/data-stores.md | 1 | パスだけ |
| docs/deploy.md | 1 | パスだけ |
| docs/oss-variant.md | 1 | パスだけ |
| docs/pipeline.md | 1 | パスだけ |
| docs/troubleshooting.md | 1 | パスだけ |
| ops/deploy-env.sh | 1 | パスだけ |
| ops/oss/down.sh | 1 | パスだけ |
| ops/oss/up.sh | 5 | パスだけ |
| tests/test_lab_debug.py | 1 | パスだけ |
| tests/test_nautobot.py | 1 | 手で: 013 の検索語（「gnmic とグラフ DB に同期」）と、017 の pathspec（`"oss",` を外す）を合わせる |
| tests/test_oss_ops.py | 3 | パスだけ |

衝突しなかったが 013 が足した文で古いパスのまま残っていた 3 か所を、PM の指示で直した。

- `ops/oss/up.sh:152`: telegraf_dialin の守りの「先に oss/ops/down.sh で消す」を `ops/oss/down.sh` にした。
- `tests/test_oss_ops.py:1097,1100`: 同じ守りの check の名前と期待する文字列。

docs の「当時のパス」の行（deploy.md:183 など）と architecture/README.md の app/gateway の行は触っていない。名前の変更（oss/ops/up.sh と down.sh → ops/oss/、tools/tools.json → app/gateway/tools.json）は git が rename として追った。

#### grep（017 の検証 2 / 4 / 8 を、マージした木で取り直した）

`grep -rn -E 'oss/ops|tools/handler|tools/tools\.json|tools/netflow_send|(^|[^/])GLOSSARY\.md' --exclude-dir=.venv --exclude-dir=.terraform --exclude-dir=.git --exclude-dir=cycles --exclude-dir=verification . | cut -d: -f1,2`:

```
./docs/oss-variant.md:66
./docs/oss-variant.md:102
./docs/deploy.md:183
./docs/architecture/README.md:7
```

上の 4 行は fcae1d2 の木で同じコマンドを打った結果と同じ 4 行。017 の design.md 検証 4 で、当時の記録として残す行。

- `git grep -n -E 'OSS (版の )?ops/(up|down)\.sh' -- . ':!docs/cycles' | wc -l` → `0`
- `git ls-files oss tools GLOSSARY.md | wc -l` → `0`
- docs/cycles と docs/verification に残る古いパス: `git grep -c -E 'oss/ops|tools/netflow_send|tools/tools\.json|tools/handler' -- docs/cycles docs/verification | awk -F: '{n+=$2; f++} END {print f" ファイル "n" 行"}'` → `44 ファイル 325 行`（この節を足す前に数えた。当時の記録なので触らない）
- 017 の検証 8 `grep -c 'created by ops/oss/up.sh' ops/oss/up.sh`: マージした木では 7、fcae1d2 では 8 だった。差の 1 は 013 のパラメータの変更による。013 は dial-in のパラメータ 3 つ（fcae1d2 の `telegraf-dialin/` の gnmi-username、gnmi-password、snmp-community）を gnmic の 2 つ（`/gnmic/gnmi-username` と `/gnmic/gnmi-password`）に置き換えている。`created by oss/ops` は 0。

```
$ grep -c 'created by ops/oss/up.sh' ops/oss/up.sh; git show fcae1d2:ops/oss/up.sh | grep -c 'created by ops/oss/up.sh'; git show fcae1d2:ops/oss/up.sh | grep -c 'telegraf-dialin/'; grep -c 'created by oss/ops' ops/oss/up.sh
7
8
3
0
```

#### 検証（マージの解消のあと。check.sh のあとに木を変えていないことを `find -newer` で確かめた）

`bash ops/check.sh`（rc=0、2881 行）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 36 本
構文エラーなし

== 4. 模擬テスト
通過 168 / 失敗 0          (test_alerts)
通過 513 / 失敗 0          (test_analytics)
通過 161 / 失敗 0          (test_app)
通過 79 / 失敗 0           (test_collectors)
通過 3 / 失敗 0            (test_dashboard_config)
通過 78 / 失敗 0           (test_graph)
通過 7 / 失敗 0            (test_kb_index)
通過 97 / 失敗 0           (test_lab_debug)
通過 138 / 失敗 0          (test_local_compose)
68 項目すべて通過             (test_nautobot)
通過 173 / 失敗 0          (test_oss)
通過 196 / 失敗 0          (test_oss_ops)
通過 66 / 失敗 0           (test_oss_roll)
通過 106 / 失敗 0          (test_stream)
通過 103 / 失敗 0          (test_sync)
通過 327 / 失敗 0          (test_workflow)

すべて通過
```

#### 範囲外で気付いたこと（直していない）

- `docs/development.md:37` の各テストの件数が、実際の件数と合わない。
  - 書いてある件数: stream 96、analytics 504、alerts 169、lab_debug 104、nautobot 69、oss_ops 194、local_compose 132。
  - 上の check.sh の実際の件数: 106、513、168、97、68、196、138。
  - 013 の設計の範囲外なので PM に報告した。
