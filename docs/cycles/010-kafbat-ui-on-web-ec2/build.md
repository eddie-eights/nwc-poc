# Cycle 010 kafbat-ui-on-web-ec2 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: xhigh

エンジニア2（PM の指示）。ブランチ `feat/kafbat-ui-on-web-ec2`（`docs/cycle-006-design` の 895cdb0 から）。AWS には何も立てていない。

### commit

| commit | 内容 | 規模 |
|---|---|---|
| 3c3e6c0 | 1: stream と base/core の Terraform（kafka_ui.tf を SSM の String 3 本 + Web のロールのポリシーに、Cloud Map の名前空間を OSS 版の kafka.tf へ、web.tf / variables / SG / oss.tf、user_data） | 14 files |
| abaedce | 2: ops と費用（up.sh の COST_CENTS=4、deploy.env.example、oss/ops の文言、tfvars.example） | 5 files |
| 556045e | 3: lab の graph（lab.sh の graph / graph-stop / down、lab の outputs.tf、ヘルプの行番号に合わせて test_alerts） | 3 files |
| 15b3de9 | 4: tests と docs | 21 files |
| 6eeed4b | セルフレビューの Should fix 2（tests/test_stream.py。user_data のスクリプトを書いて直に起こす、ECR / pull の失敗、http_tokens、Wants。81 → 83 件） | 1 file |

### 設計から逸脱した点

- `lab.sh graph` は `clab graph` ではなく `containerlab graph` を呼び、`--setenv=CLAB_VERSION_CHECK=disable` を渡す（`clab` は lab.sh のシェル関数で systemd-run の一時ユニットからは見えず、lab.sh の `export` も届かない）。`WorkingDirectory` は `$SRC` ではなく `$PWD`（lab.sh に `$SRC` は無く、自分の src に cd 済み）。検証 8 の期待はこの形で読む
- `graph-stop` は `NAME_PREFIX` か `systemctl` が無ければ何もしない（手元の compose の `lab down` を壊さない）。ヘルプが 1 行増え、test_alerts のヘルプの行番号の検査を手順 3 で直した
- Kafbat UI のユニットに `Wants=network-online.target`、スクリプトに `docker pull --quiet` と、`docker run` の前の `docker rm --force <prefix>-kafka-ui`、`docker run` は `exec`
- 検証 3 の `git grep 'service_discovery' IaC/terraform/aws-managed` は analytics（Splunk / Grafana）と nautobot の Cloud Map も拾って 0 件にならない（範囲外）。stream に絞って 0 件を見た
- `oss/ops/roll-nodes.sh` は `aws_ecs_service.$kind[` の汎用の形で kafka_ui の記述が無く、変更なし。`T_KIND=kafka_ui` は tests/test_oss_roll.py の例だけで、telegraf_dialin に替えた
- 設計の一覧に無いものも直した: tests（test_oss の Cloud Map と web → kafka 9092、test_analytics の費用と SG の数、test_stream の Fargate が無いこと、test_oss_roll、test_alerts のヘルプ）、docs（README.md、docs/setup.md、docs/pipeline.md）、コメントと説明文だけ（perimeter.tf、base/core の outputs.tf、terraform.tfvars.example、ecr/main.tf、oss/ops/up.sh・down.sh、up.sh:3 と deploy.env.example:26 の土台の金額、Web のロールの description に ECR）
- lab の図の段落は docs/pipeline.md の「lab に入る」に置いた（docs/lab.md は無く、deploy.md に lab の節が無い）
- up.sh:64 の Kafbat UI の行は消さずに書き直した（stream を作る回はいつも作ること・スイッチが無いことは残す）
- deploy.env.example の SKIP_STREAM で下がる額を $1.80 → $1.78/h（Kafbat UI の 2 セントが stream から土台へ移るため。test_analytics の `_pipeline_cents` と同じ計算）

### 未確定事項 1: `containerlab graph` の画面は CDN を読まない（containerlab v0.79.0 のソース）

`raw.githubusercontent.com/srl-labs/containerlab/v0.79.0/` から `cmd/graph.go`、`core/graph.go`、`core/graph_templates/` を取り、`api.github.com/repos/srl-labs/containerlab/git/trees/v0.79.0?recursive=1` でファイルの一覧を取った（AWS では確かめていない）。

```
$ sed -n 321,325p core/graph.go
//go:embed graph_templates/nextui/nextui.html
var defaultTemplate string

//go:embed graph_templates/nextui/static
var defaultStatic embed.FS
$ sed -n 339,361p core/graph.go
	if staticDir != "" && tmpl == "" {
		return fmt.Errorf("the --static-dir flag must be used with the --template flag")
	}

	var staticFS http.FileSystem

	if staticDir == "" {
		// extract the sub fs with static files from the embedded fs
		subFS, err := fs.Sub(defaultStatic, "graph_templates/nextui/static")
		if err != nil {
			return err
		}

		staticFS = http.FS(subFS)
	} else {
		log.Infof("Serving static files from directory: %s", staticDir)

		staticFS = http.Dir(staticDir)
	}

	svr := http.FileServer(noListFs{staticFS})
	http.Handle("/static/", http.StripPrefix("/static/", svr))
$ grep -n 'src=\|href=' core/graph_templates/nextui/nextui.html
8:    <link rel="stylesheet" href="static/css/tailwind.css">
9:    <link rel="stylesheet" href="static/css/next.css">
42:    <script src="static/js/next.js"></script>
43:    <script src="static/js/script.js"></script>
$ grep -o 'url([^)]*)' core/graph_templates/nextui/static/css/next.css | sort -u
url(../fonts/ciscosansextralight-webfont.eot)
url(../fonts/ciscosansextralight-webfont.eot?#iefix)
url(../fonts/ciscosansextralight-webfont.svg#CiscoSansExtraLight)
url(../fonts/ciscosansextralight-webfont.ttf)
url(../fonts/ciscosansextralight-webfont.woff)
url(../fonts/ciscosansregular-webfont.eot)
url(../fonts/ciscosansregular-webfont.eot?#iefix)
url(../fonts/ciscosansregular-webfont.svg#CiscoSansReg)
url(../fonts/ciscosansregular-webfont.ttf)
url(../fonts/ciscosansregular-webfont.woff)
$ # ツリーの core/graph_templates/nextui/static/fonts/ に上の 10 個（と next-font.*、*.otf）がある = embed に入る
$ grep -rnoE 'https?://[^"'"'"' )]+' core/graph_templates | sed 's/:\([0-9]*\):/ L\1 /' | sort -u
core/graph_templates/nextui/static/css/tailwind.css L1 https://tailwindcss.com*/*,:after,:before{box-sizing:border-box;border:0
core/graph_templates/nextui/static/js/next.js L10079 http://www.w3.org/1999/xlink
（next.js の残り 20 行も w3.org の名前空間（SVG / XLink / XHTML）か、コメントの出典 URL（L3589 wikipedia、L4841 github yui、L6244 nwbox、L8057-8059 requestAnimationFrame の解説、L9055 easing の解説））
$ grep -n 'loadScript' core/graph_templates/nextui/static/js/next.js
7788:            loadScript: function (url, callback) {
$ sed -n 144,160p cmd/graph.go
	var containers []clabruntime.GenericContainer
	// if offline mode is not enforced, list containers matching lab name
	if !o.Graph.Offline {
		containers, err = c.ListContainers(ctx,
			clabcore.WithListLabName(c.Config.Name))
		if err != nil {
			return err
		}

		log.Debugf("found %d containers", len(containers))
	}

	switch {
	case len(containers) == 0:
		c.BuildGraphFromTopo(&gtopo)
	case len(containers) > 0:
		c.BuildGraphFromDeployedLab(&gtopo, containers)
```

- 画面（nextui.html）が読むのは `static/` の 4 つだけで、`static/` はバイナリに `go:embed` で入り、`/static/` で配られる。CSS の `url()` は `../fonts/` だけで、フォントも embed に入る。外部 URL は tailwind.css の先頭のライセンスのコメントと、next.js の SVG / XLink の名前空間とコメントだけ。next.js の `loadScript` は定義だけで呼び元が無い
- **結論: CDN は読まない。VPC の中（インターネットに出ない lab の EC2）でも白紙にならない見込み。** `--static-dir` などの代替は要らないので BACKLOG の候補にはしない。lab を deploy していなくても `topology` の定義から図を描く（containers が 0 なら `BuildGraphFromTopo`）
- AWS での画面の確認は検証 10 で PM がまとめてやる

### 検証（6eeed4b のあと、未コミットは build.md だけの状態で 1〜9 を取り直した）

#### 1. `bash ops/check.sh`

```
$ git rev-parse --short HEAD   # 未コミットは build.md だけ
6eeed4b
$ uv sync --group dev --group web && bash ops/check.sh; echo rc=$?
Resolved 86 packages in 3ms
Audited 82 packages in 2ms

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

== 3. ops スクリプトの構文
構文エラーなし

== 4. 模擬テスト
（ここから tests/test_*.py 15 本の出力が約 2550 行。各テストの最後の集計行だけ抜く: grep -E '^通過 |項目すべて通過'）
通過 137 / 失敗 0
通過 489 / 失敗 0
通過 158 / 失敗 0
通過 3 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 91 / 失敗 0
通過 81 / 失敗 0
68 項目すべて通過
通過 167 / 失敗 0
通過 145 / 失敗 0
通過 66 / 失敗 0
通過 83 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0

すべて通過
rc=0
```

#### 2. terraform validate（4 つのルート）

```
$ terraform validate x4
aws-managed/pipeline/stream: Success! The configuration is valid. 
oss/pipeline/stream: Success! The configuration is valid. 
aws-managed/base/core: Success! The configuration is valid. 
oss/base/core: Success! The configuration is valid. 
```

#### 3. `service_discovery`（stream に絞った。設計から逸脱した点）

```
$ git grep -n 'service_discovery' IaC/terraform/aws-managed | wc -l
      17
$ git grep -n 'service_discovery' IaC/terraform/aws-managed | cut -d: -f1 | sort | uniq -c
   1 IaC/terraform/aws-managed/pipeline/analytics/ecs.tf
   3 IaC/terraform/aws-managed/pipeline/analytics/grafana.tf
   9 IaC/terraform/aws-managed/pipeline/analytics/splunk.tf
   4 IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf
$ git grep -n 'service_discovery' IaC/terraform/aws-managed/pipeline/stream | wc -l
       0
$ git grep -n 'service_discovery' IaC/terraform/oss/pipeline/stream/kafka.tf
IaC/terraform/oss/pipeline/stream/kafka.tf:132:resource "aws_service_discovery_private_dns_namespace" "stream" {
IaC/terraform/oss/pipeline/stream/kafka.tf:138:resource "aws_service_discovery_service" "kafka" {
IaC/terraform/oss/pipeline/stream/kafka.tf:147:    namespace_id   = aws_service_discovery_private_dns_namespace.stream.id
IaC/terraform/oss/pipeline/stream/kafka.tf:277:    registry_arn = aws_service_discovery_service.kafka[each.key].arn
```

#### 4. `aws_ecs|Fargate` in kafka_ui.tf

```
$ git grep -n 'aws_ecs\|Fargate' IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf | wc -l
       0
```

#### 5. SG

```
$ grep -n 'from = "web", to = "msk"' IaC/terraform/aws-managed/base/core/security_groups.tf
75:      { from = "web", to = "msk", protocol = "tcp", port = 9098, why = "Kafka IAM - Kafbat UI on the web EC2" },
$ grep -c kafka_ui IaC/terraform/aws-managed/base/core/security_groups.tf
0
```

#### 6. user_data の ASCII

```
$ python3 -c "import sys;[print(i,l) for i,l in enumerate(open('IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl').read().splitlines(),1) if not l.lstrip().startswith('#') and not l.isascii()]"
rc=0 (出力なし)
```

#### 7. user_data の `bash -n`（tests/test_stream.py。templatefile で描いたシェルの部分）

```
$ uv run --quiet --group dev --group web python tests/test_stream.py | grep -n 'bash -n\|^通過'
72:ok user_data のシェルの部分は描いたあと bash -n が通る（マネージド版と OSS 版の両方。{'': (0, False, False), 'neo4j': (0, False, False)}）
84:通過 83 / 失敗 0
```

#### 8. `lab.sh graph` の偽コマンド

scratchpad の `graphrun.sh`（偽の systemd-run / systemctl / containerlab / curl を PATH の先に置いて lab.sh を打つ。`systemctl is-active` は 3 を返す）:

```
$ NAME_PREFIX=x-nwc-poc AWS_REGION=ap-northeast-1 lab.sh graph
手元の PC で打つ（AWS CLI v2 + Session Manager plugin。IaC/terraform/aws-managed/pipeline/lab の output graph_port_forward_command と同じ）:
  aws ssm start-session --region ap-northeast-1 --target i-0123456789abcdef0 --document-name AWS-StartPortForwardingSession --parameters portNumber=50080,localPortNumber=50080
ブラウザで http://localhost:50080/ を開く。開けなければ 'sudo systemctl status x-nwc-poc-lab-graph'。止めるのは 'sudo lab graph-stop'
rc=0
-- 偽コマンドが受けた引数
systemctl is-active --quiet x-nwc-poc-lab-graph
systemd-run --unit=x-nwc-poc-lab-graph --collect --property=WorkingDirectory=<tmp> --setenv=CLAB_VERSION_CHECK=disable containerlab graph -t splab.clab.yml --srv 127.0.0.1:50080
curl -sf -m 2 -X PUT -H X-aws-ec2-metadata-token-ttl-seconds: 60 http://169.254.169.254/latest/api/token
curl -sf -m 2 -H X-aws-ec2-metadata-token: tok http://169.254.169.254/latest/meta-data/instance-id
$ NAME_PREFIX=x-nwc-poc AWS_REGION=ap-northeast-1 lab.sh graph-stop
rc=0
-- 偽コマンドが受けた引数
systemctl stop x-nwc-poc-lab-graph
$ NAME_PREFIX=x-nwc-poc AWS_REGION=ap-northeast-1 lab.sh down
rc=0
-- 偽コマンドが受けた引数
systemctl stop x-nwc-poc-lab-graph
containerlab destroy -t splab.clab.yml --cleanup
```

同じことを tests/test_lab_debug.py が検査している:

```
$ uv run --quiet --group dev --group web python tests/test_lab_debug.py | grep 'lab.sh graph\|lab.sh down\|graph_port\|^通過'
ok lab.sh graph: systemd-run の一時ユニット（<接頭辞>-lab-graph）で containerlab graph を 127.0.0.1:50080 で起こし、手元で打つポートフォワードのコマンドを出す
ok lab.sh graph: もう動いていれば systemd-run を打たず（同じ名前のユニットは作れない）、案内とコマンドだけ出す
ok lab.sh graph: NAME_PREFIX が無い（/etc/*-lab.env の無い手元）なら何も起こさずに止まる
ok lab.sh graph-stop: systemctl stop <接頭辞>-lab-graph を打つ（動いていなくても失敗にしない）
ok lab.sh down: graph-stop で図を止めてから containerlab destroy する
ok lab.sh down: NAME_PREFIX が無ければ（手元の compose）systemctl を打たずに destroy だけ
ok lab の output graph_port_forward_command は lab.sh graph が出すコマンドと同じ（宛先は aws_instance.lab.id、ポートは lab.sh の GRAPH_PORT）
通過 91 / 失敗 0
```

#### 9. `COST_CENTS` と tests/test_analytics.py

```
$ grep -n '^COST_CENTS=' ops/up.sh
561:COST_CENTS=4
562:COST_CENTS=$((COST_CENTS + ($(endpoint_count) * 14 * ENDPOINTS_AZ_NUM + 5) / 10))
$ uv run --quiet --group dev --group web python tests/test_analytics.py | grep '費用\|^通過'
[snmp_sinks] splunk: HEC が 400 を返した。3 件を捨てる: '{"text":"Invalid token"}'
ok 費用の目安にエンドポイント（1 本 1.4 セント × ENDPOINTS_AZ_NUM）を足す
ok 費用の目安: 土台は Web の EC2（t4g.medium。cycle 010 から Kafbat UI も同居）の 4 セント（NAT Gateway は無い）。ECS の Splunk はタスク 1 つ 12.3（SPLUNK_TASKS 個）、Grafana は 2
ok STORES は deploy.env を読んだあと、前のキーの検査のすぐ後で読み、Grafana・WORKFLOW の送り手・費用の検査の前に格納先の変数へ写す
ok deploy.env.example は STORES を既定の s3,grafana,splunk で書き、その前にまとまりごとの中身・外すと無くなるもの・費用と、外すとデータごと消えることを書く
ok deploy.env.example の PIPELINE=1 の金額（既定の STORES / STORES=s3 / SKIP_STREAM / SKIP_ANALYTICS で下がる分）が up.sh の費用の目安と同じ
ok up.sh は lab が無ければ lab の syslog の注意・7-3b のトポロジの投入・lab の費用・転送・最後の lab の案内を飛ばす
ok Runtime: ENDPOINTS_AZ_NUM を書いていなければ Runtime の数まで上げ、上げたこととエンドポイントの費用が増えることを 1 行出す（注意は出ない）
ok AZ_NUM の検査は deploy.env を読んだあと、aws を呼ぶ前・費用の目安より前（何も作る前）
ok 費用: エンドポイントは 1.4 × 本数 × ENDPOINTS_AZ_NUM（2 本で 1 AZ 3、3 AZ 8）。土台は Web の EC2 の 4
ok 費用: MSK は 2 AZ で 57、3 AZ で +27。Telegraf は受ける側のタスクが AZ ごとに増える（1 AZ 5、3 AZ 7）。Kafbat UI は cycle 010 から土台（Web の EC2）に入っていて stream では足さない
ok 費用: Neptune は 58 × NEPTUNE_AZ_NUM、Nautobot は Multi-AZ で 13 → 16
ok 費用: OpenSearch の OCU は 33 × OPENSEARCH_AZ_NUM（KB も logs も）、OpenSearch Serverless のエンドポイントは 1.4 × ENDPOINTS_AZ_NUM（1 AZ 1、2 AZ 3）
ok 費用: EMR / Lambda / Runtime の AZ_NUM では変わらない。AZ をまたぐ転送料は入れず、AZ_NUM を書いたときに 1 行出す
ok 費用: Spark のジョブは S3 / Splunk / OpenSearch か Prometheus で 1 つずつ 21（3 つで 63）
ok 費用: OpenSearch と Prometheus は 1 つのジョブ（OpenSearch の OCU 33 は別）。Splunk は ECS の 12 も足す。SKIP_ANALYTICS なら 0
ok 費用: Splunk のクラスター（SPLUNK_AZ_NUM が 2 / 3 でタスク 4 / 5）は ECS の Splunk が 49 / 61（タスク 1 つ 12.3）
ok 費用の Grafana の 2 は導いた値で数える（STORES=grafana なら 21 + 33 + 2、STORES=s3 なら Grafana は無く 21、3 つとも入れると 63 + 33 + 12 + 2）
通過 489 / 失敗 0
```

#### 10. AWS

未実行（PM がまとめて `ops/up.sh` で立てて確かめる）。未確定事項 2（hop limit 2 で Docker の bridge から IMDSv2 に届くか）、4（t4g.medium のメモリ）、1 の画面の実物もそこで確かめる。

### セルフレビュー

- 自分: claude-opus-5-5 / effort xhigh。入力は design.md と 895cdb0..15b3de9 のコードだけ
- 反対弁護人: Agent（general-purpose、model opus、文脈あり・読み取り専用）。結果は Must fix 0 / Should fix 2 / Nit 8。返ったあとの `git status --porcelain -uall` は `?? docs/cycles/010-kafbat-ui-on-web-ec2/build.md` だけ

#### 自分で入れた退行（scratchpad の inject.py。15 個とも該当のテストが rc=1 で落ちる）

```
## down が graph-stop を呼ばない（test_lab_debug）rc=1
## graph が 0.0.0.0 で待つ（test_lab_debug）rc=1
## lab の output のポートが lab.sh とずれる（test_lab_debug）rc=1
## hop limit を 1 に戻す（test_stream）rc=1
## umask 077 を消す（test_stream）rc=1
## ExecStopPost を消す（test_stream）rc=1
## 8082 を全アドレスで開ける（test_stream）rc=1
## 読めないとき 75 でなく 1 で終わる（test_stream）rc=1
## SASL の 3 行を PLAINTEXT でも書く（test_stream）rc=1
## パスワードを echo する（test_stream）rc=1
## SG の web -> msk 9098 を消す（test_analytics）rc=1
## 土台の費用を 2 に戻す（test_analytics）rc=1
## コメント以外に日本語を書く（test_workflow）rc=1
## ECR の pull を全リポジトリに広げる（test_stream）rc=1
## Kafbat UI の部分を exit 0 の後ろへ（test_stream）rc=1
git status: (clean)
```

#### 指摘と片付け

| # | 分類 | [観点] | 場所 | 片付け |
|---|---|---|---|---|
| 1 | Should fix | security | web.tf:109-110、web_user_data.sh.tftpl:57、stream/kafka_ui.tf:67、design.md:36・115 | 設計側。PM に判断を上げる（直していない） |
| 2 | Should fix | missing tests | web_user_data.sh.tftpl:29-30・54-55・59、tests/test_stream.py | 直した（6eeed4b） |
| 3 | Nit | missing tests | web_user_data.sh.tftpl:78、app/containerlab/lab.sh（ヘルプ・graph の render）、oss/pipeline/stream/kafka.tf:132 | 記録だけ |
| 4 | Nit | runtime | web_user_data.sh.tftpl:37-57、docs/troubleshooting.md:93 | 記録だけ（BACKLOG 候補） |
| 5 | Nit | runtime | web_user_data.sh.tftpl:27-28 | 記録だけ |
| 6 | Nit | API compatibility | web_user_data.sh.tftpl:44、design.md:51 | 記録だけ（PM へ） |
| 7 | Nit | 保守性 | app/containerlab/lab.sh:150・158 | 記録だけ（BACKLOG 候補） |
| 8 | Nit | 保守性 | web_user_data.sh.tftpl:37-40 | 記録だけ（BACKLOG 候補） |
| 9 | Nit | docs の正確さ | web_user_data.sh.tftpl:57、tests/test_stream.py:402 のチェック名 | 記録だけ |
| 10 | Nit | 保守性 | ops/up.sh:506-507 | 記録だけ（BACKLOG 候補） |

**1. Should fix [security] Kafbat UI と Gradio が Web のインスタンスロールを共有する**

- 破綻シナリオ: Kafbat UI（RCE の前歴あり。v1.5.0 で既知のものは塞がり、DYNAMIC_CONFIG も切っている）が乗っ取られると、hop limit 2 の IMDSv2 から Web のロールの資格情報が取れる。決定のキューへの `sqs:SendMessage`（workflow/proposals.tf:9 の「送れるのは Web の EC2 のロールだけ」という HITL の境界）、`ssm:GetParameter parameter/<prefix>/*` の SecureString、Neptune の書き込み、InvokeAgentRuntime まで届く。逆に Gradio（と EC2 上のどのプロセスも）が kafka_ui_web の MSK のトピックの書き込み・削除を持つ。ECS のときのタスクロールは kafka-cluster だけだった
- 確かめたこと: 読んだだけ（Web のロールの inline ポリシーは 7 本: web-assets、invoke-runtime、workflow-access、workflow-web、stream-parameters-read、graph-access、kafka-ui）
- 重大度の理由: いまは壊れていない。開き方は 127.0.0.1 と SSM のポートフォワードだけで、ログインフォームがある。SSM で入れる人はもともとシェルを持つ。ただし design.md のリスク 2 は「届くか」しか見ておらず、権限の広がりは評価されていない
- 片付け: design.md:36 が「Web のロールに付ける」と決めているので、実装で吸収しない。PM に判断を上げる。選択肢は (a) リスクとして受け入れて design.md と proposals.tf:9 のコメントに書く、(b) Kafbat UI 専用のロールを `awsRoleArn`（AssumeRole）で使う、(c) user_data で `DOCKER-USER` に 172.17.0.0/16 からの外向きの許可リスト（MSK 9098・IMDS・VPC の DNS だけ）、(d) Web のロールに付ける MSK の権限を読み取りに絞る

**2. Should fix [missing tests] chmod・shebang・set -euo pipefail を消してもテストが通る**

- 破綻シナリオ: `cat >` で書いたファイルは 0644。chmod が無い、または shebang が無いと systemd の exec が 203/EXEC か exec format error で落ち、Restart=always で 30 秒ごとに失敗し続けて Kafbat UI が一度も起きない。`set -e` が無いと ECR のログインや pull が落ちても古いイメージ（か無いイメージ）で `docker run` まで進む。テストがスクリプトを `bash script` で動かしていたので縛れていなかった
- 再現（直す前。scratchpad の self/mut.py を `git ls-files` の写しで回した）:

```
[0] chmod 0755 を消す: 素通り rc=0 通過 81 / 失敗 0
[1] スクリプトの shebang を消す: 素通り rc=0 通過 81 / 失敗 0
[2] スクリプトの set -euo pipefail を消す: 素通り rc=0 通過 81 / 失敗 0
[3] スクリプトの pipefail だけ消す: 素通り rc=0 通過 81 / 失敗 0
[4] http_tokens を optional に: 素通り rc=0 通過 81 / 失敗 0
[5] Wants=network-online.target を消す: 素通り rc=0 通過 81 / 失敗 0
[6] docker pull の失敗を無視: 素通り rc=0 通過 81 / 失敗 0
[7] ECR のログインの失敗を無視: 素通り rc=0 通過 81 / 失敗 0
[8] heredoc のクォートを外す（$ が user_data の時点で展開される）: 検出 rc=1
```

- 直したこと（6eeed4b）: テストは user_data の「`cat > … <<'__KAFKA_UI__'` から chmod まで」を置き場所だけ差し替えてそのまま打ち、bash を挟まずにスクリプトを直に起こす。ECR のログイン / docker login / docker pull が落ちたら `docker run` しないことを足した。反対弁護人の Nit 3 のうち http_tokens と Wants と `pull || true` もここで縛った
- 直したあと（同じハーネス）:

```
[0] chmod 0755 を消す: 検出 rc=1
[1] スクリプトの shebang を消す: 検出 rc=1
[2] スクリプトの set -euo pipefail を消す: 検出 rc=1
[3] スクリプトの pipefail だけ消す: 検出 rc=1
[4] http_tokens を optional に: 検出 rc=1
[5] Wants=network-online.target を消す: 検出 rc=1
[6] docker pull の失敗を無視: 検出 rc=1
[7] ECR のログインの失敗を無視: 検出 rc=1
[8] heredoc のクォートを外す（$ が user_data の時点で展開される）: 検出 rc=1
```

**3〜10. Nit（直さない）**

- 3: 反対弁護人の 30 個の改変のうち、まだ素通りするもの: `systemctl enable --now <prefix>-kafka-ui` を s3 sync の後ろへ動かす（exit 0 の後ろへ動かすのは検出される）、lab のヘルプから graph の行を消す、graph の前の `render` を消す、OSS 版の名前空間の description を変える
- 4: SSM の image を変えても、走っているコンテナは restart か reboot まで旧版のまま（スクリプトは起動時に 1 回読むだけ。ECS のときはタスク定義の更新で入れ替わった）。docs/troubleshooting.md はパスワードを変えたときの restart しか書いていない
- 5: `dnf install -y docker` か `systemctl enable --now docker` が落ちると、`set -e` で user_data がそこで終わり、Gradio の s3 sync まで行かない（反対弁護人が偽の dnf で再現: rc=1、`aws s3 sync` は呼ばれない）。その前の行（:21）の python3.13 の dnf も同じ落ち方をするので、新しい種類の失敗ではない
- 6: クラスター名を `<prefix>-stream` から `<prefix>` にしたのは「画面に出る名前だけ」（design.md:51）ではない。Kafbat UI の REST の `/api/clusters/{name}/…` の識別子でもある。docs/verification/20261008-oss-aws.md:117・419 とプロジェクトのメモリ oss-aws-check-technique.md（`kafka-ui.<名前空間>:8080` と `efukuda-nwc-oss-stream`）が古くなる。記録だけで、メモリは PM に伝える
- 7: `--collect` なので containerlab graph がすぐ落ちると一時ユニットが消え、案内の `sudo systemctl status <prefix>-lab-graph` は「could not be found」になる。`journalctl -u` なら見える。起動の成否を見ずに案内を出す
- 8: exit 75 の文言は AccessDenied やエンドポイントに届かないときも「stream がまだ無い」と言う（aws CLI の stderr は journald に残る）
- 9: パスワードは /run の env ファイルのほか、コンテナがある間は Config.Env（`/var/lib/docker/containers/<id>/config.v2.json`、root だけ）にも残る。tests/test_stream.py:402 のチェック名の「/run … にだけ書いて」は言い過ぎ。docs/pipeline.md:171 は誤りではない
- 10: ops/up.sh:506-507 の stream の ECR エンドポイントのコメントが Telegraf だけで、Web の EC2 の Kafbat UI の pull も使うことが書いていない

#### 問題なしとした観点

| 観点 | 根拠 |
|---|---|
| 再起動のときのスクリプトの書き直しと起動の競合 | 読んだだけ。書く中身は毎回同じで、user_data が変わればインスタンスは作り直し（web.tf:101 `user_data_replace_on_change`）。書きかけを読んで落ちても Restart=always で 30 秒後に戻る |
| 停止の順序（ExecStop が無い） | 未計測。docker run の CLI は SIGTERM をコンテナへ転送し（sig-proxy の既定）、`After=docker.service` で停止は逆順。残っても次の起動の `docker rm --force`（tftpl:56）が消す。反対弁護人も反証できず |
| Web のロールの inline ポリシーの合計 | 反対弁護人が scratchpad の size.py で数えた: 7 本で 4332 文字（上限 10240） |
| SSM のポートフォワードが 127.0.0.1:8082 に届く | 読んだだけ。Gradio の 127.0.0.1:8080 と同じ形（base/core outputs.tf:8） |
| ECR のエンドポイント | 読んだだけ。マネージド版は stream と一緒にでき（ops/up.sh:507）、OSS 版は固定の一覧（oss/ops/up.sh:151） |
| Cloud Map の名前空間の移動 | 読んだだけ。同じ root の中の移動でアドレス・名前・description が同じ。validate は検証 2 |
| SKIP_STREAM=1 で 30 秒ごとに再試行し続ける | 欠陥ではない（docs に書いてある）。止める手段が無いのは BACKLOG の候補 |
| VPC と Docker の bridge の CIDR | 10.0.0.0/16 と 172.17.0.0/16 で重ならない |
| graph は lab を deploy していなくても描ける | cmd/graph.go:144-160（上の未確定事項 1 に貼った） |
| ポートフォワードの短縮形 `portNumber=8082,localPortNumber=8082` | 手元の awscli 1.46.1 の ParamShorthandParser で JSON 形と同じになる |

#### ジンテーゼ

- 「IMDS に届くことは設計で許容」→ 設計が許容したのは「コンテナから IMDS に届く」ことだけで、届いた先のロールの広さ（HITL の境界の共有、Gradio 側の MSK の書き込み）は評価されていない。実装は設計どおりだが、安全性は PM が指摘 1 を受け入れるか、手当てを選ぶまで条件付き
- 「15 個の退行でテストが縛れている」→ 自分が思いつかなかった方向（実行権・shebang・set -e）が素通りだった。直したが、テストが縛るのは注入して落ちたものだけ。AWS の実物（hop limit 2 で届くか、t4g.medium のメモリ、図の画面）は検証 10 まで未確認
- 「CDN を読まないので VPC の中でも白紙にならない」→ 結論は同じだが理由を直す。設計のリスク 1 の前提（VPC がインターネットに出ないので白紙）は誤り。ページは手元のブラウザが開き、相対の `static/` はポートフォワード越しに lab の EC2 から、仮に CDN の URL があっても手元のブラウザがインターネットから取る。embed なので手元の PC がインターネットに出られなくても白紙にならない
- 差分: Must fix は 0 のまま。Should fix 2 を直し、Should fix 1 を設計側の判断として PM に上げた。「安全」は「指摘 1 の判断しだい」に、「テストが縛る」は「注入した 24 個について」に狭めた
