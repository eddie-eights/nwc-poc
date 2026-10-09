# コレクターごとに SCRAM のユーザーを分ける（031）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | コレクターごとに SCRAM のユーザーを分ける（いまは syslog-ng / GoFlow2 / gnmic が User:collectors を共有し、logs / flows / gnmi の全部に書ける） | 013 のセルフレビュー F1b の残リスク | 直す |

いまは 1 つの secret（`AmazonMSK_<prefix>-collectors`、username `collectors`）を 3 つのコレクターの ECS タスクが共有し、ACL も `User:collectors` に logs / flows / gnmi / metrics の全部の WRITE / DESCRIBE を許している。1 つのコレクターが乗っ取られると他のトピックにも書ける。ユーザーを 3 つに分け、secret と ACL をコレクターごとに絞る。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- secret を作る: `ops/up-common.sh:345-407` の `ensure_msk_scram_key`（KMS の鍵 `alias/$PREFIX-msk-scram`）と `ensure_msk_scram_secret`（`"${PY[@]}" -c` で `{"username": "collectors", "password": secrets.token_urlsafe(24)}` を一時ファイルに書き、`aws secretsmanager create-secret --cli-input-json file://…`。Description `"MSK SCRAM credentials of syslog-ng, GoFlow2 and gnmic (created by $OPS_DIR/up.sh)"`。削除予約なら restore、鍵違いなら die）。`ops/up.sh:931-932` が呼ぶ。
- secret を消す: `ops/down-common.sh:199-224` の `delete_msk_scram`（`local name="AmazonMSK_$PREFIX-collectors" alias="alias/$PREFIX-msk-scram" out arn state`）。`ops/down.sh:116-117` が呼ぶ。
- Terraform: `IaC/terraform/aws-managed/pipeline/stream/msk.tf:25-50, 185-200`。
  ```hcl
  kafka_collector_secrets = [
    { name = "KAFKA_SASL_USER", valueFrom = "${data.aws_secretsmanager_secret.msk_scram.arn}:username::" },
    { name = "KAFKA_SASL_PASS", valueFrom = "${data.aws_secretsmanager_secret.msk_scram.arn}:password::" },
  ]
  kafka_collector_execution_statements = [
    { Sid = "ScramSecret"    Effect = "Allow" Action = ["secretsmanager:GetSecretValue"] Resource = data.aws_secretsmanager_secret.msk_scram.arn },
    { Sid = "ScramSecretKey" Effect = "Allow" Action = ["kms:Decrypt"] Resource = data.aws_kms_alias.msk_scram.target_key_arn },
  ]
  …
  data "aws_secretsmanager_secret" "msk_scram" { name = "AmazonMSK_${local.name_prefix}-collectors" }
  data "aws_kms_alias" "msk_scram" { name = "alias/${local.name_prefix}-msk-scram" }
  resource "aws_msk_scram_secret_association" "collectors" { cluster_arn = …  secret_arn_list = [data.aws_secretsmanager_secret.msk_scram.arn] }
  ```
  `collectors.tf:76-79`（syslog-ng）と `:171`（GoFlow2）が `secrets = local.kafka_collector_secrets`、`gnmic.tf:85-94` が `secrets = concat(local.kafka_collector_secrets, [GNMI_TARGETS], [for … gnmic_credentials …])`。コメント `msk.tf:4`、`collectors.tf:9`、`gnmic.tf:10`（「User:collectors」）。
- ACL: `app/spark/snmp_sinks.py:95-99` の `SCRAM_USER = "collectors"` / `SCRAM_TOPICS = ("logs", "flows", "gnmi", "metrics")`、`:972-1000` の `ensure_acls(spark, bootstrap)` が `for t in SCRAM_TOPICS: for op in SCRAM_OPS:` で `User:collectors` の binding を作る。`main`（`:1030-1040`）が `log(f"ACL: User:{SCRAM_USER} に " + ", ".join(acls))`。
- die の文言: `app/gnmic/gnmic.sh:35-37` と `app/syslog-ng/syslog-ng.sh:48-51` が `AmazonMSK_<接頭辞>-collectors` を名指し。`app/syslog-ng/syslog-ng.conf.in:9` のコメントも。GoFlow2 は `collectors.tf` の args で `-transport.kafka.topic=flows` と SASL の環境変数を受ける（die の文言は無い）。
- テスト:
  - `tests/test_analytics.py:725-767`: `_r5 == ["WRITE logs", "DESCRIBE logs", "WRITE flows", …, "DESCRIBE metrics"]`、`_adm5.acls == [("TOPIC", t, "LITERAL", "User:collectors", "*", op, "ALLOW") …]`、`mod.SCRAM_USER == "collectors" and '"username": "collectors"' in _ops_common("up") and mod.SCRAM_TOPICS == (…)`、`'log(f"ACL: User:{SCRAM_USER} に " + ", ".join(acls))' in _main_src`。
  - `tests/test_stream.py:134-145`（SCRAM の配線）、`:422-423`（`"AmazonMSK_<接頭辞>-collectors" in _gbad_res["scram で KAFKA_SASL_PASS が無い"][1]`）、`:819-822`（`data "aws_secretsmanager_secret" "msk_scram" {\n  name = "AmazonMSK_${local.name_prefix}-collectors"\n}` in _msk_tf、`'local name="AmazonMSK_$PREFIX-collectors" out kms deleted' in _upc`、`'local name="AmazonMSK_$PREFIX-collectors" alias="alias/$PREFIX-msk-scram" out arn state' in _downc`）。
  - `tests/test_oss.py:1066-1069` が同じ data source を regex で照合。
  - `tests/test_oss_ops.py:476-478` の偽 AWS の inventory（`"secrets": {"AmazonMSK_x-nwc-oss-nwc-poc-collectors": {"kms": KEY_MGD}, "AmazonMSK_x-nwc-poc-collectors": {"kms": KEY_POC}}`）、`:663-716`（down の delete-secret）、`:971-1029`（ensure_msk_scram_secret: 1 回だけ作る、ある場合は作り直さない、途中で死んだとき、削除予約なら restore、鍵違いで die、`kms == "None"`）。
- docs で `collectors` のユーザー名を書いている所: `docs/deploy.md:266,300`、`docs/pipeline.md:60,84,148`、`docs/data-stores.md:532,556`、`docs/troubleshooting.md:23,131,211,218,228`、`CLAUDE.md:7`。
- OSS 版は MSK を使わず（Kafka on ECS、`KAFKA_AUTH=none`）、secret の有無は `tests/test_oss_ops.py` の inventory で見ているだけ。

## 設計方針

**A. secret を 3 本にする（名前はコレクター名、鍵は 1 本のまま）**

| コレクター | secret | username | 書けるトピック |
|---|---|---|---|
| syslog-ng | `AmazonMSK_<prefix>-syslog-ng` | `syslog-ng` | logs |
| GoFlow2 | `AmazonMSK_<prefix>-goflow2` | `goflow2` | flows |
| gnmic | `AmazonMSK_<prefix>-gnmic` | `gnmic` | gnmi, metrics |

1. `ops/up-common.sh` の `ensure_msk_scram_secret` に引数（コレクター名）を足す。`name="AmazonMSK_$PREFIX-$collector"`、JSON の `username` を `$collector`、Description を `"MSK SCRAM credentials of $collector (created by $OPS_DIR/up.sh)"`。`ops/up.sh:932` で `ensure_msk_scram_secret syslog-ng; ensure_msk_scram_secret goflow2; ensure_msk_scram_secret gnmic`（`for` は使わず 3 行。フックの都合ではなく、読みやすさ）。鍵（`ensure_msk_scram_key`）は 1 本のまま。
2. `ops/down-common.sh` の `delete_msk_scram` を同じ引数つきにし、`ops/down.sh:117` で 3 回呼ぶ。鍵の削除予約は最後の 1 回のあと（いまの関数が鍵も消しているなら、鍵の部分を `delete_msk_scram_key` に分けて 1 回だけ呼ぶ）。
3. `msk.tf`:
   ```hcl
   locals {
     scram_collectors = ["syslog-ng", "goflow2", "gnmic"]
   }
   data "aws_secretsmanager_secret" "msk_scram" {
     for_each = toset(local.scram_collectors)
     name     = "AmazonMSK_${local.name_prefix}-${each.key}"
   }
   resource "aws_msk_scram_secret_association" "collectors" {
     cluster_arn     = aws_msk_cluster.stream.arn
     secret_arn_list = [for c in local.scram_collectors : data.aws_secretsmanager_secret.msk_scram[c].arn]
   }
   kafka_collector_secrets = { for c in local.scram_collectors : c => [
     { name = "KAFKA_SASL_USER", valueFrom = "${data.aws_secretsmanager_secret.msk_scram[c].arn}:username::" },
     { name = "KAFKA_SASL_PASS", valueFrom = "${data.aws_secretsmanager_secret.msk_scram[c].arn}:password::" },
   ] }
   ```
   IAM の `ScramSecret` の Resource を `[for c in local.scram_collectors : data.aws_secretsmanager_secret.msk_scram[c].arn]` に。`collectors.tf` の syslog-ng は `secrets = local.kafka_collector_secrets["syslog-ng"]`、GoFlow2 は `["goflow2"]`、`gnmic.tf` は `concat(local.kafka_collector_secrets["gnmic"], …)`。コメント 3 箇所（`msk.tf:4`、`collectors.tf:9`、`gnmic.tf:10`）を「コレクターごとに別のユーザー（User:syslog-ng / User:goflow2 / User:gnmic）」に。
4. `gnmic.sh` / `syslog-ng.sh` の die と `syslog-ng.conf.in:9` の文言を `AmazonMSK_<接頭辞>-gnmic` / `AmazonMSK_<接頭辞>-syslog-ng` に。

**B. ACL をユーザーごとに絞る（`snmp_sinks.py`）**

```python
# コレクターごとに別の SCRAM のユーザー（031）。ops/up-common.sh の ensure_msk_scram_secret の username と同じ
SCRAM_USERS = {
    "syslog-ng": ("logs",),
    "goflow2": ("flows",),
    "gnmic": ("gnmi", "metrics"),
}
```

`SCRAM_USER` / `SCRAM_TOPICS` は消す。`ensure_acls` は `for user, topics in SCRAM_USERS.items(): for t in topics: for op in SCRAM_OPS:` で `User:<user>` の binding を作り、`made` は `f"User:{user} {op} {t}"` の形。`main` のログは `log("ACL: " + ", ".join(acls))`。

**C. テストと docs**

- `tests/test_analytics.py:725-767`: `_r5` の期待を `["User:syslog-ng WRITE logs", "User:syslog-ng DESCRIBE logs", "User:goflow2 WRITE flows", "User:goflow2 DESCRIBE flows", "User:gnmic WRITE gnmi", "User:gnmic DESCRIBE gnmi", "User:gnmic WRITE metrics", "User:gnmic DESCRIBE metrics"]`、`_adm5.acls` を `[("TOPIC", t, "LITERAL", "User:" + u, "*", op, "ALLOW") for u, ts in (("syslog-ng", ("logs",)), ("goflow2", ("flows",)), ("gnmic", ("gnmi", "metrics"))) for t in ts for op in ("WRITE", "DESCRIBE")]`、`mod.SCRAM_USERS == {…}`、`'"username": "$collector"'`（up-common の実際の文字列）、ログの行。**`User:syslog-ng` が flows に書けない**（binding に `("TOPIC", "flows", …, "User:syslog-ng", …)` が無い）の check を 1 つ足す。
- `tests/test_stream.py:134-145 / 422-423 / 818-831`、`tests/test_oss.py:1066-1069`: 新しい名前と for_each の形に。
- `tests/test_oss_ops.py:476-478`: inventory を 3 本 × 2 prefix に。`:663-716` と `:971-1029`: 3 本ぶん（1 回だけ作る → 3 回だけ作る、など）。
- docs 10 箇所と `CLAUDE.md:7`（`AmazonMSK_<prefix>-collectors` → `AmazonMSK_<prefix>-{syslog-ng,goflow2,gnmic}`、「User:collectors」→ 3 ユーザー）。`docs/pipeline.md:130-150` の ACL の節に上の表を入れる。

**やらないこと。** KMS の鍵は分けない（鍵は暗号化の単位で、権限の単位は secret の ARN で絞れる）。Spark や consumer 側（IAM 認証）は変えない。パスワードの長さや文字種は変えない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `ops/up-common.sh` / `ops/up.sh` | A | `ensure_msk_scram_secret <collector>` × 3 |
| `ops/down-common.sh` / `ops/down.sh` | A | `delete_msk_scram <collector>` × 3、鍵は 1 回 |
| `IaC/terraform/aws-managed/pipeline/stream/msk.tf` / `collectors.tf` / `gnmic.tf` | A | data source の for_each、secrets の map、IAM、association、コメント |
| `app/gnmic/gnmic.sh` / `app/syslog-ng/syslog-ng.sh` / `app/syslog-ng/syslog-ng.conf.in` | A | 文言 |
| `app/spark/snmp_sinks.py` | B | `SCRAM_USERS`、`ensure_acls`、ログ |
| `tests/test_analytics.py` / `test_stream.py` / `test_oss.py` / `test_oss_ops.py` | C | check の更新と追加 |
| `docs/deploy.md` / `pipeline.md` / `data-stores.md` / `troubleshooting.md` / `CLAUDE.md` | C | 名前とユーザー |

## 再利用するもの

- `ensure_msk_scram_secret` / `delete_msk_scram` の本体（restore・鍵違いの die はそのまま。名前と username だけ引数化）。
- `tests/test_oss_ops.py` の偽 AWS（inventory に 2 本足すだけ）。

## 実装ステップ

1. ops（A の 1・2）と `test_oss_ops.py` の更新。`bash -n` と `uv run --frozen python3 tests/test_oss_ops.py` が `失敗 0`。1 commit。
2. Terraform（A の 3）と `tests/test_stream.py` / `test_oss.py`。`terraform -chdir=IaC/terraform/aws-managed/pipeline/stream validate` が Success（init は `-backend=false`）。1 commit。
3. 文言（A の 4）と ACL（B）と `tests/test_analytics.py`。1 commit。
4. docs。1 commit。
5. `build.md`（頭は `実装モデル: opus-5.5 / effort: high`。テスト 4 本の出力、validate の出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --frozen python3 tests/test_analytics.py` / `test_stream.py` / `test_oss.py` / `test_oss_ops.py` が全部 `失敗 0`（件数は build の冒頭で実測。2026-10-10 は 528 / 不明 / 174 / 203。足した check のぶん増える）。
- `terraform validate` が stream（managed と OSS の両方）で Success。
- `grep -rn "AmazonMSK_.*-collectors\|User:collectors\|SCRAM_USER\b" app ops IaC docs tests CLAUDE.md` が `docs/cycles/` と `docs/verification/` 以外で 0 件。
- AWS（PM が 1 回だけ打つ。実装の範囲外）: `ops/up.sh` の 1 本で secret 3 本が出来て association が通り、3 つのコレクターのタスクが RUNNING、Spark のログに `ACL: User:syslog-ng WRITE logs, …` の行、logs / flows / gnmi / metrics の 4 トピックに書けている（Kafbat UI）。コレクターのログに `Topic authorization failed` が無い。`ops/down.sh` で secret 3 本が削除予約になる。

## 未確定事項とリスク

1. `aws_msk_scram_secret_association` の `secret_arn_list` を 1 本 → 3 本にすると Terraform は in-place 更新になる見込み（未確認。replace でも MSK は止まらない）。
2. secret の名前を変えるので、既存の環境（state を残したまま）で up.sh を打つと古い `-collectors` が残る。消し残しは `delete_msk_scram collectors` を 1 回だけ down.sh に入れておく（移行用。1 本も無ければ何もしない）か、手で消す。**この PoC では環境を毎回作り直しているので、移行用の削除は入れない（やらないこと）。** 2026-10-10 の検証前に古い secret が無いことを PM が確かめる。
3. GoFlow2 の SASL のユーザー名を環境変数で受ける形が `collectors.tf` の args でどう書かれているかは実装で確認（`KAFKA_SASL_USER` をそのまま使うなら変更なし）。
