# 根に残ったものを app/ ops/ docs/ に片付ける（017）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

「ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）」で根を `app docker IaC ops oss tests tools docs` にしたが、`oss/`（OSS 版のデプロイのシェル）、`tools/`（Gateway の Lambda の handler と、NetFlow を 1 つ送る試験用スクリプト）、`GLOSSARY.md`（001 の 4 語）が根に残った。ユーザーの決定（2026-10-09）: **デプロイ用のシェルは全部 `ops/` に置く**、根は `app docker IaC ops tests docs` にする。

**中身は変えない。パスだけ動かす**（007 と同じ原則）。動作の変更はしない。

### 調査で分かった事実（2026-10-09、`docs/cycle-006-design` ec0bed3）

- 根の追跡ファイル: `CLAUDE.md GLOSSARY.md IaC README.md app deploy.env.example docker docs ops oss pyproject.toml tests tools uv.lock`。`git ls-files oss terraform` は `oss/ops/` の 5 本だけ（`oss/terraform/` と根の `terraform/` は追跡されていない provider cache。`.gitignore` の `.terraform/`）。
- `oss/ops/` は `up.sh` / `down.sh` / `oss-images.sh` / `roll-nodes.sh` / `roll_health.py` の 5 本。`oss/ops/up.sh:32-41` は `. "$(dirname "$0")/../../ops/lab-common.sh"`、`. "$(dirname "$0")/oss-images.sh"`、`. "$(dirname "$0")/../../ops/deploy-env.sh"`、`cd "$(dirname "$0")/../.."`、`. ops/common.sh`、`. ops/up-common.sh`、`. oss/ops/roll-nodes.sh`、`OPS_DIR=oss/ops`。`oss/ops/down.sh:20-27` も同じ形（`../../ops/deploy-env.sh`、`cd …/../..`、`OPS_DIR=oss/ops`）。`ops/oss/` に動かしても根からの深さは 2 のままなので `cd "$(dirname "$0")/../.."` は変わらず、`../../ops/X` が `../X` になる。
- **`OPS_DIR` は SSM のパラメータのタグの値を決める。** `ops/common.sh:7` `OPS_DIR="${OPS_DIR:-ops}"`、`ops/up-common.sh`（`ensure_secret` 等が `Key=ManagedBy,Value=$OPS_DIR/up.sh` を付ける）、`ops/down-common.sh:182` `"Key=tag:ManagedBy,Values=$OPS_DIR/up.sh"`（このタグのものだけ消す）。`OPS_DIR=ops/oss` にするとタグが `ManagedBy=ops/oss/up.sh` になり、**古いタグ `oss/ops/up.sh` のパラメータは新しい `down.sh` では消えない**。2026-10-09 に PM が AWS（ap-northeast-1）を見たところ、古いタグのパラメータは `/efukuda-nwc-oss/kafka/cluster-id` の 1 本（2026-10-08 の OSS 版の検証の消し残り）だけで、その場で消した（`DeleteParameter` のあと `tag:ManagedBy=oss/ops/up.sh` で 0 本）。**移行の仕組みは要らない。** 据え置き（`OPS_DIR=oss/ops` のまま）にすると、パスとタグの値が食い違い、docs とメッセージ（`$OPS_DIR/up.sh` を案内に出す `roll-nodes.sh:119,133,152,170,173`）が存在しないパスを指すので、据え置かない。
- `oss/ops/up.sh:331-334,337,394,457,462` の SSM の description は `(created by oss/ops/up.sh)` と文字列で書いてある。`oss/ops/roll-nodes.sh:72,109` は `"$OPS_DIR/roll_health.py"` で `OPS_DIR` 経由。
- `oss/ops` の文字列を持つファイルは 76（`.venv` と `.terraform` を除く）。`docs/cycles/` の中は記録なので除くと、下の「変更対象ファイル」の一覧になる。コード・tests・docs・コメント・Terraform のコメント・`.gitignore:12` の注釈を含む。
- `tools/handler.py` と `tools/tools.json` は Gateway（MCP）の Lambda の入口と定義。`IaC/terraform/aws-managed/workflow/gateway.tf:10` `jsondecode(file("${local.repo_root}/tools/tools.json"))`、`:16` `"tools/handler.py" = "index.py"`（zip の中では `index.py`。ほかは `app/agentcore/…`）。`gateway.tf:12-14` のコメントが言う「同じ一覧」は `docker/images/agentcore/Dockerfile` と `base/core` の `upload_web_command` にもあるが、どちらも `tools/handler.py` は含まない（grep で 0）。`app/agentcore/app.py` には `tools/` のパス参照は無い（`strands.tools` の import だけ。BACKLOG の「app.py 1」は誤り）。`tests/test_workflow.py:1,105,430,440,449,538,541` がパスを固定している。`.env.example:105` の見出し。
- `tools/netflow_send.py` は GoFlow2 を試す偽の NetFlow v5 を 1 つ送る試験用で、Lambda には入らない。ユーザーの「デプロイ用のシェルは `ops/` でいい」に合わせて `ops/` に置く。参照は `tests/test_collectors.py:204`、`docker/compose/compose.yaml:112`、`docker/compose/README.md:93`、`docker/compose/check.sh:66`、`IaC/terraform/aws-managed/base/core/security_groups.tf:113-114`、`docs/deploy.md`（`SKIP_LAB` の行）、`docs/collection.md` 等。
- `GLOSSARY.md` の参照は `docs/cycles/` の中（007 の `build.md:130`、`design-log.md:43`、BACKLOG）だけ。
- `ops/check.sh:52` `find app docker ops oss tests tools -name '*.py'` と、`tests/test_oss.py:1935-1942`（`bash -n` の対象に `oss/ops` の 4 本があり、`.py` の `find` に `oss` がある）が旧パスを前提にしている。
- `docs/architecture/README.md:54` の構成表に `tools/` の行、`README.md:30,168`、`docs/deploy.md:73,183`、`docs/development.md:16` に `oss/ops` の行がある。
- `~/.claude/skills/domain-modeling/SKILL.md`（claude-settings）は `GLOSSARY.md` をリポジトリの根に書く前提。**別リポジトリなので、このサイクルの範囲外**（PM が claude-settings で直す）。

## 設計方針

1. **`git mv` で動かす**（履歴を追えるように。007 と同じ）。

   | いま | あと |
   |---|---|
   | `oss/ops/up.sh` `down.sh` `oss-images.sh` `roll-nodes.sh` `roll_health.py` | `ops/oss/<同名>` |
   | `tools/handler.py` `tools/tools.json` | `app/gateway/<同名>` |
   | `tools/netflow_send.py` | `ops/netflow_send.py` |
   | `GLOSSARY.md` | `docs/GLOSSARY.md` |

   動かしたあと `oss/` と `tools/` は追跡ファイルが無くなる（`git mv` が空のディレクトリを消す。worktree に残る untracked の `oss/terraform/` は実装者が **自分の worktree でだけ** `rm -rf oss/terraform` して消す。メインのチェックアウトと他の worktree には触らない）。

2. **参照を全部書き換える。** 置換は 5 組だけ。
   - `oss/ops/` → `ops/oss/`（パス・`OPS_DIR` の値・SSM の description・docs・コメント）。`oss/ops/up.sh` と `down.sh` の `../../ops/X` は `../X` に、`. oss/ops/roll-nodes.sh` は `. ops/oss/roll-nodes.sh` に。`cd "$(dirname "$0")/../.."` は変えない
   - `tools/handler.py` → `app/gateway/handler.py`、`tools/tools.json` → `app/gateway/tools.json`（`gateway.tf:10,16`、`tests/test_workflow.py`、`.env.example:105`、docs）。`tests/test_workflow.py:105` の `sys.path.insert(0, os.path.join(ROOT, "tools"))` と `:430,440` の `read("tools", …)` は `("app", "gateway")` に。**zip の中の名前（`index.py`）と import は変えない**
   - `tools/netflow_send.py` → `ops/netflow_send.py`（`tests/test_collectors.py:204`、compose の 3 か所、`security_groups.tf:113-114`、docs）
   - `GLOSSARY.md` → `docs/GLOSSARY.md`（`docs/cycles/` の中の記録は書き換えない）
   - `docs/cycles/` の中は **触らない**（過去のサイクルの記録。BACKLOG は PM が書く）

3. **SSM のタグは `ManagedBy=ops/oss/up.sh` に変わる**（`OPS_DIR=ops/oss` の帰結）。移行の仕組みは入れない（上の「調査で分かった事実」: 古いタグのものは 2026-10-09 に 0 本）。`docs/oss-variant.md` の SSM の節に「タグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より前に `oss/ops/up.sh` で立てたものは、そのときの commit の `oss/ops/down.sh` で消す）」を 1 文足す。

4. **`ops/check.sh:52` の `find` は `app docker ops tests`**（`oss` と `tools` を外す。`ops/oss/roll_health.py` と `ops/netflow_send.py` は `ops` が、`app/gateway/handler.py` は `app` が拾う）。`tests/test_oss.py:1935-1942` は `ops/oss/` の 4 本が `git ls-files '*.sh'` にあること、`find` に `ops` があることを見る形に直す（件数は変えない）。

5. **docs の構成表を新しい根に合わせる。** `README.md`、`docs/architecture/README.md`（`tools/` の行を `app/gateway/` に）、`docs/deploy.md`、`docs/development.md`、`docs/oss-variant.md`、`docs/architecture/resources/*.md`。「根は `app docker IaC ops tests docs`」と書く所があれば揃える。

6. **動作は変えない。** `up.sh` / `down.sh` の手順・引数・環境変数・Terraform の資源・イメージの版はそのまま。新しい test は足さない（既存の test のパスを直すだけ。件数は変えない）。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `oss/ops/{up.sh,down.sh,oss-images.sh,roll-nodes.sh,roll_health.py}` | `git mv` → `ops/oss/`。`up.sh:32-41` と `down.sh:20-27` の `../../ops/` → `../`、`. oss/ops/roll-nodes.sh` → `. ops/oss/roll-nodes.sh`、`OPS_DIR=oss/ops` → `OPS_DIR=ops/oss`、`up.sh:331-334,337,394,457,462` の `(created by oss/ops/up.sh)` → `ops/oss/up.sh`。コメントの `oss/ops` も全部 |
| `tools/handler.py` `tools/tools.json` | `git mv` → `app/gateway/`。中身は変えない（docstring に `tools/` があれば直す） |
| `tools/netflow_send.py` | `git mv` → `ops/netflow_send.py`。docstring の自分のパス |
| `GLOSSARY.md` | `git mv` → `docs/GLOSSARY.md` |
| `IaC/terraform/aws-managed/workflow/gateway.tf` | `:10` `tools/tools.json` → `app/gateway/tools.json`、`:16` `"tools/handler.py"` → `"app/gateway/handler.py"`（値の `index.py` はそのまま） |
| `IaC/terraform/aws-managed/base/core/security_groups.tf:113-114` | コメントと `why` の `tools/netflow_send.py` → `ops/netflow_send.py` |
| `IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf`、`pipeline/stream/telegraf.tf`、`IaC/terraform/oss/pipeline/{analytics/opensearch.tf,analytics/victoriametrics.tf,graph/neo4j.tf,stream/kafka.tf}` | コメントの `oss/ops/` → `ops/oss/` |
| `ops/check.sh:52` | `find app docker ops tests -name '*.py'` |
| `ops/{common.sh,up-common.sh,down-common.sh,up.sh,down.sh,deploy-env.sh,sync-graph.sh,check-grafana.sh,grafana_rules_check.py,seed_graph.py}` | コメントと die のメッセージの `oss/ops/` → `ops/oss/` |
| `app/containerlab/trex/kafka_load.sh`、`app/nautobot/requirements-oss.txt`、`app/agentcore/app.py` | コメントの `oss/ops/` → `ops/oss/`（`app.py` は `oss/ops` の文字列だけ） |
| `docker/compose/{compose.yaml:112,README.md:93,check.sh:66}` | `tools/netflow_send.py` → `ops/netflow_send.py` |
| `.env.example:105` | `tools/handler.py` → `app/gateway/handler.py` |
| `.gitignore:12` | 注釈の `oss/ops/up.sh` → `ops/oss/up.sh` |
| `tests/test_oss.py`（1468, 1480, 1550, 1894, 1935-1942 ほか `oss/ops` の全行） | パス。`:1935-1942` は `{"ops/oss/oss-images.sh","ops/oss/up.sh","ops/oss/down.sh","ops/oss/roll-nodes.sh"} <= _chk_sh` と `"ops" in _chk_find.group(1).split()` に（`oss` の条件は消す） |
| `tests/test_oss_ops.py`（`oss/ops` 80 行。`:490-494` の `for d in ("ops","oss/ops")` → `("ops","ops/oss")`、`:1019` の `^OPS_DIR=oss/ops\b` → `^OPS_DIR=ops/oss\b` ほか） | パス |
| `tests/test_oss_roll.py:32,146,167,338-339`、`tests/test_stream.py`、`tests/test_lab_debug.py:76-81,470-472`、`tests/test_local_compose.py:35`、`tests/test_analytics.py:135,269,273`、`tests/test_nautobot.py:257`、`tests/test_alerts.py`、`tests/test_sync.py` | `oss/ops` のパス |
| `tests/test_workflow.py:1,105,430,440,449,538,541` | `tools/` → `app/gateway/` |
| `tests/test_collectors.py:204` | `("tools","netflow_send.py")` → `("ops","netflow_send.py")` |
| `README.md`、`docs/{deploy.md,development.md,oss-variant.md,collection.md,data-stores.md,pipeline.md,troubleshooting.md,faq-fukuda-nwc-poc.md}`、`docs/architecture/README.md`、`docs/architecture/resources/{grafana.md,lab-ec2.md,nautobot.md,ssm-parameter-store.md}`、`docs/verification/20261008-oss-aws.md` | パスの置換と構成表。`oss-variant.md` に方針 3 の 1 文 |

`docs/cycles/**` は触らない。

## 再利用するもの

- 007 の手順（`docs/cycles/007-restructure-dirs/build.md`）: `git mv` → 参照の置換 → `grep` で拾い漏れを 0 にする → `ops/check.sh`。commit は「rename だけ」と「参照の書き換え」の 2 つに分ける。
- `ops/common.sh` の `OPS_DIR` の仕組み（値を変えるだけで、案内とタグが揃う）。

## 実装ステップ

1. `git mv` の 4 組（方針 1）。commit 1「rename だけ」。
2. 参照の置換（方針 2・4・5）。`grep -rn -E 'oss/ops|tools/handler|tools/tools\.json|tools/netflow_send|(^|[^/])GLOSSARY\.md' --exclude-dir=.venv --exclude-dir=.terraform --exclude-dir=.git --exclude-dir=cycles .` が `docs/GLOSSARY.md` 自身以外 0 行になるまで。commit 2「参照の書き換え」。
3. `bash ops/check.sh`。`docs/development.md:37` の件数と同じことを確かめる。
4. 自分の worktree の untracked `oss/terraform/` を消す（`rm -rf oss/terraform`。`oss/` が空になる）。
5. `build.md` に実測と、セルフレビュー（`/cycle-build` 手順 6）。

## 検証方法

| # | コマンド | 期待する出力 |
|---|---|---|
| 1 | `bash ops/check.sh` | 最後の行が `すべて通過`。`tests/test_*.py` の 16 本の件数が `docs/development.md:37` と同じ（`test_oss` 173、`test_oss_ops` 194、`test_workflow` 327、`test_collectors` 79 を含む。件数が変わったらそれは意図しない変更） |
| 2 | `git ls-files oss tools GLOSSARY.md` | 空 |
| 3 | `ls` | `CLAUDE.md IaC README.md app deploy.env.example docker docs ops pyproject.toml tests uv.lock`（gitignore 対象の `.venv` 等を除く） |
| 4 | 実装ステップ 2 の `grep` | 0 行（`docs/GLOSSARY.md` 自身の見出しを除く） |
| 5 | `git log --follow --oneline ops/oss/up.sh \| tail -1` と `git log --follow --oneline app/gateway/handler.py \| tail -1` | 005 以前の最初の commit まで追える（rename で履歴が切れていない） |
| 6 | `git diff --stat -M docs/cycle-006-design` | 動かした 9 本が rename（`=>`）で出る |
| 7 | `grep -n '^OPS_DIR=' ops/oss/up.sh ops/oss/down.sh` | 両方 `OPS_DIR=ops/oss` |
| 8 | `grep -c 'created by ops/oss/up.sh' ops/oss/up.sh` | 8 |
| 9 | `terraform -chdir=IaC/terraform/aws-managed/workflow validate` | `Success!`（`gateway.tf` の `file()` のパス。check.sh にも含まれる） |
| 10 | `uv run --group dev --group web python tests/test_workflow.py` | `通過 327 / 失敗 0`（`app/gateway/` から読めている） |

AWS では確かめない（動作を変えないため）。OSS 版の `ops/oss/up.sh` を AWS で打つのは次の OSS 版の検証のとき（タグ `ManagedBy=ops/oss/up.sh` で作られ、`ops/oss/down.sh` で消えることは**未確認**）。

## 未確定事項とリスク

1. **OSS 版を AWS で打ち直していない。** `ops/oss/up.sh` の `. "$(dirname "$0")/../lab-common.sh"` 等の相対パスは `bash -n` では検出できない（存在しないファイルを `.` しても構文は通る）。`tests/test_oss_ops.py` がパスの形を見ているのでそこで拾う。次の OSS 版の AWS 検証で確かめる。
2. **メインのチェックアウトと `verify-oss-20261008` worktree の `oss/terraform/`** には OSS 版の provider cache と state が残る（追跡されていない）。マージしても git は消さない。`main` へのマージのあと、PM が Terraform の state の `mv` と一緒に片付ける（このサイクルでは触らない）。
3. **他のエージェントの worktree が `oss/ops/` や `tools/` を触っていればマージで衝突する。** 013（`feat/gnmic-drop-dialin`）は `ops/` を触る。着手は 013 のマージを待たないが、**PR を出す前に `docs/cycle-006-design` の最新（013 のマージ後）を自分のブランチにマージして、check.sh を取り直す**。衝突したら解消せず PM に報告する。
4. claude-settings の `domain-modeling` スキルの `GLOSSARY.md` の置き場（根 → `docs/`）は別リポジトリ。PM が直す。
