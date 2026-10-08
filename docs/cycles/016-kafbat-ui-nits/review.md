# Kafbat UI の残りの Nit を片付ける（016）のレビュー

## Round 1

- 実行したモデル: cold reviewer = opus（`Agent` general-purpose）。PM の確認 = fable 5.1
- cold reviewer に依頼した（初回ビルド直後。対象 5be0288..41bbedd の変更 6 本）
- 結果: Must fix 0 / Should fix 1 / Nit 2
- 呼んだあとの `git status --short` は `?? docs/cycles/016-kafbat-ui-nits/review-r01.md` の 1 本だけ

### PM の判断（2026-10-09）

- **Should fix（`tests/test_stream.py:155` の 7-5 の `fullmatch` が、`run_on_instance` 行の直後の空行を消すと落ちる）: 再現した。直す。** 写し（`git archive 41bbedd`）で `oss/ops/up.sh` の 7-5 の `run_on_instance` 行と `# ---- 8. workflow` の間の空行を消して `.venv/bin/python tests/test_stream.py` → `AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: …`。41bbedd そのままは `通過 96 / 失敗 0`。design.md の設計方針 5「空行・コメントの出し入れは通す」から実装がずれている（設計が正本の (1)）ので、design.md は変えずに実装を直す。テストだけの変更なので cold reviewer の 2 回目は呼ばない。担当はエンジニア2（`fix/kafbat-ui-nits` に追加 commit → PR）
- **Nit（`docs/deploy.md:104` の「010 と 014」に 007 が無い）: 見送り。** cold reviewer のとおり、作り直しの案内は「010 より前の環境」の文でどの環境にも届く。docs だけの好みの範囲
- **Nit（`docs/pipeline.md:157` に `SKIP_STREAM` を外すことが無い）: 見送り。** design.md の設計方針 3 の文言どおりで、`docs/deploy.md` と `docs/troubleshooting.md` には書いてある

### cold review（review-r01.md をそのまま連結）

## サマリ

「Kafbat UI の残りの Nit を片付ける（016）」の cold review（Round 1）。対象は `git diff 5be0288 41bbedd` のうち変更ファイル 6 本（`docs/deploy.md` +3/-2、`docs/development.md` 1 行、`docs/pipeline.md` 1 段落、`docs/troubleshooting.md` +29/-1、`ops/up.sh` コメント 1 行、`tests/test_stream.py` +14/-6）。同じ範囲に入っている `README.md` / `docs/ai-dev-flow.md` は別の commit（7a7a266 / e4e9d4b）で、016 の対象外として見ていない。

全体として design.md の設計方針 1〜5 に沿っている。docs の事実関係（`admin-password` を作るのが `ops/up.sh` の手順 7 であること、手順 10 が自分で開くのは 8080 だけであること、`scripts-user always` で再起動のたびに user_data が走ること、OSS 版が `SKIP_STREAM` を読まないこと）は、コードと突き合わせて食い違いが無かった。Must fix は無い。tests の 7-5 の `re.fullmatch` が、設計が通すとした「空行の出し入れ」の一部（`run_on_instance` 行の直後の空行を消す）で落ちる点を Should fix に 1 件挙げる。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  設計方針 1〜5 と「変更対象ファイル」の表を、diff と 1 項目ずつ突き合わせた。`troubleshooting.md` の小節は 10 項目あり、置き場所は `## パイプラインと WORKFLOW` の表の直後（`grep -n '^## \|^### '` で `:72` の `##` の下、`:102` が `### Kafbat UI`、`:130` が次の `##`）。表の行は案内の 1 行だけ。`ops/up.sh` は 1 行（`:505`）のコメントだけでコードは変わっていない。`unmask` は `docs/` と `ops/` と `tests/` とテンプレートを grep し、残っているのは `--runtime` 付きの 2 か所だけ。「stream だけを上げ」「`ops/up.sh` を通さず」の古い案内は `docs/cycles/` の外には残っていない。
- **correctness（docs の事実）**
  `web_user_data.sh.tftpl` を読み、パラメータを読む順（`:54-57`）、75 / 69 の文言（`:48`、`:51`）、`exec docker run --rm`（`:72`）、`RestartPreventExitStatus=75`（`:87`）、`cloud_final_modules: [scripts-user, always]`（`:9-10`。troubleshooting の「EC2 を再起動する（user_data がもう一度走る）」の根拠）、Web のユニットの `Wants=`（`:138`）を確かめた。`ops/up.sh:931`（`if [ -z "$SKIP_STREAM" ]` の中の `ensure_secret …/kafka-ui/admin-password`）、`oss/ops/up.sh:115`（`SKIP_STREAM` を IGNORED に入れる）・`:334`（手順 7 の `ensure_secret`）・`:291`（OSS の手順 4-4 の reboot）、`ops/up.sh:836-839`（4-4 の reboot）、`:1243-1247`（8-3）、手順 10（8080 だけを `start-session` で開き、Kafbat UI はコマンドを表示するだけ）、`web.tf:101`（`user_data_replace_on_change = true`）を確かめた。
- **correctness（tests）**
  `_between` の境界（開始の印が無い、終わりの印が無い、終わりの印が開始より前にしか無い）を読んで確かめた。`-1 < find(a) < find(b)` は `.index()` の比較と同じ意味で、どちらかが無いときは `ValueError` ではなく `False` になる。
- **動的な確認（実際に走らせたもの）**
  - `uv run --group dev --group web python tests/test_stream.py` → `通過 96 / 失敗 0`（`docs/development.md` の `test_stream 96` と一致）。
  - docs を読む他のテスト（`test_app` 161、`test_analytics` 495、`test_lab_debug` 104、`test_nautobot` 69 項目すべて通過、`test_oss` 172、`test_oss_ops` 181、`test_local_compose` 132、`test_workflow` 327、`test_collectors` 78）→ すべて失敗 0。
  - 変異: `git archive HEAD` を scratch に展開し、その写しの中だけで変異を入れて `tests/test_stream.py` を assert のまま、と `check()` を数える形に替えたもので打った（ワークツリーは触っていない。走らせたあと `git status --short` は空）。結果は次のとおりで、design.md の検証方法 2〜5 の期待（5be0288 のマージ後の数）と一致した。
    - (a) 7-5 の `run_on_instance` 行の前に空行 → 通る（96 / 0）
    - (a2) `log "7-5.` → `7-6.`、(a3) `run_on_instance` 行を消す、(b) `8-3. Web` → `8-3. web` → どれも `ValueError` ではなく「Kafbat UI を起こす Web の restart は stream の apply より後…」の `AssertionError`。count で 95 / 1
    - (c) テンプレートの `Wants=${name_prefix}-kafka-ui.service` を消す → count で 94 / 2（既存の `_wunit` の check と新しい `_kwunit` の check）
    - (o1) 前に `[ -n "${X:-}" ] &&`、(o3) 前の行に `[ -z "${X:-}" ] || exit 0`、(o5) `: <<'__X__'` で殺す、(o7) 前の行に `false && \` → どれも同じ check で落ちる（95 / 1）
    - (o6) 後ろに空行とコメント行 → 通る（96 / 0）
    - 追加で打ったもの: Web のユニットの `Wants=` を `Requires=` に替える → 94 / 2。7-5 の `run_on_instance` 行と `# ---- 8. workflow` の間の空行を消す → **落ちる**（95 / 1。下の Should fix）
- **security**
  docs に出るのは SSM のパラメータ名だけで、値やその取り方（`--with-decryption` を打つコマンド）を新しく足してはいない。
- **API compatibility**
  `ops/up.sh` はコメントだけ。テンプレート・`web.tf`・`kafka_ui.tf`・`oss/ops/up.sh` は 5be0288 から変わっていない（設計の「触らない」に合う）。

見ていない観点:

- `bash ops/check.sh` は走らせていない（`terraform fmt` / `validate` の 18 回を含むため）。テストは上のものを個別に走らせただけで、`test_graph` / `test_sync` / `test_alerts` / `test_kb_index` / `test_oss_roll` / `test_dashboard_config` は走らせていない（016 は docs を読む検査に触れうるものに絞った）。
- AWS 上の挙動（`unmask --runtime` が AL2023 の systemd で外れるか、`daemon-reload` / `enable --now` が落ちたときの `systemctl status` の表示、Kafbat UI が上がるまでの時間）は確かめていない。design.md でも未確認として扱っていて、docs も「未確認」「見込み」と書いている。
- `build.md` / `design-log.md` の記録の中身（生ログの正しさ）は対象外として読んでいない。
- My Repo 側の設計・レビューの HTML は見ていない。

## Must fix

None

## Should fix

- **[design.md との整合性 + correctness（tests）] `tests/test_stream.py:155` の 7-5 の `re.fullmatch` が、`run_on_instance` 行の直後の空行を消すだけで落ちる**
  `_s75` は `_between(_oss_up_sh, '\nlog "7-5. ', "\n# ---- 8. workflow ")` で、終わりの印の頭の `\n` の手前で切る。いまの `oss/ops/up.sh:551-553` は `run_on_instance …\n\n# ---- 8. workflow` なので `_s75` は `…$WEB_ACTIVE"\n` で終わり、正規表現の `…\$WEB_ACTIVE"\n(?:…\n)*` に合う。ところが空行を消して `run_on_instance …\n# ---- 8. workflow` にすると、`_s75` は `…$WEB_ACTIVE"` で終わり（末尾の `\n` は終わりの印のほうに入る）、`run_on_instance` 行の直後に必須の `\n` が無いので `fullmatch` が `None` になる。scratch の写しで打って、「Kafbat UI を起こす Web の restart は stream の apply より後…」で落ちることを確かめた（count で 95 / 1）。`run_on_instance` 行の直後にコメント行を足して空行を挟まずに `# ---- 8.` に続けた形も同じ理由で落ちる。
  design.md の設計方針 5 は「空行・コメントの出し入れは通し」「014 の `split("\n")[2]` が縛っていた範囲を保つ」としていて、c4c378a の `split("\n")[2]` はこの形を通していた（`['', 'log "7-5. …"', 'run_on_instance …']` の [2] が一致する）ので、範囲が狭まっている。直し方の一例は、`run_on_instance` 行のあとを `(?:\n[ \t]*(?:#[^\n]*)?)*\n?` にする（行の終わりの `\n` を任意にする）か、`_s75` の終わりの印を `"# ---- 8. workflow "`（頭の `\n` なし）にする。
  分類の理由: いまの `oss/ops/up.sh` では通っていて壊れていないが、誰かが 7-5 と 8 の間の空行を詰めるだけの無害な編集をしたときに、Kafbat UI の起こし方の退行を示す名前の check が落ちて、編集した人が誤った理由を追うことになる。設計が明示的に「通す」とした範囲との差なので Nit より重いが、動きは壊さないので Must ではない。変異の (a)〜(c)・(o1)〜(o7) はこの形を含まないので、build.md の変異でも見つからない。

## Nit

- **[correctness（docs）] `docs/deploy.md:104` の「いまのところ 010 と 014」に「ディレクトリを app/ と docker/ と IaC/ に並べ直す（007）」が入っていない**
  `git log -- …/web_user_data.sh.tftpl` を見ると、48683dd（007 の commit 2/3）がテンプレートのコメントと `echo` の文言を 4 行書き換えていて、描いた user_data も変わる（テンプレートのコメントは user_data に入る。`web.tf:93-94` のコメントの通り）。ただ 007 より前の環境は 010 より前でもあり、そちらの「010 より前の環境は…」で取り直しの案内は届くので、実害は無い。「user_data を変えたサイクル」の一覧として読む人がいるなら 007 を足すか、「Kafbat UI のサイクルでは」と範囲を書くとよい。
  分類の理由: 一覧としては欠けているが、作り直しの案内はどの環境にも届くので好みの範囲。
- **[design.md との整合性（docs）] `docs/pipeline.md:157` の「stream を足すときは `ops/up.sh` を打ち直す」に `SKIP_STREAM` を外すことが書かれていない**
  `docs/deploy.md` の `:26` と `:279`、`docs/troubleshooting.md` の `image` などの下位項目は「`SKIP_STREAM` を外して `ops/up.sh` を打ち直す」と書いているが、pipeline.md は外すことに触れていない。`deploy.env` に `SKIP_STREAM=1` を書いたまま打ち直すと手順 7 も 8-3 の stream の条件も飛ぶので、pipeline.md だけを読んだ人は stream が足されない理由に迷う。design.md の設計方針 3 の文言どおりではある。また同じ文の「（手順 8-3 の Web の再起動で起きる）」はマネージド版の番号だけで、OSS 版は 7-5（同じ段落の前の文には両方ある）。
  分類の理由: 設計どおりで、同じ段落の前後と他の docs を読めば分かるので好みの範囲。

## 良かった点

- `_between` の導入にあわせて、同じ check の中の `.index()` の比較（`tf_apply pipeline/stream` の位置）と 7-4c の切り出しも `find` / `_between` に揃え、この check はどの印が外れても `ValueError` ではなく check の名前で落ちる形になっている。変異 (a2)(b) で確かめた。
- 7-5 の検査を `split("\n")[2]` から `fullmatch` にしたことで、行を条件・関数・ヒアドキュメント・行の継続で包む変異（o1・o3・o5・o7）を落とし、前後の空行・コメント（a・o6）は通すようになっている（上の Should fix の 1 形を除く）。
- 描いたあとの user_data の Web のユニットに `Wants=x-nwc-poc-kafka-ui.service` が 1 行だけあることを見る check を足し、`Wants=` を `Requires=` に替える変異でも新旧の 2 本が落ちることを確かめた。
- troubleshooting の 1,800 文字の 1 行を 10 項目の小節に分け、いまの行の中身（イメージを変えたとき、手で止めたときなど）を落とさずに移している。「`setup failed` があり `daemon-reload` / `enable --now` で落ちた」項目は、`systemctl status` の表示を「どちらになるかは未確認」と正直に書いている。
- `admin-password` を Terraform で作らず `ops/up.sh` が作るという、このリポジトリの決まり（シークレットは `ops/up.sh` が SSM の SecureString として作る）を docs の説明にもそのまま書いている。

## ユーザーへの質問

None

## Round 2

- 実行したモデル: PM の確認 = fable 5.1。cold reviewer は呼んでいない（Round 1 の Should fix を直した `tests/test_stream.py:518` の 1 行だけで、実装ファイルは不変。依ったセルフレビューは build.md の Round 2）
- 直したもの: Should fix（7-5 の `fullmatch` が `run_on_instance` 行の直後の空行を消すと落ちる）。エンジニア2 の commit 4d0a7a1（PR #3）。後ろ半分を `\n(?:[ \t]*(?:#[^\n]*)?\n)*` から `(?:\n[ \t]*(?:#[^\n]*)?)*\n?` に変えた。design.md は変えていない（設計方針 5 どおり）
- 根本原因（1 行）: `_s75 = _between(_oss_up_sh, '\nlog "7-5. ', "\n# ---- 8. workflow ")` は終わりの印の頭の `\n` の手前で切るので、`# ---- 8.` の直前が空行でないと `_s75` の末尾に `\n` が無く、行末の `\n` を求める旧パターンが合わない
- PM の再現（2c8e59d の HEAD 相当の写し。`git archive HEAD` に PR の `tests/test_stream.py` を重ね、`.venv/bin/python tests/test_stream.py`）:
  - そのまま: `通過 96 / 失敗 0`
  - `oss/ops/up.sh` の 7-5 と `# ---- 8. workflow` の間の空行を消す（Round 1 で落ちた形）: `通過 96 / 失敗 0`
  - 7-5 の `run_on_instance … restart $PREFIX-web.service` 行をコメントアウト: `AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: …`（落ちる）
- エンジニア2 の報告（build.md の Round 2）: HEAD で 96 / 0、d1・d2 は直す前 95 / 1 → 直したあと 96 / 0、o1/o3/o5/o7/a3 は直したあとも落ちる、`bash ops/check.sh` は `通過 327 / 失敗 0` `すべて通過` rc=0
- 見送り（Round 1 のとおり）: Nit 2 件（`docs/deploy.md:104` の 007、`docs/pipeline.md:157` の `SKIP_STREAM`）
- BACKLOG へ回したもの: 8-3 の検査（`_s83.count("\nfi\n") == 1`）が `fi` と `# ---- 8-5.` の間の空行を消すと落ちる（014 の c4c378a からある。7-5 と同じ形の弱さ）と、Round 1 の OC7（`run_on_instance` を呼ばない関数で包む形を通す）
- 結果: Must fix 0 / Should fix 0 / Nit 2 見送り。PR #3 を docs/cycle-006-design へマージした（2c8e59d）
- AWS で未確認のまま: Kafbat UI が上がるまでの時間、`status` の文言（`could not be found` / `disabled`）、コンテナの 75、AL2023 の `unmask --runtime`
<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261009-cycle-016-kafbat-ui-nits-review.html -->
