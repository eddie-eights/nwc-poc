## Round 1

- cold reviewer に依頼した（opus）。
- 実装モデル: opus-5.5 / effort: xhigh（build.md の Round 1）。

# cold review: netops の名前を nwc に揃える（019） r01

対象: `feat/019-rename-netops-to-nwc` の `083a99a..5162693`（途中で main から入った 835c5db = 723ccfd の設計変更を含む）

## サマリ

- 置き換え表どおりに、コード・設定・テスト・docs の `netops` / `NETOPS` / `NetOps` が `nwc` / `NWC` に揃っている。壊れる・危険な変更は見つからなかった（Must fix 0 件）。
- 受け入れの grep（`grep -rn -i netops` を docs/cycles と docs/verification を除いて実行）に残るのは `IaC/terraform/aws-managed/pipeline/analytics/tables.tf:54,55,61` と `tests/test_analytics.py:2225-2226` だけ。どちらも 723ccfd で設計に入った例外（moved の from と、それを確かめるテスト）の範囲に入る。
- `aws_s3tables_namespace` の moved は `netops[0] → netops` と `netops → nwc` を数珠つなぎにしてあり、同じ `to` を持つ moved を 2 本書くと Terraform が拒むことに当たらない。state に `netops[0]` / `netops` のどちらが残っていても `nwc` へ移る。
- Splunk のアラートアクションは 3 か所（`alert_actions.conf` のスタンザ、`bin/nwc_sns.py`、savedsearches の `action.nwc_sns`）が揃っている。Grafana の provider・folder・テンプレート名と、Nautobot の `JOBS` / `HOOK` / `LOCK` / `PYTHONPATH` / `NWC_SEED` も、参照する側と参照される側が一致している。ダッシュボードの uid（`nwc-metrics` / `nwc-logs`）は設計どおり変えていない。
- 自分で `bash ops/check.sh` を rename の worktree で実行した。結果は次のとおり。
  - fmt の差分なし、validate 18 件 OK、28 本のスクリプトの `bash -n` で構文エラーなし。
  - 模擬テストは 16 スイートとも失敗 0（test_alerts 168、test_analytics 513、test_app 161、test_collectors 79、test_dashboard_config 3、test_graph 78、test_kb_index 7、test_lab_debug 97、test_local_compose 138、test_nautobot 68 項目すべて通過、test_oss 173、test_oss_ops 196、test_oss_roll 66、test_stream 106、test_sync 103、test_workflow 327）。最後の行は「すべて通過」。
  - 件数は build.md に書かれた改名前の件数と同じで、「テストの中身と件数を変えない」の条件も満たしている。
- テストの diff は名前の置き換えだけで、検査を消したり緩めたりした箇所は無い。moved の検査は 1 件のまま 3 組を確かめる形に広げてある。
- docs の変更は 14 ファイルの名前の置き換えと build.md の追加だけ。`docs/cycles/` の既存ファイルと `docs/verification/` には手を入れていない。

### 見た観点 / 見ていない観点

- 見た観点:
  - 設計との整合（置き換え表、方針 1〜6、検証手順、リスク 1〜4、723ccfd の grep の例外）
  - correctness（参照する名前と参照される名前の対応、Terraform moved の連鎖）
  - runtime bugs（Splunk・Grafana・Nautobot・Spark の名前の食い違い）
  - data loss（S3 Tables の namespace が作り直しにならないこと。moved の連鎖で確認した）
  - API compatibility（旧名で作った環境に新しいコードを当てたときの振る舞い。build.md S2 の記述とコードで突き合わせた）
  - missing tests（検査の削除・緩めが無いこと、件数が変わらないこと）
  - security（シークレットの名前・SSM のパス・IAM の記述に、名前の置き換え以外の変更が無いこと）
- 見ていない観点:
  - 実際の AWS での apply・plan（moved が state 上で効くことは、コードの形と validate で見ただけ）
  - docker build と compose での実際の起動（build.md の記録に頼った。Splunk イメージの 11 項目と arm64 での Grafana・Nautobot のビルドは自分では再現していない）
  - type safety（Python の型注釈の変更が無いため対象外にした）
  - docs の diff を 1 行ずつ全部（data-stores.md・pipeline.md・faq などを抜き取りで見ただけで、置き換えの誤りは見つかっていない）

## Must fix

None

## Should fix

- [docs correctness] 日付付きの過去の記録の中の名前まで新しい名前に書き換えたので、当時の事実と食い違う。たとえば `docs/pipeline.md:310`、`docs/faq-fukuda-nwc-poc.md:2182`、`docs/alert-comparison.md:33-37` と `:134`。build.md の S1 で実装者自身も挙げている。当時の環境やログを `nwc_gnmi` などで検索しても見つからない。動作は壊れないが、過去の調べものを誤らせるので Should にした。
- [runtime / operations] 旧名で作った環境に新しいコードを当てたときの手順が、運用の docs に無い。
  - 手元の compose で `splunk-etc` の named volume が残ると、旧アプリ `netops_alerts` が動き続ける。両方のアプリが発火すれば、SNS に同じアラートが 2 回出る。
  - RDS を残した Nautobot では、新しいユーザー `nwc-web` に同じトークンキーを入れようとして `Token.key` の IntegrityError が出る。起動は続くが、旧い `netops-sync` の JobHook が残る。
  - analytics だけを apply すると、workflow を apply し直すまで旧い namespace を読み続ける。
  - どれも build.md S2 にしか書かれておらず、troubleshooting.md や deploy.md からはたどれない。
  - PoC は毎回 `ops/down.sh` から作り直すのが基本なので、壊れるのは環境を残したときだけ。ただ二重通知と古い JobHook は気づきにくいので、Should にした。

## Nit

- [design consistency] 723ccfd の例外は「moved の `from` に残る字面」と書いている。しかし grep には `tables.tf:55` の `to = aws_s3tables_namespace.netops`（旧い moved の `to`）も残る。連鎖に欠かせない行なので直すものではない。設計の文言を「moved ブロックの中」に広げれば、次に読む人が迷わないので Nit にした。
- [bisect] f4b57a5（git mv だけのコミット）を単独でチェックアウトすると、Dockerfile の COPY 元と中身が食い違ってイメージが作れない（build.md N1）。履歴を `--follow` で追えるようにするための分け方で、ブランチの先端は問題ない。bisect のときだけ困るので Nit にした。
- [operations] 古い Grafana の volume には、中身の無い `netops` フォルダが残る（build.md N2。エラー 0 件を実測済み）。見た目だけの問題なので Nit にした。

## 良かった点

- git mv を名前の書き換えとは別のコミットに分けたので、`git log --follow` で改名前の履歴をたどれる。
- Terraform の moved を 1 本で上書きせず、`netops[0] → netops → nwc` の連鎖にしたので、同じ `to` を持つ moved を Terraform が拒むことに当たらない。state がどちらの形でも namespace の作り直し（データの消失）が起きない。テストも 3 組を確かめる形に広げてある。
- 置き換えの漏れを確かめるため、旧名をわざと戻すとテストが落ちることを build.md に記録しており、テストが本当に名前を見ていることの根拠になっている。
- Splunk の実イメージでアプリの読み込みから保存済みサーチ・アラートアクションまで 11 項目を確かめており、`alert_actions.conf` と `bin/` のファイル名と savedsearches の対応が実物で裏付けられている。
- 設計に無い追加（`nwc_sns.html` の git mv、lab.sh、`netops_poll` の言い回しなど 12 件）を build.md に逸脱として正直に書き出している。

## ユーザーへの質問

- `netops` が戻ってこないよう、受け入れの grep を `ops/check.sh` かテストに常設しますか。今回は設計で「テストの中身と件数を変えない」としたので入れていないのは正しいが、次のサイクルで BACKLOG に足すかどうかを決めてほしい。
- Should fix の 1 件目（過去の記録の名前）は、当時の名前に戻しますか。それとも「2026-10-09 に nwc へ改名（cycle 019）」の注記を 1 か所に足して済ませますか。

### PM の判断（2026-10-09）

- セルフレビュー S1 と cold S1（日付つきの記録の名前も置換された）: 当時の名前には戻さない。`docs/troubleshooting.md` の冒頭に、2026-10-09 より前の記録は改名前の名前で、対応は 019 の design.md の置換の表、と 1 行足す。設計の方針 4 が記録と定めたのは `docs/cycles/` と `docs/verification/` だけで、`docs/*.md` は現行の説明文書。
- セルフレビュー S2 と cold S2（旧名で残した環境の手順が運用 docs に無い）: 58802ca で `docs/deploy.md` と `docker/compose/README.md` に 1 行ずつ足した。加えて `docs/troubleshooting.md` に、症状から引ける項（症状 3 つ → 原因 → `ops/down.sh` で消してから上げる）を足す。
- cold Nit 1（例外の文言が `from` だけで、連鎖の中継の `to`（tables.tf:55）が読めない）: design.md の検証方法の 1 つ目を「`moved` ブロックの中に残る旧名（`from` と、連鎖の中継の `to`）」に直す。
- cold Nit 2（f4b57a5 単独では build できない）・Nit 3（古い Grafana の volume の空フォルダ）: 直さない。build.md の N1・N2 と同じ。
- 質問 1（受け入れの grep を check.sh に常設するか）: このサイクルでは入れない。BACKLOG は PM が 019 のマージ後に書く。
- 質問 2: S1 のとおり。

## Round 2

- cold reviewer に依頼しない（実装ファイルが不変。変えたのは `docs/troubleshooting.md` と 019 の design.md だけ）。レビューは build.md の Round 2 に依る。
- PM の判断の 3 つを直した。取り直した検証は build.md の Round 2。
