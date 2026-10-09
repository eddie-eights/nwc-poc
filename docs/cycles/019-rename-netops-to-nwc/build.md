# build — netops の名前を nwc に揃える（019）

## Round 1

実装モデル: opus-5.5 / effort: xhigh（high へ下げる手段が無い）

基点は `docs/cycle-006-design` の 083a99a。ブランチは `feat/019-rename-netops-to-nwc`（worktree `.claude/worktrees/rename-netops-to-nwc`）。

### 実装前の件数（083a99a で `bash ops/check.sh`。実装後は 6e5d535 + tests と docs で取り直した。同じ）

| 検査 | 実装前 | 実装後 |
| :--- | ---: | ---: |
| test_alerts | 168 | 168 |
| test_analytics | 513 | 513 |
| test_app | 161 | 161 |
| test_collectors | 79 | 79 |
| test_dashboard_config | 3 | 3 |
| test_graph | 78 | 78 |
| test_kb_index | 7 | 7 |
| test_lab_debug | 97 | 97 |
| test_local_compose | 138 | 138 |
| test_nautobot | 68 項目 | 68 項目 |
| test_oss | 173 | 173 |
| test_oss_ops | 196 | 196 |
| test_oss_roll | 66 | 66 |
| test_stream | 106 | 106 |
| test_sync | 103 | 103 |
| test_workflow | 327 | 327 |

terraform fmt は差分なし、9 つのルートの validate は全部 OK、スクリプトの構文エラーなし（実装前も後も）。

### commit

| commit | 内容 |
| :--- | :--- |
| f4b57a5 | git mv だけ（17 本。中身は 1 バイトも変えない。`17 files changed, 0 insertions(+), 0 deletions(-)`） |
| 6e5d535 | 参照の書き換え（app / docker / IaC / ops / deploy.env.example / pyproject.toml。`51 files changed, 132 insertions(+), 125 deletions(-)`） |
| （この commit） | tests と docs（tests 9 本、docs 14 本、README.md）と build.md |

### 変更ファイル（`git diff --cached --name-status -M 083a99a`。この commit を含む）

```
M	IaC/terraform/aws-managed/agent/variables.tf
M	IaC/terraform/aws-managed/base/ecr/outputs.tf
M	IaC/terraform/aws-managed/pipeline/analytics/history.tf
M	IaC/terraform/aws-managed/pipeline/analytics/outputs.tf
M	IaC/terraform/aws-managed/pipeline/analytics/splunk.tf
M	IaC/terraform/aws-managed/pipeline/analytics/tables.tf
M	IaC/terraform/aws-managed/pipeline/analytics/terraform.tfvars.example
M	IaC/terraform/aws-managed/pipeline/analytics/variables.tf
M	IaC/terraform/aws-managed/pipeline/nautobot/locals.tf
M	IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf
M	IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf
M	IaC/terraform/oss/pipeline/analytics/grafana.tf
M	IaC/terraform/oss/pipeline/analytics/outputs.tf
M	README.md
M	app/containerlab/lab.sh
M	app/containerlab/lab_topology.py
M	app/containerlab/trex/kafka_load.sh
M	app/containerlab/trex/stl/udp_trap.py
M	app/dashboard/nautobot_api.py
M	app/gnmic/gnmic.yaml.in
R093	app/grafana/provisioning/alerting/netops-opensearch.yaml	app/grafana/provisioning/alerting/nwc-opensearch.yaml
R098	app/grafana/provisioning/alerting/netops-prometheus.yaml	app/grafana/provisioning/alerting/nwc-prometheus.yaml
R075	app/grafana/provisioning/alerting/netops.yaml	app/grafana/provisioning/alerting/nwc.yaml
M	app/grafana/provisioning/dashboards/logs.json
M	app/grafana/provisioning/dashboards/metrics.json
R053	app/grafana/provisioning/dashboards/netops.yaml	app/grafana/provisioning/dashboards/nwc.yaml
M	app/grafana/provisioning/datasources-oss/opensearch.yaml
M	app/grafana/provisioning/datasources-oss/prometheus.yaml
M	app/grafana/start.sh
R082	app/nautobot/jobs/netops_jobs.py	app/nautobot/jobs/nwc_jobs.py
R093	app/nautobot/netops/bootstrap.py	app/nautobot/nwc/bootstrap.py
R100	app/nautobot/netops/nb_map.py	app/nautobot/nwc/nb_map.py
R099	app/nautobot/netops/nb_sync.py	app/nautobot/nwc/nb_sync.py
M	app/spark/snmp_sinks.py
M	app/splunk/entrypoint.sh
D	app/splunk/netops_alerts/README/alert_actions.conf.spec
D	app/splunk/netops_alerts/README/savedsearches.conf.spec
R096	app/splunk/netops_alerts/bin/netops_sns.py	app/splunk/nwc_alerts/bin/nwc_sns.py
R054	app/splunk/netops_alerts/default/alert_actions.conf	app/splunk/nwc_alerts/default/alert_actions.conf
R067	app/splunk/netops_alerts/default/app.conf	app/splunk/nwc_alerts/default/app.conf
R100	app/splunk/netops_alerts/default/data/ui/alerts/netops_sns.html	app/splunk/nwc_alerts/default/data/ui/alerts/nwc_sns.html
R100	app/splunk/netops_alerts/default/props.conf	app/splunk/nwc_alerts/default/props.conf
R096	app/splunk/netops_alerts/default/savedsearches.conf	app/splunk/nwc_alerts/default/savedsearches.conf
R100	app/splunk/netops_alerts/metadata/default.meta	app/splunk/nwc_alerts/metadata/default.meta
A	app/splunk/nwc_alerts/README/alert_actions.conf.spec
A	app/splunk/nwc_alerts/README/savedsearches.conf.spec
M	app/telegraf/telegraf.conf.in
M	app/temporal/rules.py
M	deploy.env.example
M	docker/compose/README.md
M	docker/compose/check.sh
M	docker/compose/compose.yaml
M	docker/images/grafana/Dockerfile
M	docker/images/nautobot/Dockerfile
M	docker/images/splunk/Dockerfile
M	docs/alert-comparison.md
M	docs/architecture/README.md
M	docs/architecture/resources/firehose.md
M	docs/architecture/resources/grafana.md
M	docs/architecture/resources/nautobot.md
M	docs/architecture/resources/s3-tables-athena.md
M	docs/architecture/resources/splunk.md
M	docs/collection.md
A	docs/cycles/019-rename-netops-to-nwc/build.md
M	docs/data-stores.md
M	docs/deploy.md
M	docs/faq-fukuda-nwc-poc.md
M	docs/nautobot.md
M	docs/pipeline.md
M	docs/troubleshooting.md
M	ops/up-common.sh
M	ops/up.sh
M	pyproject.toml
M	tests/check_splunk_image.py
M	tests/test_alerts.py
M	tests/test_analytics.py
M	tests/test_app.py
M	tests/test_local_compose.py
M	tests/test_nautobot.py
M	tests/test_oss.py
M	tests/test_stream.py
M	tests/test_workflow.py
```

README の spec 2 本（`app/splunk/*_alerts/README/*.conf.spec`）は、基点からの通しの差分では D と A に分かれて出るが、f4b57a5 の中では R100（`git show --stat -M f4b57a5`）。

### 置換のしかた

- 中身: `sed -i '' -e 's/netops/nwc/g' -e 's/NETOPS/NWC/g' -e 's/NetOps/NWC/g'`（シンボリックリンクは飛ばす。`IaC/terraform/oss/` のリンクは触らず、実体の `aws-managed/` 側を直した）。
- 置換のあとに読み直して文として直したもの:
  - `netops_poll` の言い回し 9 か所。名前を出さない書き方にした（PM の決定）。
  - `agent/variables.tf` の例の owner（`nwc_nwc` にならないよう yamada に）。
  - `pyproject.toml` の description（「Network operations PoC: …」）。
- `tables.tf` は moved を 2 本にした（`netops[0]→netops` と `netops→nwc`。PM の案 A）。

### 実装ステップ

| # | 結果 |
| :--- | :--- |
| 1 git mv（1 commit） | f4b57a5。17 本（設計の 16 本に `default/data/ui/alerts/netops_sns.html` を足した） |
| 2 参照の書き換え | 6e5d535。`grep -rn -i netops app docker IaC ops deploy.env.example pyproject.toml` は tables.tf の moved の 3 行だけ（案 A の例外） |
| 3 tests と docs（1 commit） | この commit。docs/deploy.md と docs/architecture/README.md は netops を含む行だけ（numstat 1/1 と 3/3） |
| 4 セルフレビュー | 下の「セルフレビュー」 |

### 検証方法（この commit の内容の作業ツリー。この build.md を書く前に scratchpad の verify019.sh と check.sh を取り直した）

#### 1 bash ops/check.sh（019-final.log の要約）

```
2:== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
3:差分なし
5:== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（validate の OK の行: 18）
25:== 3. スクリプトの構文
26:bash -n: 28 本
27:構文エラーなし
29:== 4. 模擬テスト
198:通過 168 / 失敗 0
713:通過 513 / 失敗 0
1288:通過 161 / 失敗 0
1368:通過 79 / 失敗 0
1372:通過 3 / 失敗 0
1452:通過 78 / 失敗 0
1462:通過 7 / 失敗 0
1560:通過 97 / 失敗 0
1699:通過 138 / 失敗 0
1770:68 項目すべて通過
1944:通過 173 / 失敗 0
2141:通過 196 / 失敗 0
2208:通過 66 / 失敗 0
2315:通過 106 / 失敗 0
2434:通過 103 / 失敗 0
2879:通過 327 / 失敗 0
2881:すべて通過
2882:exit=0
```

#### 2 合格条件の grep と 3 terraform validate と 6 git log --follow（verify019.sh）

```
### 1 合格条件の grep（/usr/bin/grep）
./tests/test_analytics.py
./IaC/terraform/aws-managed/pipeline/analytics/tables.tf
./.git
(行数 3)
### 1b 残る行（案 A の例外）
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:54:  from = aws_s3tables_namespace.netops[0]
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:55:  to   = aws_s3tables_namespace.netops
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:61:  from = aws_s3tables_namespace.netops
tests/test_analytics.py:2225:           ("aws_s3tables_namespace.netops[0]", "aws_s3tables_namespace.netops"),
tests/test_analytics.py:2226:           ("aws_s3tables_namespace.netops", "aws_s3tables_namespace.nwc"))))
### 1c ./.git の中身
gitdir: /Users/eight/Documents/Dev/sandbox/nwc-poc/.git/worktrees/rename-netops-to-nwc
### 1d nwc_nwc / NWC_NWC の二重置換
(行数 0)
### 3 terraform validate
Success! The configuration is valid.

Success! The configuration is valid.

Success! The configuration is valid.

### 6 git log --follow app/nautobot/nwc/nb_sync.py
6e5d535 netops の名前を nwc に揃える（019）: 参照の書き換え
f4b57a5 netops の名前を nwc に揃える（019）: git mv だけ
bf6860b gNMI を gnmic で取り、dial-in をやめる（013）: 第 2 段を終える（telegraf-dialin と SNMP のポーリングを外し、gnmic を足す）
48683dd ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）commit 2/3: 参照の書き換え
c46cd7e ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）commit 1: git mv とシンボリックリンクの貼り直し
037103a Nautobot の Job を OSS 版の Neo4j につなぐ（エンジニア3、feat/nautobot-neo4j）
4bdd397 トポロジの置き場を Neptune Database から Neptune Analytics に置き換えた（問い合わせは Gremlin から openCypher。AWS では未確認）
123497a Nautobot の保守中と変更履歴を Neptune に写した（保守中の機器の異常ではワークフローを起こさない。エージェントに recent_changes）
bc734f0 Nautobot と連携した（NAUTOBOT=1。機器の一覧とケーブルの正を Nautobot に置き、Job が Telegraf と Neptune を合わせる）
### 6b git log --follow app/splunk/nwc_alerts/bin/nwc_sns.py
6e5d535 netops の名前を nwc に揃える（019）: 参照の書き換え
f4b57a5 netops の名前を nwc に揃える（019）: git mv だけ
e8eed52 名前の参照と docs と tests（011 の 4/4）
e4d45c3 ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）commit 3/3: dir_tag に Dockerfile を混ぜ、Grafana・Telegraf・Splunk の版を上げる
48683dd ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）commit 2/3: 参照の書き換え
c46cd7e ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）commit 1: git mv とシンボリックリンクの貼り直し
42e3e4c Splunk のアラートアクションは Splunk の Python が持っている boto3 を使う（同梱をやめる）
ffba169 Splunk のアラートアクションの SNS への publish を、自前の SigV4 から boto3 に替えた
4b229b4 デバッグ用の EC2（LAB_DEBUG）、lab の syslog を local7 に、SYSLOG_STANDARD（既定 RFC3164）、netops-* の名前を nwc-* に
4e2196b 検知を Spark から Grafana のアラートルールと Splunk の保存済みサーチに移した（どちらも SNS へ出す）
### 7 git diff --stat -M 083a99a（最後の行）
 81 files changed, 377 insertions(+), 367 deletions(-)
### 8 docs/cycles と docs/verification の差分（build.md を除く）
(行数 0)
### 9 deploy.md と architecture/README.md の差分の行数
3	3	docs/architecture/README.md
1	1	docs/deploy.md
```

grep に残る 3 本の内訳:

- `./.git`: worktree のポインタ（`gitdir: …/worktrees/rename-netops-to-nwc`）。`--exclude-dir=.git` はディレクトリだけを除くので、worktree では必ず当たる。
- 残り 2 本: 案 A の例外（tables.tf の moved の from 行と、それを見る test_analytics.py の検査）。

#### 4 Splunk のイメージ（linux/amd64）と `tests/check_splunk_image.py`

この検査のあと app/ と docker/ は変えていない（`git diff --stat HEAD -- app docker IaC ops` が空）。

```
# tests/check_splunk_image.py の出力（scratchpad の 019-splunk-image.log）
-- docker/images/splunk/Dockerfile と app/splunk/ を linux/amd64 でビルドして nwc-splunk-check:local にする
-- nwc-splunk-check:local を nwc-splunk-check-84250 で起こした。healthy になるのを待つ（数分）
ok Splunk が入口（app/splunk/entrypoint.sh）から起きて healthy になる（180 秒）
ok splunkd が読む設定でも python.required = 3.13（btool）
ok 偽の認証情報の口と偽の SNS がコンテナの中で動く
ok 直に: Splunk の Python 3.13 で boto3 / botocore が読め、SNS のクライアントを作れる（app に同梱していない = Splunk の site-packages のもの）
   python 3.13.11（/opt/splunk/bin/python3.13）boto3 1.37.14 botocore 1.37.14 /opt/splunk/lib/python3.13/site-packages/boto3/__init__.py
ok 直に: nwc_sns.send で偽の SNS へ 1 通届く（認証情報は偽の口から、署名つき、Query API の Publish）
-- HEC に link down（dc1-a-leaf-01 ethernet-1/1）を入れた。nwc_gnmi（毎分）が送るのを待つ
ok 本物の流れ: アラートアクションが偽の SNS へ 1 通だけ送る（次の回で重ねて送らない）
ok 本物の流れ: 本文は Grafana と同じ形の JSON（source=splunk、link_down の firing 1 件）、件名は nwc alert、form は Publish
ok 本物の流れ: splunkd は Python 3.13 で起こし、Splunk の boto3 で送っている（User-Agent: Python 3.13.11、boto3 1.37.14）
ok 本物の流れ: splunkd.log に件数（published=1/1）と exit code=0 が残る
-- bin/boto3.py を置いて、HEC に link down（ethernet-1/2）を入れた。splunkd.log に理由が出るのを待つ
ok boto3 が読めないとき: splunkd.log に理由（boto3 を読めない・Splunk の Python の版・確かめ方）が 1 行で出て、exit code=3。送らない
   n=nwc_sns STDERR -  boto3 を読めない（ModuleNotFoundError: No module named boto3 (check_splunk_image が置いた偽物)）。Splunk の Python 3.13.11（/opt/splunk/bin/python3.13）に boto3 が無い。Splunk の版を変えたなら tests/check_splunk_image.py で確かめ、無ければ boto3 を app の lib/ に同梱する（git の ffba169）
ok 見えた版が CHECKED と同じ（{'splunk': '10.4.4', 'python': '3.13.11', 'boto3': '1.37.14'}）。違うなら、上が通っているので CHECKED を書き換える（Splunk の版を変えたら Dockerfile・ops/up.sh も）
すべて通過（11 件）
exit=0
```

#### 5 Grafana と Nautobot のイメージ（linux/arm64 で `--load`。Fargate と同じ arm64。Nautobot は ops/up.sh の build_nautobot と同じ一時ディレクトリの組み方）

```
# Grafana（docker/images/grafana/Dockerfile、文脈 app/grafana）
#7 [3/4] COPY provisioning /etc/grafana/nwc
#8 [4/4] COPY --chmod=0755 start.sh /usr/local/bin/nwc-grafana
#9 naming to docker.io/library/nwc-grafana-check:local done
# Nautobot（docker/images/nautobot/Dockerfile）
#7 [2/5] COPY requirements*.txt /tmp/nwc-requirements/
#8 [4/5] COPY --chown=nautobot:nautobot jobs/ /opt/nautobot/jobs/
#9 [5/5] COPY --chown=nautobot:nautobot nwc/ graph.py toolkit.py lab_seed.json /opt/nautobot/nwc/
#10 naming to docker.io/library/nwc-nautobot-check:local done
```

### 設計からの逸脱

1. git mv が 17 本。設計の 16 本に `app/splunk/nwc_alerts/default/data/ui/alerts/netops_sns.html → nwc_sns.html` を足した（アラートアクションの画面の定義で、`[nwc_sns]` と名前で対になる）。
2. 合格条件の grep は `/usr/bin/grep` で取った（この PC のシェルの `grep` は ugrep なので、OS の grep を名指しした）。`./.git` は worktree のポインタで必ず当たる。
3. 案 A（PM の決定）: moved を 2 本残し、tables.tf の 3 行と test_analytics.py の 2 行に netops が残る。
4. test_analytics.py の moved の検査は 3 組を見るようにしたが、検査の数は 1 のまま（件数は 513 で同じ）。
5. `netops_poll` は名前を出さない言い回しに替えた（9 か所。PM の決定）。
6. `agent/variables.tf` の例の owner を yamada にした（置換すると `nwc_nwc_poc` になる）。
7. `pyproject.toml` の description は置換でなく「Network operations PoC: …」に書き直した。
8. README.md は設計の手順 2 の grep に入っているが、docs と同じ 3 本目の commit に入れた。
9. テストは `uv run pytest` でなく `bash ops/check.sh` で走らせた（tests/ は pytest のテストでなく、check.sh が 1 本ずつ uv run する作り）。
10. Grafana と Nautobot は arm64 で build した（ECS の Fargate が arm64）。amd64 は Splunk だけ（上流が amd64 だけ）。
11. `app/containerlab/lab.sh` も直した（netops を含んでいた。設計の変更対象表に無い）。
12. `IaC/terraform/oss/` の下のシンボリックリンクは触らず、リンク先の `aws-managed/` を直した。

### セルフレビュー

- 自分: opus-5.5 / effort xhigh。
- 反対弁護人: Agent general-purpose / model opus。読み取り専用で、文脈（設計の意図、置換の方法、案 A、迷った点）を渡した。
  - 返ってきたあと `git status --porcelain -uall` を取り、ステージした 24 本のほかに増えたファイルは無かった。

#### 退行の注入（1 件ずつ入れて走らせ、戻した。AWS には触らない）

| # | 入れた退行 | 落ちた検査と出力 |
| :--- | :--- | :--- |
| 1 | savedsearches.conf の `action.nwc_sns` を netops に | test_alerts。`AssertionError: どのサーチも毎分走り…` |
| 2 | Grafana の通知テンプレートの `nwc.sns` を netops に | test_alerts。`AssertionError: 連絡先は SNS（鍵は書かない = タスクロールで SigV4）。解消も送る` |
| 3 | bootstrap.py の JOBS を netops_jobs に | test_nautobot。`AssertionError: Job は名前でなくクラスの場所で引く…古い名前はコードと docs に残らない（）` |
| 4 | tables.tf の `netops→nwc` の moved を外す | test_analytics。`AssertionError: count を外したバケット・namespace は moved で…改名した namespace も moved で state のアドレスをつなぐ` |
| 5 | Firehose の sourcetype の接頭辞を netops に | test_analytics。`AssertionError: splunk_events: …sourcetype は nwc:<topic>…` |
| 5b | 5 と同じ退行で test_alerts だけ | 落ちない（`通過 168 / 失敗 0`）。5 は test_analytics が縛っている |
| 6 | alert_actions.conf の `[nwc_sns]` を `[netops_sns]` に | test_alerts。`KeyError: 'nwc_sns'` |

#### moved の 2 本が両方の古い state をつなぐか（scratchpad の moved-exp。tables.tf と同じ moved を `terraform_data` で組み、state だけ替えて plan）

```
# a: state のアドレスが terraform_data.netops[0]（count があったころ）
  # terraform_data.netops[0] has moved to terraform_data.nwc
Plan: 0 to add, 0 to change, 0 to destroy.
# b: state のアドレスが terraform_data.netops（count を外したあと）
  # terraform_data.netops has moved to terraform_data.nwc
Plan: 0 to add, 0 to change, 0 to destroy.
```

アドレスはつながる。namespace の名前（`var.namespace` の既定 `netops → nwc`）は変わるので、namespace とその中のテーブルは作り直しになる（tables.tf のコメントのとおり。設計の想定）。

#### Grafana: 旧名のイメージで立てた volume に、新名のイメージを載せる（反対弁護人の指摘 7 の実測）

旧名のイメージは Docker Hub から取れなかった（`failed to resolve source metadata … EOF`）。そこで手元の `nwc-grafana-check:local` に 083a99a の provisioning と start.sh を載せて作った（`gf-old-derive.Dockerfile`）。

```
# 旧名のイメージ、新しい volume
/api/folders: [{…,"uid":"fg0nvai1x0um8e","title":"netops","managedBy":"classic-file-provisioning"}]
nwc-metrics | netops / SNMP metrics | netops | ['netops']
nwc-logs | netops / traps and syslog | netops | ['netops']
# 同じ volume に新名のイメージ
folders: netops (fg0nvai1x0um8e), nwc (dg0nvau2augaof)
nwc-metrics | nwc / SNMP metrics | nwc | ['nwc']
nwc-logs | nwc / traps and syslog | nwc | ['nwc']
level=error の行: 0
```

ダッシュボードは uid が同じなので新しいフォルダへ移る。空の `netops` フォルダが残るだけ。

- 確かめたあと、コンテナ gf019-old と gf019-new、volume gf019-vol、イメージ gf019-old:local を消した。

#### PM の判断に回したもの

**S1 Should fix ［docs の正しさ・当時の記録の改変］**

- 場所:
  - `docs/pipeline.md:310`（「2026-10-05 の AWS で見つけた 2 つ」の `nwc_gnmi`）
  - `docs/faq-fukuda-nwc-poc.md:2182`（「聞いた時点では」の `nwc_sns.py`）
  - `docs/alert-comparison.md:33-37`・`:134`（002 の結果の「書けた（`nwc_trap`）」など。測ったときの名前は netops）
- 破綻シナリオ: 日付のついた当時の実測や経緯の記述が、当時は無かった名前に変わっている。読者がその日付のログを `nwc_gnmi` で探しても当たらない。
- 確かめたこと: 反対弁護人の挙げた行を自分で開いて読んだ（読んだだけ）。
  - 同じく挙がった `docs/alert-comparison.md:105`（Athena の例の `"nwc"."alert_events"`）と `docs/troubleshooting.md:84`（「nwc / SNMP metrics」）は今の名前を案内する行なので、置換のままで正しいとして外した。
- 直し方の案（2 つのどちらか）:
  - 置換を受け入れる。記録は今の名前で読める、とする。
  - 「当時の名前は netops」と注を足す。ただし合格条件の grep に当たるので、例外を足すことになる。
  - どちらも設計（合格条件）に関わるので、実装では直していない。
- **PM の判断（2026-10-09）: 直さない。** 設計の方針 4 が記録と定めたのは `docs/cycles/` と `docs/verification/` だけで、`docs/*.md` は現行の説明文書。review.md に PM の判断として残す。

**S2 Should fix ［運用・旧名で立てた環境をそのまま上げ直す］**

新しく立てる環境では動作は変わらない。旧名で立てて残っている環境に新しい名前を載せると、次の 3 つが起きうる。

- (a) compose の Splunk
  - 場所: `docker/compose/compose.yaml:272`・`:315`（名前つき volume `splunk-etc`）、`docker/images/splunk/Dockerfile:11`。
  - 破綻シナリオ:
    - 同じ版のまま上げると、上流の入口が etc を写し直さないので `nwc_alerts` が入らず、古い `netops_alerts` が動き続ける。
    - 版を上げると両方の app が動き、通知が 2 通出る。
  - 当てはまる範囲: compose は SNS の topic を渡さない（`compose.yaml:268`）ので、送信は失敗してログに出るだけ。ECS の Splunk は volume を持たない（`IaC/terraform/aws-managed/pipeline/analytics/splunk.tf:9`）。
  - 未再現（読んだだけ）。
- (b) Nautobot の RDS を残したまま上げる
  - 場所: `app/nautobot/nwc/bootstrap.py:66-72`、`:160`。
  - 破綻シナリオ:
    - API ユーザーの既定が `netops-web → nwc-web` に変わるので、ユーザーは新しく作られる。そこへ同じキーのトークンを作ろうとして、Token.key の一意制約で IntegrityError になる。起動は続く（ログに出して次へ）。
    - JobHook は `nwc-sync` を新しく張り、古い `netops-sync` は残る。
  - 未再現（読んだだけ）。
- (c) analytics だけを apply する
  - 場所: workflow 側が namespace を読むところ（`IaC/terraform/aws-managed/workflow/locals.tf:132` が analytics の remote state の `table_namespace` を読み、`ecs.tf:79`、`gateway.tf:214`、`proposals.tf:83` が使う）。
  - 破綻シナリオ: workflow を apply し直すまで、workflow は古い namespace を見続ける。
  - 当てはまる範囲: `ops/up.sh` を通しで打てば、analytics（`:1108`）が workflow（`:1251`）より先なので起きない。
  - 未確認（AWS に触らない）。
- 直し方の案: 「旧名で立てた環境（compose の volume、Nautobot の RDS、稼働中の AWS）は down.sh（compose は `-v`）で消してから、新しい名前で上げる」と docs/deploy.md か README に 1 行書く。
  - deploy.md は 018 と並行していて netops を含む行だけを直す約束なので、書いていない。
- **PM の判断（2026-10-09）: 直す。** `docs/deploy.md` と `docker/compose/README.md` に 1 行ずつ足した（下の「PM の判断のあとの直し」）。前の名前の字面は書かない。

#### PM の判断のあとの直し

- `docs/cycle-006-design`（d0537a1。018 の取り下げ b34e71a と、019 の合格条件の例外 723ccfd）をマージした。衝突なし。`ls docs/cycles/ | grep -c '^018'` が `0`、`grep -n 018 docs/cycles/BACKLOG.md` が空。
- S2: `docs/deploy.md` の手順の注意の最後（010 の Kafbat UI の行の次）と、`docker/compose/README.md` の「消す」の `-v` の説明の次に 1 行ずつ足した。旧名の環境は `ops/down.sh`（compose は `docker/compose/down.sh -v`）で消してから上げる、という趣旨。
- 足したあとに取り直した出力は「Round 1 の追い（PM の判断のあと）」の検証に貼る。
- 検査用のイメージ（`nwc-grafana-check:local`・`nwc-nautobot-check:local`・`nwc-splunk-check:local`）を消した（N3）。

#### 最終報告に回したもの（Nit。格下げの根拠は実行して取った）

- **N1 ［bisect］ f4b57a5 だけではイメージを組めない**
  - git mv だけの commit なので、Dockerfile は古いディレクトリを COPY したまま。
  - 3 本に分けるのは PM の指示。HEAD では解消している（検証 4・5）。
  - 実行した確認:
    ```
    $ git show f4b57a5:docker/images/splunk/Dockerfile | grep -n COPY
    11:COPY --chown=splunk:splunk netops_alerts /opt/splunk-etc/apps/netops_alerts
    $ git ls-tree --name-only f4b57a5 app/splunk/
    app/splunk/entrypoint.sh
    app/splunk/nwc_alerts
    app/splunk/peers_check.py
    ```
- **N2 ［見た目］ Grafana の空の `netops` フォルダ**
  - 旧名で立てた volume にだけ残る。エラーは 0（上の実測）。
  - ECS の Grafana は volume を持たず、起動のたびに作り直す。
- **N3 ［後片付け］ 検査で作ったイメージ**
  - 対象は `nwc-grafana-check:local`、`nwc-nautobot-check:local`、`nwc-splunk-check:local`。
  - この commit のあと `docker rmi` で消す。

#### 反対弁護人の指摘で取り消した自分の結論

- 「名前だけなので動作は変わらない」を、「新しく立てる環境では変わらない。旧名で立てて残っている環境では変わりうる（S2）」に狭めた。
- 指摘 1「tests と docs が commit されておらず、HEAD（6e5d535）だけでは検査が落ちる」は、3 本目のこの commit で解消する（もとから 3 本に分ける計画）。
  - この commit のあと `git status` が空で、検証 1 が通ることで確かめた。

#### 問題なしとした観点と根拠

- 名前の残り
  - 合格条件の grep と、`grep -rn -i netops` の残る行（検証 2）。
  - `nwc_nwc`・`nwc-nwc-poc`・`nwcnwc` の二重置換は 0 行（検証 2 の 1d）。
- 振る舞いが同じ
  - 16 本の検査の件数が実装前と同じ（上の表）。
  - 退行の注入 6 件が、それぞれの検査で落ちる（5b は縛っている検査を確かめた）。
- Splunk の 3 つの名前
  - app の名前、alert_actions の stanza、savedsearches の `action.*` が揃っている。
  - 実物で確かめた（検証 4 の「本物の流れ」で送信が 1 通届く）。
- Grafana と Nautobot のイメージ
  - build が通る（検証 5）。
  - ダッシュボードの uid は変えていない（`nwc-metrics` / `nwc-logs`。上の実測）。
- Terraform
  - 3 つの validate が Success（検証 2）。
  - moved の 2 本が両方の古いアドレスをつなぐ（moved-exp の plan）。
- 履歴
  - `git log --follow` が nb_sync.py と nwc_sns.py の改名前までたどれる（検証 2 の 6・6b）。
- 範囲
  - `docs/cycles/` と `docs/verification/` の差分は build.md だけ（検証 2 の 8）。
  - deploy.md と architecture/README.md は netops を含む行だけ（numstat 1/1・3/3）。
  - AWS には触っていない。
- 反対弁護人が反証できなかったもの
  - netops が残るのは tables.tf:54,55,61 と test_analytics.py:2225-2226 だけ。
  - sourcetype の接頭辞はサーチに効かない（サーチは `source=` で絞る）。
  - ENV_FILE は変わっていない。
  - AWS の資源名で変わるのは namespace だけ。
  - 検査は同語反復になっていない（注入で落ちる）。

### Round 1 の追い（PM の判断のあと）の検証

d0537a1 のマージと S2 の 2 行のあと、同じ作業ツリーで取り直した。

```
$ grep -rli netops --exclude-dir=.git --exclude-dir=.terraform --exclude-dir=.venv . | grep -v -e '^./docs/cycles/' -e '^./docs/verification/'
./tests/test_analytics.py
./IaC/terraform/aws-managed/pipeline/analytics/tables.tf
./.git
$ grep -rn -i netops IaC/terraform/aws-managed/pipeline/analytics/tables.tf tests/test_analytics.py
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:54:  from = aws_s3tables_namespace.netops[0]
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:55:  to   = aws_s3tables_namespace.netops
IaC/terraform/aws-managed/pipeline/analytics/tables.tf:61:  from = aws_s3tables_namespace.netops
tests/test_analytics.py:2225:           ("aws_s3tables_namespace.netops[0]", "aws_s3tables_namespace.netops"),
tests/test_analytics.py:2226:           ("aws_s3tables_namespace.netops", "aws_s3tables_namespace.nwc"))))
$ bash ops/check.sh   # 終了コード 0。件数は実装前と同じ
198:通過 168 / 失敗 0
713:通過 513 / 失敗 0
1288:通過 161 / 失敗 0
1368:通過 79 / 失敗 0
1372:通過 3 / 失敗 0
1452:通過 78 / 失敗 0
1462:通過 7 / 失敗 0
1560:通過 97 / 失敗 0
1699:通過 138 / 失敗 0
1770:68 項目すべて通過
1944:通過 173 / 失敗 0
2141:通過 196 / 失敗 0
2208:通過 66 / 失敗 0
2315:通過 106 / 失敗 0
2434:通過 103 / 失敗 0
2879:通過 327 / 失敗 0
2881:すべて通過
```

- `./.git` は worktree のポインタのファイル。
- イメージの build と `tests/check_splunk_image.py` は取り直していない（app・docker・IaC は 5162693 から変えていない。docs 2 本と build.md だけ）。
