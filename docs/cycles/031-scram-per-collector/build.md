# build: コレクターごとに SCRAM のユーザーを分ける（031）

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ `feat/031-scram-per-collector`（030 の `feat/030-gnmic-buffer-event-drop` の上に積んだ）。AWS は打っていない（設計どおり PM の範囲）。

### 変更

| 手順 | commit | 変更したファイル |
|---|---|---|
| 1 | `8a320b3` SCRAM の secret をコレクターごとに作り、消す | `ops/up-common.sh`、`ops/up.sh`、`ops/down-common.sh`、`ops/down.sh`、`tests/test_oss_ops.py` |
| 2 | `d136453` Terraform で SCRAM の secret をコレクターごとに引き、タスクに自分の分だけ渡す | `IaC/terraform/aws-managed/pipeline/stream/msk.tf` / `collectors.tf` / `gnmic.tf`、`IaC/terraform/oss/pipeline/stream/kafka.tf`、`tests/test_stream.py`、`tests/test_oss.py` |
| 3 | `0d66f8c` Kafka の ACL をコレクターごとのユーザーに絞り、案内の secret 名を直す | `app/spark/snmp_sinks.py`、`app/gnmic/gnmic.sh`、`app/syslog-ng/syslog-ng.sh`、`app/syslog-ng/syslog-ng.conf.in`、`tests/test_analytics.py`、`tests/test_stream.py` |
| 4 | `959e8b6` docs の SCRAM の secret 名とユーザーをコレクターごとに直す | `CLAUDE.md`、`docs/pipeline.md`、`docs/data-stores.md`、`docs/troubleshooting.md`、`docs/deploy.md`、`docs/architecture/pipeline.md`、`docs/architecture/architecture-managed.deck.md`、`docs/architecture/resources/{telegraf,emr-serverless,ssm-parameter-store,msk}.md`、`tests/test_analytics.py` |
| 5 | （この commit）セルフレビューの直しと build.md | `ops/down-common.sh`、`tests/test_oss_ops.py`、`app/spark/snmp_sinks.py`、`msk.tf`、`gnmic.tf`、`docs/troubleshooting.md`、この build.md |

- secret は `AmazonMSK_<prefix>-{syslog-ng,goflow2,gnmic}`、username はコレクター名、鍵 `alias/<prefix>-msk-scram` は 1 本のまま。
- `delete_msk_scram <コレクター>` は secret だけを消す。鍵は `delete_msk_scram_key` が 3 本のあとに 1 回だけ消す。1 本でも消せなかった（か確かめられなかった）ときは `MSK_SCRAM_KEEP_KEY=1` にして鍵を残す。
- `kafka_collector_secrets` はコレクター名 → ECS の secrets の map。タスクは自分のキーだけを引く。IAM の `ScramSecret` と association は 3 本の ARN。
- `SCRAM_USERS = {"syslog-ng": ("logs",), "goflow2": ("flows",), "gnmic": ("gnmi", "metrics")}`。`ensure_acls` はユーザーごとに自分のトピックの WRITE / DESCRIBE だけを入れる。

### 設計との差

1. `IaC/terraform/oss/pipeline/stream/kafka.tf` の `kafka_collector_secrets` を `[]` から `{ "syslog-ng" = [], "goflow2" = [], "gnmic" = [] }` に変えた。
   - 設計の変更対象ファイルに無い。`collectors.tf` と `gnmic.tf` は OSS 版の root にシンボリックリンクされているので、`["syslog-ng"]` の添字で OSS 版の validate が落ちる。
2. `tests/test_analytics.py` は設計の `'"username": "$collector"'` ではなく、up-common.sh の実際の文字列 `'json.dumps({"username": user, "password": secrets.token_urlsafe(24)})'` と、`SCRAM_USERS` のキーが up.sh の `ensure_msk_scram_secret` の引数と同じ順であることを照合した。
   - username は bash の変数ではなく python の argv で渡しているため。
3. docs は設計の一覧（deploy / pipeline / data-stores / troubleshooting / CLAUDE.md）のほかに、検証の grep が当たった次も直した。
   - `docs/architecture/pipeline.md`、`architecture-managed.deck.md`、`resources/telegraf.md`、`emr-serverless.md`、`ssm-parameter-store.md`、`msk.md`
4. 検証の grep に `SCRAM_TOPICS` も足した（消した定数の名前）。
5. セルフレビューで、設計に無い 2 つを足した。
   - `tests/test_oss_ops.py` の偽 AWS に `FAKE_SM_DESCRIBE_DENY`、およびその check。
   - `docs/troubleshooting.md` の「031 をまたいで作って消すと止まる」。

### 検証

#### テスト（最後の編集のあとに取り直した）

```
$ bash runall.sh   # 各 tests/test_*.py を uv run --frozen python3 で
tests/test_agentcore.py: 通過 162 / 失敗 0
tests/test_alerts.py: 通過 168 / 失敗 0
tests/test_analytics.py: 通過 529 / 失敗 0
tests/test_collectors.py: 通過 79 / 失敗 0
tests/test_dashboard_config.py: 通過 3 / 失敗 0
tests/test_graph.py: 通過 78 / 失敗 0
tests/test_kb_index.py: 通過 7 / 失敗 0
tests/test_lab_debug.py: 通過 110 / 失敗 0
tests/test_local_compose.py: 通過 142 / 失敗 0
tests/test_nautobot.py: rc=1
  File ".../tests/../app/dashboard/topology_view.py", line 12, in <module>
    import gradio as gr
ModuleNotFoundError: No module named 'gradio'
tests/test_oss.py: 通過 174 / 失敗 0
tests/test_oss_ops.py: 通過 206 / 失敗 0
tests/test_oss_roll.py: 通過 66 / 失敗 0
tests/test_stream.py: 通過 112 / 失敗 0
tests/test_sync.py: 通過 103 / 失敗 0
tests/test_workflow.py: 通過 333 / 失敗 0
```

- `test_nautobot.py` は gradio が手元に無くて import で落ちる。031 の前（030）から同じで、031 の変更とは関係しない。
- 設計の 4 本の件数は、設計の時点（2026-10-10）で 528 / 不明 / 174 / 203。いまは 529 / 112 / 174 / 206。

#### terraform（init は `-backend=false`。AWS にも state にも触れていない）

```
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/stream validate -no-color
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/oss/pipeline/stream validate -no-color
Success! The configuration is valid.
$ terraform fmt -check -recursive IaC/terraform/aws-managed/pipeline/stream ; echo rc=$?
rc=0
```

#### bash -n

```
bash -n ops/up.sh rc=0
bash -n ops/up-common.sh rc=0
bash -n ops/down.sh rc=0
bash -n ops/down-common.sh rc=0
bash -n app/gnmic/gnmic.sh rc=0
bash -n app/syslog-ng/syslog-ng.sh rc=0
```

#### 古い名前の grep

```
$ grep -rn -e "AmazonMSK_.*-collectors" -e "User:collectors" -e "SCRAM_USER\b" -e "SCRAM_TOPICS" app ops IaC docs tests CLAUDE.md \
    | grep -v -e "^docs/cycles/" -e "^docs/verification/"
（出力なし。0 件）
```

#### AWS

未実行（設計どおり PM が 1 回打つ）。次はどれも未確認。

- association が in-place で 3 本になるか。
- 3 つのタスクが RUNNING になるか。
- `ACL: User:syslog-ng WRITE logs, …` の行が出るか。
- `Topic authorization failed` が出ないか。
- down.sh で secret が 3 本とも消えるか。

### セルフレビュー

- 自分: opus-5.5 / effort high。
- 反対弁護人: general-purpose、model opus、読み取り専用、文脈あり。
  - 終わったあとの `git status --porcelain -uall` は空だった。

#### 変異の注入

元に戻すスクリプト `mutate.py` / `mutate2.py` で注入した。直しのあとに取り直した結果:

| # | 注入 | 落ちたテスト |
|---|---|---|
| M1 | syslog-ng のタスクに `kafka_collector_secrets["gnmic"]` | test_stream rc=1 |
| M2 | `SCRAM_USERS["syslog-ng"]` に flows | test_analytics rc=1 |
| M3 | delete-secret が落ちた枝で `MSK_SCRAM_KEEP_KEY=1` を立てない | test_oss_ops rc=1 |
| M4 | up.sh から `ensure_msk_scram_secret gnmic` を落とす | test_stream rc=1 |
| M5 | username を固定の `"collectors"` に戻す | test_analytics rc=1 |
| M6 | OSS の map から gnmic を落とす | test_oss rc=1 |
| M7 | `delete_msk_scram_key` が KEEP_KEY を見ない | test_oss_ops rc=1 |
| M8 | IAM の `ScramSecret` を gnmic の 1 本に | test_stream rc=0（通る）、test_oss rc=1 |
| M9 | association を gnmic の 1 本に | test_stream rc=0（通る）、test_oss rc=1 |
| M10 | describe-secret が NotFound 以外で落ちる枝で KEEP_KEY を立てない | 直す前は test_oss_ops rc=0「通過 205 / 失敗 0」（縛れていなかった）。直したあとは rc=1 |

#### 指摘と片付け

- **R4 Should fix [missing tests] `ops/down-common.sh:219`**
  - 破綻シナリオ: describe-secret が AccessDenied などで落ちる枝で KEEP_KEY を立て忘れても、テストが通る。そうなると secret が残ったまま鍵の削除が予約され、7 日後にその secret を復号できなくなる。
  - 反対弁護人の指摘。M10 で再現した（上の表）。
  - 直した。偽 AWS に `FAKE_SM_DESCRIBE_DENY` を足し、「gnmic の 1 本が確かめられない → ほかの 2 本は消し、kms の呼び出しは 0」の check を足した（test_oss_ops 205 → 206）。
  - 鍵を残すときの文言を「消せなかった（か確かめられなかった）secret がある」に直した。
- **R1 Should fix [data loss / 課金] 031 をまたいで作って消すと止まる（`msk.tf` の data source の for_each）**
  - 破綻シナリオ: 031 より前のコードで作った stream を今の `ops/down.sh` で消すと、3 本の secret が無くて destroy が止まり、MSK が残る（逆向きも同じ）。
  - 反対弁護人の指摘。AWS では再現していない（未確認）。
  - destroy で data source を読み直すことは、既存の troubleshooting の `msk_scram` の節（012 のとき）と同じ理屈。
  - `docs/troubleshooting.md` に、031 をまたぐときと回避（同じコードで消すか `-refresh=false`）を足した。
  - 移行のコードは入れていない（設計の「やらないこと」）。
- **R3 Should fix [security / docs] `app/spark/snmp_sinks.py:96` のコメントが言い過ぎ**
  - 「1 つの資格情報が漏れても、ほかのコレクターのトピックには書けない」とだけ書いていた。
  - 実際には、ACL の無いトピックと ACL を入れる前は、allow.everyone.if.no.acl.found が効いていればどのユーザーも書ける（推測。2026-10-09 の AWS で ACL 無しで書けた事実から）。
  - 条件付きに直した。docs/pipeline.md は step 4 で条件付きに書いてある。
- **R2 Should fix [security / 最小権限] `msk.tf:40-46` 未対応・最終報告に回す**
  - どの実行ロールも 3 本全部を `GetSecretValue` できる。
  - 破綻シナリオ: `iam:PassRole` と `ecs:RegisterTaskDefinition` を持つ主体なら、syslog-ng の実行ロールで gnmic の secret を入れたタスク定義を作れる。
  - コンテナの乗っ取りだけでは届かない。task role は実行ロールと別で、collectors.tf と gnmic.tf を読んで確かめた（読んだだけ）。
  - 設計が「Resource を 3 本の ARN」と明記しているので変えていない。`kafka_collector_execution_statements` をコレクター名の map にすれば直る。
- **R5 Should fix [後片付け] 古い共有の secret（031 より前の名前）を down.sh が消さない 未対応・最終報告に回す**
  - 設計の「やらないこと」。troubleshooting に「手で消す」と書いた。
- **Nit 直した**
  - `msk.tf:40` の「2 つの実行ロール」を 3 つに。
  - `gnmic.tf:11-12` のコメントの切れ目を直した。
- **Nit 直さない**
  - N1 `MSK_SCRAM_KEEP_KEY` はグローバルで、同じシェルで 2 周呼ぶと前の値が残る（down.sh は 1 周だけ）。
  - N2 test_oss は OSS の map を文字列で照らすだけで、`scram_collectors` とキーの集合を比べない。
  - N3 古い `User:collectors` の ACL は同じ MSK を使い回すと残る（作り直す運用なので実害はほぼ無い）。
  - N4 ハイフン入りの SCRAM のユーザー名（`syslog-ng`）。RFC 5802 でエスケープが要るのは `,` と `=` だけで問題ない見込み。MSK 独自の制約は未確認（AWS で確かめる）。

#### 問題なしとした観点と根拠

- **設計整合**: design.md を読み直し、実装ステップ 1〜5 と変更対象を diff と突き合わせた（読んだだけ）。差は上の「設計との差」。
- **ECS の valueFrom の形**: `<ARN>:username::` は変えていない。反対弁護人も問題なしとした（読んだだけ）。
- **association の更新が in-place か**: 未確認（設計の未確定 1 のまま）。AWS の plan で `~` か `-/+` かを見る。
