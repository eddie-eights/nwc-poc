# ops/down.sh と OSS 版（005）の oss/ops/down.sh が読む共通の関数（ルートの destroy、消し残しの片付けと数え上げ）。
# 先に ops/common.sh と ops/deploy-env.sh を読む（log / die / tf / tf_logged を使う）。REGION / PREFIX / OWNER は呼ぶ前に決める。
# 名前で探すものは、どれも接頭辞の完全一致か「接頭辞-」で絞る（OSS 版の <owner>-nwc-oss は、OWNER=<名前>-nwc-oss のマネージド版の
# <名前>-nwc-oss-nwc-poc の頭と同じ文字列になる。前方一致で探すと相手のものを消す）
has_resources() {  # has_resources <ルート>  state があり、リソースが 1 つ以上載っている（init もここで済ませる）
  [ -f "$TF_DIR/$1/terraform.tfstate" ] || return 1
  tf_init_root "$1"  # ops/common.sh。OSS 版は -lockfile=readonly が付く
  [ -n "$(tf "$1" state list 2>/dev/null)" ]
}
# SG が消えないときの DependencyViolation は「まだ何かが掴んでいる」としか言わないので、掴んでいるものを名指しで出す。
# 掴んでいるのは 2 種類ある:
#   1. ENI — サービスが持つもの（RequesterManaged=true。MSK のブローカー、VPC エンドポイント、
#      AgentCore Runtime）は自分では消せないので、AWS 側が片付けるのを待つしかない。それ以外で status=available のものは
#      誰も使っていない残骸なので、ここで消す
#   2. 他の SG のルート — その SG をこの SG から参照していると、参照している側が消えるまでこの SG は消せない
show_and_reap_sg() {  # show_and_reap_sg <SG ID>
  local sg="$1" eni st managed desc
  echo "  SG $sg を掴んでいる ENI:"
  aws ec2 describe-network-interfaces --region "$REGION" --filters "Name=group-id,Values=$sg" \
    --query 'NetworkInterfaces[].[NetworkInterfaceId,Status,RequesterManaged,Description]' --output text 2>/dev/null \
    | while IFS=$'\t' read -r eni st managed desc; do
        echo "    $eni $st RequesterManaged=$managed $desc"
        if [ "$st" = available ] && [ "$managed" = False ]; then
          aws ec2 delete-network-interface --region "$REGION" --network-interface-id "$eni" >/dev/null 2>&1 \
            && echo "      → 誰も使っていないので消した"
        fi
      done
  echo "  SG $sg を参照しているルールを持つ他の SG:"
  aws ec2 describe-security-groups --region "$REGION" --filters "Name=ip-permission.group-id,Values=$sg" \
    --query "SecurityGroups[?GroupId!='$sg'].[GroupId,GroupName]" --output text 2>/dev/null | sed 's/^/    ingress /'
  aws ec2 describe-security-groups --region "$REGION" --filters "Name=egress.ip-permission.group-id,Values=$sg" \
    --query "SecurityGroups[?GroupId!='$sg'].[GroupId,GroupName]" --output text 2>/dev/null | sed 's/^/    egress  /'
}
FAILED_ROOTS=""  # 消えなかったルート（最後にまとめて出して、終了コードを 1 にする）
destroy_root() {  # destroy_root <ルート> [-var 名前=値 …]  消えたら 0、消えなかったら 1（呼ぶ側は止まらない）
  local root="$1"; shift
  if ! has_resources "$root"; then echo "$TF_DIR/$root: 無い（state が無いか空）"; return 0; fi
  echo "$TF_DIR/$root: 消す"
  local logf try sg
  logf=$(tf_log_file "$root" destroy)
  # DependencyViolation は、消したサービスの ENI を AWS 側が片付けるまでの数分だけ出ることが多い。
  # 掴んでいるものを名指しで出しながら 3 回まで打ち直す（誰も使っていない ENI はその場で消える）
  for try in 1 2 3; do
    if tf_logged "$root" destroy -input=false -auto-approve -var "owner=$OWNER" "$@"; then
      echo "$TF_DIR/$root: 消えた"
      return 0
    fi
    # DependencyViolation 以外の失敗（変数の不足・権限・state の食い違い）は待っても変わらないので打ち直さない
    grep -q DependencyViolation "$logf" 2>/dev/null || break
    for sg in $(grep DependencyViolation "$logf" | grep -Eo 'sg-[0-9a-f]+' | sort -u); do
      show_and_reap_sg "$sg"
    done
    [ "$try" -lt 3 ] || break
    echo "$TF_DIR/$root: DependencyViolation だった。2 分待って $((try + 1)) 回目を打つ"
    sleep 120
  done
  # ここで止めない。1 つのルートで抜けると後ろのルート（lab / agent / 土台）が消えず、EC2 が動いたまま課金が続く。
  # 覚えておいて残りを消しにいき、最後にまとめて出す（AWS 側の ENI 待ちなら、待ってから打ち直せば消える）
  FAILED_ROOTS="$FAILED_ROOTS $root"
  echo "NG: $TF_DIR/$root が消えなかった（上のエラー。全文は ${logf}）。先へ進んで、残りのルートを消す"
  return 1
}
# VPC の中の Lambda は、関数を消しても ENI が available のまま 20〜40 分残り、SG とサブネットの削除を DependencyViolation で待たせる
# （2026-09-18 に graph の destroy が 20 分以上止まった）。destroy の間、その関数の available な ENI だけを裏で消し続ける。
reap_lambda_enis() {  # reap_lambda_enis <関数名>  親（このスクリプト）が終われば止まる
  local e
  while kill -0 $$ 2>/dev/null; do
    for e in $(aws ec2 describe-network-interfaces --region "$REGION" \
        --filters "Name=description,Values=AWS Lambda VPC ENI-$1-*" Name=status,Values=available \
        --query 'NetworkInterfaces[].NetworkInterfaceId' --output text 2>/dev/null); do
      if aws ec2 delete-network-interface --region "$REGION" --network-interface-id "$e" >/dev/null 2>&1; then
        echo "Lambda（$1）の残った ENI を 1 つ消した"
      fi
    done
    sleep 20
  done
}
destroy_lambda_root() {  # destroy_lambda_root <ルート> <VPC の中の Lambda の関数名> [-var 名前=値 …]
  local root="$1" fn="$2" reaper; shift 2
  reap_lambda_enis "$fn" &
  reaper=$!
  destroy_root "$root" "$@"
  kill "$reaper" 2>/dev/null; wait "$reaper" 2>/dev/null || true
}
LOG_GROUP=""  # agent の state から読んだ Runtime のロググループ（delete_runtime_log_groups が一緒に消す）
destroy_agent() {  # agent（Runtime / ガードレール / KB）を消す。IaC/terraform/aws-managed/base/core のロールにポリシーを付けているので base/core より先
  local agent_vars=()
  LOG_GROUP=""
  if has_resources agent; then
    LOG_GROUP=$(tf agent output -raw runtime_log_group_name 2>/dev/null || true)
  fi
  # create_knowledge_base=true で作った agent は、既定の false のまま destroy すると KB のリソースを state から外そうとして止まらないよう、state から読む
  if has_resources agent && tf agent state list 2>/dev/null | grep -q '^aws_opensearchserverless_collection\.kb\['; then
    agent_vars+=(-var create_knowledge_base=true)
  fi
  # KB を作っていれば、ベクトルインデックスを作る Lambda（IaC/terraform/aws-managed/agent/kb.tf）が VPC の中にいる。ENI を刈りながら消す
  destroy_lambda_root agent "$PREFIX-kb-index" ${agent_vars[@]+"${agent_vars[@]}"}
}
# 土台（base/core）を消す。
# Runtime の ENI（種類 agentic_ai。AWS 側の所有で、自分では外せない）は Runtime を消したあとも最大 8 時間残り、その間はサブネットと
# runtime の SG（IaC/terraform/aws-managed/base/core の security_groups.tf）が DependencyViolation で消えない（terraform は 20 分待ってから落ちる）。
# runtime の SG を参照するルール（endpoints の受信、runtime 自身の送信）は別のリソースなので一緒に消え、ほかの SG は消せる。
# 残っているあいだは、それ以外だけを消して先へ進む（2026-09-28 より前の state なら NAT Gateway・EIP・IGW も。時間課金があるのでこのとき消す）。
# 残る VPC・サブネット・SG に時間課金は無く、次の up.sh はそのまま使い回す
MAIN_LEFT=0
destroy_base_core() {
  local vpcs="" src v out agent_enis addr main_targets=()
  if ! has_resources base/core; then
    echo "$TF_DIR/base/core: 無い（state が無いか空）"
    return 0
  fi
  # 確認そのものが落ちたときに黙って全部消しにいくと 20 分待ちに戻るので、結果は必ず表示し、エラーも隠さない
  # VPC はこのルートの state の aws_vpc.this から読む。terraform の output は使わない（destroy が途中で落ちた state には output が残らず
  # （terraform は output を先に外す）、`terraform output -raw` は空を返して成功するので、打ち直しのとき = いちばん要るときに読めない）。
  # タグ Name で引くのは state から読めないときだけ。同じ名前の VPC が 2 つあると 1 つ目だけでは古い方を引くことがある
  # （2026-10-08 の OSS 版の検証。今回の VPC の Runtime の ENI を見落として全部消しにいき、SG の削除待ちを 3 回繰り返して落ちた）ので、
  # 当たったものを全部見て、どれか 1 つにでも ENI があれば残す側に倒す
  vpcs=$(tf base/core state show aws_vpc.this 2>/dev/null \
    | awk -F'"' '/^[[:space:]]*id[[:space:]]*=/ && v == "" { v = $2 } END { print v }') || vpcs=""
  case "$vpcs" in vpc-*) src="state の aws_vpc.this" ;; *) vpcs="" ;; esac
  if [ -z "$vpcs" ]; then
    src="タグ Name=$PREFIX-vpc"
    if out=$(aws ec2 describe-vpcs --region "$REGION" --filters "Name=tag:Name,Values=$PREFIX-vpc" \
        --query 'Vpcs[].VpcId' --output text); then
      for v in $out; do
        case "$v" in vpc-*) vpcs="${vpcs:+$vpcs,}$v" ;; esac
      done
    else
      echo "注意: VPC を引けなかった（上のエラー）"
    fi
  fi
  echo "Runtime の ENI を探す VPC: ${vpcs:-（読めない）}（$src）"
  agent_enis=""
  if [ -n "$vpcs" ]; then
    agent_enis=$(aws ec2 describe-network-interfaces --region "$REGION" --filters "Name=vpc-id,Values=$vpcs" \
      --query "NetworkInterfaces[?InterfaceType=='agentic_ai'].NetworkInterfaceId" --output text) \
      || echo "注意: ENI の確認に失敗した（上のエラー）。残っていない扱いで進む"
  fi
  echo "Runtime の ENI の確認: VPC=${vpcs:-（読めない）} 残り=${agent_enis:-なし}"
  if [ -n "$agent_enis" ] && [ "$agent_enis" != None ]; then
    MAIN_LEFT=1
    echo "Runtime の ENI が残っている: $agent_enis"
    echo "VPC・サブネット・runtime の SG は残し、それ以外を消す"
    while IFS= read -r addr; do
      case "$addr" in
        # aws_security_group.internal は 2026-09-29 より前の state（全ワークロード共用の SG 1 つだった）
        ""|data.*|aws_vpc.this|aws_subnet.*|'aws_security_group.workload["runtime"]'|aws_security_group.internal) ;;
        *) main_targets+=("-target=$addr") ;;
      esac
    done < <(tf base/core state list 2>/dev/null)
    if [ "${#main_targets[@]}" -gt 0 ]; then
      tf_logged base/core destroy -input=false -auto-approve -var "owner=$OWNER" "${main_targets[@]}" || {
        FAILED_ROOTS="$FAILED_ROOTS base/core"
        echo "NG: $TF_DIR/base/core の ENI に関わらない部分が消えなかった（上のエラー）。先へ進んで、残りを消す"
      }
    else
      echo "$TF_DIR/base/core: 残っているのは VPC・サブネット・runtime の SG だけ"
    fi
  else
    destroy_root base/core
  fi
}
delete_runtime_log_groups() {  # Runtime のロググループ（AgentCore が作るもので Terraform の管理外）。destroy_agent のあとに呼ぶ
  # 名前は agent の state からも読めるが、前回の down.sh が途中（main の destroy など）で落ちていると、打ち直しのときには state が空で読めない。
  # Runtime はここまでに消してあるので、この接頭辞のロググループを全部消す（Runtime を作り直すたびに末尾の ID が変わり、古いものが溜まる）。
  # 接頭辞は「<PREFIX の - を _ に>_agent-」まで書く（<名前>_nwc_oss_agent- は <名前>_nwc_oss_nwc_poc_agent- に当たらない）
  local log_prefix="/aws/bedrock-agentcore/runtimes/${PREFIX//-/_}_agent-" log_groups g
  log_groups=$(aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$log_prefix" \
    --query 'logGroups[].logGroupName' --output text) || echo "注意: ロググループを引けなかった（上のエラー）"
  if [ -n "$LOG_GROUP" ]; then log_groups="$log_groups $LOG_GROUP"; fi
  log_groups=$(printf '%s\n' $log_groups | grep -v '^None$' | sort -u)
  if [ -z "$log_groups" ]; then echo "$log_prefix*: 無い"; fi
  for g in $log_groups; do
    aws logs delete-log-group --region "$REGION" --log-group-name "$g" 2>/dev/null && echo "$g: 消した" || echo "$g: 無い"
  done
}
delete_up_ssm_params() {  # up.sh が作った SSM のパラメータ（/<PREFIX>/ の下で、タグ ManagedBy=<OPS_DIR>/up.sh のもの）を消す
  # Terraform の state に値を載せないよう up.sh が作ったもので、Terraform の管理外。タグ ManagedBy の付いたものだけ消す
  # （手で入れたパラメータは消さない）。値は読まない。Path は「/<PREFIX>/」と末尾の / まで書くので、/<PREFIX>-…/ の下には届かない
  local ssm_params n
  ssm_params=$(aws ssm describe-parameters --region "$REGION" \
    --parameter-filters "Key=Path,Option=Recursive,Values=/$PREFIX/" "Key=tag:ManagedBy,Values=$OPS_DIR/up.sh" \
    --query 'Parameters[].Name' --output text) || echo "注意: SSM のパラメータを引けなかった（上のエラー）"
  ssm_params=$(printf '%s\n' $ssm_params | grep -v '^None$' || true)
  if [ -z "$ssm_params" ]; then echo "/$PREFIX/（ManagedBy=$OPS_DIR/up.sh）: 無い"; fi
  for n in $ssm_params; do
    # nautobot が消えなかったときは、その secrets を残す（RDS のパスワードを Terraform が destroy でも読むので、消すと打ち直しても消せなくなる）
    case "$n" in "/$PREFIX/nautobot/"*)
      case " $FAILED_ROOTS " in *" pipeline/nautobot "*) echo "$n: 残す（$TF_DIR/pipeline/nautobot が消えなかったので、次の $OPS_DIR/down.sh で消す）"; continue ;; esac ;;
    esac
    # OSS 版の Kafka の CLUSTER_ID は、stream か土台（データの EFS）が消えなかったときは残す。消すと次の up.sh が別の値を作り、
    # EFS に残った古い CLUSTER_ID のデータと合わずに Kafka が起きない（マネージド版にはこのパラメータが無い）
    case "$n" in "/$PREFIX/kafka/"*)
      case " $FAILED_ROOTS " in *" pipeline/stream "* | *" base/core "*) echo "$n: 残す（$TF_DIR/pipeline/stream か base/core が消えなかったので、次の $OPS_DIR/down.sh で消す）"; continue ;; esac ;;
    esac
    aws ssm delete-parameter --region "$REGION" --name "$n" 2>/dev/null && echo "$n: 消した" || echo "$n: 無い"
  done
}
report_leftovers() {  # タグ Project=<PREFIX>（完全一致）の付いたリソースの ARN を並べ、数を出す。タグの API は消えたリソースも返すので、消えたかはこれで決めない
  local arns rc=0
  arns=$(aws resourcegroupstaggingapi get-resources --region "$REGION" --tag-filters "Key=Project,Values=$PREFIX" \
    --query 'ResourceTagMappingList[].ResourceARN' --output text) || rc=$?
  arns=$(printf '%s\n' "$arns" | tr '\t' '\n' | sed '/^$/d' | grep -v '^None$' || true)
  if [ -n "$arns" ]; then printf '%s\n' "$arns"; fi
  if [ "$rc" -ne 0 ]; then
    echo "残り: 数えられなかった（上のエラー）"
  else
    echo "残り: $(printf '%s' "$arns" | grep -c . || true) 件（Project=$PREFIX のタグ）"
  fi
}
