---
title: lab の構成と通信の流れ
subtitle: はじめての人向け。スイッチ 6 台と TRex 1 台で、離れた 2 本のケーブルを同じ LAN に見せる 3 つの仕掛け
date: 2026-10-10
source: app/containerlab/gen_lab.py、trex/、docs/cycles/011-lab-isis-trex-x86/design.md、docs/pipeline.md
---

# この lab は何をする場所か

本物のスイッチ OS を 6 台動かし、パケットを撃ち、起きたことを AWS に集める。

## この lab は、撃って、運んで、見る場所である

::: columns

**撃つ: TRex**

- ネットワークに負荷をかける道具
- 本番のサーバの代わりに大量のパケットを作って流す

**運ぶ: スイッチ 6 台**

- Nokia の本物のスイッチ OS（SR Linux）をコンテナで動かす
- 設定は本物と同じ。この資料はここの話

---

**見る: AWS に集める**

- スイッチが出す「線が切れた」の通知やログを集める
- 落ちたら気づける仕組みを作るのが PoC の本題

:::

::: notes
- 6 台は dc1-spine-01/02、dc1-s-leaf-01/02、dc1-a-leaf-01/02。TRex は dc1-trex-01（app/containerlab/gen_lab.py）。
- SR Linux の機種は ixr-d2l（ライセンス不要）。
:::

## 登場するのは、真ん中の spine 2 台と端の leaf 4 台と TRex 1 台

- 真ん中: dc1-spine-01、dc1-spine-02
- 端（WAN 側）: dc1-s-leaf-01、dc1-s-leaf-02
- 端（DC 側）: dc1-a-leaf-01、dc1-a-leaf-02
- leaf は 2 台の spine 両方につながる（ケーブル 8 本。2 台の spine × 4 台の leaf）。leaf 同士は直接つながない
- TRex は leaf にだけつながる（ケーブル 4 本。eth1 〜 eth4 を各 leaf に 1 本ずつ）

::: notes
- spine と leaf は全組み合わせを結ぶ。spine 側の口は ethernet-1/<leaf の番号>、leaf 側の口は ethernet-1/<spine の番号>。
- 図は Claude のデッキ（https://claude.ai/artifact/3DtfxGFkTehxxwRpHRcB8u）の 3 枚目にある。
:::

## spine は IP で運ぶだけ、leaf はスイッチとルータの両方をする

::: columns

**spine（真ん中）**

- TRex のケーブルは挿さっていない
- leaf から来た荷物を、宛先 IP を見て別の leaf へ渡す
- あとで出てくる「誰がどこにいるか」の掲示板役もする

---

**leaf（端）**

- TRex 側の口では MAC を見るスイッチ
- spine 側の口では IP で話すルータ
- 2 つの顔をつなぐ「包む・ほどく」も leaf の仕事

:::

6 台とも同じ SR Linux で、どちらの仕事もできる。違いは機種ではなく、どこに置いて何を設定したか。

## leaf が 2 台ずつあるのは、パケットを必ず spine 経由にするため

- TRex はポートを 2 本ずつ組にして撃ち合う（port 0 ↔ 1、port 2 ↔ 3）
- 組の 2 本を別々の leaf に挿すので、パケットは必ず leaf → spine → leaf と本線を渡る
- leaf が 1 台だけなら、同じスイッチの中で折り返して終わり。試したい部分を通らない
- spine が 2 台なのは、片方が落ちても通る形（冗長）にするため
- 「線が切れた」を安全に起こして、監視を試せる

::: notes
- TRex の組は trex/trex_cfg.yaml.in（port 0 の default_gw が port 1、port 2 の default_gw が port 3）。
- mac-vrf の ecmp は 2（spine 2 台に振り分ける）。
:::

# 先に知っておく言葉

ケーブルの口の名前、住所の 2 種類、LAN の中と外。

## ケーブルの口は、見る側によって名前が違う

| TRex の番号 | Linux の口 | 挿してある先 | この口の IP |
|---|---|---|---|
| port 0 | eth1 | dc1-s-leaf-01 の ethernet-1/3 | 10.100.0.11 |
| port 1 | eth2 | dc1-s-leaf-02 の ethernet-1/3 | 10.100.0.12 |
| port 2 | eth3 | dc1-a-leaf-01 の ethernet-1/3 | 10.100.0.13 |
| port 3 | eth4 | dc1-a-leaf-02 の ethernet-1/3 | 10.100.0.14 |

- **eth1**: Linux がつける名前。TRex は Linux のコンテナなので、eth0 が管理用、eth1 〜 eth4 が leaf へ挿した 4 本
- **ethernet-1/3**: スイッチ（SR Linux）側の名前。1 枚目の基板の 3 番の口。1/1 と 1/2 は spine へ、1/3 は TRex へ
- **port 0**: TRex のソフトが数える番号。0 から始まるので eth1 が port 0

## 住所は MAC と IP の 2 種類ある

::: columns

**MAC アドレス**

- ケーブルの口ごとに最初から付いている 12 桁の番号
- 使えるのは同じ LAN の中だけ
- スイッチは「この MAC はどの口の先か」の表（MAC 表）で出す口を決める

---

**IP アドレス**

- あとから人が振る 4 つの数字
- LAN をまたいで届く。ルータが宛先 IP を見て次に渡す相手を決める
- TRex の口は 10.100.0.11 〜 .14、スイッチ自身は 10.255.x.y

:::

## LAN の中は MAC で届き、LAN をまたぐと IP で届く

::: columns

**同じ LAN の中（L2）**

- 送る側は「この IP の MAC は何?」と LAN 全体に聞く（ARP）
- 返事の MAC を宛先に書いて送る
- スイッチが MAC 表を見てその口へ出す

---

**LAN をまたぐ（L3）**

- ルータが宛先 IP を見て、道順表（ルーティングテーブル）に従って次へ渡す
- MAC は 1 区間ごとに書き換わる

:::

この lab では、スイッチ同士のあいだは IP で運ぶ。そのままでは leaf 1 に挿した口と leaf 2 に挿した口は同じ LAN にならない。

# やりたいこと

leaf 1 に挿したケーブルと leaf 2 に挿したケーブルを、同じ LAN に見せたい。

## 間は IP で運ぶので、3 つの仕掛けが要る

- 間にあるのは IP で運ぶスイッチ。MAC では届かない
- 仕掛け 1: 道を教え合う（IS-IS）
- 仕掛け 2: 誰がどこにいるか共有する（BGP EVPN）
- 仕掛け 3: 封筒に入れて運ぶ（VXLAN）

## 仕掛け 1: IS-IS は、スイッチ同士が道順表を自動で教え合う

スイッチ同士は IP で話すので「どのスイッチへはどの口から行けるか」の道順表が要る。人が手で書かず自動で教え合う仕組みが IS-IS。

- 名乗る: 各スイッチは「自分はこういう者で、隣にはこいつがいる」を隣へ送る。6 台が同じ情報を持つまで回す
- 地図を描く: 集まった情報で全体の地図を作り、「どこへは最短でどの口か」を道順表に書く
- 線が切れたら: 隣へ伝え、全員が地図を描き直す。spine が 2 台あるのでもう一方を通る道に切り替わる
- 教え合うのは主に「各スイッチ自身の住所」（ループバック）。口が 1 本切れても消えない
- これで leaf から他の leaf の本体へ IP で荷物を送れる。この土台を underlay（下の層）と呼ぶ

::: notes
- ループバック: spine は 10.255.0.1/.2、WAN 側 leaf は 10.255.1.1/.2、DC 側 leaf は 10.255.2.1/.2。
- IS-IS は level 2、point-to-point、インスタンス名 main（app/containerlab/gen_lab.py）。
- まだ TRex のパケットは運べない。運ぶ土台ができただけ。
:::

## 仕掛け 2: BGP EVPN は、leaf 同士が「どの MAC がどの leaf の先か」を見せ合う

leaf は自分の TRex 側の口で見た MAC しか知らない。「その MAC は向こうの leaf の先にいる」と知るために MAC 表を見せ合う。

- BGP: ルータ同士が「こういう宛先を知っている」と知らせ合う、インターネットでも使われる仕組み
- EVPN: その BGP に、IP の道順ではなく MAC の居場所を載せる使い方
- 載せる内容: 「MAC ◯◯ は私（leaf 10.255.2.1）の先にいる」。受け取った leaf は 10.255.2.1 へ送ればよいと分かる
- spine が掲示板: leaf は spine にだけ知らせ、spine が全 leaf に配り直す（ルートリフレクタ）。spine 2 台が同じ役
- 目印: 全員が番号 65100（AS 番号）を名乗り、見せ合う LAN に 100 という番号を付ける

::: notes
- iBGP AS 65100、EVPN だけ、ループバック同士で張る。spine が route reflector で cluster-id は自分のループバック。
- mac-vrf macvrf-100: EVI 100、VNI 100、route-target 65100:100。
:::

## 仕掛け 3: VXLAN は、leaf がパケットをまるごと IP の封筒に入れて運ぶ

TRex のパケットは MAC 宛てなので、IP で運ぶ spine はそのままでは扱えない。leaf が封筒に入れ、宛先は仕掛け 2 で分かった向こうの leaf の住所にする。

| 封筒の部分 | 中身 | 意味 |
|---|---|---|
| 封筒の宛先 IP | 10.255.2.1 → 10.255.2.2 | leaf 本体から leaf 本体へ |
| 封筒の種類 | UDP 4789 | VXLAN と分かる番号 |
| LAN の番号 | VNI 100 | どの LAN の荷物か |
| 中身 | 10.100.0.13 → 10.100.0.14 | TRex の元のパケット。MAC も含めてそのまま |

- 包む側の leaf: 宛先 MAC がいる leaf の住所へ封筒で送る
- 途中の spine: 封筒の宛先 IP しか見ない。道順表どおり隣へ渡すだけ
- ほどく側の leaf: 封筒を開け、VNI 100 の荷物だと確かめて TRex の口へ出す
- この封筒の層を overlay（上の層）と呼ぶ

## 1 つのパケットは、包む・運ぶ・ほどくの 5 歩で port 2 から port 3 に届く

- 1 TRex port 2: 宛先 10.100.0.14 の MAC 宛てに送る。普通の LAN と同じ動き
- 2 a-leaf-01 が包む: 「その MAC は 10.255.2.2 の先」と知っているので、封筒に入れて送る
- 3 spine が運ぶ: 封筒の宛先 10.255.2.2 を道順表で引き、a-leaf-02 の口へ出す
- 4 a-leaf-02 がほどく: 封筒を開け、中のパケットを ethernet-1/3 から出す
- 5 TRex port 3: 送られたときと同じ形で届く。間に 3 台いたことは分からない

::: notes
- spine は 2 台どちらを通っても同じ距離なので、leaf がパケットごとに振り分ける。1 本切れればもう一方だけになる。
- 相手の MAC をまだ知らないとき（ARP）は LAN 全体宛てなので、leaf は同じ LAN 番号を持つ全 leaf に封筒を 1 通ずつ送る（ingress replication）。
- port 0 ↔ port 1 は s-leaf-01 と s-leaf-02 のあいだで同じ動きをする。
:::

# 見る・替える・まとめ

スイッチが知らせる 3 つの口、SR-MPLS への差し替え、覚えること。

## スイッチは、トラップ・ログ・購読の 3 つの口で起きたことを知らせる

| 口の名前 | 何を知らせるか | どう届くか | AWS 側で誰が受けるか |
|---|---|---|---|
| SNMP トラップ | 「口が落ちた」を起きた瞬間に短く通知 | スイッチが自分から送る | Telegraf が貯め場へ |
| syslog | 人が読む文章のログ。理由つき | スイッチが自分から送る | syslog-ng が貯め場へ |
| gNMI | 口の状態、隣との状態、通信量の数字 | AWS 側から接続して購読する | gnmic が貯め場へ |

- 貯め場: 届いた通知を種類ごとの列に並べて順に貯める場所（AWS の MSK）
- そのあと: 貯め場から読んで整え、検索する場所・グラフにする場所・長く残す場所・アラートの仕組みへ配る
- スイッチは 1 つの決まった宛先に送るだけで、lab 側の機械が AWS 内の受け口へ転送する

::: notes
- SNMP trap 162/udp → Telegraf → MSK の traps。syslog 5140/udp → syslog-ng → logs。gNMI 57400 ← gnmic → gnmi（状態の変化）と metrics（60 秒ごとの数字）。
- 宛先 203.0.113.1 は lab の EC2 で、iptables の DNAT で NLB へ転送する（docs/pipeline.md）。
- 後ろは Spark → OpenSearch / Prometheus / S3 Tables / Splunk、アラートは SNS → Lambda → Neptune。
:::

## 最初の依頼は SR-MPLS で、いまは封筒を VXLAN で代用している

2026-10-08 の依頼は「ISIS / SR-MPLS」だった。SR-MPLS は仕掛け 3 の封筒を別の種類にする話で、仕掛け 1 と 2 はそのまま使う。

| 項目 | いま: VXLAN | 依頼: SR-MPLS |
|---|---|---|
| 封筒 | IP（宛先は leaf の住所） | 短いラベル（宛先 leaf の番号） |
| spine の仕事 | 道順表を引く普通の IP ルータ | ラベルを見て回すだけ |
| 機種 | ixr-d2l（ライセンス不要） | ixr-6e とライセンスが要る |

- ライセンスが届くまで、同じ形で動かせる VXLAN で代用すると「lab を IS-IS と TRex で組む（011）」で決めた
- 替えるときは lab の生成スクリプトで機種とライセンスを指定し直し、leaf の封筒の設定を替える
- スイッチの並び・ケーブル・IS-IS・BGP EVPN・TRex・監視の 3 つの口は変わらない

::: notes
- docs/cycles/011-lab-isis-trex-x86/design.md。gen_lab.py の type を ixr-6e にし license を付ける。
- Neptune の構成図は vtep を SR のラベルと読み替えるだけで、id と edge は変えない（docs/data-stores.md）。
:::

## 覚えることは 3 つ

- スイッチ同士は IP で話す。だから leaf 1 と leaf 2 の TRex の口は、そのままでは同じ LAN にならない
- IS-IS で道を教え合い、BGP EVPN で「誰がどこにいるか」を共有し、VXLAN の封筒で運ぶ。leaf が包み、spine が運び、leaf がほどく
- SR-MPLS は封筒の種類が違うだけ。ライセンスが来たら封筒を替える。監視の 3 つの口はそのまま
