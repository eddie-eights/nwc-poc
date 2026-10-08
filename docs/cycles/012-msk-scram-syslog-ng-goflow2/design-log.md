# MSK に SASL/SCRAM を足し、syslog-ng と GoFlow2 を立てる（012）— design-log

## Round 1（2026-10-09、エンジニア3。セルフレビューの C1 からの差し戻し）

Round 1 の design.md（PM(fable-5.1) / effort: high）は、MSK に SCRAM を足して `AmazonMSK_<prefix>-collectors` を関連付ければ、SCRAM のユーザー `collectors` が `logs` / `flows` に書けるものとして組んでいた。Kafka の ACL を誰がいつ付けるかは、設計のどこにも無かった。実装（ed8edf1..c699806）もそのとおりで、`git grep -n -i -w -e acl -e acls -e allow.everyone.if.no.acl.found -- IaC ops oss app` は 0 行。

### 何が分かったか

- セルフレビューの C1（`build.md` の Round 1）。AWS の文書（2026-10-09 確認）:
  - `iam-access-control.html`: IAM のアクセス制御を使うクラスターでは `allow.everyone.if.no.acl.found` が効かない。Kafka の ACL は IAM の主体には効かない
  - `msk-acls.html`: ACL の追加は Kafka の AdminClient（`kafka-acls.sh`）でする。同じページに「ブローカーは super user」とある一方、手順にはブローカーに Read の ACL を足す段がある
  - つまり SCRAM の主体（`User:collectors`）は ACL が無ければ何もできない。IAM のクライアント（Spark・Kafbat UI・Telegraf）は ACL と関係なく IAM のポリシーで動く
- PM が Must fix で確定（2026-10-09）。**原因は「SCRAM のユーザーは既定で書ける」という置いた前提**で、syslog-ng / GoFlow2 の実装の不具合ではない。同じ前提は `app/syslog-ng/syslog-ng.conf.in` のコメント（「トピックは MSK / Kafka が自動で作る」）にもあった。ACL が無い主体は自動作成も起こせない（下の実測）
- 反証になりうるもの: AWS のブログ（SCRAM と IAM を併用する例）には ACL の段が無い。上の文書のほうが具体的なので文書に従い、AWS 検証で TOPIC_AUTHORIZATION_FAILED が出るかを見る（design.md の未確定事項）

### C1 の破綻シナリオのうち、実測で外れたもの

C1 には「syslog-ng は librdkafka の待ちが切れるたびに捨て、GoFlow2 は落ちては置き換えられる」と書いた。手元で ACL を効かせた Kafka（apache/kafka:4.3.1、StandardAuthorizer、`allow.everyone.if.no.acl.found=false`、SASL_PLAINTEXT の SCRAM-SHA-512。使い捨ての資格情報）で測ると、どちらも外れた（生ログは build.md の Round 2）。

- syslog-ng（axosyslog 4.29.0）: 落ちない。`err kafka: failed to publish message; topic='logs', error='Broker: Topic authorization failed'` を約 3 回/秒出し、メッセージは捨てずに持っている（queued 1 / dropped 0）。ACL を入れると再起動なしで書いた（written 1 / dropped 0）
  - ソース（axosyslog の `modules/kafka/kafka-dest-worker.c`）: `rd_kafka_produce` が同期で -1 を返すと `LTR_RETRY`。`lib/logthrdest/logthrdestdrv.c` の `_process_result_retry` は 3 回で `_process_result_not_connected`（batch を巻き戻して `time_reopen` 休む）に移る。**捨てるのは `LTR_ERROR` の 3 回目だけで、kafka の宛先は publish の失敗で `LTR_ERROR` を返さない**
  - 捨てうるのは、librdkafka が受け取ったあとに配送が失敗した分（`kafka-dest-driver.c` の delivery report。debug ログに `message is lost` を出すだけ）。トピックのメタデータが認可の失敗の状態になる前、つまり起動の直後の数秒に来た分だけが当たりうる
  - 上限はメモリのキュー（`log-fifo-size` の既定 10000。conf では指定していない）。syslog-ng が再起動するとキューは消える
- GoFlow2（v2.2.7）: 落ちない。`level=ERROR msg="transport error" … The client is not authorized to access this topic` を出し（`-err.cnt 10` / `-err.int 10s` で黙る）、**そのフローは捨てる**（sarama の非同期の producer が Errors に返したものは再送しない）。ACL を入れたあとの新しいフローは書いた
  - `/__health` は `app.collecting`（収集を始めたか）しか見ないので、ECS も NLB も healthy のまま
- 認証の失敗（C2: ユーザーがまだ無い）は別物で、GoFlow2 は exit 1 で止まる。C1 の文面はこれと混ざっていた
- ACL の無い主体の書き込みでは、`auto.create.topics.enable=true` でもトピックはできない

### ACL の待ちが librdkafka の `message.timeout.ms`（既定 300 秒）を超えたとき

AWS では collectors（stream）が立ってから Spark（analytics）が ACL を入れるまで数十分あくので、待ちが librdkafka の配送の待ち（既定 300 秒）を超えても syslog が残るかを測った（同じ手元の Kafka。syslog 3 行と NetFlow 3 つを ACL の無いうちに送り、約 340 秒待ってから ACL を入れ、1 つずつ足す。生ログは build.md の Round 2）。

- syslog-ng: 4 行とも書いた（written 4 / dropped 0）。待ちのあいだは queued 3 で、認可の失敗の行は 1011（約 3 回/秒）。再起動は 0
  - 300 秒で捨てないのは、`rd_kafka_produce` がその場で失敗するので、メッセージが librdkafka のキューに入らない（`message.timeout.ms` の時計が回らない）から。持っているのは syslog-ng のキュー
- GoFlow2: ACL の無いうちの 3 つは捨て（`flows` に sequence_num 4 の 1 件だけ）、再起動は 0
- **結論: 待ちの長さで syslog の扱いは変わらない。設計は ACL の入れ場所（Spark）のままでよい**。待ちのあいだの NetFlow / sFlow は捨てる（流量の統計なので許容し、design.md の未確定事項に書く）

### ACL をどこで入れるか（採った案と、採らなかった案）

| 案 | 判断 | 理由 |
|---|---|---|
| A. `app/spark/snmp_sinks.py` の `ensure_topics` のあとに `ensure_acls`（IAM の AdminClient で `createAcls`） | **採った** | Spark は既に IAM の AdminClient でトピックを作っている（同じ接続・同じ jar・同じ実行ロール）。足すのは IAM の `kafka-cluster:AlterCluster` 1 つ。`createAcls` は同じものを何度入れても同じ（冪等）なので、3 本のジョブが毎回入れてよい。手元（KAFKA_AUTH=none）では呼ばない |
| B. Terraform の kafka provider（`Mongey/kafka` の `kafka_acl`） | 採らない | MSK は閉域で、Terraform を打つ手元の端末から届かない。provider を足すと plan のたびに MSK へ繋ぐ |
| C. stream 側の一回きりの ECS タスク（up.sh が `run-task`） | 採らない | AdminClient と `aws-msk-iam-auth` を持つイメージを別に作るか、Spark のイメージを stream で使うことになる。up.sh に待ちの段が 1 つ増える。collectors が立つ直後に ACL が入る利点はあるが、トピックはどのみち Spark が作る（下の CREATE）ので、待ちの長さは A と変わらない |
| D. VPC の Lambda | 採らない | Lambda の層に Kafka のクライアントと IAM の認証の jar（または Python の kafka のライブラリ + MSK の IAM の署名）が要り、依存が増える |
| E. Web の EC2 / Kafbat UI から手で | 採らない | 手作業。up.sh で戻らない |

### ACL の中身で採らなかったもの

- `CREATE`（自動作成のため）: 要らない。`logs` / `flows` は Spark の `ensure_topics` が作る（`log_topics` の既定に両方ある）。syslog-ng / GoFlow2 に自動作成の権限を持たせると、設定の誤り（トピック名の綴り違い）で知らないトピックができる
- CLUSTER の ACL（`IDEMPOTENT_WRITE` 等）: 要らない。syslog-ng（librdkafka の `enable.idempotence` の既定 false）も GoFlow2（sarama の `Idempotent` の既定 false）も冪等の producer を使っていない。re:Post に「SCRAM のクラスターで CLUSTER の ACL を入れるとブローカー間（主体は `User:ANONYMOUS`）の複製が止まる」とあるので、トピックの ACL だけにする
- `PREFIXED` / ワイルドカード（`*`）: 採らない。`logs` / `flows` の 2 つだけを `LITERAL` で
- ブローカーの Read の ACL（`msk-acls.html` の手順）: 入れない。同じページの「ブローカーは super user」を採り、AWS 検証で `UnderReplicatedPartitions` が 0 のままかを見る（design.md の未確定事項）

### Round 2 の build とセルフレビューで足したもの（エンジニア3。設計方針・範囲は変えない）

- `tests/test_oss.py` を変更対象と実装ステップ 1 に足した（`KAFKA_AUTH=none` で `ensure_acls` が何もしないことを OSS 側の検査でも縛る。検証 4 の「ACL の行が出ない」の手元の代わり）
- セルフレビューの反対弁護人の S1（Should）: AWS の文書は「IAM のクラスターでは `allow.everyone.if.no.acl.found` が効かない」（`iam-access-control.html`）と「MSK はこれを既定で true にする」（`msk-acls.html`）の両方を書き、併用のときにどちらが SCRAM の主体に効くかは書いていない（2026-10-09 に読み直した）。背景の文書の要約と未確定事項 8 に後者と「効いていた場合」の帰結を足し、検証 3 に「ACL の前の認可の失敗」（ジョブの前に syslog と NetFlow を 1 つずつ送り、失敗が出なければ止めて報告）を足した。docs とコメントの言い切りは「文書から読んだ想定。MSK では未確認」に直した（変更対象ファイルの「SCRAM の収集器は自動作成できない」も同じく条件付きにした）。`allow.everyone.if.no.acl.found=false` を足すかは設計方針に関わるので PM に回した（このサイクルでは入れない）
- 同じく N1（Nit）: `AlterCluster` の幅を「ACL の削除・パーティションの再配置・リーダー選出」から「Kafka の ALTER CLUSTER と同じ（どの主体・資源への ACL の作成と削除、SCRAM の資格情報の変更 等。MSK でどれが効くかは未確認）」に直した（方針 7 の IAM、未確定事項 10。`kafka-actions.html` が ALTER CLUSTER と同じとする）
- PM: 検証 3 の結果で決める（2026-10-09。S1 の `allow.everyone.if.no.acl.found=false` はいまは足さない。IAM と SCRAM の併用でこの設定が IAM 側のジョブ（Spark）に効くかも未確認で、確かめずに false を入れると壊す側のリスクが残るため。検証 3 で「ACL の前の認可の失敗」が出なければ、PM が design.md に足してから MSK の `server_properties` に false を入れる順で差し戻す）
