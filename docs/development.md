# 手元で変える・確かめる

← [README](../README.md)

`<prefix>` は `deploy.env` の `OWNER` から作る接頭辞 `<owner>-nwc-poc`。

2026-10-08 の cycle 007 でディレクトリを `app/`（コード）・`docker/`（Dockerfile と compose）・`IaC/`（Terraform と CloudFormation）に並べ直した。**007 より前のサイクルの文書（`docs/cycles/001`〜`006`）と `docs/verification/` は当時のパス**（`agent/` `workflow/` `web/` `terraform/` `oss/terraform/` `local/compose/` など）で書かれている。読み替えは [007 の design.md](cycles/007-restructure-dirs/design.md) の表。前のチェックアウトに残った state の移し方は [deploy.md](deploy.md) の「007 で並べ直したとき」。

## 変更するとき

| 変えたもの | やること |
|---|---|
| `app/dashboard/` の `.py`、手順書 | `ops/up.sh` を打つ（手順 4 で S3 に置き直して Web を再起動する）。apply は要らない |
| `app/agentcore/`（`app/agentcore/data/` を含む）、`app/temporal/` | `deploy.env` の `IMAGE_TAG` を上げて `ops/up.sh`。同じタグのままだとビルドを飛ばす |
| `app/telegraf/`、`app/grafana/`（アラートのルール `provisioning/alerting/` を含む）、`app/splunk/`（保存済みサーチとアラートアクションを含む）、`app/nautobot/` | `ops/up.sh` を打つ。イメージのタグがディレクトリの中身から決まるので、作り直してタスクが入れ替わる（タグを上げる操作は要らない） |
| Dockerfile（8 本。007 で各ソースのディレクトリから `docker/images/<名前>/Dockerfile` へ移した。build の context は `app/<名前>/` のままなので、手で打つときは `-f docker/images/<名前>/Dockerfile app/<名前>/`） | `agentcore`・`temporal` は `app/agentcore/` と同じく `IMAGE_TAG` を上げて `ops/up.sh`。ほかの 6 本（`telegraf`・`grafana`・`splunk`・`nautobot`、OSS 版の `spark`・`neo4j`）はタグのハッシュに Dockerfile も入るので、`ops/up.sh`（`spark`・`neo4j` は `oss/ops/up.sh`）を打つだけ |
| ガードレール（`IaC/terraform/aws-managed/agent/kb.tf`） | `aws_bedrock_guardrail_version.r1` の `description` の末尾を `r2` のように上げて `ops/up.sh`。上げないと Runtime は古い版のまま判定する |
| `templates/*.sh.tftpl` | シェルの `${…}` は `$${…}`、`%{` は `%%{` と書く（`templatefile` を通るため）。user_data は 16 KB まで |
| 変数の既定 | `IaC/terraform/aws-managed/<ルート>/terraform.tfvars.example` を `terraform.tfvars` に写して書く |
| lab と Telegraf の版 | `ops/lab-common.sh` を正本に、`IaC/terraform/aws-managed/pipeline/lab` の変数の既定値・`IaC/cloudformation/lab-debug.yaml` のパラメータの既定値（Telegraf は `docker/images/telegraf/Dockerfile` の ARG も）を全部そろえる（`tests/test_lab_debug.py` が見る） |
| `IaC/cloudformation/lab-debug.yaml` の UserData | `Fn::Sub` を通るので、シェルの変数は `${…}` でなく `$LAB` の形で書く。EC2 の中の支度は `app/containerlab/setup.sh` に書き、UserData には足さない |

- user_data や AMI（apply のたびに最新の AL2023 を引く）が変わると、**EC2 が作り直されてインスタンス ID が変わる。**利用者に配った `start_session_command` は配り直す。

## 手元で確かめる

AWS に触らずに、Terraform の構文検査と模擬テストを打てる。**変更したら、まずこれを打つ。**

```bash
uv sync --group dev --group web
```

```bash
bash ops/check.sh
```

`web` のグループ（gradio・boto3・pyyaml。pandas は gradio と一緒に入る）も入れるのは、`test_nautobot` が Web の画面のモジュールを読むため。最後の行が `すべて通過` なら健全。中身は `terraform fmt`（`IaC/terraform/aws-managed/` と `IaC/terraform/oss/`）、9 ルートの `terraform validate`（`IaC/terraform/aws-managed/` と `IaC/terraform/oss/` の両方で 18 回）、git が追跡している `.sh` 全部（`git ls-files '*.sh'`）の `bash -n` と `.py` 全部の構文、`tests/test_*.py` の全部（2026-10-09 で 15 本。`test_app` 161 項目、`test_graph` 78、`test_stream` 89、`test_sync` 103、`test_analytics` 489、`test_workflow` 327、`test_alerts` 158、`test_kb_index` 7、`test_lab_debug` 104、`test_nautobot` 69、`test_oss` 171、`test_oss_ops` 156、`test_oss_roll` 66、`test_local_compose` 122、`test_dashboard_config` 3）。途中で落ちたらそこで止まる。

## Web を手元で動かす

画面だけ見たいとき、EC2 で Web が立たない原因を切り分けるとき。チャットには `IaC/terraform/aws-managed/agent` の apply が済んでいることが要る（無ければチャットだけエラー表示になる）。

```bash
cp .env.example .env
```

```bash
RUNTIME_ARN=$(terraform -chdir=IaC/terraform/aws-managed/agent output -raw agent_runtime_arn); echo "$RUNTIME_ARN"; echo "RUNTIME_ARN=$RUNTIME_ARN" >> .env
```

```bash
uv sync --group web
```

```bash
uv run python app/dashboard/app.py
```

ブラウザで http://127.0.0.1:8080 を開く。環境変数の意味は `.env.example` に書いてある。007 より前に `.env` を作った人は、`DATA_DIR` を `app/agentcore/data` に書き換える（相対パスはリポジトリの直下から見る）。

## 入っていないもの

- 会話の永続化。履歴は Runtime のセッションの中にだけあり、画面を再読み込みすると消える。
- Temporal の永続化と UI の認証。履歴はタスクと一緒に消え、UI にはポートフォワーディングでしか届かない。
- 実機への修復。打てるのは lab の `sudo lab heal-main`（`dc1-a-leaf-01 ethernet-1/1` の fabric を戻す）と `sudo lab check` だけ。
- 生データ（`raw_telemetry`）の検索。エージェントの `query_history` が Athena で読むのはアラートの通知の履歴（`alert_events`）だけ。analytics が無ければ案内だけ返す。
- Web の画面の中のグラフ。修復案は S3 Tables の `proposal_events` を Athena で読んで、そのまま表に出す（メトリクスとログのグラフは 2026-09-28 から Grafana（`STORES` の `grafana`）で見る）。
- Web の異常一覧と、障害の履歴を見る画面。2026-10-02 に検知を Grafana と Splunk へ移したときにやめた（[data-stores.md](data-stores.md) の「5. 経緯」）。いまの異常は「トポロジ」タブの `status`、アラートは Grafana / Splunk の画面で見る。通知の履歴は 2026-10-04 から S3 Tables の `alert_events` に残り、エージェントの `query_history` で引ける。
- 複数の機器にまたがるアラートの相関。まとめるのは同じ機器・種類・対象のアラートだけ（送り手が違っても異常の id が同じになる）で、「Spine が落ちたので配下の Leaf のアラートを 1 つの障害にする」ようなルールは入れていない。
- 修復案の経過（S3 Tables の `proposal_events` の全部の行）を追う画面。「承認」タブとエージェントの `list_proposals` が出すのは修復案ごとの最新の 1 行だけ。経過は Athena（ワークグループ `<prefix>-history`）で読む。
- state の共有。1 人が 1 台の PC で打つ前提。
