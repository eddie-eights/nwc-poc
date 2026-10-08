#!/usr/bin/env bash
# lab EC2（IaC/terraform/aws-managed/pipeline/lab / デバッグ用は IaC/cloudformation/lab-debug.yaml）の上で containerlab を動かす。setup.sh が /usr/local/bin/lab に置くので、SSM セッションから `sudo lab check` で使う。
#   lab.sh render | pull | up | down | status | check | snmp [node] | logs [node] | cli <node> [cmd...] | fail-main | heal-main | failover | clab <args...>
#   lab.sh fail-bgp | heal-bgp | trap-test   （Grafana と Splunk のアラートを比べる障害: BGP の隣接 1 本を止める / 戻す、link 以外の trap を 1 通送る）
#   lab.sh forward | forward-status     （stream: ECS の Telegraf へ SNMP / gNMI / trap / syslog を通す。デバッグ用の EC2 は trap の 162 を 1162 へ向けるだけ。up が毎回呼ぶ）
#   lab.sh telegraf run | stop | status | test | gnmi | logs [-f]   （デバッグ用の EC2 だけ。この EC2 の Telegraf。中身は app/telegraf/telegraf.sh、出力は標準出力）
#   lab.sh trex start | stop | status   （dc1-trex-01 の TRex。トポロジを上げても起動しない。負荷試験のときだけ。中身は trex/README.md）
# 手元の containerlab と違うのは 3 つ: containerlab を直接呼ぶ（root）、イメージは ECR から取る（pull）、
# splab.clab.yml はテンプレート（.in）からイメージ URI を埋めて作る（render）。
# トポロジは Spine-Leaf（gen_lab.py の図。SR Linux 6 台 = s-leaf 2 + spine 2 + a-leaf 2、TRex 1 台 = dc1-trex-01（各 leaf の ethernet-1/3 に eth1〜eth4 で 1 本ずつ））
set -euo pipefail
# /usr/local/bin/lab（シンボリックリンク）から呼ばれても、テンプレートのある src/ で動く
SELF=$(readlink -f "$0")
cd "$(dirname "$SELF")"
LAB=splab
TOPO=splab.clab.yml
# containerlab の管理ネットワーク（splab.clab.yml.in の mgmt）と、その上のこの EC2 のアドレス（srlinux/*.cli の trap と syslog の宛先）。
# Telegraf のタスク（IaC/terraform/aws-managed/pipeline/stream の ECS）は VPC のルートでここへ来る（IaC/terraform/aws-managed/pipeline/lab の telegraf.tf の local.mgmt_cidr）
MGMT=203.0.113.0/24
MGMT_GW=203.0.113.1
# trap を受けるポート（app/telegraf/telegraf.sh の TRAP_PORT。非 root の Telegraf は 162 で待てない）。デバッグ用の EC2 は機器が 162 に送るのをここへ向ける
TRAP_PORT=1162
# デバッグ用の EC2 の Telegraf のコンテナ名
TG=telegraf
# SR Linux の syslog（RFC 5424 / udp）を Telegraf へ送るポート（srlinux/*.cli の remote-port、app/telegraf/telegraf.conf.in の inputs.syslog、
# IaC/terraform/aws-managed/pipeline/lab の local.log_port と同じ）
LOG_PORT=5140
# SR Linux の syslog の形式。デバッグ用の EC2 の Telegraf に SYSLOG_STANDARD で渡す（app/telegraf/telegraf.sh の既定は本番の Cisco に合わせた RFC3164。
# ops/lab-common.sh の LAB_SYSLOG_STANDARD と同じ）
LOG_STANDARD=RFC5424
# containerlab が SR Linux 全台に入れる既定の認証情報（lab だけの公開既定値。ops/lab-common.sh の LAB_GNMI_USERNAME / LAB_GNMI_PASSWORD / LAB_SNMP_COMMUNITY と同じ）。
# デバッグ用の EC2 の Telegraf に渡す（stream の ECS は SSM の SecureString から受ける）
GNMI_USERNAME=admin
GNMI_PASSWORD='NokiaSrl1!'
SNMP_COMMUNITY=public
# gNMI（containerlab が SR Linux 全台で開ける。Telegraf の inputs.gnmi が BGP / IS-IS / EVPN の状態を購読する）
GNMI_PORT=57400
# SR Linux がコンテナの中に書くログ（lab.sh logs が読む。Telegraf へは syslog で別に送る）
SRL_LOG=/var/log/srlinux/file/messages
# forward が入れる iptables の規則の目印（入れ直す前にこれの付いた規則を全部消す）
FW_TAG=nwc-lab-telegraf
# TRex（負荷生成。各 leaf の mac-vrf に 1 本ずつ。trap-test の送り元にも使う）。ポート n（ethN+1）のアドレスは $TREX_NET.(TREX_IP_BASE+n) で、
# default_gw は組の相手（0-1、2-3）のアドレス。どれも同じ mac-vrf（VNI 100）にいるので、EVPN が通っていれば ARP が解ける
TREX=dc1-trex-01
TREX_NET=10.100.0; TREX_IP_BASE=11
# lab trex start がコンテナの中に書く設定（TRex の既定の置き場。t-rex-64 は --cfg 無しでこれを読む）、TRex の出力の置き場、
# trex/stl のプロファイルを写す先（trex-console の start -f に渡すパス。trex/README.md）
TREX_CFG=/etc/trex_cfg.yaml; TREX_LOG=/var/log/trex.log; TREX_PROFILES=/opt/nwc-trex
# fail-bgp / heal-bgp が止める iBGP（EVPN）の隣接: dc1-a-leaf-01 から dc1-spine-01 のループバックへの 1 本（srlinux/dc1-a-leaf-01.cli の bgp neighbor）
BGP_NODE=dc1-a-leaf-01; BGP_PEER=10.255.0.1
# trap-test が送る trap の OID（net-snmp の NET-SNMP-EXAMPLES-MIB::netSnmpExampleHeartbeatNotification）。link でも起動の知らせでもないので、
# Splunk の netops_trap と Grafana の trap ルールがどちらも kind = trap にする
TEST_TRAP_OID=.1.3.6.1.4.1.8072.2.3.0.1
# user_data が書く（キーは setup.sh の頭）。TELEGRAF_IMAGE があるのはデバッグ用の EC2（IaC/cloudformation/lab-debug.yaml）だけ
ENV_FILE=$(ls /etc/*-lab.env 2>/dev/null | head -1 || true)
[ -n "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

# VPC にインターネットへの経路が無い（IaC/terraform/aws-managed/base/core は NAT Gateway も IGW も作らない）ので、GitHub への版の確かめをしない
export CLAB_VERSION_CHECK=disable
clab() { containerlab "$@"; }
x() { docker exec "clab-$LAB-$1" "${@:2}"; }
# SR Linux の CLI。引数を 1 行ずつ流す（sr_cli "a" "b" は 1 行に繋がるので stdin から）
srl() { printf '%s\n' "${@:2}" | docker exec -i "clab-$LAB-$1" sr_cli -d; }
routers() { for f in srlinux/*.cli; do basename "$f" .cli; done; }
mgmt_ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "clab-$LAB-$1"; }
# $BGP_NODE の neighbor $BGP_PEER の admin-state を enable / disable にし、state を読み直して変わったことを確かめる。
# commit が通らなかったとき sr_cli -d が 0 以外で終わるかは確かめていないので、終了コードに頼らない。読み直した出力は変数に取ってから grep する
# （pipe の後ろの grep -q が先に終わると、前の sr_cli が SIGPIPE で落ち、pipefail で失敗に見える）
bgp_admin() {
  srl "$BGP_NODE" "enter candidate" "set / network-instance default protocols bgp neighbor $BGP_PEER admin-state $1" "commit now"
  local st; st=$(srl "$BGP_NODE" "info from state / network-instance default protocols bgp neighbor $BGP_PEER admin-state")
  grep -qw "admin-state $1" <<<"$st" || { echo "$BGP_NODE の neighbor $BGP_PEER の admin-state が $1 になっていない（commit が通らなかった）: $st" >&2; exit 1; }
}
# ifName / ifAdminStatus / ifOperStatus を ifIndex で突き合わせて出す（この EC2 から機器の管理 IP を snmpwalk。net-snmp-utils）。
# SR Linux は未設定の物理ポートも全部 ifTable に出す（admin=down）ので、admin=up の行だけ見せる。
# サブインタフェース（ethernet-1/1.0）も行になるが、回線の状態は親の行で見る
snmp_if() {
  local ip; ip=$(mgmt_ip "$1")
  join -t $'\t' \
    <(snmpwalk -v2c -c public -Oqn "$ip" 1.3.6.1.2.1.31.1.1.1.1 | sed 's/^[.0-9]*\.\([0-9]*\) /\1\t/; s/"//g' | sort) \
    <(join -t $'\t' \
        <(snmpwalk -v2c -c public -Oqn "$ip" 1.3.6.1.2.1.2.2.1.7 | sed 's/^[.0-9]*\.\([0-9]*\) /\1\t/' | sort) \
        <(snmpwalk -v2c -c public -Oqn "$ip" 1.3.6.1.2.1.2.2.1.8 | sed 's/^[.0-9]*\.\([0-9]*\) /\1\t/' | sort)) \
  | awk -F'\t' '$3 == "up" || $3 == 1 { printf "  ifIndex %-11s %-16s admin=%s oper=%s\n", $1, $2, $3, $4 }'
}
# TRex のポート（コンテナの eth1〜。eth0 は管理ネットワーク）を番号順に
trex_ports() { x "$TREX" ls /sys/class/net | grep -E '^eth[1-9][0-9]*$' | sort -V; }
# TRex の回線の両端が up か: leaf 側（splab.clab.yml.in の links で $TREX につながる SR Linux の e1-N = ethernet-1/N）の oper-state と、TRex 側の ethN。
# TRex のポートの L2 が leaf のあいだを通るか（EVPN）は、lab trex start のあとに leaf の bridge-table で見る（trex/README.md）
edge_ports() {
  local tp node n st
  while read -r tp node n; do
    st=$(srl "$node" "info from state / interface ethernet-1/$n oper-state" 2>/dev/null | grep -oE 'oper-state [a-z-]+' | head -1) || true
    printf '  %-14s %-14s %s\n' "$node" "ethernet-1/$n" "${st:-（読めない）}"
  done < <(grep -oE "\"$TREX:eth[0-9]+\", *\"[^\"]+:e1-[0-9]+\"" "$TOPO.in" | sed -E 's/^"[^:]+:(eth[0-9]+)", *"([^:]+):e1-([0-9]+)"$/\1 \2 \3/')
  for tp in $(trex_ports); do
    printf '  %-14s %-14s %s\n' "$TREX" "$tp" "oper-state $(x "$TREX" cat "/sys/class/net/$tp/operstate" 2>/dev/null || echo '（読めない）')"
  done
}
# trex/trex_cfg.yaml.in を埋めて標準出力へ。引数はポートの IF 名（eth1 eth2 …。この順が TRex のポート 0, 1, …）
trex_cfg() {
  local n=$# i list="" line
  for ((i = 1; i <= n; i++)); do list+="${list:+, }'${!i}'"; done
  while IFS= read -r line; do
    if [ "$line" = __PORT_INFO__ ]; then
      for ((i = 0; i < n; i++)); do
        printf '    - ip: %s.%d\n      default_gw: %s.%d\n' "$TREX_NET" $((TREX_IP_BASE + i)) "$TREX_NET" $((TREX_IP_BASE + (i ^ 1)))
      done
    else
      line=${line//__PORT_LIMIT__/$n}; printf '%s\n' "${line//__INTERFACES__/$list}"
    fi
  done < trex/trex_cfg.yaml.in
}
# Telegraf がこのホストの host ネットワークにいるか: デバッグ用の EC2（TELEGRAF_IMAGE）か、手元の compose（docker/compose/lab.sh が TELEGRAF_LOCAL=1 を渡す）
local_telegraf() { [ -n "${TELEGRAF_IMAGE:-}" ] || [ "${TELEGRAF_LOCAL:-0}" = 1 ]; }
# 障害を入れたあとにどこを見るか。デバッグ用の EC2 はこの EC2 の Telegraf の標準出力、手元は compose の Grafana / Splunk、stream は Grafana / Splunk が SNS のトピックに出すアラート
hint() {  # hint <デバッグ用の EC2 の文> <stream の文>
  if [ -n "${TELEGRAF_IMAGE:-}" ]; then echo "  この EC2 の Telegraf: $1"
  elif local_telegraf; then echo "  compose の Telegraf: 数分で Grafana（:3000）と Splunk（:8000）に出る（SNS のトピックは無い）"
  elif iptables -t nat -S PREROUTING 2>/dev/null | grep -q -- "--comment $FW_TAG"; then echo "  stream: $2"
  else echo "  Telegraf への転送が張られていない（sudo lab forward-status）"; fi
}
unforward() {  # forward が入れた規則（目印 ${FW_TAG}）を全部消す
  local t rules r
  for t in raw filter nat; do
    rules=$(iptables -t "$t" -S 2>/dev/null | grep -- "--comment $FW_TAG" || true)
    [ -n "$rules" ] || continue
    while read -ra r; do iptables -t "$t" -D "${r[@]:1}"; done <<<"$rules"
  done
}

case "${1:-}" in
  render)
    : "${SRLINUX_IMAGE:?}" "${MULTITOOL_IMAGE:?}" "${TREX_IMAGE:?}"
    sed -e "s#__SRLINUX_IMAGE__#$SRLINUX_IMAGE#" -e "s#__MULTITOOL_IMAGE__#$MULTITOOL_IMAGE#" -e "s#__TREX_IMAGE__#$TREX_IMAGE#" "$TOPO.in" > "$TOPO"
    echo "$TOPO を作った（イメージは ${REGISTRY:-?}）"
    ;;
  pull)
    # ECR の認証は 12 時間で切れるので、毎回ログインしてから取る（署名はインスタンスロール）。
    # REGISTRY が無い（手元。docker/compose/lab.sh がイメージを ghcr.io のまま渡す）ならログインせずに取る
    if [ -n "${REGISTRY:-}" ]; then
      : "${AWS_REGION:?}"
      aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
    fi
    for i in "$SRLINUX_IMAGE" "$MULTITOOL_IMAGE" "$TREX_IMAGE" ${TELEGRAF_IMAGE:+"$TELEGRAF_IMAGE"}; do docker pull -q "$i"; done
    ;;
  up)
    [ -f "$TOPO" ] || "$SELF" render
    docker image inspect "$SRLINUX_IMAGE" >/dev/null 2>&1 || "$SELF" pull
    # SR Linux は 1 台の起動に 1〜2 分かかる（containerlab が 6 台とも起動して設定が入るまで待つ。上限は 1 台 5 分）
    clab deploy -t "$TOPO" --reconfigure
    # Docker は管理ネットワークを作るたびに自分の MASQUERADE を nat の先頭に入れるので、deploy のあとに毎回入れ直す。
    # 失敗してもトポロジは上がっている（Telegraf に届かないだけ。sudo lab forward-status で見る）
    "$SELF" forward || echo "forward に失敗した（トポロジは動いている）。sudo lab forward-status で見る" >&2
    ;;
  down)   clab destroy -t "$TOPO" --cleanup ;;
  status) clab inspect -t "$TOPO" ;;
  clab)   clab "${@:2}" ;;
  cli)    srl "$2" "${@:3}" ;;
  check)
    echo "== BGP EVPN（dc1-spine-01 = ルートリフレクタ。s-leaf 2 + a-leaf 2 が established なら OK）=="; srl dc1-spine-01 "show network-instance default protocols bgp neighbor"
    echo "== IS-IS の隣接（dc1-a-leaf-01。spine 2 台が Up なら OK）=="; srl dc1-a-leaf-01 "show network-instance default protocols isis adjacency"
    echo "== TRex の回線（leaf の ethernet-1/3 と $TREX の eth1〜。両端とも up なら OK。L2 の疎通は lab trex start のあと trex/README.md の手順で見る）=="
    edge_ports
    echo "== SNMP（dc1-a-leaf-01 の ifName + ifOperStatus。admin=up の行だけ）=="
    snmp_if dc1-a-leaf-01
    ;;
  snmp) snmp_if "${2:-dc1-a-leaf-01}" ;;
  logs)
    for n in ${2:-$(routers)}; do
      echo "== $n =="; x "$n" tail -n "${LINES:-20}" "$SRL_LOG"
    done
    ;;
  fail-main)
    echo "DC 側 Leaf の fabric (dc1-a-leaf-01 ethernet-1/1 / dc1-spine-01) を落とす"
    # コンテナの中の veth（e1-1 = ethernet-1/1）を落とす。admin-state は enable のままなので、機器からは回線断（oper down）に見える。
    # IS-IS の隣接（dc1-a-leaf-01 - dc1-spine-01）が落ち、経路は dc1-spine-02 経由に切り替わる。iBGP はループバック同士なので張り直さない
    x dc1-a-leaf-01 ip link set e1-1 down
    echo "  IS-IS の隣接は数秒で落ちる。切替の確認は 'lab failover' が待ってくれる"
    ;;
  heal-main) echo "DC 側 Leaf の fabric (dc1-a-leaf-01 ethernet-1/1) を戻す"; x dc1-a-leaf-01 ip link set e1-1 up ;;
  fail-bgp)
    echo "$BGP_NODE の iBGP（EVPN）の隣接 1 本（dc1-spine-01 = $BGP_PEER）を止める"
    # neighbor の admin-state を disable にする（回線は落とさない）。$BGP_NODE 側と dc1-spine-01 側（neighbor は $BGP_NODE のループバック）の
    # session-state が established でなくなり、gNMI の on_change（bgp_neighbor）で流れる。EVPN の経路は dc1-spine-02 からも来るので、mac-vrf（TRex のポートのあいだ）は通ったまま
    bgp_admin disable
    hint "'sudo lab telegraf logs' に bgp_neighbor の session_state（established 以外）が出る。戻すのは 'lab heal-bgp'" \
      "数分で Grafana と Splunk の両方が bgp_down（$BGP_NODE の $BGP_PEER と、dc1-spine-01 の $BGP_NODE 側）を SNS のトピックに出す。戻すのは 'lab heal-bgp'"
    ;;
  heal-bgp)
    echo "$BGP_NODE の iBGP の隣接（$BGP_PEER）を戻す。established に戻るまで数十秒（'lab check' で見る）"
    bgp_admin enable
    ;;
  trap-test)
    # link 以外の trap を 1 通送る。機器（SR Linux）には出させず、この EC2 の net-snmp の snmptrap（setup.sh が入れる net-snmp-utils）を
    # $TREX の network namespace で動かす。送り元は $TREX の管理 IP になり、機器の trap と同じく $MGMT_GW の 162 に届くので、forward の規則
    # （デバッグ用の EC2 は REDIRECT、stream は NLB への DNAT）に乗る。この EC2 から直接送ると PREROUTING を通らないので乗らない。
    # 機器名は Spark（--device-map）と Splunk のアラートアクション（DEVICE_MAP）が送り元の IP から引く（$TREX になる）
    command -v snmptrap >/dev/null || { echo "snmptrap が無い（net-snmp-utils）" >&2; exit 1; }
    pid=$(docker inspect -f '{{.State.Pid}}' "clab-$LAB-$TREX")
    nsenter -t "$pid" -n snmptrap -v2c -c "$SNMP_COMMUNITY" "$MGMT_GW:162" '' "$TEST_TRAP_OID" .1.3.6.1.4.1.8072.2.3.2.1 i 1
    echo "trap $TEST_TRAP_OID を $TREX（$(mgmt_ip "$TREX")）から $MGMT_GW:162 へ送った"
    hint "'sudo lab telegraf logs' に snmp_trap（oid=$TEST_TRAP_OID）が出る" \
      "数分で Grafana と Splunk の両方が trap（$TREX の $TEST_TRAP_OID）を出し、次の trap が来なければおよそ 10 分後に両方が解消を出す"
    ;;
  failover)
    # dc1-a-leaf-01 から dc1-s-leaf-01 のループバック（10.255.1.1）への経路。切替前は spine 2 台（172.16.0.4 / 172.16.0.12）の ECMP、切替後は 172.16.0.12 だけ
    # 26.7.2 の sr_cli には "show … route-table ipv4-unicast prefix …" が無い（Unknown token 'ipv4-unicast'。2026-09-27 実測）ので、state の経路 → next-hop-group → next-hop の ip-address をたどる
    route() {
      local nhg i
      nhg=$(srl dc1-a-leaf-01 "info from state network-instance default route-table ipv4-unicast route 10.255.1.1/32 id * route-type isis route-owner * origin-network-instance * next-hop-group" 2>/dev/null | grep -oE 'next-hop-group [0-9]+' | head -1 | awk '{print $2}')
      [ -n "$nhg" ] || { echo "  (IS-IS の経路が無い)"; return 0; }
      for i in $(srl dc1-a-leaf-01 "info from state network-instance default route-table next-hop-group $nhg next-hop * next-hop" 2>/dev/null | grep -E '^ *next-hop [0-9]+ *$' | awk '{print $2}' | sort -u); do
        srl dc1-a-leaf-01 "info from state network-instance default route-table next-hop $i" 2>/dev/null | grep -oE 'ip-address [0-9.]+|subinterface [^ ]+' | tr '\n' ' ' || true
        echo
      done
    }
    echo "== 切替前: dc1-a-leaf-01 -> dc1-s-leaf-01 (10.255.1.1) の経路 =="; route
    "$SELF" fail-main
    echo "== dc1-spine-02 だけに切り替わるのを待つ（最大 60 秒）=="
    for i in $(seq 12); do
      if r=$(route 2>/dev/null) && grep -q "172.16.0.12" <<<"$r" && ! grep -q "172.16.0.4\b" <<<"$r"; then
        echo "  切替 OK（$((i*5)) 秒以内）"; break
      fi
      sleep 5
    done
    echo "== 切替後: dc1-a-leaf-01 -> dc1-s-leaf-01 の経路 =="; route
    echo "== SNMP で断が見えるか（dc1-a-leaf-01）=="
    # ifOperStatus は実際のリンク状態から数秒遅れる。down が見えるまで最大 10 秒待つ
    for i in $(seq 10); do
      w=$(snmp_if dc1-a-leaf-01)
      grep -qE 'ethernet-1/1 +admin=up oper=down' <<<"$w" && break
      sleep 1
    done
    echo "$w"
    if [ -n "${TELEGRAF_IMAGE:-}" ]; then
      echo "== Telegraf（この EC2。標準出力）=="
      echo "  'sudo lab telegraf logs' に interface（ifOperStatus）、snmp_trap の linkDown、device_log（syslog）、isis_interface の down が出る。戻すのは 'lab heal-main'"
    elif local_telegraf; then
      echo "== Telegraf（compose の Telegraf）=="
      echo "  数分で Grafana（:3000）の metrics ダッシュボードの dc1-a-leaf-01 ethernet-1/1 が DOWN、logs ダッシュボードと Splunk（:8000）に linkDown の trap と syslog が出る。戻すのは 'heal-main'"
    elif iptables -t nat -S PREROUTING 2>/dev/null | grep -q -- "--comment $FW_TAG"; then
      echo "== Telegraf（stream。ECS のタスク）=="
      echo "  ポーリング（10 秒周期）と SR Linux の linkDown トラップ、syslog、gNMI の IS-IS の隣接が MSK に流れ、Grafana のアラートルール（ポーリング）と Splunk の保存済みサーチ（trap と gNMI）が SNS のトピックに出す。"
      echo "  数分で GUI の「トポロジ」の dc1-a-leaf-01 ethernet-1/1 が DOWN になり（link_down と isis_down）、WORKFLOW=1 なら「承認」に修復案が出る。アラートは Grafana / Splunk で見る。戻すのは 'lab heal-main'"
    fi
    ;;
  forward)
    if local_telegraf; then
      # デバッグ用の EC2 と手元の compose: Telegraf はこのホストの host ネットワークにいるので、ポーリングと syslog（$MGMT_GW:$LOG_PORT）はそのまま届く。
      # 機器の trap は $MGMT_GW の 162 に来るので、Telegraf が待つ $TRAP_PORT へ向けるだけ（送り元は機器の管理 IP のまま）
      unforward
      c=(-m comment --comment "$FW_TAG")
      iptables -t nat -I PREROUTING 1 -s "$MGMT" -d "$MGMT_GW" -p udp --dport 162 "${c[@]}" -j REDIRECT --to-ports "$TRAP_PORT"
      if [ -n "${TELEGRAF_IMAGE:-}" ]; then w="この EC2 の Telegraf"; else w="compose の Telegraf"; fi
      echo "$w へ: trap 162/udp を $TRAP_PORT/udp へ向けた（syslog $LOG_PORT/udp とポーリングはそのまま）"
      exit 0
    fi
    # ECS の Telegraf（IaC/terraform/aws-managed/pipeline/stream の telegraf.tf）へ 4 つを通す。SSM の $PARAM_PREFIX/telegraf-address（内部 NLB の IP。trap と syslog の DNAT の宛先）と
    # $PARAM_PREFIX/telegraf-source-cidr（タスクのサブネット。タスクの IP は作り直すたびに変わるので、ポーリングはサブネットで通す）を読む。
    # 無ければ（stream を作っていない）何もしない。何度打っても同じ規則になる（目印の付いた規則を消してから入れる）
    : "${AWS_REGION:?}" "${PARAM_PREFIX:?}"
    t=$(aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/telegraf-address" --query Parameter.Value --output text 2>/dev/null) || t=""
    s=$(aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM_PREFIX/telegraf-source-cidr" --query Parameter.Value --output text 2>/dev/null) || s=""
    unforward
    if [ -z "$t" ] || [ -z "$s" ]; then
      echo "SSM $PARAM_PREFIX/telegraf-address か telegraf-source-cidr が無い（IaC/terraform/aws-managed/pipeline/stream を作っていない）ので、Telegraf への転送は張らない"
      exit 0
    fi
    c=(-m comment --comment "$FW_TAG")
    # ポーリング: Telegraf → SR Linux の SNMP（161/udp）と gNMI（$GNMI_PORT/tcp）。VPC のルートでこの EC2 に来る。Docker は外から管理ネットワークへの転送を落とすので DOCKER-USER で先に通す
    iptables -I DOCKER-USER 1 -s "$s" -d "$MGMT" -p udp --dport 161 "${c[@]}" -j ACCEPT
    iptables -I DOCKER-USER 1 -s "$s" -d "$MGMT" -p tcp --dport "$GNMI_PORT" "${c[@]}" -j ACCEPT
    # Docker 28 以降は raw の PREROUTING でブリッジ以外から来たコンテナ宛てを落とす。その前で抜ける（古い Docker では何もしない規則になる）
    iptables -t raw -I PREROUTING 1 -s "$s" -d "$MGMT" -p udp --dport 161 "${c[@]}" -j ACCEPT
    iptables -t raw -I PREROUTING 1 -s "$s" -d "$MGMT" -p tcp --dport "$GNMI_PORT" "${c[@]}" -j ACCEPT
    # trap と syslog: 機器の宛先（この EC2 の $MGMT_GW の 162 と $LOG_PORT）を Telegraf の NLB へ向け直す（NLB がタスクの 1162 と $LOG_PORT へ）
    iptables -t nat -I PREROUTING 1 -s "$MGMT" -d "$MGMT_GW" -p udp --dport 162 "${c[@]}" -j DNAT --to-destination "$t:162"
    iptables -t nat -I PREROUTING 1 -s "$MGMT" -d "$MGMT_GW" -p udp --dport "$LOG_PORT" "${c[@]}" -j DNAT --to-destination "$t:$LOG_PORT"
    iptables -I DOCKER-USER 1 -s "$MGMT" -d "$t" -p udp --dport 162 "${c[@]}" -j ACCEPT
    iptables -I DOCKER-USER 1 -s "$MGMT" -d "$t" -p udp --dport "$LOG_PORT" "${c[@]}" -j ACCEPT
    # 送り元（機器の管理 IP）を残す。Docker の MASQUERADE（-s $MGMT ! -o <bridge>）より前で抜ける。Spark とエージェントは送り元の IP で機器を引く
    iptables -t nat -I POSTROUTING 1 -s "$MGMT" -d "$t" "${c[@]}" -j RETURN
    echo "Telegraf へ通した: ${s} から SNMP 161/udp と gNMI $GNMI_PORT/tcp の転送、NLB（${t}）へ trap 162/udp と syslog $LOG_PORT/udp の DNAT"
    ;;
  forward-status)
    echo "== iptables（目印 ${FW_TAG}）=="
    for tb in raw filter nat; do iptables -t "$tb" -S 2>/dev/null | grep -- "--comment $FW_TAG" || true; done
    echo "== nat POSTROUTING（$FW_TAG の RETURN が Docker の MASQUERADE より上にあること）=="
    iptables -t nat -S POSTROUTING
    echo "== 機器から見た trap / syslog の宛先（$MGMT_GW。DNAT で Telegraf へ）と gNMI の受け口（$GNMI_PORT）=="
    for n in $(routers); do
      printf '  %-14s ' "$n"
      # list はキー無しだと "Missing value for 'host'" になるので * で全部出す。grep が空でも set -e / pipefail で止めない
      srl "$n" "info from state / system logging remote-server *" 2>/dev/null | grep -oE 'remote-server [0-9.]+' | head -1 | tr '\n' ' ' || true
      srl "$n" "info from state / system snmp trap-group * destination *" 2>/dev/null | grep -oE 'address [0-9.]+' | head -1 | tr '\n' ' ' || true
      srl "$n" "info from state / system grpc-server mgmt port" 2>/dev/null | grep -oE 'port [0-9]+' | head -1 | tr '\n' ' ' || true
      echo
    done
    ;;
  telegraf)
    : "${TELEGRAF_IMAGE:?TELEGRAF_IMAGE が無い（Telegraf をこの EC2 で動かすのはデバッグ用の EC2 だけ。lab の EC2 では stream の ECS の Telegraf を使う）}"
    case "${2:-status}" in
      run)
        # ポーリング先と gNMI の購読先は stream と同じく lab の定義から作る（ops/up.sh が stream の変数に渡すのと同じ lab_topology.py）。
        # SNMP のポーリングはこの EC2 では既定で止める（trap だけ。stream は既定でする）。見るときは `sudo SNMP_POLL=1 lab telegraf run`（起動時の systemd は既定の 0 で起こす）
        agents=$(python3 lab_topology.py . --snmp-agents)
        gnmi=$(python3 lab_topology.py . --gnmi-targets)
        docker image inspect "$TELEGRAF_IMAGE" >/dev/null 2>&1 || "$SELF" pull
        docker rm -f "$TG" >/dev/null 2>&1 || true
        # host ネットワーク: 管理ネットワーク（$MGMT）の機器へそのまま届き、機器からの $MGMT_GW:$LOG_PORT / $TRAP_PORT もそのまま受ける
        docker run -d --name "$TG" --restart unless-stopped --network host --log-opt max-size=50m --log-opt max-file=3 \
          -e SINK=stdout -e SYSLOG_STANDARD="$LOG_STANDARD" -e SNMP_POLL="${SNMP_POLL:-0}" -e AWS_REGION -e SNMP_AGENTS="$agents" -e GNMI_TARGETS="$gnmi" \
          -e GNMI_USERNAME="$GNMI_USERNAME" -e GNMI_PASSWORD="$GNMI_PASSWORD" -e SNMP_COMMUNITY="$SNMP_COMMUNITY" "$TELEGRAF_IMAGE" run >/dev/null
        echo "Telegraf を起こした（$TELEGRAF_IMAGE。出力は 'sudo lab telegraf logs -f'）"
        ;;
      stop)   docker rm -f "$TG" >/dev/null 2>&1 || true ;;
      status) docker ps -a --filter "name=^$TG\$" --format '{{.Names}}  {{.Status}}  {{.Image}}' ;;
      test)   docker exec "$TG" tg test ;;
      gnmi)   docker exec "$TG" tg gnmi ;;
      logs)   docker logs --tail "${LINES:-50}" "${@:3}" "$TG" ;;
      *) sed -n '6p' "$SELF"; exit 1 ;;
    esac
    ;;
  trex)
    # TRex 本体（t-rex-64 -i = stateless のサーバ。RPC は 4501 / 4507）を $TREX の中で起こす。ポートは Linux の IF を af_packet で使う（hugepages 不要、1 コア、~1 Mpps）。
    # 負荷を撃つのは trex-console か trex/stl のプロファイル（trex/README.md）。トポロジを上げても起動しないのは、負荷試験をやると決めるまで CPU を取らせないため
    case "${2:-status}" in
      start)
        mapfile -t ports < <(trex_ports)
        # TRex はポートを 2 本ずつ組にする（0-1、2-3）ので偶数本が要る（gen_lab.py も --leaves を偶数に限る）
        if [ "${#ports[@]}" -lt 2 ] || [ $(( ${#ports[@]} % 2 )) -ne 0 ]; then echo "$TREX のポートが偶数本でない（${ports[*]:-無し}）。lab check の TRex の回線を見る" >&2; exit 1; fi
        if x "$TREX" pgrep -f _t-rex-64 >/dev/null 2>&1; then echo "TRex はもう動いている（'lab trex status'。止めるのは 'lab trex stop'）"; exit 0; fi
        trex_cfg "${ports[@]}" | docker exec -i "clab-$LAB-$TREX" sh -c "cat > $TREX_CFG"
        x "$TREX" mkdir -p "$TREX_PROFILES"
        docker cp trex/stl "clab-$LAB-$TREX:$TREX_PROFILES/"
        # t-rex-64 は自分の置き場（イメージの版のディレクトリ）から打つ。置き場はイメージで違うので探す
        dir=$(x "$TREX" find / -xdev -maxdepth 5 -name t-rex-64 -type f 2>/dev/null | head -1) || true
        [ -n "$dir" ] || { echo "$TREX の中に t-rex-64 が無い（TREX_IMAGE が TRex のイメージか）" >&2; exit 1; }
        docker exec -d "clab-$LAB-$TREX" sh -c 'cd "$1" && exec ./t-rex-64 -i --no-key --iom 0 > "$2" 2>&1' sh "${dir%/*}" "$TREX_LOG"
        echo "TRex を起こした（ポート ${ports[*]}、設定 $TREX_CFG、出力 $TREX_LOG、プロファイル $TREX_PROFILES/stl）。起動に数十秒。'lab trex status' で見る"
        ;;
      stop)   x "$TREX" pkill -f t-rex-64 || echo "TRex は動いていない" ;;
      status)
        x "$TREX" pgrep -af t-rex-64 || echo "TRex は動いていない（'lab trex start'）"
        x "$TREX" tail -n "${LINES:-20}" "$TREX_LOG" 2>/dev/null || true
        ;;
      *) sed -n '7p' "$SELF"; exit 1 ;;
    esac
    ;;
  *) sed -n '2,7p' "$SELF"; exit 1 ;;
esac
