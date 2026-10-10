# 042 の設計の経緯

## Round 0（2026-10-10。PM(fable-5-1)）

ユーザーにしか決められない選択は無かったので質問はせず、置いた前提をここに残す（設計の承認は要らない。2026-10-10 のユーザー指示）。

- **init のタスクの起こし方は `ops/up.sh` の `ecs run-task`。** 却下した案: (a) Terraform の `null_resource` + `local-exec`（apply の中で AWS CLI を打つことになり、`ops/down.sh` の destroy と `plan` の見え方が濁る。リポジトリに前例が無い）、(b) Lambda から起こす（権限とコードが増える。一回きりの処理に常駐のものを足す理由が無い）、(c) `desired_count=0` で apply → run-task → `desired_count=1` で apply の 2 段（apply が 2 回で遅い。サーバー側で init を待てば順序に依存しないので要らない）。
- **サーバーは init を待つ（`schema_version.curr_version` が最新の版になるまで）。** 「表がある」だけでは `setup-schema -v 0.0` の直後（`update-schema` の途中）に進んでしまうので版で見る。temporal-server 自身も起動時にスキーマの版を確かめるが、それに任せるとクラッシュループになり、ログも分かりにくい。
- **namespace の作成はサーバーに残す。** QUEUE 140 の文言は「ロール・DB・スキーマ・namespace」だが、namespace は動いている frontend にしか作れず秘密も要らない。init へ移すなら「サービスが上がってから 7233 へ届く別のタスク」になり、worker と ui の HEALTHY 待ち（namespace ができてから起きる）の意味まで変わる。master のパスワードを外すという目的には関係しない。
- **`update-schema` はサーバーから外し、init だけがやる。** 2 か所で同じことをすると「どちらが正か」が消える。`up.sh` を打つたびに init が走るので、イメージの版を上げたときもそこで上がる。
- **共通部分（log / trap / TLS の導出 / DB 待ち）は `common.sh` に切り出す。** 2 本のスクリプトに同じ 30 行を持たせると 040 のような直しが二重になる。
- **healthCheck と ECS Exec はそのまま。** 渡さなくなるので経路として消える。healthCheck を `temporal` CLI 以外に替える理由は無い。

## Round 1 の補正（2026-10-10。PM(fable-5-1)）

エンジニアのセルフレビュー（`build.md` Round 1）の Should 2〜5 を design に取り込んだ（いずれも correctness / runtime / data loss なので自動で直す）。ユーザーの判断は要らない。

- Should 2: 待ちの比較を「一致」から「DB ≥ イメージ」に変えた。Temporal 本体が DB の新しい側を許すのに entrypoint だけ拒むと、イメージを戻したときに起きない。
- Should 3: ループを 60 回（600 秒）から 30 回（300 秒 = startPeriod）に短くし、毎回の log に init のログの案内を入れた。ECS が約 360 秒で止めるので 600 秒のループの最後の行は出ない。
- Should 4: psql の失敗時に stderr の 1 行目を log に出す。
- Should 5: `run_temporal_init` が前の init を `list-tasks` で見て止まるのを待つ。
- Must 1（タスクのロールの SSM の Deny）は design の検証 1 に取り込んだ。
