# 042 のレビュー

## Round 1（2026-10-10。PM(fable-5-1)。cold reviewer は呼んでいない）

- 対象: Round 1 の実装 `b1f91f7`（18 ファイル、+1352 / -264）。レビューは `build.md` Round 1 の `### セルフレビュー`（実装モデル opus-5.5 + 反対弁護人）に依った。
- cold reviewer を呼ばなかった理由: セルフレビューで Should 4 件（2 イメージを戻すと待ちが終わらない / 3 ループが healthCheck より長い / 4 psql の失敗が「まだ無い」に見える / 5 init の同時実行でスキーマが壊れる）が既に出ていて、いずれも correctness / runtime / data loss なので自動で直す。直す前のコードに 2 回しか無い cold review の 1 回を使わない。cold review は Round 2（直した後）と完了判定の直前で呼ぶ。
- Must 1（エンジニアが直した。読んで確かめた）: タスクのロールの `Parameters`（`iam.tf:141-145`）が `/<prefix>/*` を許すので、env から外しても ECS Exec から `get-parameter` で `/<prefix>/nautobot/db-password` を引けた。`DenyNautobotParameters` を足した。`app/temporal/awsio.py` の ssm の呼び出しは `send_command` / `get_command_invocation` だけ、`IaC/terraform/aws-managed/workflow/` に `/nautobot/` の SSM を読む経路は無い（`grep -rn 'nautobot/'` で確認）ので、worker は壊れない。AWS で Deny が効くことは 147 で見る。
- Should 2〜5: design.md の設計方針 2〜3 と検証 1 に取り込み（`40bedb1`。経緯は design-log.md の「Round 1 の補正」）、エンジニアに Round 2 として振った。
- Nit 6〜9: 直さない。最終報告に載せる。
- 設計に無いファイルの変更: `docs/architecture/resources/nautobot.md`（master のパスワードを読むタスクの記述）と `tests/test_oss_ops.py`（`ops/oss/up.sh` の 8 の順序）。読んで妥当。
- 見た観点: design 整合性（build.md の記録とファイルの一覧）/ security（Must 1 と SSM の経路）。見ていない観点: correctness / runtime / data loss / API compatibility / type safety / missing tests は Round 2 の cold review で見る。
- 事故: エンジニアが手元の docker のイメージ 126 本を消した（原因と再発防止は build.md の末尾）。復旧は「必要になったときでいい」（2026-10-10 のユーザー決定。まとめて pull / build し直さない。手元の検証でイメージが要るときに、そのイメージだけ pull / build する）。
