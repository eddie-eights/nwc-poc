# MSK に SASL/SCRAM を足し、syslog-ng と GoFlow2 を立てる（012）— cold review（Round 2）

## サマリ

- 対象: 5944704（2ae0b61 の上の 1 commit）。実際の Round 2 の差分は `git diff 2ae0b61 5944704`（14 ファイル、+664 / -91）。指定の `git diff a3b1a48 5944704` には、ブランチが含まない基準側の 015 / 016 の変更（`ops/up-common.sh`、`docs/ai-dev-flow.md` 等）が逆向きに混ざるので、中身の判定は 2ae0b61 からの差分で行い、基準とのぶつかりだけ a3b1a48 側と突き合わせた。
- 前回の Must fix C1（SCRAM のユーザー `collectors` に ACL を付ける処理が無い）は、**コードの上では解消している**と判断する。
  - `app/spark/snmp_sinks.py:872-896` の `ensure_acls` が `User:collectors` に `logs` / `flows` の `WRITE` / `DESCRIBE` を `LITERAL`・host `*`・ALLOW で `createAcls` する。`main` で `ensure_topics` の直後に呼ぶ（`:931-935`）。設計の方針 7（`design.md:73-`）の中身（入れるもの・入れないもの・呼ぶ場所・none の扱い・失敗は上げる・ログは iam のときだけ）と一致する。
  - 必要な IAM 権限 `kafka-cluster:AlterCluster` は `IaC/terraform/aws-managed/pipeline/analytics/access.tf:47` の KafkaCluster 文（Resource はクラスター ARN）にだけ足してある。
  - `logs` / `flows` の作成は既存の `ensure_topics`（`log_topics` の既定に両方ある）が担い、収集器に CREATE を持たせない。
- ただし MSK の上で C1 が本当に起き、ACL で解けるか（未確定事項 8・9・10）は AWS 検証 3 待ちで、本レビューでも確かめていない。
- Must fix 0 / Should fix 1 / Nit 1。

### 見た観点 / 見ていない観点

- 見た: design.md との整合性（方針 7、変更対象ファイル、未確定事項 8-13、検証 3・6）、correctness（`ensure_acls` の binding の組み立て・close・none の分岐・例外の伝わり方、`main` の順序）、security（AlterCluster の付け先と幅、CREATE / CLUSTER ACL / PREFIXED を入れていないこと）、runtime bugs（3 ジョブ同時起動での `createAcls` の冪等性、ACL 前の収集器の振る舞いの記述）、API compatibility（Kafka の `AclBinding` / `AccessControlEntry` / `ResourcePattern` の引数の形、基準ブランチとのマージのぶつかり）、missing tests（`tests/test_analytics.py` の ensure_acls の 8 項目、`tests/test_collectors.py` のキューの既定の検査、`tests/test_oss.py:709-710` の none）、docs の更新（deploy.md / pipeline.md / msk.md / troubleshooting.md / development.md）。
- 自分で走らせたテスト（展開したツリーで `.venv/bin/python tests/<file>.py`）:
  - `tests/test_analytics.py`: 通過 504 / 失敗 0
  - `tests/test_collectors.py`: 通過 79 / 失敗 0
  - `tests/test_oss.py`: ACL の項目（`ok KAFKA_AUTH=none: SASL/SCRAM の収集器の ACL は入れない…`）を含む 73 項目が ok を出したあと、`tests/test_oss.py:912` の `git ls-files` が `CalledProcessError`（128）で止まった。展開先が git のリポジトリでないための環境の制約で、それ以降の項目は判定していない。173 本全部が通るとは主張しない。
- 見ていない:
  - `ops/check.sh` 全体、`terraform fmt` / `terraform validate`（自分では走らせていない。build.md の「全部通過」は build 側の申告として読んだだけ）
  - AWS の上の振る舞い（MSK の IAM での `CreateAcls`、SCRAM の主体に ACL が効くか、`allow.everyone.if.no.acl.found` の既定がどちらに効くか、`UnderReplicatedPartitions`）。検証 3 / 4 は未実施
  - build.md の検証 6（実 Spark + apache/kafka 4.3.1 の StandardAuthorizer、待ち 340 秒、同時起動 3 本）の再現。生ログを読んだだけで docker では打ち直していない
  - 基準ブランチへの実際のマージ（下の Should fix は hunk の位置からの判断）

## Must fix

None

## Should fix

- [API compatibility][design.md との整合性] `docs/development.md:37` / `docs/deploy.md:26-27`: 5944704 は 2ae0b61 から切られていて、基準（a3b1a48）が同じ行を先に変えている。`docs/development.md:37` は基準側が `test_stream` 96・`test_alerts` 169・`test_oss_ops` 194 に、ブランチ側が `test_analytics` 504・`test_oss` 173・`test_collectors` 79 に、同じ 1 行を書き換えているので、マージで必ずぶつかる。`docs/deploy.md` も基準側の `SKIP_STREAM` の行（26）とブランチ側の `SKIP_ANALYTICS` の行（27）が隣り合う行の変更で、git はこれもぶつかりとして扱う。どちらかの側だけを採ると、テストの本数か `SKIP_STREAM` の新しい説明のどちらかが黙って消え、設計の検証 5（「本数が実際と同じ」）を満たさなくなる。マージ前に基準を取り込み、両方の数字を合わせたうえで、マージ後の実際の本数を数え直す必要がある。動作は壊さないが、docs の正しさがマージの手作業次第になるので Should fix とする（`docs/troubleshooting.md` は基準側の hunk @77 / @96 / @101 とブランチ側の @91 の挿入が離れていて、`docs/pipeline.md` もぶつからない見込み）。

## Nit

- [design.md との整合性] `docs/architecture/resources/emr-serverless.md:39`、`docs/architecture/resources/msk.md:41`: 接続の表は「Spark ← MSK 9098 SASL_SSL + AWS_MSK_IAM」の読み取りだけで、Spark が起動のたびに同じ IAM の接続で `createAcls`（`kafka-cluster:AlterCluster`）を打つことが載っていない。`msk.md:19` の ACL の行と `access.tf:42-44` のコメントには書いてあるので情報は失われていないが、接続の表から Spark の権限の幅（ALTER CLUSTER 相当）を追う読み手には見えない。動作に影響しない表記の漏れなので Nit とする。

## 良かった点

- 方針 7 をそのまま実装している。`env_choice` が none を返したら JVM に触れずに `[]` を返し（`snmp_sinks.py:880-881`）、綴り違いは `ValueError` で止まり、`createAcls` の失敗は上げつつ `finally` で `close` する（`:891-895`）。`admin_client` の切り出しで `ensure_topics` と認証の組み立てが 1 か所にまとまった。
- 定数 `SCRAM_USER` / `SCRAM_TOPICS` / `SCRAM_OPS`（`snmp_sinks.py:84-86`）を、`ops/up-common.sh` の username・`syslog-ng.conf.in` の topic・`collectors.tf` の `-transport.kafka.topic`・`LOG_TOPICS` と `variables.tf` の既定とテストで突き合わせていて、どこか 1 か所だけ変えると落ちる。
- `AlterCluster` を KafkaCluster 文にだけ置くことを正規表現で縛り、その幅（ALTER CLUSTER と同じ）をコメントと未確定事項 10 に正直に書いている。CREATE・CLUSTER ACL・PREFIXED を入れない理由も design-log に残っている。
- 検証 6 で実 Spark と StandardAuthorizer の Kafka を使い、2 回目の冪等、3 ジョブ同時、ALTER 無しで `ClusterAuthorizationException` が上がること、`message.timeout.ms` の 300 秒を超える待ちでも syslog-ng が捨てないこと（GoFlow2 は捨てる）まで測り、C1 の破綻シナリオのうち外れた部分を design-log で訂正している。
- AWS の文書どうしの食い違い（`allow.everyone.if.no.acl.found` が効くか）を言い切らず、docs とコメントを「文書から読んだ想定。MSK では未確認」に弱め、検証 3 に「ACL の前の認可の失敗」を確かめる手順を足している。

## ユーザーへの質問

- `IaC/terraform/aws-managed/pipeline/stream/msk.tf:118-124` の `server_properties` に `allow.everyone.if.no.acl.found=false` を、このサイクルで足すか、検証 3 の結果を待つか。未確定事項 8 の「(b) 既定の true が SCRAM の主体に効いている」場合、収集器の SCRAM の資格情報で ACL の無いトピック（`metrics` / `gnmi` / `traps` 等）とクラスターに何でもできる。文書どおりなら足しても IAM の主体には効かず変化は無いが、MSK の configuration の改訂（ローリング更新）になる。いまは PM 判断として範囲外になっている。
- 検証 3 の「ACL の前の認可の失敗」は、stream ができてから analytics のジョブが起きる前に送る必要がある。`ops/up.sh` の流れでその間に人が送れる時間があるか（無ければ「判定できず」になる）を、実行する PM が見込んでいるか。

### PM の確認（2026-10-09、fable-5.1）

cold reviewer は 1 回目（opus、adf6b8c と実装が同じ 5944704 に対して）。Must fix 0 / Should fix 1 / Nit 1。

- **Should fix（docs のマージ衝突）**: adf6b8c で再現しようとしたが、`git show adf6b8c:docs/development.md | sed -n 37p` は 16 本の test を両方の側の件数で列挙しており、`git show adf6b8c:docs/deploy.md` には `SKIP_STREAM` と `SKIP_ANALYTICS` の行が両方ある。Round 2 の docs/cycle-006-design のマージ（a3b1a48）で解消済み。件数の裏付けは、PR #4 のマージ後（ec0bed3）で `bash ops/check.sh` を実行: `terraform fmt` / 18 ルートの validate / `bash -n` / 16 本の test が全部通り、最後の行が `すべて通過`、rc=0。件数は `test_alerts` 169、`test_analytics` 504、`test_app` 161、`test_collectors` 79、`test_dashboard_config` 3、`test_graph` 78、`test_kb_index` 7、`test_lab_debug` 104、`test_local_compose` 132、`test_nautobot` 69、`test_oss` 173、`test_oss_ops` 194、`test_oss_roll` 66、`test_stream` 96、`test_sync` 103、`test_workflow` 327 で、`docs/development.md:37` と同じ。→ 解消
- **Nit（`docs/architecture/resources/emr-serverless.md:39` と `msk.md:41` の接続表に Spark の `createAcls`（AlterCluster）が無い）**: 読んで確認した（読んだだけ）。012 では直さず、docs の更新（BACKLOG の 111）で一緒に直す
- **質問 1（`allow.everyone.if.no.acl.found=false` をいま入れるか）**: 入れない。design.md の S1 のとおり、AWS の検証 3 で ACL が効いて書けることを見てから。検証で確かめるのは `describeAcls` が 4 件と、`logs` / `flows` が書けていること
- **質問 2（stream と analytics のジョブの間に syslog / NetFlow を送る窓があるか）**: `ops/up.sh:300-302`（`SKIP_STREAM` は `SKIP_ANALYTICS` を強制）と `docs/deploy.md` の「あとから `up.sh` を打ち直して足す」で作れる。AWS の検証 3 は、1 回目を `SKIP_ANALYTICS=1` で立てて（stream まで）、syslog（`logger -n <NLB> -P 5140 -d --rfc5424`）と NetFlow（`tools/netflow_send.py <NLB>:2055`）を送り、`/ecs/<prefix>-syslog-ng` と `/ecs/<prefix>-goflow2` に認可の失敗が出るのを見てから、`SKIP_ANALYTICS` 無しで打ち直す。ジョブのあとは 3 ジョブの stderr の ACL の行、失敗が止まること、`logs` / `flows` に書けること、`describeAcls` 4 件、`UnderReplicatedPartitions` 0 を見る（未確定事項 8 / 9 / 10 を埋める）

Must fix 0 なので PR #4 を docs/cycle-006-design にマージした（a964c43）。AWS の検証は design.md の表のまま未確認（2026-10-09 の AWS の検証でまとめて行う）。
