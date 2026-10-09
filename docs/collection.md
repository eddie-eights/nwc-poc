# 機器から集めるデータ

← [README](../README.md)

機器から何を集めたいか、いまどこまで集めているか、telemetry と性能メトリクスをどう取るか（方針と未決定事項）。2026-10-04 時点。

## 欲しいもの

| 種類 | 中身 |
|---|---|
| syslog | 機器のログ |
| SNMP trap | linkDown / linkUp などのイベント |
| telemetry | 機器の状態（BGP / IS-IS / EVPN など）と、下の性能メトリクス |
| 性能メトリクス | CPU 使用率、メモリ使用率、セッション数（アクティブ数・上限到達率）、帯域使用率（IF・集約リンク）、パケットドロップ数、収容回線数に対する使用率 |

- 性能メトリクスは Grafana と Splunk のしきい値で検知する。パケットドロップ数だけは増加傾向を見る（回数そのものでなく、ふだんとの比で見る）。
- 「本番と同じデータ」は、本番（Cisco）と同じ型・情報のこと。lab（SR Linux）の値を Cisco の YANG そのものにする意味ではない。取り方が違っても、同じ形にそろえて Kafka に流せばよい。

## いまの状態

| 種類 | 既定で取れるか | 経路 |
|---|---|---|
| syslog | 取れる | 機器 → 5140/udp → NLB → syslog-ng → `logs`（Telegraf と同じ `device_log` の形）。既定の `SYSLOG_STANDARD=RFC3164` は本番の Cisco 向けで、lab の SR Linux（RFC5424）のログは崩れる（[deploy.md](deploy.md)） |
| SNMP trap | 取れる | 機器 → 162/udp → NLB → Telegraf → `traps`。異常として上げるのは Grafana と Splunk（表の下の `SNMP trap`） |
| NetFlow / sFlow | 受け口だけ（lab の機器からは来ない） | 機器 → 2055/udp（NetFlow）・6343/udp（sFlow）→ NLB → GoFlow2 → `flows`。Spark が Telegraf と同じ形（measurement `flow`）に読み替え、`traps` / `logs` と同じ格納先（S3 Tables・OpenSearch・Splunk の `sourcetype=nwc:flows`）へ流す |
| telemetry | 一部 | gnmic → 機器の gNMI（57400/tcp）で IF / BGP / IS-IS の状態を `gnmi` トピックへ。2026-10-09 の AWS では `gnmi` トピックができず、1 件も書かれていなかった（原因は確かめていない）。本番の MDT の受け口（57000/tcp → `mdt` トピック）は無い（下の「方針」） |
| 性能メトリクス | lab だけ（CPU・メモリ・IF のカウンタ） | gnmic → lab の SR Linux の gNMI で CPU・メモリ・IF のカウンタ（`statistics`）を 60 秒ごとに `metrics` トピックへ（2026-10-09 の AWS で `interface_stats` と `system` の event が `metrics` に入るのを確かめた。Spark から先は未確認）。本番の MDT の受け口は無い |

- `SNMP trap`: 異常として上げるのは Grafana と Splunk。linkDown / linkUp の trap から `link_down` を出すのは Splunk だけ。
  - Grafana: `STORES` の `grafana`。link 以外の trap を `trap` として上げる。
  - Splunk: `STORES` の `splunk`。linkDown / linkUp を `link_down`、ほかを `trap` として上げる。
- `NetFlow / sFlow`: lab の SR Linux は NetFlow を送れないので、`ops/netflow_send.py` で 1 パケット送って確かめる。

trap と syslog では性能の時系列は取れない（届くのはイベントか、しきい値を越えたという知らせだけ）。性能メトリクスにはポーリングか telemetry が要る。

**集める側:** 種類ごとに別のサービスで受ける。
どれも stream の ECS（Fargate ARM64、0.25 vCPU / 0.5 GB）で、機器から送ってくるものは同じ内部 NLB の後ろにいる。
定義は [telegraf.tf](../IaC/terraform/aws-managed/pipeline/stream/telegraf.tf) の `collector_listeners`、[collectors.tf](../IaC/terraform/aws-managed/pipeline/stream/collectors.tf)。

| 受けるもの | 集める側 | トピック |
|---|---|---|
| gNMI の購読 | gnmic（`<prefix>-gnmic`。`app/gnmic/`。NLB の後ろにいない） | `gnmi`（状態）、`metrics`（カウンタ） |
| SNMP trap（162/udp） | Telegraf の受ける側（`<prefix>-telegraf-dialout`） | `traps` |
| syslog（5140/udp） | syslog-ng（`<prefix>-syslog-ng`。`app/syslog-ng/`） | `logs` |
| NetFlow（2055/udp）・sFlow（6343/udp） | GoFlow2（`<prefix>-goflow2`） | `flows` |

syslog-ng と GoFlow2 と gnmic は MSK の IAM 認証を話せないので、SASL/SCRAM（9096/tcp）で書く（資格情報の置き場は [pipeline.md](pipeline.md) の冒頭の箇条書き）。

- Kafka の ACL は Spark が起動時に入れる。ACL の無いうちも書けることを 2026-10-09 の AWS で確かめた（[pipeline.md](pipeline.md)）。

**syslog の形は Telegraf のときと同じ（2026-10-08 に手元の docker で比べた）:** 同じ RFC5424 の 1 行

```
<184>1 2026-10-08T12:34:56.000001Z leaf1 app23_0 77 ID23 - msg fac=23 sev=0
```

を Telegraf 1.40.1（`inputs.syslog` + `processors.rename` で `hostname` → `sysName`）と syslog-ng 4.29.0（`app/syslog-ng/syslog-ng.conf.in`）に送ると、`logs` に書く行はキーの並び以外同じになる。

```
Telegraf : {"fields":{"facility_code":23,"message":"msg fac=23 sev=0","msgid":"ID23","procid":"77","severity_code":0,"timestamp":1791462896000001000,"version":1},"name":"device_log","tags":{"appname":"app23_0","facility":"local7","severity":"emerg","source":"172.17.0.1","sysName":"leaf1"},"timestamp":1791465836}
syslog-ng: {"timestamp":1791465836,"tags":{"sysName":"leaf1","source":"172.17.0.1","severity":"emerg","facility":"local7","appname":"app23_0"},"name":"device_log","fields":{"version":1,"timestamp":1791462896000001000,"severity_code":0,"procid":"77","msgid":"ID23","message":"msg fac=23 sev=0","facility_code":23}}
```

- `tags` は 5 つ（`sysName` / `appname` / `facility` / `severity` / `source`）、`fields` は 7 つ（RFC3164 は `msgid` と `version` が無い）。
  `severity_code` / `facility_code` / `version` / `timestamp` は数値。
- `fields.timestamp` は送り元の時刻（ns）、最上位の `timestamp` は受けた時刻（秒）。
- 全 severity と全 facility を RFC5424 / RFC3164 で 1 行ずつ、壊れた行 2 つと合わせて 34 行ずつ送ると、30 行は値まで同じだった。違ったのは次の 4 種類。
  - facility 15 の名前: Telegraf は `cron2`、syslog-ng は `solaris-cron`。
  - 頭の無い行（`garbage line without header`）: Telegraf は捨てる、syslog-ng は facility `user` / severity `notice` / `sysName` に送り元の IP を入れて書く。
  - RFC5424 の本文の頭の空白: Telegraf は残す、syslog-ng は落とす。
  - RFC3164 の受け口に来た RFC5424 の行: Telegraf は facility と severity だけの行（本文も `sysName` も無い）を書く。
    syslog-ng は `appname` に `1`、`sysName` に送り元の IP、本文に残りを入れて書く。
- キーの集合と型は `tests/test_collectors.py` が conf.in の `format-json` と上の Telegraf の行で突き合わせる。

## 方針: telemetry と性能メトリクスは Cisco MDT の dial-out で受ける（2026-10-04）

機器のほうから送らせる（dial-out）方針は変えていない。ただし受け口（Telegraf の `inputs.cisco_telemetry_mdt`）は外してある。いまの受け口は次のとおりで、MDT を受ける口は無い。

```
機器 ─ trap 162/udp ─────────────────→ NLB ─→ Telegraf の受ける側（1162。何台でもよい）─→ MSK
     ─ syslog 5140/udp ──────────────→ NLB ─→ syslog-ng ─→ MSK
     ─ NetFlow 2055/udp・sFlow 6343/udp → NLB ─→ GoFlow2 ─→ MSK
     ─ MDT dial-out（TCP 57000）  ×  2026-10-08 に外した
（NLB は 3 つとも同じ <prefix>-tg）
```

**受け口は外してある:** 機器を送らせる段取り（本番の機種と TLS）が決まるまで、使わない口を開けておかない。下の方針（dial-out で受ける）は変えていない。

- 戻すときは `git show ed8edf1^:app/telegraf/telegraf.conf.in`（`inputs.cisco_telemetry_mdt` の区間）と `git show ed8edf1^:IaC/terraform/aws-managed/base/core/variables.tf`（`mdt_source_cidrs`）を元にする。
  NLB の 57000/tcp のリスナー、Telegraf の NLB の SG の行、`ops/up.sh` の `MDT_SOURCE_CIDRS` を足し直す。

**外した受け口（当時の形）:**

- Telegraf の `inputs.cisco_telemetry_mdt`（gRPC、57000/tcp、タグ `collector=mdt`）→ Kafka の `mdt` トピック（生のまま。共通の形への変換は本番の sensor path が決まってから）。
- NLB に TCP 57000 のリスナーを置き、Telegraf の NLB の SG は `deploy.env` の `MDT_SOURCE_CIDRS` だけを通していた。
  `MDT_SOURCE_CIDRS` は機器の CIDR をカンマで。既定は空でどこからも受けない。`0.0.0.0/0` は拒む。
- Spark は `mdt` もメトリクスのトピックとして S3 Tables と Prometheus に流していた。
- TLS と機器側の設定（`telemetry ietf subscription` / `receiver`）は未決定。

**選んだ理由: Telegraf を増やしやすい。**

- 機器から送ってくるもの（trap・syslog・MDT・NetFlow / sFlow）は、NLB が 1 つのタスクにだけ渡すので、タスクを増やしても重複しない。
- こちらから取りにいくもの（gNMI の購読）は、タスクごとに同じ機器へ取りにいくので、増やすと MSK に同じデータが何回も入る。
  取りにいくサービス（`<prefix>-gnmic`）だけを 1 タスクに固定しているのはこのため（下の「Telegraf を受ける側と取りにいく側に分けた」）。
  固定は [gnmic.tf](../IaC/terraform/aws-managed/pipeline/stream/gnmic.tf) の `desired_count = 1` と `deployment_maximum_percent = 100`。
- 集める側から機器への通信（161/udp や gNMI の TCP）を本番で開けてもらえるか分からないので、機器 → 集める側の向きだけで済むほうが安全。

**MDT で取れる性能メトリクス:**
MDT は機器の運用データ（oper の YANG モデル）を周期（periodic）か変化時（on-change）で送るので、性能の時系列も送れる。
どのモデルが使えるかは機種と版で変わる。IOS XE を仮定した例（本番では未確認）:

| 指標 | モデルの例 |
|---|---|
| CPU | `Cisco-IOS-XE-process-cpu-oper` |
| メモリ | `Cisco-IOS-XE-memory-oper` |
| 帯域・ドロップ・エラー（IF と Port-channel） | `Cisco-IOS-XE-interfaces-oper`（`statistics` の octets / discards / errors、`speed`） |
| セッション・上限 | 機能ごとに別のモデル（下の「セッションと上限」） |

**Telegraf を受ける側と取りにいく側に分けた（ユーザー決定）。取りにいく側は gnmic:**

| サービス | 入力 | 数 | SG |
|---|---|---|---|
| `<prefix>-telegraf-dialout`（Telegraf） | trap（NLB の後ろ。syslog は syslog-ng、NetFlow / sFlow は GoFlow2 の別のサービスで受け、MDT の受け口は無い） | 増やしてよい（`TELEGRAF_AZ_NUM` の数。既定 1。入れ替えは新しいタスクが立ってから古いものを止める） | `telegraf_dialout`（NLB から受けるだけ） |
| `<prefix>-gnmic`（gnmic） | gNMI の購読 5 つ（[pipeline.md](pipeline.md)） | 1 に固定（入れ替えは古いものを止めてから。そのあいだ購読が数十秒切れる） | `gnmic`（受けない。機器の 57400/tcp と MSK の 9096/tcp へ出る） |

Telegraf の Kafka の出力は `traps` だけ（`gnmi` / `metrics` は gnmic、`logs` は syslog-ng、`flows` は GoFlow2 が書く）。デバッグ用の EC2 の Telegraf も trap だけ受ける（gnmic は無い）。

gnmic は機器の一覧（`GNMI_TARGETS`）を持つ。
一覧は SSM のパラメータ（`/<prefix>/gnmic/{lab|nautobot}/gnmi-targets`）に置き、タスクは起動時に ECS の secrets として受ける。
持ち主は Nautobot（`…/lab/*` は stream を手で `gnmi_targets_from_nautobot=false` にして apply したときだけ使う）。

- Nautobot の Job が `…/nautobot/gnmi-targets` を書き換えて、gnmic のサービスを作り直す。Terraform は最初の値（lab の一覧）だけ書く。くわしくは [pipeline.md](pipeline.md) の「Nautobot」

デバッグ用の EC2 は Nautobot を使わず、lab の定義の一覧のまま。

## セッションと上限（一般的な解釈で調べた。2026-10-04）

要件の「セッション」と「収容回線数」は中身が決まっていないので、一般的な意味で読み、Cisco の公開モデルで機器から取れるかを確かめた（実機では未確認）。

- YANG は [YangModels/yang](https://github.com/YangModels/yang) の `vendor/cisco/xe/2621` と `vendor/cisco/xr/24330`。
- MIB は [cisco/cisco-mibs](https://github.com/cisco/cisco-mibs)。

**セッション数と上限到達率:** 機器の役割で「セッション」の中身が変わる。

| 役割 | セッション | 今の数 | 上限 | 上限に当たった印 |
|---|---|---|---|---|
| BNG（IOS XR） | 加入者（PPPoE / IPoE） | `Cisco-IOS-XR-iedge4710-oper` の `iedge-license-manager-summary/session-count` | 同じ所の `session-limit`（設定値）・`session-license-count`（ライセンス数）・`session-threshold` | — |
| BNG（IOS XE） | 加入者（PPPoE） | `Cisco-IOS-XE-ppp-oper` のセッションの一覧（数を数える） | config | — |
| FW（IOS XE のゾーンベース FW） | 接続 | `Cisco-IOS-XE-fw-oper` の `current-active-conn`（トラフィッククラスごと） | config | `l4-session-limit`（上限を越えて捨てたパケット数） |
| NAT（IOS XE） | 変換エントリ | `Cisco-IOS-XE-nat-oper` の `entries` | config（`ip nat translation max-entries`） | `limit-entry-add-fail` |
| IPsec VPN（IOS XE） | トンネル | `Cisco-IOS-XE-crypto-oper` | `ipsec-tunnels/maximum` と `available`（機種の上限） | — |
| ハードウェアの表 | TCAM などのエントリ | `Cisco-IOS-XE-tcam-oper` の `tcam-entries-used`、`Cisco-IOS-XE-switch-dp-resources-oper` の `entries-used` | 同じ所の `tcam-entries-max` / `max-entries`（`percentage-used` もある） | — |

- 「config」と書いた上限は機器の設定値。
  IOS XE の MDT は運用データだけでなく設定データも購読できる（yang-push のストリームに両方入る。[Cisco の設定ガイド][cisco-ietf-telemetry]）ので、dial-out のまま取れる見込み。
- **SNMP のポーリングにしても上限は増えない。**
  - 今の数の MIB はある（`csubAggStatsCurrUpSessions`、`cufwConnGlobalNumActive`、`cfwConnectionStatValue`（ASA）、`cneAddrTranslationNumActive`、`cipSecGlobalActiveTunnels`）。
  - が、上限の MIB は無く、あるのは最大値（high-water mark）としきい値の設定だけ。上限まで取れるのは YANG（MDT）のほう。

[cisco-ietf-telemetry]: https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/prog/configuration/1712/b_1712_programmability_cg/m_1712_prog_ietf_telemetry.html

**収容回線数に対する使用率:** 一般には「その機器に何本のお客さまの回線を載せているか」。形は 2 つ。

- ポート単位（集約スイッチや PE）: 使っている回線 ＝ お客さま向けのポート（またはサブインターフェース）で使用中のもの、上限 ＝ 機器のポート数。
  - どちらも `Cisco-IOS-XE-interfaces-oper`（IF の一覧と状態）と `Cisco-IOS-XE-device-hardware-oper`（部品表）から取れる。
  - 「お客さま向け」の見分け方（IF の description、名前の規則など）は決めが要る。
- 加入者単位（BNG）: 上の BNG のセッションと同じ。
- どちらでも、運用で決めた設計上の上限（「この機器は N 回線まで」「80% で増設」）は機器から来ないので静的データ。

**割る上限の選び方（決定）:** 設定上の上限（機器の config に書いた上限）を使う。無いときは機器の上限（ライセンス数・機種の上限・ポート数）、それも無いときだけ設計上の上限（静的データ）。

## lab（SR Linux）での取り方

**lab からは MDT は取れない。**
SR Linux は Cisco MDT を話さない（送れるのは gNMI だけ）。本番の MDT の受け口（`inputs.cisco_telemetry_mdt`）も無い。
lab では gnmic から gNMI（dial-in、57400/tcp）で購読し、Spark が Telegraf の形に読み替える（下の「gnmic の event と読み替え」）。
gNMI の購読はこちらから取りにいくので、lab の値は gnmic のタスク（1 つに固定）から来る（上の「Telegraf を受ける側と取りにいく側に分けた」）。

**lab のセッション数と収容回線数は本番の値の代替。**
lab の機器には BNG・FW・NAT・IPsec が無いので、本番と同じ役割（今の数・上限・どこが食っているか）を持つ値で代える。
エージェントが本番と同じ要素で判断できるかを要素ごとに比べ、揃わない要素は下に書いた。

- SR Linux 26.7.2 の YANG（[nokia/srlinux-yang-models](https://github.com/nokia/srlinux-yang-models) の `v26.7.2`）で確かめた。
- いまは購読していない。lab の実機で値が出るかは未確認のまま。下は調べた結果として残す。

### セッション数の代替: MAC テーブルのエントリ数

本番のセッション（NAT の変換、FW の接続、BNG の加入者）も lab の MAC も「通信や端末が増えると増え、上限で新しいものを入れられなくなる表のエントリ」。BNG の IPoE の加入者は MAC ごとなので、いちばん近いのは加入者。

| エージェントが使う要素 | 本番（Cisco） | lab の代替（SR Linux） | 揃うか |
|---|---|---|---|
| 今の数 | nat-oper の entries、fw-oper の `current-active-conn`、XR の `session-count` など | `/network-instance[name=*]/bridge-table/statistics/active-entries`（`mac-type` ごとにも出る。学習した分は `learnt`） | 揃う |
| 設定上の上限 | `ip nat translation max-entries`、`l4-session-limit`、XR の `session-limit` | `/network-instance[name=*]/bridge-table/mac-limit/maximum-entries`（既定 250）。機器の上限 `/system/bridge-table/mac-limit/maximum-entries` は、mac-vrf に既定値があるので使わない | 揃う |
| 警告のしきい値 | XR の `session-threshold`。XE は機能による | `mac-limit/warning-threshold-pct`（既定 95。超えると警告、5 下がると解除） | 揃う |
| どこが食っているか | 加入者ごと、内側の IF ごと | サブインターフェースごと: `/interface[name=*]/subinterface[index=*]/bridge-table/statistics/active-entries` と上限 `…/bridge-table/mac-limit/maximum-entries`（1〜8192、既定 250） | 揃う |
| 上限で断った数 | NAT の `limit-entry-add-fail` など | **無い。**`failed-entries` はデータパスへの書き込みに失敗した数で、上限で断った数ではないので使わない | 揃わない。今の数が上限に達したら「上限に当たっている」とみなす。断った件数は出ない |
| 上限に当たったときの影響 | 新しい通信・加入者が入れない | 新しい MAC を覚えない（覚えていない端末宛はフラッディングになるのが一般的な L2 の動き。SR Linux では確かめていない） | 揃わない。影響の説明は機能ごとに KB に書く |
| 値の動き | 時間帯で増減する | lab の mac-vrf にいるのは TRex のポート 4 本なので、数個のまま動かない | 揃わない。MAC を増やす仕掛け（TRex で送り元の MAC を変えて撃つプロファイルなど）が要る（まだ無い） |

- しきい値を試すときは、lab の `mac-limit maximum-entries` を小さくすれば到達率を上げられる。

### 収容回線数の代替: お客さま向けの IF の数

| エージェントが使う要素 | 本番（Cisco） | lab の代替（SR Linux） | 揃うか |
|---|---|---|---|
| 今の数 | お客さま向けの IF の数（何をお客さま向けとみなすかは未決定） | `type bridged` のサブインターフェースを持つ IF（lab では TRex へ向かう `ethernet-1/3`。fabric の IF は routed なので数えない）。`/interface[name=*]/subinterface[index=*]/type` と `oper-state` | 数え方は揃う。ただ「お客さま向け」の決め方が機器の設定に依存する。本番と lab で同じ決め方（トポロジで相手が host やお客さまの IF）にすると揃う |
| 上限 | 物理ポート数 | 物理ポート数（`/interface[name=ethernet-*]` の数。SR Linux は未使用のポートも状態に出す）。設定上の上限は無いので機器の上限を使う | 揃う |
| 値の動き | 開通・解約で変わる（ほぼ静的） | 設定を変えたときだけ変わる | 揃う |

### ほかに調べて使わなかった候補

| 候補 | 使わない理由 |
|---|---|
| IPv6 の ND の上限（サブ IF ごとの `ipv6/neighbor-discovery/limit/max-entries`。`log-only false` なら上限で断る） | 断る動きは本番に近いが、今の数の葉が無く一覧を数えることになる。lab は IPv4 が主で値が動かない |
| ARP | 上限の設定が無い（ND の limit は IPv6 だけ） |
| DHCP snooping | 統計はパケット数だけで、バインディングの数も上限も無い |
| BGP のピア数 | 上限が無い |
| データパスの資源（`/platform/.../datapath/.../resource` の used / free） | コンテナの SR Linux で値が出るか未確認 |

## gnmic の event と読み替え（2026-10-09、cycle 013）

gnmic は購読した値を event の形で Kafka に書く（`app/gnmic/gnmic.yaml.in` の `format: event`。`split-events: true` で 1 メッセージ 1 件）。
下は gnmic v0.49.0 のソースから組んだ形。2026-10-09 の AWS で `metrics` の実物（`interface_stats` と `system`）と照らし、同じ形だった。

- values のキーはモジュールの接頭辞つきの絶対パス、カウンターは文字列。
  tags のキーは `interface_name` / `source` / `subscription-name` / `control_slot` / `cpu_index`。
- values の無い event（tags だけ。400 件のうち 359 件）もあり、`read_rows` も `gnmic_message` もこれを捨てる（025 で `read_rows` の判定を直した。それまでは Telegraf の行として通り、sinks が落ちていた）。
- `gnmi`（`interface_state` など）の event は無かったので、oper-state / admin-state の綴りは見ていない（`docs/verification/20261009-aws-managed.md` の「B.」）。

```
{"name": "interface_state", "timestamp": 1700000000123456789, "tags": {"source": "203.0.113.31", "interface_name": "ethernet-1/1", "subscription-name": "interface_state"}, "values": {"/srl_nokia-interfaces:interface/oper-state": "down"}}
```

Spark が、形で見分けて（`fields` が無い）Telegraf の形（`timestamp` / `name` / `tags` / `fields`）に読み替えてから格納先へ流す。
読み替えは `app/spark/snmp_sinks.py` の `read_rows`。同じ読み替えを Python で書いた `gnmic_message` を `tests/test_stream.py` が縛る。
S3 Tables にも読み替えた形で入る（Kafka には event のまま）。

| event | Telegraf の形 |
|---|---|
| `timestamp`（ナノ秒） | `timestamp`（秒。小数は切り捨て） |
| `name`（購読の名前） | measurement。`interface_state` / `interface_stats` は `interface`、ほか（`bgp_neighbor` / `isis_interface` / `system`）はそのまま |
| `tags` のキー（接頭辞 `…:` を落とす） | `interface_name` → `ifName`、`neighbor_peer-address` → `peer_address`、`interface_interface-name` → `interface_name`、`control_slot` → `slot`。ほか（`source`、`subscription-name` など）はそのまま |
| `values` のキー | 最後の要素の接頭辞を落とし、`-` を `_` に（`/interface/oper-state` → `oper_state`） |
| `tags.source`（target の名前 = 機器の管理 IP） | 列 `agent_host`。機器名（`sysName`）は Spark が device map（`--device-map`）で引く |
| `deletes` だけの event | 捨てる |

Prometheus の系列の名前は Telegraf のころの `snmp_` の頭のまま（Grafana のダッシュボードとルールが読む名前を変えない）。状態の文字列は 1 / 0 にする（`STATE_FIELDS`）。

| 購読 | トピック | 系列 |
|---|---|---|
| `interface_state` | `gnmi` | `snmp_interface_oper_up`（`up` で 1）、`snmp_interface_admin_up`（`enable` で 1） |
| `interface_stats` | `metrics` | `snmp_interface_in_octets`、`snmp_interface_out_error_packets` など（`statistics` の葉ごと） |
| `bgp_neighbor` | `gnmi` | `snmp_bgp_neighbor_session_up`（`established` で 1） |
| `isis_interface` | `gnmi` | `snmp_isis_interface_oper_up`（`up` で 1） |
| `system` | `metrics` | `snmp_system_instant`（CPU）、`snmp_system_utilization`（メモリ） |

- Splunk の `source` も Telegraf のころと同じ `telegraf:<measurement>`（保存済みサーチ `nwc_gnmi` が読む）。
- 仮の共通の形（`device_cpu` / `device_memory` / `if_stats` / `sessions` / `circuits`）への変換はしていない（ユーザー決定）。本番の形は下の「未決定事項」の「共通の形」で決める。

## 未決定事項

| 項目 | 決まると何が決まるか | 状態 |
|---|---|---|
| 本番の機種と OS の版 | MDT が使えるか（旧来の IOS 15.x などには MDT が無く、SNMP のポーリングしか無い）。使える YANG モデル | ユーザーが確認中 |
| 集める側から機器へ通信を開けられるか | MDT が使えない機種に、ポーリングの道を残すか | 不明 |
| 購読するパス（sensor path）と間隔 | 共通の形の項目、Telegraf の受け口の負荷、`mdt` トピックから共通の形への変換（受け口は外してあるので、まず戻す） | 本番の版が分かってから |
| MDT の TLS と機器側の設定 | 受け口（外してある）は平文の gRPC だった。証明書を誰が持つか、機器の `receiver` の書き方 | 本番の版が分かってから |
| 「セッション」が何のセッションか | 一般的な解釈で候補を上に並べた（BNG の加入者、FW の接続、NAT、IPsec、ハードウェアの表）。本番の機器の役割が分かれば絞れる。lab は MAC の数で代える（上の「lab での取り方」） | 本番は候補まで。lab は決定 |
| セッションの上限と収容回線数の上限の出どころ | 機器の上限（ライセンス・設定値・機種の上限・ポート数）は多くが MDT で取れる。設計上の上限だけ静的データ（`app/agentcore/data/devices.yaml` か Neptune）。割るのは設定上の上限を先に使う（上の「割る上限の選び方」） | 決定 |
| 共通の形 | Grafana と Splunk のルールを書く相手。本番の機種が 1 種類なら Cisco の形を正にし、混ざるなら独自の共通の形にする | 本番の機種が分かってから決める。lab の仮の形への変換はしていない。いまは gnmic の event を Telegraf の形に読み替えている（上の「gnmic の event と読み替え」） |
| lab からどう送るか | gNMI を gnmic で取り、Spark が Telegraf の形に読み替える（上の「lab での取り方」。2026-10-09 の AWS で gnmic が `metrics` に書くところまで確かめた。`gnmi` は書かれず、Spark の読み替えから先は未確認） | 決定 |
| lab に Cisco の機器を足すか | 足せば MDT の受け口と Cisco の YANG の名前を lab で試せ、IOS XE ならセッションも代替ではなく本物（NAT / FW）が取れる見込み。候補と費用は表の下の `lab に Cisco の機器を足すか` | **当面は SR Linux のまま**（費用と arm64 の決定を優先）。本番の機種が分かったら見直す。lab の EC2 は TRex のため x86_64（`m6i.xlarge`、約 $0.25/h）なので、「x86 だけ」は妨げではない（費用と入手の条件は残る） |
| Grafana と Splunk の分担 | 同じ指標を両方で見るとルールを 2 か所でそろえることになる（異常の id が同じなので通知は 1 つにまとまる） | 未定 |

- `lab に Cisco の機器を足すか`: 2026-10-04 に調べた。
  - MDT の受け口（`cisco_telemetry_mdt`）は外してあるので、戻してから試す。
  - 本番が XR なら XRd（コンテナ。KVM 不要、1 台 2 GiB）。
  - XE なら Cat8000v（VM。KVM が要るので lab の EC2 を Graviton の t4g から x86 の C8i / M8i などのネステッド仮想化か .metal に変える）。
  - どちらも x86 だけで、入手に Cisco の契約が要る見込み（未確認）。
  - IOL は NETCONF が無く MDT を出せない見込みで、CML の同梱イメージは CML の中でしか使えないライセンス。
  - SR Linux のファブリックは残し、本番と同じ OS の Cisco を 1〜2 台足すのが候補。
  - XRd の control-plane 版は転送が最小限で leaf の代わりにならない。
  - Nexus（N9Kv）でファブリックを組むと 1 台 6〜10 GB で、lab の EC2 が約 $0.17/h（t4g.xlarge）から $0.64〜1.28/h（r7i.2xlarge〜4xlarge）になる。
  - Cat8000v を 2 台足すだけなら m7i.2xlarge で約 $0.52/h（東京のオンデマンド）。

## 経緯

- 2026-09-26: lab は arm64 で通すと決めた。2026-10-04 に「lab に Cisco の機器を足すか」を当面見送ったのは、費用とこの決定を優先したため。2026-10-08 に TRex のため lab の EC2 を x86_64 にした。
- 2026-10-04: Telegraf を受ける側（`telegraf-dialout`）と取りにいく側（`telegraf-dialin`）に分けた（ユーザー決定）。同じ Telegraf のイメージで、役割は環境変数 `TELEGRAF_ROLE` で決めていた。
- 2026-10-04: MDT の受け口（Telegraf の `inputs.cisco_telemetry_mdt`）を作った。2026-10-08（012）に外した。
- 2026-10-04〜013: lab の gNMI は Telegraf で購読し、Telegraf の中で仮の共通の形（`device_cpu` など）に変換していた（`app/telegraf/lab_gnmi.star` と `lab_circuits.star`、2 つめの `inputs.gnmi`（`lab_*`））。
- 2026-10-08（012）: syslog の受け口を Telegraf から syslog-ng に移し、GoFlow2（NetFlow / sFlow）を足し、MDT の受け口を外した。
- 2026-10-09（013）: gNMI の購読を Telegraf（`<prefix>-telegraf-dialin`。EVPN と MAC も取っていた）から gnmic に替えた。Telegraf は受ける側だけになったので `TELEGRAF_ROLE` を外した。
- 2026-10-09（013）: SNMP のポーリング（IF の状態と 32 ビットカウンタ、エラー数）をやめた。
- 2026-10-09（013）: セッション数と収容回線数の代替（MAC テーブルの数、お客さま向けの IF の数）の購読と共通の形への変換をやめた（ユーザー決定）。star のファイルは消した。
