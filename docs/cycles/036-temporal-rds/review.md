## Round 1

レビューモデル: opus-5.5 / effort: xhigh（cold reviewer に依頼。初回ビルド直後）。確認は PM(fable-5-1)。2026-10-10。

# Temporal の履歴を RDS に残す（036） cold review Round 1

- 対象: `485fa44...ac101ee`（39 ファイル）
- 根拠: `docs/cycles/036-temporal-rds/design.md` と実コードだけ
- 前回の Must fix: なし（Round 1）

## サマリ

Must fix 0 / Should fix 1 / Nit 7。

実装の正しさを崩す欠陥は見つからなかった。Should fix は、正本の design.md が実装とずれたままの 6 か所。build.md には書いてあるが、design.md には反映されていない。

テストはレビューする側で走らせて確かめた。

- コマンド: `./ops/check.sh`（HEAD `ac101ee`）
- 終了コード 0。test_workflow の部は `通過 355 / 失敗 0`、最後の行は `すべて通過`
- 走らせたあとの `git status --short` は空

### 見た観点 / 見ていない観点

見た観点

- design.md との整合: entrypoint の手順、env の表、ECS の 3 コンテナ・healthCheck・デプロイ率、IAM、SG の流れ、ops の並び、docs
- correctness: べき等性、setup-schema の条件、`GRANT ... TO CURRENT_USER` の条件、イメージタグと再ビルドの条件
- security: パスワードの通り道（コマンドライン・env・ログ）、IAM の範囲、SG の範囲
- runtime: 起動の順序、背景のプロセス、PID 1
- data loss: DROP や schema のリセットの有無、RDS の大版の扱い
- API compatibility: nautobot の新しい output、`temporal_image_tag` の既定値を消したことと、その呼び出し元（up.sh / oss/up.sh / down.sh / oss/down.sh）
- type safety: 変数の validation（正規表現と `destroy` の照合）
- missing tests: test_workflow / test_analytics / test_lab_debug / test_oss_ops の追加分

見ていない観点

- AWS の上での動作。RDS の 18、RDS の TLS、`rds_superuser` での `btree_gin`、Fargate の hostname / getent による broadcast、接続数、デプロイ中の 2 タスクの重なりが該当する。design.md のリスク 1〜6・9 で、QUEUE の最後の AWS 動作確認に持ち越されている
- 手元の Docker での再現。entrypoint の通しの動作と TLS の 12 本の接続は build.md の記録を見ただけで、自分では打っていない
- 公式イメージの中身。`/etc/temporal/entrypoint.sh` と config テンプレートが読む env の名前は読み直していない
- arm64 以外のホストでの `docker buildx build --platform linux/arm64`

## Must fix

None

## Should fix

- [design.md 整合性] 正本の design.md が、実装で変えた判断 6 か所を反映していない。build.md:19-28 には書かれているが、正本は design.md
  - design.md:25 は「DB は `up.sh` のたびに作り直すので升級の手順は要らない」とある。一方 `IaC/terraform/aws-managed/pipeline/nautobot/database.tf:20` は `engine_version = var.db_engine_version` で、`allow_major_version_upgrade` が無い。17 から 18 に上がるのは down.sh を打ったあとだけ
  - design.md:48-61 の Dockerfile には dynamicconfig が無い。実装には `docker/images/temporal-server/Dockerfile:18` の `COPY dynamicconfig.yaml /etc/temporal/config/dynamicconfig/docker.yaml` がある
  - design.md:80 は `psql -v pw="$POSTGRES_PWD"` で、パスワードをコマンドラインに載せる。実装は `docker/images/temporal-server/entrypoint.sh:48` の `\getenv pw POSTGRES_PWD`
  - design.md:84 は無条件の `GRANT %I TO CURRENT_USER`。実装は `entrypoint.sh:53-54` で `WHERE NOT pg_has_role(CURRENT_USER, :'role', 'SET')` の条件が付く
  - design.md:169-170 の SG の why は「タスクの中の 5 サービス」。実装の `IaC/terraform/aws-managed/base/core/security_groups.tf:82-83` は「between the tasks during a deployment」。タスクの中は localhost なので SG を通らず、実装の方が正しい
  - design.md:187 は「`ops/down.sh` は変えない」。実装は `ops/down.sh:71` と `ops/oss/down.sh:52` で `-var "temporal_image_tag=destroy"` を渡している（既定値を消したので、渡さないと destroy が止まる）
  - 壊れる状況: 次のサイクルで design.md を正本として読み、そのとおりに直したり書き戻したりすると、3 つの不具合が戻る
    - master のパスワードが psql のコマンドラインに載る（ECS Exec から `/proc/*/cmdline` で見える）
    - down.sh の workflow の destroy が `No value for required variable` で止まり、リソースが残る
    - dynamicconfig の無いイメージになり、temporal-server の起動が変わる
  - Should とした理由: コードそのものは正しく、テストも通っている。ただ、正本の記録が重要な判断について古いままで、後のサイクルで誤りを呼び戻す経路になる

## Nit

- [correctness] `ops/up.sh:635` と `ops/oss/up.sh:220` の `dir_tag "$TEMPORAL_SERVER_VERSION" docker/images/temporal-server` は、`POSTGRES_MAJOR`（`ops/up-common.sh:258`）をタグに含めない。build の `--build-arg "POSTGRES_MAJOR=$POSTGRES_MAJOR"`（`ops/up-common.sh:334`）はタグに効かない
  - 壊れる状況: design.md のリスク 1 の逃げ道で `ops/up-common.sh:258` だけを 16 にし、check.sh を飛ばして up.sh を打つ。タグが変わらないので ECR に既にあるとビルドが飛ばされ、イメージの psql は 18 のまま残る
  - Nit とした理由: Dockerfile の `ARG POSTGRES_MAJOR` と up-common.sh の値が等しいことを test_workflow が要求するので、check.sh を通せば Dockerfile も直すことになり、ディレクトリのハッシュが変わる。守りがテストにしか無いだけで、check.sh を通す運用なら起きない
- [runtime / data loss] `IaC/terraform/aws-managed/pipeline/nautobot/database.tf:20` は `engine_version = var.db_engine_version`（既定 18）で、`allow_major_version_upgrade` が無い
  - 壊れる状況: 17 の RDS が残っている環境で up.sh を打つと、nautobot の apply が RDS の API の `InvalidParameterCombination` で止まる
  - Nit とした理由: 止まるだけで DB は変わらない（データは失われない）。AWS は確認後すぐ down.sh する運用で、17 の環境が残らない。Should fix の design.md:25 の件と同じ根から来ている
- [security] `docker/images/temporal-server/entrypoint.sh:16` で必須にした `NAUTOBOT_DB_PASSWORD`（RDS の master のパスワード）は、`:107` の `exec` のあとも temporal-server の env に残る
  - 壊れる状況: `ecs:ExecuteCommand` できる人が `/proc/1/environ` を読むと、Temporal 用のロールではなく master のパスワードが見える
  - Nit とした理由: design.md のリスク 7 が受け入れた代償で、読める範囲は Nautobot のコンテナと同じ。exec の前に `unset NAUTOBOT_DB_PASSWORD` すれば、この面は 1 行で閉じられる
- [security] `entrypoint.sh:51` の `ALTER ROLE %I WITH LOGIN PASSWORD %L` は、起動のたびに平文のパスワードを SQL 文として送る
  - 壊れる状況: この文がエラーになると、RDS の既定の `log_min_error_statement = error` で文がそのまま postgresql.log に書かれる。ログは `DownloadDBLogFilePortion` で落とせる。実行中の短い間は `pg_stat_activity.query` にも見える
  - Nit とした理由: `%L` で引用しているので、エラーになる入力は考えにくい。パラメータグループを既定のまま使い、CloudWatch Logs への出力も無い（`database.tf` に `parameter_group_name` / `enabled_cloudwatch_logs_exports` が無い）ので、見える経路は狭い
- [runtime] `entrypoint.sh:82-104` の背景のサブシェルは、`:107` の `exec` のあと temporal-server（PID 1）の子になる。抜けたあとは誰も回収しないのでゾンビが 1 つ残る。`ecs.tf` には `initProcessEnabled`（`linuxParameters`）が無い
  - 壊れる状況: 起動のたびにゾンビが 1 つ残る。プロセス表に残るだけで、資源は食わない
  - Nit とした理由: 1 起動に 1 つで増えないので害は無い。気になるなら `linuxParameters.initProcessEnabled = true` を足す
- [security / 整合性] `entrypoint.sh:67` の `sql_tool` は `SQL_TLS_DISABLE_HOST_VERIFICATION=true` を決め打ちしている
  - 壊れる状況: design.md のリスク 2 の逃げ道で、ecs.tf にホスト名検証を有効にする設定と `SQL_CA` を足しても、サーバー本体だけが検証し、temporal-sql-tool（スキーマの投入）は検証しないまま残る
  - Nit とした理由: いまは両方とも検証しない設定（`SQL_HOST_VERIFICATION=false` 相当）で揃っていて、矛盾は無い。逃げ道を使うときにだけ直す箇所が増える
- [文書] `IaC/terraform/aws-managed/base/core/security_groups.tf:39` の workflow の SG の説明は、いまも「Temporal dev server and worker ECS task」のまま。036 で dev server はやめている
  - 壊れる状況: AWS のコンソールで SG を見た人が、まだ dev server だと誤解する
  - Nit とした理由: SG の description を変えると SG が作り直しになる（ENI が付いていると置き換えに時間がかかる）。触らないのは妥当で、ずれはコメントで足りる

## 良かった点

- entrypoint はべき等に組まれている。ロールと DB は無いときだけ作り、`setup-schema -v 0.0` は `schema_version` が無いときだけ打つ。DROP は 1 つも無いので、再起動やタスクの入れ替えで履歴は消えない
- パスワードは `\getenv`、`PGPASSWORD`、`SQL_PASSWORD` の env で渡していて、コマンドラインにもログにも出ない。design.md の案より良くしている
- PostgreSQL 16 以降の CREATEROLE の意味の変化（作った側は ADMIN だけで SET が無い）に合わせて、`pg_has_role(..., 'SET')` で GRANT の要否を見ている。build.md では superuser でない master で確かめている
- TLS は手元で確かめてある（build.md の `pg_stat_ssl` で 12 本）
- 既定値を消した `temporal_image_tag` を down.sh / oss/down.sh が渡していないことをセルフレビューで見つけて直し、必須の変数が apply と destroy の両方で渡っているかを見る汎用のテストを test_oss_ops に足している
- 並びが正しい。シークレットを作ってから apply（`ops/up.sh:1253-1254`、`ops/oss/up.sh:584-585`）、nautobot を workflow より先に apply、destroy は workflow を先にしている
- IAM の `ssm:GetParameters` は 2 つのパラメータの ARN だけに絞られている。nautobot が無い構成で ARN が空になっても `compact()` で落ちない
- SG の自己参照の流れは workflow の SG だけに限り、理由（デプロイ中のタスク間）を正しく書き直している。test_analytics が「7233 は workflow から workflow 以外は閉じている」を見ている
- Temporal server・UI・PostgreSQL の版を、up-common.sh / Dockerfile / variables.tf の間で等しいかテストで見ている
- OSS 版はシンボリックリンクと `active_sg_flows` で同じ定義を使っていて、二重管理になっていない

## ユーザーへの質問

None

### PM の確認

cold reviewer の指摘を読み合わせて確かめた（design.md と実装の食い違いなので、実行ではなく該当行を読んで突き合わせた）。

- Should fix（design.md の 6 か所）: 6 つとも実装どおりに design.md を直した。`design.md:25`（17 → 18 は `allow_major_version_upgrade` が無いので先に `ops/down.sh`）、Dockerfile の `COPY dynamicconfig.yaml`、psql の `\getenv pw POSTGRES_PWD`、`GRANT ... WHERE NOT pg_has_role(..., 'SET')`、SG の why を「between the tasks during a deployment」に、`ops/down.sh` / `ops/oss/down.sh` の `-var "temporal_image_tag=destroy"`。あわせてリスク 5 の「2 クラスターが並ぶ」を「新旧 2 タスクが `cluster_membership` で 1 つのクラスターになる」に直し、ポートの段落（152 行目）の SG の説明も揃えた。実装側は変えていない
- Nit 7 件: 直さない。QUEUE に 2 行で足した（entrypoint の守り: `unset NAUTOBOT_DB_PASSWORD`、`sql_tool` のホスト名検証の決め打ち、`initProcessEnabled`。SG の description は AWS の環境が無いときに）。Nit 1（`dir_tag` に `POSTGRES_MAJOR` が入らない）と Nit 2（`allow_major_version_upgrade` が無い）と Nit 4（`ALTER ROLE ... PASSWORD` の SQL 文がエラー時にログへ出る）は、test_workflow の照合と down.sh の運用と `%L` の引用で守られているので QUEUE にも入れない（Nit 2 は design.md:25 と QUEUE の AWS 動作確認の行に「先に down.sh」として書いた）
- 全テスト: `./ops/check.sh`（design.md と QUEUE を直したあと）→ `すべて通過`、exit 0。fmt 差分なし、20 ルートの validate OK
- 2 回目の cold review は呼ばない。Round 1 のあと実装ファイルは 1 バイトも変えていない（直したのは design.md と QUEUE と review.md だけ）
- 見た観点: design 整合性（cold reviewer の 6 か所を突き合わせ）/ security（Nit の分類の確認）。見ていない観点: AWS での動作（QUEUE の「残った修正をまとめて AWS で動作確認して直す」に、リスク 1〜6・9 の見るものを書いた）
- 全体設計 HTML は触らない。承認時の設計（Temporal の履歴を RDS に書く 3 コンテナの構成）から全体の設計は変わっていない（変わったのは entrypoint の細部と down.sh の変数）

結論: Must fix 0 / Should fix 0（1 件を直した）/ Nit 7（QUEUE に 2 行）。サイクル完了。
