# OSS 版（cycle 005）の ops/oss/up.sh が読む。Kafka（pipeline/stream）と OpenSearch（pipeline/analytics）の ECS のサービスを
# 1 台ずつ入れ替える（設計の未確定事項 2・4）。どちらも台ごとにサービスが分かれ、入れ替えでは古いタスクを止めてから新しいタスクを起こす
# （deployment_minimum_healthy_percent = 0）。そのまま terraform apply するとタスク定義が変わった台が同時に入れ替わり、
# Kafka は controller の過半数を、OpenSearch は（インデックスがタスクの中にあるので）インデックスを失う。
#
#   roll_nodes <kafka|opensearch> <ルート> [-var 名前=値 …]
#     ルートの apply（呼ぶ側がこのあと打つ tf_apply と同じ -var を渡す）で変わる台を plan で調べ、変わる台があれば:
#       1. 入れ替える前に、クラスターが健全か確かめる（5 分まで待つ。健全でなければ何も入れ替えずに止まる）
#       2. リーダー（Kafka は KRaft のアクティブな controller、OpenSearch は cluster manager）でない台から 1 台選び、
#          その台のサービスだけ -target で apply して、サービスが安定するのを待ち、ほかの台から見てクラスターが健全に戻るのを待つ
#          （Kafka 15 分・OpenSearch 30 分。OpenSearch は入れ替えた台のシャードが複製し直されて green になるまで）
#       3. 残りの台で 2 を繰り返す（リーダーは最後）
#     止まったときは、もう一度 ops/oss/up.sh を打てば残りの台だけ入れ替える（plan で変わる台だけを拾うので）。
#     初めて作るとき（state にその種類のサービスが無い）と、変わる台が無いときは何もしない。
#     OSS_ROLL が空（ops/oss/up.sh の flag_value で 0 は空になる）なら何もせず、このあとの apply が変わる台を一度に入れ替える。
#   健全さは ECS Exec（aws ecs execute-command）でタスクの中のコマンドを打って見る。Kafka の 9092 と OpenSearch の 9200 は
#   VPC の外からも Web の EC2 からも届かない（SG で絞っている）ため。読むのは ops/oss/roll_health.py（判定の条件もそちら）。
#   ECS Exec は手元に Session Manager plugin が要り、タスクの中のコマンドの終了コードを返さないので、終了コードは出力に印で書く。
#   ECS Exec（--interactive）は標準入力が端末でないと切れるので、端末が無いときは script で疑似端末を付けて打つ（script も打てなければ止まる）。
#   OpenSearch の admin のパスワードは、タスクの secrets の環境変数（OPENSEARCH_INITIAL_ADMIN_PASSWORD）をタスクの中で読む（手元には持ってこない）。
# 先に ops/common.sh・ops/deploy-env.sh・ops/up-common.sh を読む（log / die / tf / tf_logged / tf_log_file / tf_init / tf_apply_only を使う）。
# REGION / PY / PREFIX / OWNER / OPS_DIR / OSS_ROLL は呼ぶ前に決める。plan のファイル ROLL_PLAN は、読む側の EXIT の trap でも消す
ROLL_PLAN=""
ROLL_MINUTES_PRE=5          # 入れ替える前の健全さを待つ時間（分）
ROLL_MINUTES_KAFKA=15       # Kafka の台を入れ替えたあと、健全に戻るのを待つ時間（分。EFS のログに追いつくまで）
ROLL_MINUTES_OPENSEARCH=30  # OpenSearch の台を入れ替えたあと、green に戻るのを待つ時間（分。シャードの複製し直しまで）
# タスクの中で打つコマンド。ECS Exec の --command に「/bin/bash -c '<これ>'」で渡すので、シングルクォートとバックスラッシュを使わない。
# 節ごとに「==nwc-roll <節>」と「==nwc-rc <終了コード>」を出す（ops/oss/roll_health.py が読む）。bootstrap はその台の 9092（どの台も broker）
ROLL_PROBE_KAFKA='B=/opt/kafka/bin; S=localhost:9092; echo "==nwc-roll brokers"; timeout 60 $B/kafka-broker-api-versions.sh --bootstrap-server $S 2>&1 | grep -E "[(]id: |Error|Exception"; echo "==nwc-rc ${PIPESTATUS[0]}"; echo "==nwc-roll urp"; timeout 60 $B/kafka-topics.sh --bootstrap-server $S --describe --under-replicated-partitions 2>&1; echo "==nwc-rc $?"; echo "==nwc-roll quorum"; timeout 60 $B/kafka-metadata-quorum.sh --bootstrap-server $S describe --replication 2>&1; echo "==nwc-rc $?"; echo "==nwc-roll end"'
ROLL_PROBE_OPENSEARCH='P=${OPENSEARCH_INITIAL_ADMIN_PASSWORD:-}; if [ -z "$P" ]; then echo "==nwc-roll nopass"; exit 0; fi; echo "==nwc-roll health"; curl -s -m 20 -u "admin:$P" http://localhost:9200/_cluster/health; R=$?; echo; echo "==nwc-rc $R"; echo "==nwc-roll manager"; curl -s -m 20 -u "admin:$P" "http://localhost:9200/_cat/cluster_manager?h=node"; R=$?; echo; echo "==nwc-rc $R"; echo "==nwc-roll end"'
# roll_nodes が決める（roll_service / roll_exec / roll_wait が読む）
ROLL_KIND=""; ROLL_LABEL=""; ROLL_CLUSTER=""; ROLL_CONTAINER=""; ROLL_PROBE=""
ROLL_SVCS=""     # 「台 サービス名」の行
ROLL_NODES=""    # 全部の台（空白区切り）
ROLL_LEADER=""   # roll_wait が健全と見たときのリーダーの台
ROLL_REASON=""   # roll_wait が健全でないと見た最後の理由
ROLL_TTY=""      # 標準入力が端末でないときに ECS Exec を包む script の形（linux / bsd。空なら包まずに打つ）

roll_service() {  # roll_service <台>  その台の ECS のサービス名
  printf '%s\n' "$ROLL_SVCS" | awk -v k="$1" '$1 == k { print $2 }'
}
roll_exec() {  # roll_exec <サービス名>  そのサービスの動いているタスクで ROLL_PROBE を打ち、出力（Session Manager の案内も混ざる）を出す
  local task cmd
  task=$(aws ecs list-tasks --region "$REGION" --cluster "$ROLL_CLUSTER" --service-name "$1" --desired-status RUNNING \
    --query 'taskArns[0]' --output text 2>&1) || task=""
  case "$task" in
    arn:aws:ecs:*) ;;
    *) echo "$1 に動いているタスクが無い（${task:-空}）"; return 0 ;;
  esac
  # 標準入力はつなげたまま（--interactive の Session Manager plugin が読む）。端末が無いときは roll_nodes が決めた形の script で疑似端末を付ける。
  # 疑似端末の中では aws の出力がページャに回りうるので AWS_PAGER を空にする
  cmd=(aws ecs execute-command --region "$REGION" --cluster "$ROLL_CLUSTER" --task "$task" --container "$ROLL_CONTAINER" \
    --interactive --command "/bin/bash -c '$ROLL_PROBE'")
  case "$ROLL_TTY" in
    # util-linux の script は -c の文字列を $SHELL -c で読むので、いま動いている bash に printf %q で割らせる
    linux) AWS_PAGER="" SHELL="$BASH" script -q -c "$(printf '%q ' "${cmd[@]}")" /dev/null 2>&1 || true ;;
    bsd) AWS_PAGER="" script -q /dev/null "${cmd[@]}" 2>&1 || true ;;
    *) "${cmd[@]}" 2>&1 || true ;;
  esac
}
roll_wait() {  # roll_wait <分> [<除く台>]  除く台以外から順に見て、健全なら ROLL_LEADER を決めて 0。時間切れなら ROLL_REASON を残して 1
  local limit=$(($1 * 60)) skip="${2:-}" start=$SECONDS i=0 k out last=""
  local probes=()
  for k in $ROLL_NODES; do
    if [ "$k" != "$skip" ]; then probes+=("$k"); fi
  done
  while :; do
    k=${probes[$((i % ${#probes[@]}))]}
    i=$((i + 1))
    out=$(roll_exec "$(roll_service "$k")")
    # shellcheck disable=SC2086
    if ROLL_LEADER=$(printf '%s\n' "$out" | "${PY[@]}" "$OPS_DIR/roll_health.py" "$ROLL_KIND" $ROLL_NODES); then
      echo "   $ROLL_LABEL は健全（$((SECONDS - start)) 秒。$k の中から見た。リーダーは $ROLL_LEADER）"
      return 0
    fi
    ROLL_REASON=$ROLL_LEADER
    ROLL_LEADER=""
    if [ "$ROLL_REASON" != "$last" ]; then
      echo "   まだ（$((SECONDS - start)) 秒。$k の中から見た）: $ROLL_REASON"
      last=$ROLL_REASON
    fi
    [ $((SECONDS - start)) -lt "$limit" ] || return 1
    sleep 20
  done
}
roll_nodes() {  # roll_nodes <kafka|opensearch> <ルート> [-var 名前=値 …]
  local kind="$1" root="$2"; shift 2
  local st keys planned names minutes hint left rolled next k svc
  case "$kind" in
    kafka) ROLL_LABEL="Kafka"; names=kafka_service_names; minutes=$ROLL_MINUTES_KAFKA; ROLL_PROBE=$ROLL_PROBE_KAFKA ;;
    opensearch) ROLL_LABEL="OpenSearch"; names=opensearch_service_names; minutes=$ROLL_MINUTES_OPENSEARCH; ROLL_PROBE=$ROLL_PROBE_OPENSEARCH ;;
    *) die "roll_nodes の種類は kafka か opensearch: $kind" ;;
  esac
  ROLL_KIND=$kind
  ROLL_CONTAINER=$kind
  if [ -z "$OSS_ROLL" ]; then
    echo "OSS_ROLL=0: $ROLL_LABEL の台を 1 台ずつ入れ替えない（このあとの apply で、変わる台が同時に入れ替わる）"
    return 0
  fi
  tf_init "$root"
  # 初めて作るときは入れ替える台が無い（grep -q はパイプの途中で抜けて pipefail に引っかかるので、いったん受けてから見る）
  st=$(tf "$root" state list 2>/dev/null) || st=""
  case "$st" in *"aws_ecs_service.$kind["*) ;; *) return 0 ;; esac
  log "$ROLL_LABEL の台のうち、このあとの apply で変わる台を plan で調べる（変わる台は 1 台ずつ入れ替える。OSS_ROLL=0 で一度に）"
  ROLL_PLAN=$(mktemp "${TMPDIR:-/tmp}/$PREFIX-roll-plan.XXXXXX") || die "一時ファイルを作れない（TMPDIR）"
  planned=0; keys=""
  if tf_logged "$root" plan -input=false -out="$ROLL_PLAN" -var "owner=$OWNER" "$@"; then
    planned=1
    keys=$(tf "$root" show -json "$ROLL_PLAN" | "${PY[@]}" "$OPS_DIR/roll_health.py" plan "$kind") || planned=2
  fi
  rm -f "$ROLL_PLAN"; ROLL_PLAN=""
  [ "$planned" != 0 ] || die "$TF_DIR/$root の plan に失敗した（上のエラー。全文は $(tf_log_file "$root" plan)）。まだ何も入れ替えていない"
  [ "$planned" != 2 ] || die "$TF_DIR/$root の plan（terraform show -json）から変わる台を読めなかった。まだ何も入れ替えていない"
  if [ -z "$keys" ]; then
    echo "$ROLL_LABEL の台は変わらない"
    return 0
  fi
  command -v session-manager-plugin >/dev/null \
    || die "Session Manager plugin が無い（$ROLL_LABEL の台を 1 台ずつ入れ替えるとき、ECS Exec でクラスターの様子を見るのに使う。docs/setup.md「Terraform を打つ PC 側」）。まだ何も入れ替えていない。待たずに一度に入れ替えるなら OSS_ROLL=0 $OPS_DIR/up.sh"
  # ECS Exec の --interactive は、標準入力が端末でない（CI、nohup、< /dev/null、エージェントのシェル）と「Cannot perform start session: EOF」で
  # 出力が返る前に切れる（2026-10-08 の OSS 版の検証。入れ替える前の確認が 5 分空回りして止まった）。端末が無ければ script で疑似端末を付ける。
  # script の形は 2 つある（util-linux は script -q -c "<文字列>" <ファイル>、macOS などの BSD は script -q <ファイル> <コマンド…>）。
  # util-linux は BSD の形を渡しても 0 で終わる（コマンドを無視して対話のシェルを起こす）ので、終了コードではなく出力の印で見分ける
  ROLL_TTY=""
  if [ ! -t 0 ]; then
    if command -v script >/dev/null; then
      case "$(SHELL="$BASH" script -q -c 'echo nwc-roll-tty' /dev/null </dev/null 2>/dev/null)" in
        *nwc-roll-tty*) ROLL_TTY=linux ;;
        *) case "$(script -q /dev/null echo nwc-roll-tty </dev/null 2>/dev/null)" in *nwc-roll-tty*) ROLL_TTY=bsd ;; esac ;;
      esac
    fi
    [ -n "$ROLL_TTY" ] \
      || die "標準入力が端末でなく、疑似端末を付ける script も打てない（$ROLL_LABEL の台を 1 台ずつ入れ替えるとき、ECS Exec の --interactive は端末が無いと「Cannot perform start session: EOF」で切れる）。まだ何も入れ替えていない。端末から打つか、待たずに一度に入れ替えるなら OSS_ROLL=0 $OPS_DIR/up.sh"
    echo "標準入力が端末でないので、ECS Exec は script で疑似端末を付けて打つ（$ROLL_TTY の形）"
  fi
  case "$kind" in
    kafka)
      ROLL_CLUSTER=$(tf "$root" output -raw kafka_ecs_cluster_name) || die "$TF_DIR/$root の output kafka_ecs_cluster_name を読めなかった"
      hint="ロググループ $(tf "$root" output -raw kafka_log_group_name 2>/dev/null || echo '（読めなかった）')" ;;
    opensearch)
      ROLL_CLUSTER=$(tf "$root" output -raw analytics_cluster_name) || die "$TF_DIR/$root の output analytics_cluster_name を読めなかった"
      hint="ロググループ $(tf "$root" output -raw opensearch_log_group_name 2>/dev/null || echo '（読めなかった）')" ;;
  esac
  ROLL_SVCS=$(tf "$root" output -json "$names" \
    | "${PY[@]}" -c 'import json, sys; print("\n".join(k + " " + v for k, v in sorted(json.load(sys.stdin).items())))') \
    || die "$TF_DIR/$root の output $names を読めなかった"
  ROLL_NODES=$(printf '%s\n' "$ROLL_SVCS" | awk 'NF { printf "%s ", $1 }')
  ROLL_NODES=${ROLL_NODES% }
  echo "$ROLL_LABEL の変わる台: $keys（全部の台: $ROLL_NODES）"
  log "$ROLL_LABEL が健全か確かめる（入れ替える前。${ROLL_MINUTES_PRE} 分まで待つ。ECS Exec で台の中から見る）"
  roll_wait "$ROLL_MINUTES_PRE" "" \
    || die "$ROLL_LABEL が入れ替える前から健全でない（$ROLL_REASON）。まだ何も入れ替えていない。様子を見て（ECS のクラスター $ROLL_CLUSTER、$hint）、直ったらもう一度 $OPS_DIR/up.sh。健全さを待たずに変わる台を一度に入れ替えるなら OSS_ROLL=0 $OPS_DIR/up.sh"
  left=$keys; rolled=""
  while [ -n "$left" ]; do
    next=""
    for k in $left; do
      if [ "$k" != "$ROLL_LEADER" ]; then next=$k; break; fi
    done
    [ -n "$next" ] || next=$ROLL_LEADER   # 残りがリーダーだけ
    svc=$(roll_service "$next")
    [ -n "$svc" ] || die "$TF_DIR/$root の output $names に台 $next が無い（$ROLL_NODES）"
    log "$ROLL_LABEL の台 $next（$svc）を入れ替える（${rolled:+入れ替えた台:$rolled。}残り: $left。リーダー: $ROLL_LEADER）"
    tf_apply_only "$root" "$@" -target="aws_ecs_service.$kind[\"$next\"]"
    rolled="$rolled $next"
    left=$(for k in $left; do [ "$k" = "$next" ] || printf '%s ' "$k"; done)
    left=${left% }
    # services-stable は 10 分で諦めるので、2 回まで待つ
    aws ecs wait services-stable --region "$REGION" --cluster "$ROLL_CLUSTER" --services "$svc" 2>/dev/null \
      || aws ecs wait services-stable --region "$REGION" --cluster "$ROLL_CLUSTER" --services "$svc" \
      || die "$svc が 20 分たっても安定しない（入れ替えにかかった台:$rolled。残り: ${left:-なし}）。aws ecs list-tasks --region $REGION --cluster $ROLL_CLUSTER --service-name $svc --desired-status STOPPED と $hint を見る。直ったらもう一度 $OPS_DIR/up.sh（残りの台だけ入れ替える）"
    echo "   $svc は安定した。ほかの台から見て $ROLL_LABEL が健全に戻るのを待つ（${minutes} 分まで）"
    roll_wait "$minutes" "$next" \
      || die "$ROLL_LABEL の台 $next を入れ替えたあと、${minutes} 分たっても健全に戻らない（$ROLL_REASON）。入れ替えた台:$rolled。残り: ${left:-なし}。様子を見て（ECS のクラスター $ROLL_CLUSTER、$hint）、戻ったらもう一度 $OPS_DIR/up.sh（残りの台だけ入れ替える）"
  done
  echo "$ROLL_LABEL の台を 1 台ずつ入れ替えた（入れ替えた順:$rolled）"
}
