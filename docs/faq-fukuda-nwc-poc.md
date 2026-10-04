# 学習 FAQ（nwc-poc を進めながら質問したこと）

nwc-poc の作業中に質問したことと、その答えをまとめた。答えは会話の中身を要約したもの。コードのパスはこのリポジトリの中のもの。

- [1. 学習教材の選び方](#1-学習教材の選び方)
- [2. syslog の基本](#2-syslog-の基本)
- [3. lab の syslog を local7 にした](#3-lab-の-syslog-を-local7-にした)
- [4. デバッグ用の EC2（lab + Telegraf）](#4-デバッグ用の-ec2lab--telegraf)
- [5. 本番の Cisco から送るとき](#5-本番の-cisco-から送るとき)
- [6. SNMP のポーリングを既定で止めた](#6-snmp-のポーリングを既定で止めた)
- [7. Nautobot（機器の一覧とケーブルの正）](#7-nautobot機器の一覧とケーブルの正)
- [8. Neptune Database と Neptune Analytics](#8-neptune-database-と-neptune-analytics)
- [9. 障害の情報をどこに残すか](#9-障害の情報をどこに残すか)
- [10. データの流し先とテーブル](#10-データの流し先とテーブル)
- [11. Neptune に置くもの](#11-neptune-に置くもの)

---

## 1. 学習教材の選び方

### Q. 「ネットワーク技術＆設計入門 第2版」と「図解入門TCP/IP 第2版」、どっちから読めばいい？

**A. 図解入門TCP/IP が先。** 2 冊とも著者はみやたひろしさんで、役割が分かれている。

| | 図解入門TCP/IP | ネットワーク技術＆設計入門 |
|---|---|---|
| 問い | パケットがどう動くか | ネットワークをどう組むか |
| 中身 | Ethernet・ARP・IP・ICMP・TCP/UDP・TLS・HTTP・DNS を層ごとに | 物理設計・論理設計（VLAN・ルーティング）・冗長化・FW や LB・運用管理（SNMP・Syslog・NTP） |
| 前提 | ほぼ不要 | プロトコルの基本を知っていること |

- 設計本は「この要件ならこの技術」という選び方の話が中心。プロトコルの土台がないと「なぜその設計か」がつかみにくい。
- 次の 4 つを説明できるなら、設計本から読んでもよい。
  - 3 ウェイハンドシェイク、再送、ウィンドウ制御
  - ARP がいつ飛ぶか、L2 と L3 の転送の違い
  - サブネット計算、ルーティングテーブルの最長一致
  - NAT やステートフル FW のセッションの扱い
- 設計本の運用管理の章（SNMP・Syslog・NTP）は、今の構成（trap と syslog を Telegraf に集める）にそのまま当てはまる。
- 設計本はオンプレミスが前提で、VPC や PrivateLink の話はほとんど無い。

### Q. Udemy の 5 講座（PySpark、CCNA 総合、CCNA Part2、Packet Tracer、Cisco ルーターのセットアップ）はどう？

**A. 役に立つ順に「CCNA Part2 の一部」→「PySpark の DataFrame の章」→「CCNA 総合のつまみ食い」。Packet Tracer とルーターのセットアップはやらなくてよい。**

判断の元にした、プロジェクトで使っている技術:

- lab（SR Linux）: IS-IS（アンダーレイ）、BGP EVPN / VXLAN、LAG / LACP
- 集める情報: SNMP（trap を含む）、gNMI、syslog
- Spark（`spark/snmp_sinks.py`）: Kafka を Structured Streaming で読み、DataFrame で処理する。異常検知はルールベースで、機械学習は使っていない。

| 講座 | 判定 | 見る範囲と理由 |
|---|---|---|
| CCNA Part2 | やる（一部） | 「管理」（SNMP・syslog・NTP）、「自動化」（YANG・REST。gNMI の前提になる）、「SDN」 |
| PySpark | やる（一部） | 「Spark DataFrame」の章。Structured Streaming と Kafka 連携は無いので公式ドキュメントで補う。MLlib は後回し |
| CCNA 総合（41 時間） | 必要なところだけ | VLAN・STP・OSPF（IS-IS と同じリンクステート型）。TCP/IP の本と重なる |
| Packet Tracer ハンズオン | やらない | Cisco IOS 専用で、範囲は CCNA まで |
| Cisco ルーターのセットアップ | やらない | 同上 |

lab の中心にある IS-IS、BGP EVPN / VXLAN、gNMI / OpenConfig は、5 本とも扱っていない。

### Q. IS-IS、BGP EVPN / VXLAN、gNMI / OpenConfig を学べる教材は Udemy にない？

**A. あるが、全部英語。** gNMI だけを扱う Udemy の講座は見つからなかった。

- **第一候補**: Nokia DataCenter [IPFabric] EVPN/VXLAN with SR Linux（英語、5.5 時間）
  - SR Linux を containerlab で動かし、IS-IS / BGP のアンダーレイ、EVPN / VXLAN、L2 / L3 EVPN、IRB まで扱う。lab にいちばん近い。
  - gNMI は扱わない。評価の件数はまだ少ない。
- **EVPN をもっと深く**: Cisco Data Centers | VXLAN EVPN（定番、実習は NX-OS）、VXLAN BGP EVPN by Arash Deljoo（ベンダー中立）、Juniper IP Fabric EVPN-VXLAN。
- **IS-IS**: 専用講座はあるが、lab の IS-IS は単純なアンダーレイなので、Nokia の講座で足りるはず。
- **gNMI / OpenConfig**: Udemy の外で学ぶ。
  - 無料: learn.srlinux.dev、gNMIc のドキュメント、SR Experts のハッカソン教材
  - 有料: Packet Coders の Modern Network Telemetry（Telegraf の gNMI プラグインと gNMIc。nwc-poc の Telegraf とほぼ同じ形）

英語の講座でも自動翻訳字幕が付くことがある。買う前にプレビューで確かめる。

### Q. Packet Tracer って何をやるの？

**A. Cisco が無料で配っているネットワークシミュレーター。** Cisco Networking Academy に登録すると使える。

- 機器のアイコンを並べてケーブルでつなぐ。
- Cisco IOS 風のコマンドで設定する。
- `ping` や `tracert` を打つ。
- **シミュレーションモード**では、パケットが 1 つずつ進む様子と、各地点でヘッダーがどう書き換わったかを見られる。いちばんの特徴。

| | Packet Tracer | containerlab + SR Linux（lab） |
|---|---|---|
| 中身 | 動きをまねたシミュレーター | 本物のネットワーク OS がコンテナで動く |
| 扱える技術 | CCNA の範囲 | IS-IS、BGP EVPN、VXLAN、gNMI まで |
| 強み | パケットの動きが目で見える | 実機と同じ動き、Telegraf など外のツールとつながる |

講座は買わずにツールだけ入れて、TCP/IP の本で ARP やルーティングがピンとこないときに使うのがよい。

### Q. CCNP ENCOR の講座（ccnp-encor-master）はどう？

**A. 優先度は低め。** 日本語で約 5 時間と手軽だが、試験範囲を広く浅く見る構成。

- 「ネットワーク管理」（41 分）は Telegraf が集めているものなので、役に立つ。
- 「自動化」は 24 分と短い。CCNA Part2 の方が厚い（約 2 時間 + 約 1 時間）。
- VXLAN は Cisco SD-Access（LISP と組み合わせる方式）の部品として出てくる。lab の「BGP EVPN で VXLAN を制御する」方式とは違う。
- IS-IS、BGP EVPN、gNMI は紹介ページに出てこない。

### Q. Cisco ルーターのセットアップ講座は、スイッチやルーターの知識として役に立たない？

**A. 技術の知識にはほとんどならない。実機を触る作業の作法を学ぶ講座（55 分）。**

- **身につくもの**: コンソール接続、起動と初期設定、作業ログを取る習慣、running-config と startup-config の違い。
- **身につかないもの**: ルーティングやスイッチングの仕組み、設計の考え方。
- lab は SR Linux で、「候補の設定を作ってから commit する」方式。コンソールケーブルも使わない。
- 仕事で Cisco の実機を初めて触る予定があるなら、見る価値はある。

### Q.（画像の 2 講座）どっちだっけ？ / CCNP ENARSI の Packet Tracer 講座はどう？

**A. 最初に貼ったのは右の「【超絶入門】CCNA対策 Packet Tracer」。ENARSI の講座も後回しでよい。**

- ENARSI の講座は評価が高い（4.8）。扱うのは IPv6、OSPFv2 / v3、EIGRP、経路の再配布。
- lab の IS-IS と BGP EVPN は出てこない。EIGRP はもとは Cisco 独自（RFC 7868 で公開されたが、実装はほぼ Cisco だけ）。
- CCNP の資格を取る人や、仕事で Cisco のルーティングを扱う人には向いている。

### Q. やることと順番をまとめて

**A.** 図解入門TCP/IP は最初から並行して読む。講座はデータが流れる順（lab → 収集 → 処理）に進める。

| 順 | やること | 範囲 | 目安 | 効くところ |
|---|---|---|---|---|
| 1 | Nokia SR Linux EVPN/VXLAN（英語） | 全部 | 5.5 時間 | `lab/srlinux/` の IS-IS・BGP EVPN・VXLAN |
| 2 | learn.srlinux.dev と gNMIc（無料） | gNMI / テレメトリ | – | Telegraf の `inputs.gnmi` |
| 3 | CCNA Part2（日本語） | 管理・自動化・SDN の章 | 約 4.5 時間 | SNMP の trap、syslog、YANG |
| 4 | PySpark（日本語）と Spark の公式ドキュメント | DataFrame の章、Structured Streaming | 約 2 時間〜 | `spark/snmp_sinks.py` |
| 5 | ネットワーク技術＆設計入門（本） | 全体、特に運用管理の章 | – | 全体の設計の見直し |

- **必要になったら**
  - EVPN をもっと深く知りたいとき: Cisco か Arash Deljoo の EVPN 講座。
  - VLAN・STP・OSPF の基礎が足りないとき: CCNA 総合の該当章。
- **今はやらない**: CCNP ENCOR、CCNP ENARSI、CCNA の Packet Tracer 講座、Cisco ルーターのセットアップ、PySpark の MLlib の章。

### Q. 英語の動画は厳しいので CCNA Part2 から始めたい。セクション 5 は全部見る？

**A. 30 以外は全部見る。**

| 講義 | 判定 | 理由 |
|---|---|---|
| 26. SNMP | 済 | – |
| 27. システムログの管理 | 見る | 重要度（severity）とファシリティの考え方が、lab の syslog にそのまま出てくる |
| 28. NTP | 見る | 時計がずれると、SNMP・syslog・gNMI の時刻を並べて比べられない。lab では NTP を設定していない（コンテナは EC2 の時計を共有するのでずれない。実機では要る） |
| 29. CDP と LLDP | 見る | 実機のトポロジを取るときの元ネタになる（`lab/lab_topology.py` は lab の定義から作っている）。CDP は Cisco 独自なので軽く流す |
| 30. IOS の管理 | 飛ばす | Cisco の運用作業の話 |

このセクションの後は、「ネットワーク自動化とプログラマビリティ」（REST・JSON・YANG）、余裕があれば「SDN」。HSRP・QoS・無線 LAN・セキュリティ・IPv6 は今は飛ばす。

---

## 2. syslog の基本

### Q. システムログと syslog は別物？

**A. 同じものを指すことが多いが、厳密には別の言葉。**

| | システムログ | syslog |
|---|---|---|
| 何か | 機器が書き残す出来事のメッセージ（IF ダウン、BGP の隣接断、ログインなど） | ログを外のサーバーへ送る形式と送り方（RFC 3164 は昔の BSD の実装を書き留めたもの、RFC 5424 が標準。UDP での送り方は RFC 5426）。普通は UDP 514 |
| どこにあるか | 機器の中（コンソール、メモリ、ファイル） | ネットワークを流れて受け手のサーバーに届く |

- Cisco のログの主な出し先は、コンソール、メモリ（`show logging`）、SSH の端末（`terminal monitor`）、syslog サーバー（`logging host`）の 4 つ。最後の「外へ送る」部分が syslog。
- lab でも 2 つは分かれている。
  - SR Linux がコンテナの中に書くログ（`lab.sh logs` で読む）。
  - 同じ出来事を RFC 5424 で UDP 5140 に送る syslog（Telegraf の `inputs.syslog` が受け、MSK の `logs` トピックへ流す）。

### Q.（ファシリティの表の画像）

**A. 表は syslog のファシリティ（ログの出どころの分類）。重要度（severity）と組み合わせて使う。**

| | 意味 | 例 |
|---|---|---|
| ファシリティ | 出どころ（どの機能やプログラムか） | kern、authpriv、local7 |
| 重要度 | どれだけ重大か（0〜7、小さいほど重大） | 0 emergency、3 error、6 informational、7 debug |

- 表は Linux / Unix サーバーの一覧。lpr や news は昔の名残。
- ネットワーク機器で大事なのは **local0〜7**。機器には kern や mail のような決まった出どころが無いので、空き番号の local を使う。Cisco の既定は local7。
- lab の SR Linux の設定（`lab/srlinux/*.cli`）:

  ```
  set / system logging remote-server 203.0.113.1 subsystem bgp priority match-above informational
  ```

  - `subsystem`: SR Linux 独自の細かい出どころの分類。
  - `priority match-above informational`: informational（6）以上だけ送る。debug（7）は送らない。

### Q. ルーター側で設定することなのね

**A. 基本は送る側（ルーター）で設定する。受ける側でも絞れる。**

| どこで | 決めること | lab では |
|---|---|---|
| 送る側（ルーター） | 送り先とポート、ファシリティ、どの重要度以上を送るか | 203.0.113.1:5140/udp、subsystem ごとに informational 以上 |
| 受ける側（syslog サーバー） | 届いたものをどこに保存し、何を捨てるか | Telegraf は全部受け、ファシリティと重要度を付けたまま MSK へ |
| その先（分析側） | どの重要度を異常として扱うか | Spark・OpenSearch・Grafana で絞れる |

- ルーター側で絞ると、量は減るが、送らなかったログは後から見られない。
- 受ける側で絞ると、全部残るが量が増える。
- lab は中間で、debug だけルーター側で落とす。Cisco では `logging trap <重要度>` がルーター側の絞り込みにあたる。

### Q. subsystem って画像の 8 個のこと？

**A. 別物。数が 8 つで同じなのはたまたま。**

| | 画像の 8 つ（ファシリティ） | lab の 8 つ（subsystem） |
|---|---|---|
| 中身 | authpriv、cron、kern、lpr、mail、news、syslog、local0〜7 | bgp、chassis、evpn、isis、lag、linux、netinst、xdp |
| 誰が決めたか | syslog の標準（共通） | SR Linux 独自 |
| 細かさ | 粗い | 機能ごとに細かい（全部で数十種類。lab はそのうち 8 つ） |

SR Linux は送る前に subsystem で「BGP と IS-IS だけ」のように選べる。外へ送るときは標準のファシリティも付く。

### Q. lab から送る syslog の形式自体は Cisco と同じってこと？

**A. 外側の形（ヘッダー）は同じ標準だが、標準には新旧 2 つの版がある。本文はメーカーごとに違う。**

| | RFC 3164（旧、BSD 形式） | RFC 5424（新） |
|---|---|---|
| 例（形の説明用） | `<189>Oct  3 12:00:00 router1 ...`（1 桁の日は空白で 2 桁に埋める） | `<190>1 2026-10-03T12:00:00.123+09:00 leaf1 bgp - - - ...` |
| 時刻 | 年もタイムゾーンも無い | 年・秒の小数（最大マイクロ秒）・タイムゾーンまで |
| 使っているところ | Cisco IOS（BSD 形式に近いが RFC 3164 どおりではない。ホスト名が無く、番号や ms 付きの時刻が入る） | lab の SR Linux |

- どちらも先頭の `<数字>` が PRI。
- 本文はメーカーごとに違う。
  - Cisco: `%LINEPROTO-5-UPDOWN: ...`
  - SR Linux: 独自の文面
- 本文を読む処理（Spark の検知など）は、メーカーごとに読み方を用意する必要がある。lab が IF の状態を syslog ではなく gNMI や SNMP で見ているのは、そのほうが機械で扱いやすいから。
- まとめ: 送り方と PRI の考え方は共通。ヘッダーの版と本文は機器による。

### Q. PRI とは？

**A. Priority の略で、syslog の先頭の `<数字>`。ファシリティと重要度を 1 つの数値にしたもの。**

```
PRI = ファシリティの番号 × 8 + 重要度
```

| PRI | 計算 | 意味 |
|---|---|---|
| `<189>` | 23 × 8 + 5 | local7・notice（Cisco の `%LINEPROTO-5-UPDOWN`） |
| `<190>` | 23 × 8 + 6 | local7・informational |
| `<3>` | 0 × 8 + 3 | kern・error |

- 主なファシリティの番号: kern 0、user 1、mail 2、cron 9、authpriv 10、local0 16、local7 23。
- 読むときは 8 で割る。商がファシリティ、余りが重要度（189 ÷ 8 ＝ 23 余り 5）。
- 標準の PRI は両方を合わせた値。SR Linux の `priority match-above` の priority は重要度だけを指す。

### Q. 8 は 8 進数にしてるってこと？

**A. 半分正解。** PRI 自体は 10 進数で書く。「× 8 ＋ 重要度」は 8 進数で桁をずらすのと同じ操作なので、8 進数に直すと一番下の桁が重要度になる。

```
189（10進数）＝ 275（8進数）
  下の桁 5 → 重要度（notice）
  上の 27（8進数）＝ 23（10進数）→ ファシリティ（local7）
```

重要度が 0〜7 の 8 種類なので、ちょうど 3 ビットに収まる。プログラムでは「3 ビット右シフトで商、下 3 ビットで余り」。

### Q. ファシリティは Cisco と SR Linux で違うってこと？

**A. 一覧と番号は共通の標準。違うのは「どれを使って送るか」の既定値。**

| | Cisco IOS | SR Linux |
|---|---|---|
| 既定のファシリティ | local7 | local6（SR Linux 自身の機能のログ） |
| 変え方 | `logging facility <名前>` | `system logging subsystem-facility` |
| informational の PRI | `<190>` | `<182>` |

- SR Linux の中身は Linux なので、authpriv（ログイン認証）や kern も出てくる。
- 既定のままなら、Cisco と SR Linux を同じサーバーに送るとファシリティで機器の種類を見分けられる。全部の機器を同じ local に揃える運用もあり、lab は本番の Cisco に合わせて local7 に揃えたので（3 章）、lab では見分けられない。

### Q. Cisco には cron や mail ではなく local7 のログがあるってこと？

**A. そう。** Cisco IOS は Unix ではないので、機器のログは全部まとめて 1 つのファシリティ（既定 local7）で送る。「どの機能のログか」は本文に書かれる。

```
<189>... %LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/1, changed state to down
          機能名  重要度  種類
```

**紛らわしい点**: Cisco は本文先頭の機能名（`LINEPROTO`、`OSPF` など）も「facility」と呼ぶ。

| | 標準のファシリティ | Cisco の本文の「facility」 |
|---|---|---|
| どこにあるか | 先頭の PRI の中 | 本文の `%` の直後 |
| 値 | local7 など | LINEPROTO、OSPF、BGP など（数百種類） |

| | 機器全体の分類（PRI の中） | 機能ごとの分類 |
|---|---|---|
| Cisco | local7 | 本文の `%LINEPROTO` など |
| SR Linux | local6（既定。lab は local7 に変えた） | subsystem（bgp、isis など） |

講義で Cisco の「ファシリティ」が出てきたら、PRI の中の話か本文の話かを確かめる。

### Q. SR Linux も local7 を送れないの？

**A. 送れる。** `set / system logging subsystem-facility local7`

- 変わるのは SR Linux の機能のログだけ。
- 送り先ごとには変えられない。remote-server の `facility` は付け替えではなく「このファシリティだけ送る」という絞り込み。
- 前に「`subsystem linux` は Linux 側のログ（authpriv など）」と答えたのは誤り。正しくは SR Linux のアプリ `linux_mgr` のログ。lab が送る 8 つの subsystem は全部 SR Linux 自身のログなので、全部 local7 になる。

---

## 3. lab の syslog を local7 にした

### Q. 本番は Cisco を想定しているので、local7 で syslog を送ってほしい

**A. lab の SR Linux 6 台を local7 で送る設定にした。**

- `lab/gen_lab.py` に `set / system logging subsystem-facility local7` を加えた（定数 `LOG_FACILITY`）。`lab/srlinux/*.cli` は生成し直した。
- `tests/test_stream.py` に「6 台とも local7」のチェックを加えた。
- Telegraf と Spark はファシリティの値を見ていないので、その先には影響しない。
- 実機ではまだ確かめていない。次に lab を立てたら、lab の EC2 で受信を見て、informational が `<190>1 ...` になっていることを確かめる。

  ```bash
  sudo tcpdump -A -n -i any udp port 5140
  ```

---

## 4. デバッグ用の EC2（lab + Telegraf）

### Q. lab と Telegraf を入れたデバッグ用の EC2 を用意したい。設定やソースは共通化して terraform 側とずれないように。運用を分かりやすくするため CloudFormation で

**A. CloudFormation のスタック `<接頭辞>-lab-debug`（`cloudformation/lab-debug.yaml`）を作った。** MSK・ECS・NLB を作らずに、機器の設定と Telegraf の設定を 1 台で確かめるためのもの。

**使い方**

| コマンド | すること |
|---|---|
| `ops/lab-debug.sh up` | イメージと `lab/` を置き、スタックを作る・変える |
| `ops/lab-debug.sh sync` | `lab/` を置き直して EC2 を再起動する |
| `ops/lab-debug.sh status` | スタックと EC2 の状態 |
| `ops/lab-debug.sh down` | スタックを消す（`ops/down.sh` では消えない。次の Q） |

- EC2 の中では次のコマンドが使える。
  - `sudo lab telegraf logs -f`: Telegraf の出力（MSK に載るのと同じ JSON）
  - `sudo lab telegraf test` / `sudo lab telegraf gnmi`
- 待機の費用は約 $0.23/h（EC2 $0.17/h とエンドポイント 4 本。次の Q で自分の VPC を持つ形にしてから）。

**共通化したところ**

- 版とイメージの作り方は `ops/lab-common.sh` 1 か所にした。`up.sh` と `lab-debug.sh` の両方が読む。
- EC2 の中の支度は `lab/setup.sh` 1 つ。terraform の user_data も CloudFormation の UserData も、env を書いてこれを呼ぶだけ。違うのは `TELEGRAF_IMAGE` が空か値があるか（と、次の Q からはイメージのリポジトリの名前）だけ。
- Telegraf は stream の ECS と同じイメージと同じ `telegraf.conf.in` を使い、出力だけ `SINK=stdout` にする。
- 機器は trap を 162 に送る。デバッグ用の EC2 では iptables の REDIRECT で Telegraf の 1162 へ回す。
- `tests/test_lab_debug.py` が次の一致を確かめる。
  - 版の既定値
  - IAM の権限
  - 2 つの user_data
  - Telegraf の入力（kafka と stdout で同じか）

### Q. Telegraf は何が変わった？

**A. ECS で動いている Telegraf の動きは変わらない。同じイメージをデバッグ用の EC2 でも動かせるようにしただけ。** この時点では収集の中身（inputs と processors）には手を入れていない（その後 `inputs.syslog` の `syslog_standard` を `SYSLOG_STANDARD` で選ぶようにした。最後の Q）。

- `telegraf.conf.in`
  - 出力を `# >>> sink kafka` と `# >>> sink stdout` の区間に分けた。
  - stdout の区間は `[[outputs.file]]` で、形式は json、時刻は秒。
- `telegraf.sh`
  - 環境変数 `SINK`（既定 `kafka`）で出力を選び、選ばなかった区間を消す。
  - `SINK=stdout` では `KAFKA_BROKERS` も MSK の IAM 用の `aws_config` も要らない。
  - `tg render`（設定を作るだけ）を足した。
- 影響: `telegraf/` の中身が変わったのでイメージのタグ（ディレクトリのハッシュ）が変わる。次の `ops/up.sh` でビルドし直し、ECS のタスクが入れ替わる。

### Q. デバッグ用の EC2 は ops/up.sh や ops/down.sh とは別にして。CloudFormation だけで運用したい

**A. スタックだけで閉じる形にした（2026-10-04）。** `ops/up.sh` / `ops/down.sh` はもうデバッグ用の EC2 を作らない・消さない・見ない。扱うのは `ops/lab-debug.sh` だけ。

**前の形で困ること**

- 土台（`terraform/base/core`）の VPC・サブネット・バケットと、`ops/up.sh` が足す ECR のエンドポイントを借りていた。
- なので先に `ops/up.sh` が要り、`ops/down.sh` で土台を消すときはスタックを先に消す必要があった。

**今の形**

| スタックが持つもの | 中身 |
|---|---|
| VPC | 既定 `10.20.0.0/24`、1 AZ・1 サブネット。IGW も NAT も無い閉域。どこともつながないので土台と CIDR が重なってよい |
| エンドポイント | ssm / ssmmessages（SSM で入る）、ecr.api / ecr.dkr（イメージを引く）の 4 本と、S3 の gateway（無料） |
| バケット | `<接頭辞>-lab-debug-<アカウント>`。`lab/` だけを置く |
| ECR | `<接頭辞>-debug-lab-srlinux` / `-debug-lab-multitool` / `-debug-telegraf`。スタックを消すとイメージごと消える |
| ロール | 前と同じ権限。`NETWORK_PERIMETER` のときは VPC の外からの呼び出しを拒む Deny を、この VPC に向けて持つ |

- `ops/lab-debug.sh up` の初回は、EC2 の無い器を先に作り、イメージと `lab/` を置いてから EC2 を作る（置く前に EC2 を起こしても引けないため）。
- `ops/lab-debug.sh down` はバケットを空にしてからスタックを消す。
- `deploy.env` の `LAB_DEBUG` は使わない。残っていれば `ops/up.sh` が注意を出すだけ。
- 代わりに増えたもの:
  - 待機の費用: 約 $0.20/h → 約 $0.23/h（エンドポイントを土台と共用しなくなった。2026-10-04 に EC2 の単価を t4g.xlarge の $0.17/h に直した値）
  - 初回の push: SR Linux（約 1 GB）を別のリポジトリにもう一度置く

---

## 5. 本番の Cisco から送るとき

### Q. ルーターの `logging host <IPアドレス | ホスト名>` の host は AWS の NLB になるってこと？

**A. はい。Telegraf を今の構成（ECS + 内部 NLB）のまま使うなら、NLB の IP を書く。**

- **今の lab は NLB を直接指していない。**
  - SR Linux の宛先は lab の EC2 の docker ネットワークのゲートウェイ `203.0.113.1:5140`。
  - それを lab の EC2 が iptables で NLB へ DNAT している（`lab forward`）。
- **本番の Cisco ならこう書く。**

  ```
  logging host <NLB のプライベート IP> transport udp port 5140
  logging facility local7
  logging trap informational
  ```

- **送り元とヘッダー**
  - `logging source-interface <管理 IF>` で送り元 IP を機器の管理 IP に固定する（Spark は送り元 IP で機器を引く）。
  - ホスト名を載せる `logging origin-id hostname`、番号を消す `no logging message-counter syslog`、時刻の形を決める `service timestamps log datetime msec` は、RFC3164 の解析がどう変わるかを実機で確かめてから決める。
  - `logging facility local7` と `logging trap informational` は IOS の既定と同じなので、書かなくても変わらない（明示のため書く）。
- **ポート**
  - Cisco の既定は 514。Telegraf は 5140 で待っているので（非 root は 1024 未満で待てない）、`port 5140` が要る。
  - trap は `snmp-server host <NLB の IP> version 2c <community>` と `snmp-server enable traps snmp linkdown linkup` で 162 に送れば、NLB が Telegraf の 1162 へ渡す（版を書かないと v1 で送る。Telegraf は 2c で受ける）。
  - Cisco の linkDown の varbind には ifName が無い（ifIndex・ifDescr など）。Spark は ifName → ifDescr → ifIndex の順で IF を引くので ifDescr で引くことになり、SR Linux の ifName とは名前の形が違う。
- **経路**
  - 内部 NLB なので、オンプレから届くには Direct Connect か Site-to-Site VPN が要る。
  - NLB の SG でオンプレの CIDR を通す必要もある（今は `terraform/base/core/security_groups.tf` の通信の表で lab の `203.0.113.0/24` だけ）。NACL はコードで作っていない（既定で全部通す）ので、絞っている環境だけ見直す。
- **IP の固定**: NLB を作り直すと IP が変わる。機器に IP を書くなら、`subnet_mapping` の `private_ipv4_address` で固定したほうが安全。今のコードは固定していない（`subnets` から `subnet_mapping` に変えると NLB は 1 回作り直しになる）。
- **形式**: Cisco IOS の既定は BSD 形式（RFC 3164 に近いが、そのままではない）。Telegraf は RFC3164 で受けるが、きれいに解析できるかは実機で確かめる（次の Q）。

### Q. syslog_standard を環境変数で選べるようにして。既定は RFC3164 に

**A. Telegraf の syslog の形式を `SYSLOG_STANDARD` で選べるようにした。既定は `RFC3164`。**

| どこ | 中身 |
|---|---|
| `telegraf/telegraf.sh` | `SYSLOG_STANDARD`（既定 `RFC3164`。空も既定。大文字の `RFC3164` / `RFC5424` 以外は止まる）で `telegraf.conf.in` の `syslog_standard` を埋める |
| `terraform/pipeline/stream` | 変数 `syslog_standard`（既定 `RFC3164`）を ECS タスクの環境変数 `SYSLOG_STANDARD` に渡す |
| `ops/up.sh` | `deploy.env` の `SYSLOG_STANDARD`（空なら `RFC3164`）を渡す（次の Q で足した） |
| `lab/lab.sh` | デバッグ用の EC2 の Telegraf に `LOG_STANDARD=RFC5424` を渡す |

- 本番の Cisco を受けるときは RFC3164 にする。`terraform/pipeline/stream` を直接打つなら、`syslog_standard` を渡さなければ既定の RFC3164 になる。`ops/up.sh` も書かなければ RFC3164（次の Q）。lab の SR Linux（RFC 5424 で送る）のログまで見るときだけ `RFC5424` にする。
- Cisco IOS の既定のヘッダー（シーケンス番号や `*` 付きの時刻、ホスト名の有無）が RFC3164 でどう解析されるかは、実機で確かめていない。

### Q. ops/up.sh で syslog の形式を切り替えられるようにして

**A. `deploy.env`（か環境変数）の `SYSLOG_STANDARD` で選ぶようにした。空なら本番の Cisco に合わせて `RFC3164`。**

| どこ | 中身 |
|---|---|
| `ops/up.sh` | `SYSLOG_STANDARD`（空なら `RFC3164`）を stream の `syslog_standard` に渡す。大文字の `RFC3164` / `RFC5424` 以外は何も作る前に止まる。lab の SR Linux の形式（`ops/lab-common.sh` の `LAB_SYSLOG_STANDARD` = `RFC5424`）と違えば「lab のログの項目が崩れる」と注意を出す |
| `ops/deploy-env.sh` / `deploy.env.example` | 読めるキーに `SYSLOG_STANDARD` を足した |

```bash
SYSLOG_STANDARD=RFC5424 PIPELINE=1 ops/up.sh   # lab の SR Linux のログまで見るとき
```

- 既定は stream の変数と同じ `RFC3164`（本番の Cisco に合わせる）。lab の SR Linux は RFC 5424 で送るので、既定のままだと lab のログはホスト名・本文などがきれいに取れない。lab のログまで見るときだけ `RFC5424` にする。
- 変えて打ち直すと、ECS の Telegraf のタスクが入れ替わる（環境変数が変わるので）。
- デバッグ用の EC2 の Telegraf は lab 専用なので、この値によらず `lab/lab.sh` の `LOG_STANDARD`（RFC5424）のまま。

## 6. SNMP のポーリングを既定で止めた

### Q. snmp ポーリングはデフォルトでは無効にして、snmp trap だけにしたい。環境変数で書き換えられるようにできる？

**A. Telegraf の SNMP のポーリング（`inputs.snmp`）を既定で止め、SNMP は trap（`inputs.snmp_trap`）だけ受けるようにした。`SNMP_POLL=1` で戻せる。** gNMI と syslog は変えていない。

| どこ | 中身 |
|---|---|
| `telegraf/telegraf.conf.in` | `[[inputs.snmp]]` を `# >>> snmp_poll` 〜 `# <<< snmp_poll` で囲んだ |
| `telegraf/telegraf.sh` | `SNMP_POLL`（既定 `0`。`0` / `1` 以外は止まる）が `0` ならその区間を消す。`SNMP_AGENTS` を見るのは `1` のときだけ。`tg test` は `0` なら「止めてある」と出して終わる |
| `terraform/pipeline/stream` | 変数 `snmp_poll`（bool、既定 `false`）をタスクの環境変数 `SNMP_POLL`（`1` / `0`）に渡す。ECS Exec の既定のコマンド（出力 `telegraf_exec_command`）を `tg test` から `tg gnmi` にした |
| `ops/up.sh` / `ops/deploy-env.sh` / `deploy.env.example` | `deploy.env` の `SNMP_POLL`（`1` / `0`、`true` / `false` も可）を stream の `snmp_poll` に渡す。読めるキーに足した |
| `lab/lab.sh` | デバッグ用の EC2 の Telegraf にも `SNMP_POLL`（既定 `0`）を渡す |

```bash
SNMP_POLL=1 PIPELINE=1 ops/up.sh     # ポーリングもするとき（deploy.env に SNMP_POLL=1 でもよい）
sudo SNMP_POLL=1 lab telegraf run    # デバッグ用の EC2 で、ポーリングありで起こし直す
```

- **止めると空になるもの:** `metrics` トピック（measurement `system` / `interface`）が出なくなるので、Grafana のダッシュボード「netops / SNMP metrics」、エージェントの `query_metrics`、S3 Tables のポーリングの行が空になる。
- **アラート:** Grafana のルール `link_down` はポーリングの `ifOperStatus` を見るので発火しない。IF の up / down は trap から Splunk（`SINK_SPLUNK=1`）が `link_down` を出す。そのため `ops/up.sh` は Grafana を `SNMP_POLL=1` のときだけアラートの送り手に数え、既定のまま `WORKFLOW=1` にすると「`SINK_SPLUNK=1` か `SNMP_POLL=1` が要る」と出して止まる。Grafana と `SINK_PROMETHEUS` があってどちらの送り手も無いときは注意を出す。
- NLB のヘルスチェック（`outputs.health`）は、何も書いていないうちは 200 を返すので、ポーリングを止めても通る。Spark は無いトピックを作るので、`metrics` が無くても動く。
- 変えて打ち直すと、ECS の Telegraf のタスクが入れ替わる（環境変数が変わるので）。

---

## 7. Nautobot（機器の一覧とケーブルの正）

### Q. Neptune のグラフの追加は、Nautobot の Job がやっているという理解で合ってる？

**A. 物理層の更新だけが Job。** Neptune に書くものは 4 つあり、書き手が分かれている。

| 書くもの | 書き手 |
|---|---|
| 全層（物理 / IP / EVPN・BGP）の最初の投入 | `ops/up.sh` の手順 7-3b（`lab/lab_topology.py` の出力を `ops/seed_graph.py` が入れる） |
| 物理層（機器・IF・回線）の差分 | Nautobot の Job（`nb_sync.sync` → `agent/graph.py` の `sync_physical()`。openCypher） |
| `status`（アラートで変わる） | graph の Lambda（`graph/status_handler.py`） |
| 修復案 | ワークフローと Web |

- Job は `status` と IP 層より上には触らない。IP 層・EVPN/BGP 層は Nautobot からは入らない。
- Job は Neptune のほかに、Telegraf の取りにいく側（dialin）の機器の一覧（SSM のパラメータ）も書き換える。

### Q. Nautobot を機器の正本として常に起動させたい。環境変数でオン・オフを切り替えるのではなく、いつも起動するようにして

**A. `NAUTOBOT` の切り替えをやめ、`PIPELINE=1` ならいつも立つようにした。**

- 立たないのは、`PIPELINE=0` のときと、`SKIP_STREAM` と `SKIP_GRAPH` を両方書いたとき（Job の書き先が無い）。
- 前の `deploy.env` に `NAUTOBOT=...` が残っていても止まらない。`ops/up.sh` が「もう使わない」と注意を出す。
- Telegraf の dialin の一覧は、いつも Nautobot の Job が書く SSM のパラメータ（`/<prefix>/telegraf-dialin/nautobot/*`）から受ける。
- 費用は Nautobot の分（$0.14/h）が PIPELINE に入り、約 $1.63/h。
- デバッグ用の EC2（`ops/lab-debug.sh`）は Nautobot を使わない（lab の定義の一覧のまま）。

### Q. Nautobot にトポロジの情報を入れているのはシェルスクリプトだと思うけど、どこからの情報を引っ張ってきて入れている？

**A. リポジトリの中の lab の定義ファイルから。** 実機や AWS から取ってきてはいない。入れるのはシェルではなく、コンテナの中の Python。

1. 元の情報: `lab/splab.clab.yml.in`（containerlab の機器と配線）と `lab/srlinux/<機器>.cli`（SR Linux の設定）。
2. 変換: `ops/up.sh` がイメージを作るときに手元で `lab/lab_topology.py` を実行し、機器 8 台・回線 12 本を `lab_seed.json` にしてイメージに入れる。
3. 投入: コンテナが起動時に `nautobot/netops/bootstrap.py` を実行し、**機器が 1 台も無いときだけ** `lab_seed.json` から入れる。

- 入るもの: 拠点、役割、機器、インタフェース（LAG を含む）、アドレス、管理 IP、ASN、Service（`gnmi` / `snmp`）、ケーブル（主 / 副と帯域）。
- 2 回目からは seed を飛ばすので、Nautobot で変えた内容が正になる。`ops/down.sh` で DB ごと消えるので、作り直すとまた lab の定義から入る。
- 実機なら LLDP や gNMI で取るところを、PoC では定義ファイルで代用している。

### Q. 本番だったら、SDN などの機器を管理しているワーカーが、Nautobot に API で格納すればいいってこと？

**A. その通り。** REST API（GraphQL もある）で機器・インタフェース・ケーブルを書けば、あとは今の仕組みがそのまま動く（変更 → JobHook → Job → SSM と Neptune）。先に API で入れれば、機器が 0 台ではなくなるので lab の seed は入らない。

本番で決めること:

- **どちらが正か。** ふつう Nautobot は「あるべき姿」（設計・台帳）、SDN コントローラは「実際の姿」を持つ。コントローラから一方的に流し込むと、Nautobot は正本ではなくコントローラの写しになる。正本にするなら、Nautobot で変える → コントローラや機器へ反映、の向きにして、実機との差分は検出だけにする。
- **取り込み方。** 外のワーカーが API で書くほかに、Nautobot の側から取りにいく方法がある（下の質問）。
- **Job が読む項目に合わせる。** Job は Device の Service `gnmi` / `snmp` を監視対象の印にし、ケーブルの主 / 副と帯域、ASN などを決まった場所から読む（対応は `nautobot/netops/nb_map.py`）。同じ形で入れないと Telegraf や Neptune に映らない。
- 今の構成は閉域で、Nautobot は VPC の中からしか届かない。外のワーカーから書くなら経路と API トークン（発行と SSM での保管）が要る。PoC にはどちらも入っていない。

### Q. Nautobot から取りにいくというのは、Nautobot の Job が取りにいくということ？

**A. そう。** SSoT アプリや Device Onboarding アプリの中身は Job の集まり。

- Device Onboarding: Job が実機に SSH などで入り、機種・インタフェース・アドレスを読んで台帳に書く。
- SSoT: Job が外のシステム（SDN コントローラや別の台帳）の API を呼び、差分を出して台帳に書く。逆向き（Nautobot から外へ）もできる。

向きが違うだけで、どれも同じ Job の仕組み。

| | 向き |
|---|---|
| 今の PoC の Job | Nautobot の台帳 → 外（SSM と Neptune） |
| 取り込みの Job | 外（実機やコントローラ）→ Nautobot の台帳 |

取り込みの Job を足すなら、worker が実機やコントローラに届く経路が要る。今の PoC の worker は閉域の VPC の中にいて、lab の機器へ取りにいく設定は入っていない。

### Q. そもそも Nautobot は、データベースと Job が一緒になっているということ？

**A. そう。台帳（データベース）と、Job を動かす基盤が 1 つにまとまっている。**

| 部品 | 役割 | この PoC では |
|---|---|---|
| Web / API | 画面と REST API・GraphQL | ECS のタスクの web コンテナ |
| データベース | 機器・インタフェース・ケーブルなどの台帳 | RDS の PostgreSQL |
| Job | Nautobot の中で動く Python のプログラム。台帳を直接読み書きできる | `nautobot/jobs/netops_jobs.py` |
| Celery worker | Job を実際に動かすプロセス | 同じタスクの worker コンテナ |
| Redis | web から worker へ Job を渡すキュー | 同じタスクの Redis コンテナ |

- Job は Nautobot のプロセスの中で動くので、API を通さずに台帳を扱える。
- Job が動くきっかけは 4 つ: 画面のボタン、スケジュール、API、台帳の変更（JobHook）。この PoC は JobHook（`netops-sync`）と、起動時の 1 回。

### Q. Nautobot はもともと Web・データベース・Job・Celery・Redis がセットになったもの？ 今回新しく足したわけではない？

**A. この構成は Nautobot の標準で、今回足したものではない。** ただし全部が 1 つのイメージに入っているわけではなく、PostgreSQL と Redis は Nautobot が「必ず要る」と決めている外の部品で、使う側が用意する。

| 部品 | もともとか | この PoC での用意 |
|---|---|---|
| Web / API、Job の仕組み、Celery worker | Nautobot 本体（公式イメージに入っている。worker は同じイメージを別のコマンドで起こす） | 公式イメージ 3.2.6 をそのまま使う |
| PostgreSQL | Nautobot の必須の部品（本体には入っていない） | RDS |
| Redis | Nautobot の必須の部品（本体には入っていない） | 同じタスクの中の Redis コンテナ |

今回足したのは、Nautobot の上で動く中身だけ。

- Job のコード（`nautobot/jobs/netops_jobs.py`）と、台帳とトポロジの対応付け・同期（`nautobot/netops/nb_map.py` / `nb_sync.py`）
- 起動時の用意（`nautobot/netops/bootstrap.py`: 管理者、API のユーザー、custom field、最初の seed、Job の有効化と JobHook）
- 公式イメージに boto3 と上のファイルを足す `nautobot/Dockerfile`

### Q. 運用管理者ダッシュボードの Web から機器の変更ができたと思うけど、その変更先を Nautobot にして、変更を検知した Nautobot の Job が Neptune に書きにいく、という構成でいいのでは？

**A. その構成にした。** Web の「トポロジ」タブのリンクの追加・削除は Nautobot の REST API に書き、Nautobot の JobHook が呼ぶ Job が Neptune の物理層（と Telegraf の一覧）に反映する。Web が Neptune の物理層を直接書くことは無くなった。

```mermaid
flowchart LR
  W["Web の「トポロジ」タブ<br/>リンクの追加・削除"] -->|"REST API"| NB["Nautobot（台帳）"]
  NB -->|"JobHook → Job"| N["Neptune の物理層"]
  N -->|"再読み込み"| W
```

- 前は、Nautobot があるあいだ Web の編集を止めていた（Web が Neptune に書いても、次の同期で Nautobot の中身に戻るため）。書き先を Nautobot にすれば、正が 1 か所のまま Web からも変えられる。
- Web の画面にあるのはリンク（ケーブル）の追加・削除だけ。機器の追加・削除は前から画面に無く、Nautobot の画面でする。
- 反映は数秒〜十数秒あと（Job が走ってから）。画面の「再読み込み」で確かめる。
- 種別（fabric / l2 / lag）は Web で選んだものではなく、両端の機器の Role と LAG から決まる。
- 「静的データを投入」は Nautobot があるあいだ使えない（Job が Nautobot の中身に戻すため）。
- Web が使う API のトークンは SSM の SecureString（`/<prefix>/nautobot/api-token`）。

詳しくは [nautobot.md](nautobot.md) の 5 章。

---

## 8. Neptune Database と Neptune Analytics

### Q. Neptune Database と Neptune Analytics の使い分けは？ いまの構成でも問題ない？

**A. いまの構成（Neptune Analytics にトポロジと status を置く）で問題ない。** この PoC の使い方は Analytics のほうに合っている。ただし AWS の上ではまだ動かしていない（2026-10-04 時点）。

使い分けは次のとおり。Database は「書き込みを落とさず持ち続ける置き場」、Analytics は「載せて調べる道具」。

| | Neptune Database | Neptune Analytics |
|---|---|---|
| 向いている用途 | 業務の正本。小さな読み書きが常に大量に来る | 分析。グラフ全体をメモリに載せて、経路や影響範囲を調べる |
| 問い合わせ | Gremlin、openCypher、SPARQL | openCypher だけ |
| 分析の機能 | 自分で書く | 経路、中心性、コミュニティなどのアルゴリズムとベクトル検索が組み込み |
| データの入れ方 | 書き込みを積む。一括ロードもある | S3 から一括で載せるのが速い。書き込みもできる |
| 構成 | クラスタとインスタンス。リードレプリカ、複数 AZ | グラフ 1 つにメモリ量を指定するだけ |

いまの構成に合う理由:

- **データが小さく、書き込みが少ない。** トポロジは機器とリンクが数十件で、書くのは status の更新だけ。Database の強み（大量の同時書き込み、レプリカ）を使う場面が無い。
- **正本を Neptune に置かない方針と合う。** 修復案や履歴の正は S3 Tables で、Neptune は壊れても作り直せる置き場。S3 から一括で載せるのが得意な Analytics は、あとで履歴をグラフで分析するときにもそのまま使える。
- **やりたい問いが分析寄り。** 「このリンクが落ちたら、どの機器に影響するか」は経路や到達可能性の問いで、組み込みのアルゴリズムが使える。

気を付ける点:

| 点 | 中身 |
|---|---|
| 書き込みの集中 | アラートが一度に大量に来て status の更新が重なる使い方は、本来 Database の領分。PoC の量なら問題にならない見込み |
| 料金 | Analytics はメモリ量 × 時間の課金で、最小構成でも動かしているあいだは掛かる。単価と、止めておけるかは確かめていない |
| 未確認 | 閉域のエンドポイントと IAM の Deny が通るかは、`ops/up.sh` を流すまで分からない |

Database に戻すのは、次のどれかに当てはまったとき:

- status 以外の業務データも Neptune を正本にして、常時書き込むようになった
- 複数 AZ での可用性やリードレプリカが要件になった
- Gremlin や SPARQL が要る

何をどこに置いているかは [data-stores.md](data-stores.md)。

---

## 9. 障害の情報をどこに残すか

2026-10-04 に聞いたこと。ここでの結論が「アラートの履歴を残す（Cycle 001）」の設計になった（[設計](cycles/001-alert-history-firehose/design.md)。実装は main に入る前）。

### Q. いまネットワークの障害情報はどこに書いてる？

**A. 障害の履歴（開いた・閉じた）を書いている場所は、聞いた時点では無かった。** 2026-10-02 に検知を Spark から Grafana と Splunk に移したとき、それまでの置き場（Neptune の頂点 `anomaly` と S3 Tables の `anomaly_events`）をやめ、新しい置き場を決めていなかった。

| 知りたいこと | どこで見るか | 補足 |
|---|---|---|
| いま何が落ちているか | Neptune の機器・IF・層の頂点の `status`。Web の「トポロジ」タブ | Lambda graph-status がアラートを受けて書き換える。前の状態は残らない |
| アラートが出た・消えた履歴 | Grafana と Splunk のアラートの履歴 | ECS のタスクを止めると消える |
| 1 回の障害で何をしたか | S3 Tables の `proposal_events`（作成・承認・適用・確認を 1 行ずつ） | 修復案を作らなかった障害は残らない |
| 機器から来た生データ | S3 Tables の生データのテーブル | 読む側（Athena）は聞いた時点では未配備 |

- `proposal_events` は修復案の流れの記録で、障害の記録ではない。
- `ops/down.sh` はテーブルバケットごと消すので、履歴も消える。

### Q. 障害情報は S3 に持っておくのは適切？

**A. 履歴の置き場としては適切。「いま開いている障害」の一覧には使わない。**

向いている理由:

- **追記するだけの記録だから。** 開いた・閉じたは、書いたら書き換えない。`proposal_events` と同じ使い方になる。
- **量が少ない。** 1 回の障害で数行。費用はほぼ掛からない。
- **生データと合わせて集計できる。** 同じ場所にあるので、「この機器で月に何回落ちたか」「障害の前後のメトリクス」を SQL で出せる。
- **方針に合う。** Neptune にはトポロジと `status` だけを置く。

向いていないところと対処:

| 弱いところ | 中身 | 対処 |
|---|---|---|
| 「いま開いている障害」を見る | 1 行を書き換えるのが苦手。開いた行と閉じた行を突き合わせないと分からない | Neptune の `status` と、Grafana / Splunk のアラートの状態で見る |
| 読む手段 | Athena が要る | 「アラートの履歴を残す（Cycle 001）」で Athena まで作る |
| Lambda から書きにくい | PyIceberg と pyarrow は Lambda の素の zip には重い。同時に何本も動くと、同じテーブルへの書き込みがぶつかる | Lambda は Firehose に送るだけにし、Firehose がまとめて書く |
| 環境を壊すと消える | `ops/down.sh` がテーブルバケットごと消す | PoC のあいだは消えてよいと決めた |

Lambda から書く経路は 2 案あった。

| 案 | 中身 | 判断 |
|---|---|---|
| Data Firehose → S3 Tables | Lambda は Firehose に 1 件ずつ送るだけ | **採った。** 生データの流れ（Spark）が止まっていても履歴が残る |
| MSK の新しいトピック → Spark → S3 Tables | Spark のいまの書き込みに乗る。新しいサービスは要らない | 採らない。Spark が止まっているあいだは書かれない |

### Q. worker（Temporal）からのほうが、複数のソースから障害情報を取得したあとに整形して S3 に書ける？ 同じ情報を agent に渡せば情報源が揃う？

**A. どちらもできる。ただし、ワークフローの中で書くと障害の一部しか残らないので、書く場所を 2 つに分ける。**

- **集めて書くのはできる。** worker はすでに Neptune を読み、PyIceberg で S3 Tables に追記している。ログ（OpenSearch）、メトリクス（Prometheus）、Nautobot の変更履歴を取る処理は `agent/evidence.py` にあり、worker から呼べる。足りないのは worker の IAM とエンドポイントの環境変数。
- **ワークフローは全部の障害を見ていない。** 起こすのは `link_down` だけ。保守中の機器の通知、重複、閉じたあとに届いた解消は捨てている。
- **情報源は、聞いた時点では揃っていない。** エージェントに渡しているのはアラートの 5 項目（機器、種類、対象、内容、発生時刻）だけで、証拠はエージェントが自分のツールで取り直している。何を見て判断したかは残らない。

揃えるには、ワークフローの `investigate` の前に証拠を集めるアクティビティ（`collect_evidence`）を置き、集めたものを S3 に書いて、同じものをプロンプトに入れる。気を付ける点:

| 点 | 中身 |
|---|---|
| ツールで取り直す余地 | 「まず渡した証拠で判断し、足りないときだけツールを使う」と指示し、使ったツールの呼び出しも記録する |
| 大きさ | Temporal でアクティビティのあいだに渡すデータには上限がある（既定で 1 件 2MB）。本体は S3 に置き、id と要約だけを受け渡す |
| ログの遅れ | OpenSearch に入るのが遅れることがある。集めた時刻を証拠と一緒に残す |

`collect_evidence` は別のサイクルでやる（まだ設計していない）。

### Q. Temporal が障害情報を S3 に記載するのは良くない？

**A. 悪くはない。Temporal に任せるのは「証拠を集めて整形する部分」までで、「開いた・閉じた」の記録は任せない。**

| 記録 | 書く担当 | 理由 |
|---|---|---|
| 開いた・閉じた（全部のアラート） | Lambda graph-status | SNS から全種類のアラートを受けていて、パイプライン（`PIPELINE=1`）と一緒にいつも作られる。Temporal の有無に左右されない |
| 整形した証拠と、エージェントの判断 | Temporal（アクティビティ） | 何か所かに取りにいき、失敗したら再試行する処理は Temporal が得意。集めて、同じものをエージェントに渡すまでを 1 か所でやれる |

「開いた・閉じた」を Temporal に任せない理由:

1. **記録が修復の仕組みに縛られる。** SQS と worker は `WORKFLOW=1` のときしか作られない。検知は Temporal が無くても動くのに、履歴だけが残ったり残らなかったりする。
2. **ワークフローが全部の障害を見ていない。** 上の Q のとおり。
3. **この PoC の Temporal は消える前提。** データはタスクの中の SQLite で、タスクが入れ替わると走っていたワークフローが消える。「開いた」は書けても「閉じた」を書く担当がいなくなる。

2 つの記録は、異常の id（`<device_id>#<kind>#<target>`）と発生時刻でつなぐ。

---

## 10. データの流し先とテーブル

### Q. ログ形式は OpenSearch、メトリクスは Prometheus に流してるよね？

**A. 合っている。** Spark が MSK のトピックを種類で振り分けている。

| 流し先 | 入るトピック | 中身 |
|---|---|---|
| OpenSearch（インデックス `snmp-logs`） | traps / logs | trap と syslog |
| Prometheus | metrics / gnmi / mdt | メトリクスの時系列 |
| S3 Tables の生データのテーブル | 5 つ全部 | 正本 |
| Splunk（`SINK_SPLUNK=1` のときだけ） | 5 つ全部 | 比較用 |

構成図は [architecture/pipeline.md](architecture/pipeline.md)。

### Q. Spark のジョブは 1 つで、Kafka の購読も 1 つ？

**A. ジョブは 1 つ。Kafka の購読は 1 つではなく、格納先ごとに 1 つずつ（最大 4 つ）。** `spark/snmp_sinks.py` の `build` が、格納先ごとに別のストリーミングクエリを起こしている。

| クエリ（格納先） | 購読するトピック | 有効になる条件 |
|---|---|---|
| iceberg（S3 Tables の生データ） | metrics / gnmi / mdt / traps / logs | `SINK_S3` |
| prometheus | metrics / gnmi / mdt | Prometheus の格納先が有効なとき |
| opensearch | traps / logs | OpenSearch の格納先が有効なとき |
| splunk | metrics / gnmi / mdt / traps / logs | `SINK_SPLUNK=1` |

- **1 つのクエリは、複数のトピックをまとめて 1 回で購読する。** トピックごとに購読を分けてはいない（`subscribe` にカンマ区切りで渡す）。
- **クエリごとに checkpoint が別。** どこまで読んだかを格納先ごとに覚えているので、Splunk への書き込みが遅れても、Prometheus の読み進みは止まらない。同じトピックを複数のクエリが読むので、Kafka からは同じ行を格納先の数だけ読むことになる。
- **どのクエリも 60 秒ごと**（`TRIGGER`）にまとめて書く。
- **1 つのクエリが止まったら、ジョブごと終わらせる。** EMR Serverless が起こし直し、どのクエリも checkpoint の続きから読むので、データは落ちない。
- ジョブは Terraform のリソースではなく、`ops/up.sh` が `start-job-run` で起こす。

### Q. Grafana は OpenSearch と Prometheus をデータソースにしてる？

**A. その 2 つ。** 定義は `grafana/provisioning/datasources/`。

| データソース | 接続先 | 入っているもの | 使い道 |
|---|---|---|---|
| Prometheus (AMP)。既定 | Amazon Managed Service for Prometheus | metrics / gnmi / mdt | ダッシュボード `metrics.json` と、アラートルール `link_down` |
| OpenSearch (logs) | OpenSearch Serverless の logs コレクション | traps / logs | ダッシュボード `logs.json` |

- 聞いた時点（2026-10-04）では、アラートは Prometheus だけを見ている。OpenSearch のログで発火するルールは無く、trap や BGP / IS-IS の落ちは Splunk が検知する。これを両方で揃えるのが「Splunk と Grafana のアラートを比べる（Cycle 002）」。
- S3 Tables は Grafana のデータソースではない。
- どちらも認証はタスクロールの SigV4 で、VPC エンドポイント経由。

### Q. snmp_metrics って何？

**A. 機器から来た生データを、全部そのまま溜めておく S3 Tables（Iceberg）のテーブル。**

- **入るもの。** MSK の 5 つのトピック（metrics / gnmi / mdt / traps / logs）の全部。Spark が up か down かを判断せず、行をそのまま追記する。どのトピックから来た行かは `topic` 列で分かる。
- **役割。** メトリクスとログの履歴の正本。OpenSearch と Prometheus は検索やグラフのための写し。
- **作られる条件。** `SINK_S3` が有効なときだけ。
- **名前。** SNMP のメトリクスだけではないので、`raw_telemetry` に改名すると決めた（ブランチ `rename-raw-telemetry` に実装済み。main にはまだ入っていない）。

### Q. S3 Tables には 1 つのテーブルしかない？ メトリクスもログも 1 つの同じテーブル？

**A. テーブルは 1 つではない。ただし生データに限れば、メトリクスもログも同じ 1 つのテーブルに入る。**

| 中身 | テーブル名 | 書く人 | 状態 |
|---|---|---|---|
| 機器から来た生データ（metrics / gnmi / mdt / traps / logs の全部） | `snmp_metrics`（`raw_telemetry` に改名予定） | Spark | `SINK_S3` が有効なときだけ作る |
| 修復案の証跡（作成・承認・却下・適用・確認） | `proposal_events` | Temporal の worker | いつも作る |
| アラートの通知の履歴（発火と解消） | `alert_events` | Lambda graph-status（Firehose 経由） | 「アラートの履歴を残す（Cycle 001）」で実装中 |

生データのテーブルの列は 8 つ（`terraform/pipeline/analytics/tables.tf`）。

- `ts`、`ingested_at`: 時刻
- `topic`: どのトピックから来たか。メトリクスとログはこの列で見分ける
- `measurement`、`agent_host`、`host`: Telegraf が付ける名前と送り元
- `tags_json`、`fields_json`: 中身。JSON の文字列のまま

メトリクスとログでは項目がまったく違うので、項目ごとの列は作らず、JSON の文字列 2 列に丸ごと入れている。読むときは `topic` で絞ってから JSON を取り出す。

何をどこに置いているかの全体は [data-stores.md](data-stores.md)。

---

## 11. Neptune に置くもの

### Q. Neptune には修復案は書かないよね？ status 更新だけよね？

**A. 聞いた時点（2026-10-04）の実装では、修復案も Neptune に書いている。これをやめて status だけにすると決めた。**

いまの実装で Neptune に入るもの:

| 入るもの | 書く人 | 読む人 |
|---|---|---|
| トポロジの `status` | Lambda graph-status | Web、エージェント |
| 修復案の「いま」（頂点 `proposal`。pending → approved …） | worker、Web の承認タブ | worker、Web の承認タブ、エージェント |

- 修復案の履歴は別で、S3 Tables の `proposal_events` に worker が 1 段ごとに追記している。同じ内容を 2 か所に書いている状態。
- 「修復案を S3 Tables にまとめる（Cycle 003）」で、修復案は `proposal_events` だけに置き、Neptune はトポロジと `status` だけにする（[設計](cycles/003-proposals-in-s3tables/design.md)。実装はまだ）。Web の承認は SQS で worker に届け、読むのは Athena。

### Q. Neptune Analytics で分析するときに障害情報や修復案も必要になるなら、プロパティとして入れたほうがいい？

**A. いまは入れない。正は S3 Tables に置き、グラフの分析で必要になったときに、S3 Tables から「写し」として Neptune に載せる。** そのときはプロパティでなく、機器や回線に辺でつないだ頂点にする。

- **プロパティに向くのは「いまの値が 1 つ」のものだけ。** 機器や回線の `status` がそれ。障害や修復案は 1 つの機器に何件も積み重なるので、プロパティには収まらない。
- **いまの修復案の頂点は、グラフとして使われていない。** 辺が 1 本も無く、id で引いて書き換えるだけ。S3 Tables に移しても分析で失うものは無い。
- **Neptune Analytics は、あとからデータを載せて分析する作り。** S3 のファイルを一括で読み込めるので、履歴が要る分析をやるときに、その期間の分だけ載せればよい。

| 分析 | 要るもの | いま足りているか |
|---|---|---|
| この障害で影響を受ける機器はどれか | トポロジ + いまの `status` | 足りている |
| link → IS-IS → BGP を 1 つの障害にまとめる | トポロジ + いまの `status` | 足りている |
| 隣の機器で過去に似た障害があったか | トポロジ + 障害の履歴 | 履歴を頂点として載せる必要がある |
| この処置は過去にこの構成で効いたか | トポロジ + 修復案の履歴 | 同上 |

件数や期間の集計だけなら Athena で足り、Neptune は要らない。

| | 正は S3 Tables、必要なときに写しを載せる | 最初から Neptune にも書く |
|---|---|---|
| メリット | 書く場所が 1 つ。食い違わない。Neptune が止まっても修復が止まらない | 分析をすぐ始められる |
| デメリット | 分析の前に読み込みの手順が 1 つ要る | 二重に書く。使うか分からない分析のために複雑さが残る |

Database と Analytics の使い分けは [8 章](#8-neptune-database-と-neptune-analytics)。
