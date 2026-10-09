# lab の小さな直しを片付ける（024）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

009・010・011 のレビューで出て BACKLOG に残っていた、lab（`app/containerlab/`、`ops/lab-*.sh`、`docker/compose/lab.sh`）まわりの小さな直し 11 件を 1 サイクルに束ねる。互いに独立で、1 件 1 commit にする（H の multitool 外しだけ範囲が広いので、先に A〜G・I〜K を終えてから最後にやる）。

| # | BACKLOG の行 | 出所 |
|---|---|---|
| A | `lab.sh` の failover の `route()` が IS-IS の経路が無いと `set -e` と `pipefail` で止まる | 009 の実装 |
| B | `lab graph` の案内の `sudo systemctl status …` を `journalctl -u` にする | 010 セルフレビュー Nit 7 |
| C | `docker/compose/lab.sh` の down / check でも `TREX_IMAGE` を要らなくするか README に書く | 011 セルフレビュー N2 |
| D | lag / ES の名残を消す | 011 セルフレビュー N3、cold review Nit |
| E | 011 の design.md:27 の `ROLE_ORDER` の記述を `app/agentcore/topology.py` に揃える | 011 セルフレビュー N5 |
| F | `kafka_load.sh` の gnmi の peer の固定を lab の構成から取る | 011 セルフレビュー N6 |
| G | `lab.sh` の `${TREX_IMAGE:?}` を見張るテストを足す | 011 セルフレビュー N8 |
| H | 使われない multitool のイメージを lab から外す | 011 セルフレビュー N4 |
| I | デバッグ用の Telegraf の ECR タグにも `-$LAB_ARCH` を付ける | 011 cold review Round 1 Nit |
| J | `trex_cfg` / `edge_ports` / `trex/stl/*.py` をテストで見張る | 011 cold review Round 1 Nit |
| K | `upload_lab` の除外に `app/containerlab/clab-*/` を足す | 011 cold review Round 2 Nit 1 |

### 調査で分かった事実（2026-10-09、origin/main 5a0b2d4）

**A. `route()`**

- `app/containerlab/lab.sh:12` は `set -euo pipefail`。`:255-263` の `route()` は `nhg=$(srl … 2>/dev/null | grep -oE 'next-hop-group [0-9]+' | head -1 | awk '{print $2}')` で next-hop-group の番号を取り、`:258` `[ -n "$nhg" ] || { echo "  (IS-IS の経路が無い)"; return 0; }` で経路無しを案内するつもり。しかし経路が無いと `grep` が 1 で終わり、`pipefail` でパイプ全体が 1、代入の終了コードも 1 になるので `set -e` が関数ごと止め、`:258` に来ない。
- 呼び方は 2 通り。`:264` `echo "== 切替前 …"; route` は素の呼び出しで `set -e` が効く（ここで止まる）。`:268` `if r=$(route 2>/dev/null) && …` は `if` の条件の中なので `set -e` が効かず、`r` が空のまま先へ進む（止まらないが、こちらも `nhg` が空なら `(IS-IS の経路が無い)` を出さずに終わる）。
- `:260-262` の next-hop の `for` の中は `|| true` を付けてあり、同じ配慮が `:257` だけに無い。
- テストの土台。`tests/test_lab_debug.py:440-460` の `_lab_graph(*args, active=False, **env)` が偽の `systemd-run` / `containerlab` / `systemctl` / `curl` を `PATH` の先頭に置き、`lab.sh` を写して打つ。`docker` の偽物は無い（`srl()` は `:75` `printf '%s\n' "${@:2}" | docker exec -i "clab-$LAB-$1" sr_cli -d`。`failover` は `"$SELF" fail-main` も呼ぶので通しでは打たず、`route` だけを抜き出して打つ方が簡単）。

**B. graph の案内**

- `app/containerlab/lab.sh:196` `echo "ブラウザで http://localhost:$GRAPH_PORT/ を開く。開けなければ 'sudo systemctl status $NAME_PREFIX-lab-graph'。止めるのは '$LAB_CMD graph-stop'"`。`:190` 付近の起動は `systemd-run --unit=$NAME_PREFIX-lab-graph --collect …` なので、containerlab graph がすぐ落ちると一時ユニットが消えて `systemctl status` は `could not be found`。`journalctl -u <unit>` は消えたユニットのログも出す。
- テスト `tests/test_lab_debug.py:464-470` は `_r.returncode == 0`、`systemd-run` の引数、`_fwd`、`http://localhost:50080/` を見るだけで、`systemctl status` の字面は見ていない。

**C. `docker/compose/lab.sh` の `TREX_IMAGE`**

- `docker/compose/lab.sh:13-16` が `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` / `TREX_IMAGE` を `.env` から取り、`:16` の `: "${…:?…}"` でサブコマンドに関わらず 3 つとも必須にしている。`:17` の `lab()` は 3 つを `sudo env` で渡し、`:20` で `up` のときだけ先に `render`。`app/containerlab/lab.sh` でイメージを使うのは `render`（`:154-157`）と `pull`（`:166`。手元では使わない）だけ。`down` / `status` / `check` / `fail-main` / `heal-main` / `trap-test` はイメージを見ない。
- `docker/compose/README.md:80-84` が「渡す環境は 5 つ」と、`.env` に無いと止まることを説明している。
- テスト `tests/test_local_compose.py:567-573` は `up` で `MULTITOOL_IMAGE` / `TREX_IMAGE` が無いと `sudo` を打たずに止まることを見る（`_c == ["docker compose --env-file .env config --environment"]`）。`:560-565` は `status` で 3 つを渡す字面（`split()[2:5]`）。

**D. lag / ES**

- `evpn_es` と `lab_lag_speed` は `app/` `IaC/` `docker/` に無い（`grep -rn` で 0 件）。残っているのは「無いこと」を見る負のテスト `tests/test_stream.py:383` と `tests/test_analytics.py:601-602` だけ。
- `app/nautobot/nwc/nb_map.py:31-36,147-151,160-170` の lag の分岐は Nautobot の `type: lag` の interface を一般に扱うもので、`tests/test_nautobot.py:42-45,52,78` が見ている。lab に LAG が無くても Nautobot の機能として正しい。
- `app/dashboard/topology_view.py:172` の凡例「紫 = lag（LACP。いまの lab には無い）」は事実と合っている。
- → **消すものが残っていない。** 011 の時点で既に外れていた（011 の実装で消え、BACKLOG の行だけ残った）。コードは触らず、この設計に確認結果を残して閉じる。

**E. 011 の design.md**

- `docs/cycles/011-lab-isis-trex-x86/design.md:27` が `app/nautobot/netops/nb_map.py` と書いている。019 で `netops` → `nwc` に改名したので今の正しいパスは `app/nautobot/nwc/nb_map.py`。`ROLE_ORDER` の値そのものは `app/agentcore/topology.py:27` と一致している（ずれはパスだけ）。
- `ops/check.sh` の 5.（023）は `docs/cycles/` を `netops` の grep から除いているので、この行は検査に掛からない。直さなくても壊れないが、011 を読む人が無いパスへ行く。

**F. `kafka_load.sh` の peer**

- `app/containerlab/trex/kafka_load.sh:37` `nodes()` が `../splab.clab.yml.in` から `kind: nokia_srlinux` のノード名と `mgmt-ipv4` を取る。`:53` の bgp_neighbor のレコードは `"neighbor_peer-address":"10.255.0.1"` を全ノードに固定で入れる。
- lab の BGP の peer は `app/containerlab/srlinux/<node>.cli` にある。`dc1-a-leaf-01.cli:46-49` `set / network-instance default protocols bgp neighbor 10.255.0.1 peer-group overlay`（と `10.255.0.2`）、`dc1-spine-01.cli:58-60` は `10.255.1.1` / `10.255.1.2`、`dc1-s-leaf-01.cli` は 4 行。ノードごとに **最初の** `protocols bgp neighbor <IP> peer-group overlay` を取れば、そのノードに実在する peer になる。
- `kafka_load.sh` は `app/containerlab/trex/` を cwd にして打つ（`:37` の `../splab.clab.yml.in` がその前提）。

**G. `${TREX_IMAGE:?}`**

- `app/containerlab/lab.sh:155` `: "${SRLINUX_IMAGE:?}" "${MULTITOOL_IMAGE:?}" "${TREX_IMAGE:?}"`。`tests/test_local_compose.py:573` は `docker/compose/lab.sh` 側の `TREX_IMAGE が無い` を見るが、`app/containerlab/lab.sh render` 自体に `TREX_IMAGE` が無いとき止まることは見ていない（011 で `TREX_IMAGE` を足したときに気付いた穴）。

**H. multitool**

- `app/containerlab/splab.clab.yml.in:35-38` の `kinds: … linux: image: __MULTITOOL_IMAGE__` が唯一の使い先だが、`kind: linux` のノードは TRex だけで、TRex は `image: __TREX_IMAGE__` を自分で持つ（`:98` 付近）。`app/containerlab/gen_lab.py:236-266` も同じ kinds ブロックと TRex ノードを出す。**どのノードも multitool を使わない。**
- 参照箇所（`grep -rn -i multitool`、docs/cycles と docs/verification を除く）:
  - lab 本体: `app/containerlab/lab.sh:155-157`（render）, `:166`（pull の 3 イメージ）、`app/containerlab/splab.clab.yml.in:37-38`、`app/containerlab/gen_lab.py`（kinds の linux）、`app/containerlab/setup.sh:6`（コメント）
  - ECR: `IaC/terraform/aws-managed/base/ecr/main.tf:1,14`（`lab_repositories = toset(["srlinux", "multitool", "trex"])`）、`outputs.tf:11-13`（`lab_multitool_repository_url`）、`variables.tf:31`
  - 画像のミラー: `ops/lab-common.sh:5,10,17,23,84,87-88`（`MULTITOOL_TAG` / `MULTITOOL_ECR_TAG` / `MULTITOOL_UPSTREAM` / `mirror_lab_images` の 2 行）、`ops/up.sh:123,624-626,674`、`ops/oss/up.sh:182-184`、`ops/lab-debug.sh:37,55,138`
  - lab の EC2: `IaC/terraform/aws-managed/pipeline/lab/variables.tf:91-92`（`multitool_image_tag`）、`terraform.tfvars.example:20`、`instance.tf:24`、`templates/lab_user_data.sh.tftpl:27`（`MULTITOOL_IMAGE=` を lab.sh に渡す）、`IaC/cloudformation/lab-debug.yaml:10,70,308-311,361,461,481`（Parameter `MultitoolImageTag`、ECR の repo、IAM、user-data）
  - 手元: `docker/compose/lab.sh:14,16,17`、`docker/compose/.env.example:16,19`、`docker/compose/README.md:36,82`、`.env.example:133`
  - docs: `docs/data-stores.md:263,287,306,317,326`、`docs/setup.md:56`、`docs/pipeline.md:352`、`docs/faq-fukuda-nwc-poc.md:346`、`docs/deploy.md:250,327,365`、`docs/architecture/README.md:118`、`docs/architecture/resources/lab-ec2.md:15,16,58`、`docs/architecture/resources/ecr.md:31,81,83,93`
  - テスト: `tests/test_lab_debug.py:67,101,173,216,222,369-370`（9 か所）、`tests/test_local_compose.py:96-98,332,475,489,495-501,553,560-570,589-590`（17 か所）
- ECR の repo は `force_delete = true`（`base/ecr/main.tf:4`）なので、`lab_repositories` から外せば次の apply で中のイメージごと消える。`ops/down.sh` の `KEEP_ECR=1` は ECR のルートを destroy しないだけで、apply の差分には関わらない。
- 2026-10-09 の AWS 検証で立っている lab の EC2 は `MULTITOOL_IMAGE` を user-data から受けているが、使うノードが無いので、外しても動いているトポロジには影響しない（次に `up.sh` を打つとき `lab_user_data` が変わって EC2 が作り直されるのは、いつもの挙動）。

**I. デバッグ用 Telegraf のタグ**

- `ops/lab-debug.sh:129` `TELEGRAF_TAG=$(telegraf_tag)`、`:141` `ecr_has "$REPO_PREFIX-telegraf" "$TELEGRAF_TAG"`、`:145` `build_telegraf "$REG/$REPO_PREFIX-telegraf:$TELEGRAF_TAG" linux/amd64`、`:154` `deploy true "$TELEGRAF_TAG"`（CFn の `TelegrafImageTag`）。`ops/up.sh:633-634` は同じ `telegraf_tag` で **arm64** を stream の ECS 用に作って同じ repo `<prefix>-telegraf` に置く。同じタグ名に amd64 と arm64 が別々に push されると、後に push した方が上書きし、lab-debug の EC2（x86_64）が arm64 のイメージを引いて起きなくなる（または逆）。lab の 3 イメージは `ops/lab-common.sh:15-18` の `LAB_ARCH=amd64` と `*_ECR_TAG="$*_TAG-$LAB_ARCH"` で既に分けてある。
- `IaC/cloudformation/lab-debug.yaml:76-79` の `TelegrafImageTag` の Description は `"<Telegraf version>-<hash …> (ops/lab-common.sh telegraf_tag)"`。
- テスト `tests/test_lab_debug.py:106-107` が `up` と `dbg` の両方に `"TELEGRAF_TAG=$(telegraf_tag)"` の字面を要求している。`dbg` 側を `-$LAB_ARCH` 付きにすると、この check が落ちる（直す必要がある）。

**J. `trex_cfg` / `edge_ports` / `stl/*.py`**

- `app/containerlab/lab.sh:53-54` `TREX=dc1-trex-01`、`TREX_NET=10.100.0`、`TREX_IP_BASE=11`。`:107-116` `edge_ports()` は `$TOPO.in` から `"$TREX:eth[0-9]+", *"[^"]+:e1-[0-9]+"` を grep/sed で `eth1 dc1-s-leaf-01 3` の形にし、`srl` で oper-state を聞く。`:118-130` `trex_cfg()` は引数のポート名から `trex/trex_cfg.yaml.in` の `__PORT_LIMIT__` / `__INTERFACES__` / `__PORT_INFO__` を埋める。`port_info` の `ip` は `$TREX_NET.$((TREX_IP_BASE + i))`、`default_gw` は `$TREX_NET.$((TREX_IP_BASE + (i ^ 1)))`（組の相手。0↔1、2↔3）。
- `app/containerlab/splab.clab.yml.in:104-107` の TRex のリンクは `dc1-trex-01:eth1 ↔ dc1-s-leaf-01:e1-3`、`eth2 ↔ dc1-s-leaf-02:e1-3`、`eth3 ↔ dc1-a-leaf-01:e1-3`、`eth4 ↔ dc1-a-leaf-02:e1-3`。
- `app/containerlab/trex/stl/udp_syslog.py` の `syslog_payload(host, app, severity, msg)` は `"<%d>1 - %s %s - - -" % (23*8+severity, host, app) + " " + msg` を utf-8 にし、severity が 0〜7 の外なら `ValueError`。`DEFAULTS` は dst 203.0.113.1 / dport 5140 / host dc1-trex-01 / app sr_bgp_mgr / severity 5。`trex_stl_lib` は `UdpSyslog.get_streams` の中で遅延 import するので、モジュールの import と `syslog_payload` の呼び出しに TRex は要らない。`udp_trap.py` の `trap_payload(community="public", ifindex=1, ifname="ethernet-1/1", uptime=100, request_id=1)` は BER で SNMPv2c の linkDown を組む（先頭は SEQUENCE の 0x30）。
- 既存のテストでは `tests/test_lab_debug.py` が `lab.sh` の関数を字面で見るだけで、`trex_cfg` / `edge_ports` を打っていない。関数を bash で打つ手本は `tests/test_lab_debug.py:408` `bash -c '. ops/lab-common.sh; dir_tag "$@"'`。

**K. `upload_lab` の除外**

- `ops/lab-common.sh:104` `aws s3 sync --only-show-errors --delete app/containerlab/ "s3://$1/lab/" --exclude "splab.clab.yml" --exclude "__pycache__/*" --exclude "*.DS_Store" --exclude "$CONTAINERLAB_RPM"`。`IaC/terraform/aws-managed/pipeline/lab/outputs.tf:26-28` の `upload_lab_command` も同じ 4 つ。`.gitignore:16` は `app/containerlab/clab-*/` を無視している（手元の containerlab が `app/containerlab/clab-splab/` に TLS の鍵と各ノードの設定を書く）。WSL で `docker/compose/lab.sh up` を打ったチェックアウトから `ops/up.sh` を打つと、`clab-splab/` が S3 に上がる（root 所有で読めなければ sync が落ちる）。
- テスト `tests/test_lab_debug.py:236-247` の `_ul_ex` が除外の集合を `{"splab.clab.yml", "__pycache__/*", "*.DS_Store", "$CONTAINERLAB_RPM"}` と `{…, "containerlab_${var.containerlab_version}_linux_amd64.rpm"}` に固定している。

## 設計方針

**A. `route()` の nhg の取り方を `pipefail` で止まらない形にする**

1. `app/containerlab/lab.sh:257` の末尾に `|| true` を付ける（`… | awk '{print $2}' || true)`。`:260-262` の `for` の中と同じ書き方）。これで `nhg` が空のまま `:258` に来て `(IS-IS の経路が無い)` を出し `return 0`。
2. テスト。`tests/test_lab_debug.py` に、`lab.sh` から `route()` の本体を正規表現で抜き出し（`^    route\(\) \{.*?^    \}` を `re.M | re.S`）、`set -euo pipefail` と偽の `srl()`（`srl() { :; }` で何も出さない）を前に置いて `bash -c` で打つ check を 1 つ足す: 終了コード 0、stdout に `(IS-IS の経路が無い)`。**先に直す前の `lab.sh` で打って終了コードが 0 でなく案内も出ないことを 1 回見て `build.md` に貼る（赤→緑）。** 次に偽の `srl()` が `next-hop-group 3` と `next-hop 1` と `ip-address 172.16.0.12 subinterface ethernet-1/50.0` を順に返す（`case "$2" in *route-table\ ipv4-unicast*) … ;; *next-hop-group*) … ;; *) … ;; esac`）形で打ち、stdout に `ip-address 172.16.0.12 subinterface ethernet-1/50.0` が出て終了コード 0 の check を 1 つ（経路があるときの形が変わっていないことを見る）。

**B. 案内を `journalctl -u` にする**

1. `app/containerlab/lab.sh:196` の `'sudo systemctl status $NAME_PREFIX-lab-graph'` を `'sudo journalctl -u $NAME_PREFIX-lab-graph'` にする。起動の成否を見る処理は足さない（`--collect` の一時ユニットは成否を聞く前に消えることがあり、聞いても確実でない。ログを見る案内で十分）。
2. テスト。`tests/test_lab_debug.py:464-467` の graph の check に `"sudo journalctl -u x-nwc-poc-lab-graph" in _r.stdout and "systemctl status" not in _r.stdout` を足す（check の件数は変えない）。

**C. `docker/compose/lab.sh` はイメージを使うサブコマンドだけで要求する**

1. `docker/compose/lab.sh:16` の `: "${…:?…}"` を `if [ "${1:-}" = up ] || [ "${1:-}" = render ]; then … fi` で囲む。`lab()` の `sudo env` は 3 つを渡したままにする（空文字でも `app/containerlab/lab.sh` の `down` 等は見ない。`render` は `:155` で自分で `:?` を打つので二重に守られる）。`:4-5` のコメントに「イメージの 3 つは up / render のときだけ必須（down / check などは .env に無くても打てる）」を 1 行足す。
2. `docker/compose/README.md:82` を「`.env` にイメージの 3 つが無いと `up` が止まる（`down` / `check` などは打てる。011 より前の `.env` には `TREX_IMAGE` が無いので `.env.example` から写す）」の趣旨に直す。
3. テスト。`tests/test_local_compose.py:573` の隣に、`SRLINUX_IMAGE` だけの `.env` で `down` を打つと `_r.returncode == 0` で `sudo` の呼び出しが 1 回あり（`_c` の 2 番目が `sudo ` で始まり `TREX_IMAGE=` が空で渡っている）、stderr に `が無い` が無い check を 1 つ足す。既存の `:567-573`（`up` で止まる）はそのまま通ること。

**D. lag / ES は確認のみ**

1. コードは触らない。この設計の「調査で分かった事実 D」が確認の記録。BACKLOG の行は `（2026-10-09 完了。確認のみ。011 で既に外れていて、残るのは無いことを見る負のテストと Nautobot の lag 一般の分岐と凡例の「いまの lab には無い」だけ）` で閉じる。

**E. 011 の design.md のパス**

1. `docs/cycles/011-lab-isis-trex-x86/design.md:27` の `app/nautobot/netops/nb_map.py` を `app/nautobot/nwc/nb_map.py` にする。`ROLE_ORDER` の値は一致しているので触らない。
2. テストは足さない（`docs/cycles` は記録で、`ops/check.sh` の 5. も対象外）。`grep -rn 'nautobot/netops' docs/cycles/011-lab-isis-trex-x86/` が 0 行になることを検証で見る。

**F. `kafka_load.sh` の peer を各ノードの cli から取る**

1. `app/containerlab/trex/kafka_load.sh` に `peer() { sed -n 's#^set / network-instance default protocols bgp neighbor \([0-9.]*\) peer-group overlay.*#\1#p' "../srlinux/$1.cli" | head -1; }` を `nodes()` の下に足す。`payload()` の bgp_neighbor の分岐で `p=$(peer "$node")` を取り（`while read -r node ip` に名前も受ける）、空なら `echo "$node の BGP の peer が ../srlinux/$node.cli に無い" >&2; exit 1`、`"neighbor_peer-address":"%s"` に `$p` を入れる。コメントの「established なので…発火しない」は残す。
2. テスト。`tests/test_lab_debug.py`（lab のテスト。`kafka_load.sh` を読む check が無ければ新設の節）に、`kafka_load.sh` から `nodes()` と `peer()` を正規表現で抜き出し、cwd を `app/containerlab/trex` にして `bash -c` で打つ check を 2 つ: `nodes` の出力が 6 行で `dc1-a-leaf-01 203.0.113.11` 等を含む（`.in` の現物から期待値を組む。`mgmt-ipv4` を `.in` から読んで照合）、`peer dc1-a-leaf-01` が `10.255.0.1`、`peer dc1-spine-01` が `10.255.1.1`。`payload` の本文に `"neighbor_peer-address":"10.255.0.1"` の固定が無く `"neighbor_peer-address":"%s"` があることを字面で 1 つ。

**G. `${TREX_IMAGE:?}` を見張る**

1. `tests/test_lab_debug.py` に、`lab.sh` の `render)` の次の行が `: "${SRLINUX_IMAGE:?}" "${TREX_IMAGE:?}"`（H で multitool を外したあとの形）であることの字面の check と、`_lab_graph("render", SRLINUX_IMAGE="x")`（`TREX_IMAGE` 無し）が終了コード 0 でなく stderr に `TREX_IMAGE` を含む check を足す。`_lab_graph` は `TELEGRAF_IMAGE` 等を環境から落とすので、`TREX_IMAGE` が外の環境から混ざらないよう `e` の除外に `SRLINUX_IMAGE` と `TREX_IMAGE`（H の前なら `MULTITOOL_IMAGE` も）を足す。
2. H を先にやると `MULTITOOL_IMAGE` が消えるので、**G の commit は H のあと**（実装ステップの順）。

**H. multitool を lab から外す**

1. lab 本体。`splab.clab.yml.in:37-38` の `linux:` の kind と `gen_lab.py` の同じ出力を消す（`kinds:` は `nokia_srlinux` だけ残る）。`lab.sh:155-157` の `render` を `: "${SRLINUX_IMAGE:?}" "${TREX_IMAGE:?}"`、sed は 2 プレースホルダ、echo は「イメージは $SRLINUX_IMAGE と ${TREX_IMAGE}」に。`:166` の `pull` は 2 イメージ。`setup.sh:6` のコメント。
2. ECR。`base/ecr/main.tf:14` を `toset(["srlinux", "trex"])`、`:1` のコメント、`outputs.tf:11-13` の `lab_multitool_repository_url` を消す、`variables.tf:31` の説明。**この差分で次の apply が `<prefix>-lab-multitool` の repo を中のイメージごと消す**（`force_delete = true`）。消えることは検証で見る。
3. ミラー。`ops/lab-common.sh` の `MULTITOOL_TAG` / `MULTITOOL_ECR_TAG` / `MULTITOOL_UPSTREAM` と `mirror_lab_images` の 2 行（`:87-88`）、`:5` のコメント。`ops/up.sh:123,624-626,674`、`ops/oss/up.sh:182-184`、`ops/lab-debug.sh:37,55,138` の `ecr_has … multitool` と文言「lab の 3 つ」→「2 つ」。
4. lab の EC2。`pipeline/lab/variables.tf:91-92` の `multitool_image_tag` と `terraform.tfvars.example:20`、`instance.tf:24` の templatefile の引数、`lab_user_data.sh.tftpl:27` の `MULTITOOL_IMAGE=`。`IaC/cloudformation/lab-debug.yaml` の Parameter `MultitoolImageTag`（`:70`）、ECR の repo の参照（`:308-311`）、IAM の Resource（`:361`）、user-data の `MULTITOOL_IMAGE`（`:461,481`）、`:10` のコメント。`ops/lab-debug.sh` の `deploy()` が渡す `MultitoolImageTag=` も消す。
5. 手元。`docker/compose/lab.sh:14,16,17`（C と同じ行を触るので、C を先に commit してから）、`docker/compose/.env.example:16,19`、`.env.example:133`、`docker/compose/README.md:36,82`（「渡す環境は 5 つ」→「4 つ」）。
6. docs。上の一覧の 8 ファイルから multitool の記述を消す（イメージの表の行、「3 つのイメージ」→「2 つ」、ECR の repo の一覧）。`docs/cycles/` と `docs/verification/` は記録なので触らない。
7. テスト。`tests/test_lab_debug.py` の 9 か所と `tests/test_local_compose.py` の 17 か所から `MULTITOOL` を外す（期待集合・`split()[2:5]` → `[2:4]`・`.env` の内容・`MT` の定数・render の文言）。`:567-569` の「`MULTITOOL_IMAGE` が無ければ止まる」は意味を失うので消す（check は −1）。**multitool が戻らないことを見る check を 1 つ足す**: `git ls-files -z | xargs -0 grep -l -i multitool` を `docs/cycles/` と `docs/verification/` を除いて打ち、0 本（`tests/test_lab_debug.py` 自身は `multitool` の字面を持つので、この check の文字列は `"multi" "tool"` の連結で書く）。
8. `ops/check.sh` の 5.（netops）と同じ段を足すことはしない（テストの 1 check で十分。段を増やすたびに check.sh が長くなる）。

**I. デバッグ用 Telegraf のタグに `-$LAB_ARCH`**

1. `ops/lab-debug.sh:129` を `TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH" || die …` にする（`LAB_ARCH` は `. ops/lab-common.sh` で入っている）。`:141,145,154` はそのまま（変数経由）。`:127` の log の「どれも x86_64 の EC2 に載るので amd64」の後ろに「（タグも lab の 3 つと同じく `-amd64` を付け、stream の arm64 と同じ repo で混ざらないようにする）」を足す。
2. `IaC/cloudformation/lab-debug.yaml:79` の Description を `"<Telegraf version>-<hash …>-amd64 (ops/lab-common.sh telegraf_tag plus -LAB_ARCH; the stream ECS pushes the arm64 build of the same version to the same repository without the suffix)"` にする。
3. テスト。`tests/test_lab_debug.py:106-107` の `dbg` 側の字面を `'TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH"'` にする（`up` 側は `TELEGRAF_TAG=$(telegraf_tag)` のまま）。check の名前に「デバッグ用は -$LAB_ARCH 付き（stream の arm64 と同じ repo に置くので混ざらない）」を足す。

**J. `trex_cfg` / `edge_ports` / `stl/*.py` のテスト**

1. `tests/test_lab_debug.py` に次の check を足す（`lab.sh` から関数を正規表現で抜き出し、`TREX=dc1-trex-01` / `TREX_NET` / `TREX_IP_BASE` / `TOPO=splab.clab.yml` の定義行を `sh_const` で取って前に置き、cwd を `app/containerlab` にして `bash -c` で打つ）。
   - `trex_cfg eth1 eth2 eth3 eth4` の出力が `port_limit: 4`、`interfaces: ['eth1', 'eth2', 'eth3', 'eth4']` を含み、`port_info` の `ip` が `10.100.0.11`〜`.14` の順、`default_gw` がそれぞれ `.12 .11 .14 .13`（組の相手）。期待値は `.in` の `TREX_NET` / `TREX_IP_BASE` から組む。
   - `edge_ports` の `srl` を呼ぶ前までの解析（`grep`/`sed` のパイプ）を打ち、`.in` の TRex のリンク 4 本 `eth1 dc1-s-leaf-01 3` / `eth2 dc1-s-leaf-02 3` / `eth3 dc1-a-leaf-01 3` / `eth4 dc1-a-leaf-02 3` が出る。関数全体を打つなら偽の `srl() { echo up; }` を置く。
   - Python: `sys.path` に `app/containerlab/trex/stl` を足して `udp_syslog` と `udp_trap` を import（TRex 無しで import できること自体が check）。`syslog_payload("dc1-trex-01", "sr_bgp_mgr", 5, "msg") == b"<189>1 - dc1-trex-01 sr_bgp_mgr - - - msg"`、`severity=8` で `ValueError`、`trap_payload()` が `bytes` で先頭が `0x30`、`DEFAULTS["dport"]` が syslog 5140 / trap 162（stream の NLB の受け口と同じ。`tests/test_stream.py` の定数があれば照合）。
2. 赤→緑は `trex_cfg` の `default_gw` の `(i ^ 1)` を一時的に `i` に変えて落ちることを 1 回見る（commit しない）。

**K. `upload_lab` の除外に `clab-*/*` を足す**

1. `ops/lab-common.sh:104` に `--exclude "clab-*/*"` を足す（`aws s3 sync` の `--exclude` はソースからの相対パスに対する glob。`clab-splab/…` に当たる。`.gitignore:16` と同じ趣旨をコメントに 1 行）。`outputs.tf:28` の `upload_lab_command` にも同じ `--exclude \"clab-*/*\"`。
2. テスト。`tests/test_lab_debug.py:241,246` の期待集合に `"clab-*/*"` を足す。

**やらないこと。** A で `failover` を通しで打つテスト（`fail-main` と `docker` の偽物が要る）。B で graph の起動の成否判定。C で `sudo env` の渡し方の変更（3 つを渡したまま）。D のコード変更。H で `docs/cycles` / `docs/verification` の multitool の記述、`docker/compose/compose.yaml`（multitool を使っていない）。I で `ops/up.sh` の stream 側のタグ（`-arm64` を付けない。ECS のタスク定義と `tests/test_stream.py` が今の形を見ている）。K で `aws s3 sync` の `--exclude` の順序や他の除外の変更。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `app/containerlab/lab.sh:155-157,166,196,257` | render / pull の 2 イメージ（H）、journalctl（B）、`\|\| true`（A） |
| `app/containerlab/splab.clab.yml.in:35-38`、`gen_lab.py` | kinds の linux を消す（H） |
| `app/containerlab/setup.sh:6` | コメント（H） |
| `app/containerlab/trex/kafka_load.sh:37-56` | `peer()`、payload の peer（F） |
| `ops/lab-common.sh:5,10,17,23,84-88,104` | multitool を外す（H）、`clab-*/*`（K） |
| `ops/up.sh:123,624-626,674`、`ops/oss/up.sh:182-184` | multitool を外す（H） |
| `ops/lab-debug.sh:37,55,127-129,138` と `deploy()` | multitool を外す（H）、`-$LAB_ARCH`（I） |
| `IaC/terraform/aws-managed/base/ecr/main.tf, outputs.tf, variables.tf` | repo の集合から multitool（H） |
| `IaC/terraform/aws-managed/pipeline/lab/variables.tf, terraform.tfvars.example, instance.tf, templates/lab_user_data.sh.tftpl, outputs.tf` | multitool（H）、`clab-*/*`（K） |
| `IaC/cloudformation/lab-debug.yaml` | `MultitoolImageTag` ほか（H）、`TelegrafImageTag` の説明（I） |
| `docker/compose/lab.sh`、`docker/compose/README.md`、`docker/compose/.env.example`、`.env.example` | up / render だけ必須（C）、multitool（H） |
| `docs/data-stores.md`, `setup.md`, `pipeline.md`, `faq-fukuda-nwc-poc.md`, `deploy.md`, `architecture/README.md`, `architecture/resources/lab-ec2.md`, `architecture/resources/ecr.md` | multitool（H） |
| `docs/cycles/011-lab-isis-trex-x86/design.md:27` | パス（E） |
| `tests/test_lab_debug.py` | A・B・F・G・I・J・K の check、multitool（H） |
| `tests/test_local_compose.py` | C の check、multitool（H） |

## 再利用するもの

- `app/containerlab/lab.sh:260-262` の `|| true`（A の手本）。
- `tests/test_lab_debug.py:440-460` の `_lab_graph`（B・G）、`:408` の `bash -c '. …; fn "$@"'`（A・F・J で関数を抜き出して打つ手本）、`:236-247` の `_ul_ex`（K）、`sh_const`（J の定数）。
- `tests/test_local_compose.py:480-592` の `run` / `tree`（C）。
- `ops/lab-common.sh:15-18` の `LAB_ARCH` と `*_ECR_TAG`（I）。
- `docs/cycles/023-ops-small-fixes/design.md` の C（「戻っていないこと」を見る check の形。H の 7）。

## 実装ステップ

1 件 1 commit。順序は A → B → C → E → F → I → J → K → **H → G**（G は H の後。C は H の前）。D は commit なし。

1. A: `lab.sh:257` → テスト（先に直す前で打って**落ちる**ことを見る）。
2. B: `lab.sh:196` → テスト。
3. C: `docker/compose/lab.sh` → README → テスト。
4. E: 011 の design.md 1 行。
5. F: `kafka_load.sh` → テスト。
6. I: `lab-debug.sh` → CFn → テスト。
7. J: テストだけ（`(i ^ 1)` の一時改変で赤→緑）。
8. K: `lab-common.sh` → `outputs.tf` → テスト。
9. H: lab 本体 → ECR → ミラー → lab の EC2 と CFn → 手元 → docs → テスト（最後に「戻らない」check）。`terraform fmt -check` と `validate` を `base/ecr` と `pipeline/lab` で打つ。
10. G: テスト（H 後の形で）。
11. 全テストと `bash ops/check.sh`（`terraform validate` と `bash -n` を含む）の出力を `build.md` に貼る。セルフレビュー（`/robust`）。

## 検証方法（期待出力まで）

1. テスト（check の件数。A +2、B ±0、C +1、F +3、G +2、H +1 −1、I ±0、J +6 程度、K ±0）。
   ```
   uv run --group dev --group web python tests/test_lab_debug.py     # 通過 111 程度 / 失敗 0（2026-10-09 は 97。+14）
   uv run --group dev --group web python tests/test_local_compose.py # 通過 138 / 失敗 0（+1 −1）
   uv run --group dev --group web python tests/test_stream.py        # 通過 106 / 失敗 0
   uv run --group dev --group web python tests/test_analytics.py     # 通過 515 / 失敗 0
   uv run --group dev --group web python tests/test_oss_ops.py       # 通過 200 / 失敗 0（`*.sh` の全角の走査が lab.sh の変更も見る）
   uv run --group dev --group web python tests/test_oss.py           # 通過 174 / 失敗 0
   ```
   件数の「程度」は実装で確定し、`build.md` に実測を書く。
2. A の赤→緑。直す前の `lab.sh` の `route()` を偽の `srl() { :; }` で打つと終了コードが 1 で stdout が空、直すと 0 で `  (IS-IS の経路が無い)`。出力を `build.md` に。
3. C。`.env` が `SRLINUX_IMAGE=…` 1 行だけの木で `docker/compose/lab.sh down` が `sudo env SRLINUX_IMAGE=… MULTITOOL_IMAGE= TREX_IMAGE= …`（H の後は `MULTITOOL_IMAGE=` 無し）を 1 回打って終了コード 0。同じ木で `up` は `TREX_IMAGE が無い` で止まる。
4. F。`cd app/containerlab/trex && bash -c '<nodes と peer の抜き出し>; for n in $(nodes | cut -d" " -f1); do echo "$n $(peer $n)"; done'` が 6 行で、`dc1-a-leaf-01 10.255.0.1`、`dc1-spine-01 10.255.1.1` を含み、空の peer が無い。
5. H。`git ls-files -z | grep -z -v -e '^docs/cycles/' -e '^docs/verification/' | xargs -0 grep -l -i multitool` が 0 本（終了コード 1）。`terraform -chdir=IaC/terraform/aws-managed/base/ecr validate` と `pipeline/lab` が Success。**AWS**: 次に `ops/up.sh` を打つとき 1. の ECR の apply が `1 to destroy`（`aws_ecr_repository.lab["multitool"]`）で、`aws ecr describe-repositories --repository-names efukuda-nwc-poc-lab-multitool` が `RepositoryNotFoundException` になる。lab の EC2 の 7 コンテナが上がる（`lab status`）。これは次回の AWS 検証にまとめる（このサイクルの中では打たない）。
6. I。`grep -n 'TELEGRAF_TAG=' ops/lab-debug.sh ops/up.sh` が `lab-debug.sh: TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH"` と `up.sh: TELEGRAF_TAG=$(telegraf_tag)`。`bash -n ops/lab-debug.sh` が 0。
7. J。`trex_cfg eth1 eth2 eth3 eth4` の出力（`build.md` に貼る）に `port_limit: 4` と 4 組の `ip` / `default_gw`。`(i ^ 1)` → `i` の一時改変で該当 check が**失敗 1**、戻して 0。
8. K。`grep -c 'clab-\*/\*' ops/lab-common.sh IaC/terraform/aws-managed/pipeline/lab/outputs.tf` が 1 / 1。
9. `bash ops/check.sh` の最後が `すべて通過`。

## 未確定事項とリスク

1. **H の ECR の repo の削除は次の `up.sh` の apply で起きる。** 2026-10-09 の AWS 検証は 024 のマージ前の main で立てているので、このサイクルの中では確かめられない。次回の AWS 検証（`docs/verification/` に書く）の項目に入れる。`force_delete = true` なのでイメージが入っていても消える（`base/ecr/main.tf:4` のコメント）。
2. **`lab_user_data.sh.tftpl` から `MULTITOOL_IMAGE=` を消すと user-data が変わり、次の apply で lab の EC2 が作り直される**（`user_data_replace_on_change` の設定による。`instance.tf` を読んで確かめ、作り直されるなら design の 5. に「EC2 の作り直し 10 分」を書く）。既に立っている環境では `terraform plan` が `1 to replace` を出すのが期待。
3. **`aws s3 sync --exclude "clab-*/*"` の glob がソースの相対パスに当たること。** `--exclude "__pycache__/*"` が同じ形で動いている（`tests/test_lab_debug.py:241` の既存の期待）ので同じ挙動のはず。実機では次回の AWS 検証で `aws s3 ls s3://<bucket>/lab/ --recursive | grep clab-` が 0 行であることを見る（手元に `clab-splab/` を置いて打つ）。
4. **F の `peer()` は「最初の overlay の neighbor」を取る。** s-leaf は 4 つの neighbor を持つが、負荷試験のレコードはどれか 1 つで足りる（Splunk / Grafana のルールは established では発火しない）。固定の `10.255.0.1` が a-leaf 以外のノードに実在しない peer だったのを「実在する peer」にするのが目的で、全 peer を回すことはしない。
5. **J の `edge_ports` は `srl` を呼ぶので、解析部分だけを打つには関数を分けるか偽の `srl` を置く。** 偽の `srl() { echo up; }` を置けば関数全体を打てる（出力が `eth1 dc1-s-leaf-01 3 up` の形になる想定。実装で `edge_ports` の実際の出力形を読んで期待値を合わせる）。
6. **I でタグを変えると、lab-debug の Telegraf は次回 1 回ビルドし直し**（`<prefix>-telegraf:<ver>-<hash>-amd64` が ECR に無い）。時間が増えるだけ。
