# ops の小さな直しを片付ける（023）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

012・014・016・019 のレビューで出て BACKLOG に残っていた、ops まわりの小さな直し 4 件を 1 サイクルに束ねる。どれも互いに独立で、1 件ずつ 1 commit にする。

| # | BACKLOG の行 | 出所 |
|---|---|---|
| A | `ensure_secret` / `ensure_fixed_secret` の一時ファイルを `on_exit` でも消す | 012 Round 2 セルフレビュー C5 |
| B | `ensure_topics` のログを TopicExists のときに「作った」と出さないようにする | 012 Round 2 セルフレビュー N4 |
| C | `netops` が戻らないよう受け入れの grep を `ops/check.sh` に常設する | 019 cold review の質問 |
| D | `tests/test_stream.py` の 8-3 の検査（`_s83`）の検出力を上げる | 016 セルフレビュー OC7 と Round 2 |

### 調査で分かった事実（2026-10-09、origin/main 78dac1a）

**A. 一時ファイル**

- `ops/up-common.sh:111-146` `ensure_secret` と `:147-170` `ensure_fixed_secret` は、`local input` に `mktemp "${TMPDIR:-/tmp}/nwc-secret.XXXXXX"`（`umask 077`）で作った一時ファイルに SecureString の値を JSON で書き、`aws ssm put-parameter --cli-input-json "file://$input"` に渡し、**関数の最後の `rm -f -- "${input:?}"` だけ**で消す。`put-parameter` の最中に Ctrl+C / kill で止まると平文のファイルが残る。
- 同じファイルの MSK の SCRAM は既に対策済み。`ops/up-common.sh:376` にグローバル `MSK_SCRAM_INPUT=""`、`:396` で `MSK_SCRAM_INPUT=$(umask 077; mktemp …)`、`:406` で `rm -f -- "${MSK_SCRAM_INPUT:?}"; MSK_SCRAM_INPUT=""`。`ops/up.sh:156-165` の `on_exit()`（`trap on_exit EXIT`）が `if [ -n "$MSK_SCRAM_INPUT" ]; then rm -f -- "$MSK_SCRAM_INPUT"; fi` で消す。
- `ops/oss/up.sh:45` の trap は 1 行の文字列で、`TF_AWS_CONFIG` / `NAUTOBOT_CTX` / `ROLL_PLAN` だけ消す（`ensure_msk_scram_secret` は OSS 版では呼ばれないので `MSK_SCRAM_INPUT` は無くてよい）。`:650-653` は `exec aws ssm start-session` の前に `trap - EXIT` して同じ 2 つを手で消す。
- 呼び出し元は 8 か所。`ops/up.sh:921,922,928,1038`、`ops/oss/up.sh:342,343,344,347,404,467,472`。
- テスト。`tests/test_oss_ops.py:876-893` は偽の `aws` で `ensure_secret` を 2 回打ち、`:893` `check("ensure_secret: 値を書いた一時ファイルを残さない", not [f for f in os.listdir(TMP) if f.startswith("nwc-secret.")])`。`:956-966` は `FAKE_SM_CREATE_SIGNAL`（偽の `aws` が `create-secret` の最中に親 shell と自分へ SIGINT / SIGTERM を送る。`:232-235`）で `ops/up.sh` の `on_exit` を正規表現で抜き出して組み込み、一時ファイルが残らないことを見る。**`ssm put-parameter` 側（`:167`）には同じ割り込みの仕掛けが無い。** `tests/test_analytics.py:261,273-275` は `'--cli-input-json "file://$input"' in up` と `"umask 077" in up` を字面で見る。

**B. ensure_topics**

- `app/spark/snmp_sinks.py:921-946`。`missing` を計算し、`createTopics(new).all().get()` が `TopicExistsException` なら握って `return missing`。`:1010-1011` の `main` が `made = ensure_topics(...)` を受けて `log("トピック: … （作った: …）" / "（全部あった）")` と出す。3 本のジョブ（splunk / http / iceberg）が同時に起きると、作っていないジョブも「作った: logs, flows」と出す。ずれるのは文言だけ。
- `createTopics(new).all()` は全部まとめた future なので、一部だけ TopicExists のときに「どれが作れたか」は `.all()` では分からない。`createTopics(new).values()` が `Map<String, KafkaFuture<Void>>` を返し、トピックごとに `get()` できる（Kafka の AdminClient の API。py4j 経由で `values().entrySet()` を回せる）。
- テスト。`tests/test_analytics.py:666-679`。`_Admin` の偽物は `createTopics` が `err` を持つと `.all().get()` で例外を投げる作り（`:674` の期待は `== ["traps"]`）。`:732-733` は `main` の中の呼び順と `log(f"ACL: …")` の字面。`tests/test_oss.py:696` は `ensure_topics(ns(_jvm=jvm), "b:9098", ["metrics"])` を OSS 版の環境変数で呼び、接続の props を見る（戻り値は見ていない）。偽物は `tests/test_analytics.py:625-635` の `class _Admin`（`createTopics` が `{"all": lambda: _Fut(None, self.err)}` を返す）。`values()` を足すときは `err` を持つなら各トピックの `_Fut` が投げる形にし、「一部だけ TopicExists」のために `err` をトピック名→例外の dict でも受けられるようにする。

**C. netops の grep**

- 019 の受け入れ条件（`docs/cycles/019-rename-netops-to-nwc/design.md:77`）: `grep -rli netops --exclude-dir=.git --exclude-dir=.terraform --exclude-dir=.venv . | grep -v -e '^./docs/cycles/' -e '^./docs/verification/'` の出力が `IaC/terraform/aws-managed/pipeline/analytics/tables.tf` と `tests/test_analytics.py` の 2 本だけ。残ってよいのは `tables.tf:54-61` の `moved` ブロックの `from = aws_s3tables_namespace.netops[0]` / `to = aws_s3tables_namespace.netops` / `from = aws_s3tables_namespace.netops` の 3 行と、`tests/test_analytics.py:2225-2226` のそれを見る検査の 2 行（合わせて 5 行）。
- **`git ls-files` を対象にすると 3 本になる。** `IaC/terraform/oss/pipeline/analytics/tables.tf` は aws-managed の同名ファイルへのシンボリックリンク（git の mode 120000）で、`grep -r` はリンクを辿らないが `git ls-files` には出て `grep -l` が中身を読む。2026-10-09 の実測（origin/main 78dac1a）: `git ls-files -z | grep -z -v -e '^docs/cycles/' -e '^docs/verification/' | xargs -0 grep -l -i netops -- | sort` → `IaC/terraform/aws-managed/pipeline/analytics/tables.tf` / `IaC/terraform/oss/pipeline/analytics/tables.tf` / `tests/test_analytics.py` の 3 本。`grep -c -i netops` は tables.tf 3 行、test_analytics.py 2 行。
- `ops/check.sh` は `1. terraform fmt` → `2. validate` → `3. スクリプトの構文` → `4. 模擬テスト` の 4 段。`tests/test_oss.py:1940-1948` が `SH=$(git ls-files '*.sh')` / `find … -name '*.py'` / `for t in tests/test_*.py; do` の字面を、`tests/test_analytics.py:1886` が `find … app …` を見る。段を足しても壊れない。
- `grep -rli` はディレクトリ全体を歩くので `.venv` / `.terraform` / `.claude/worktrees` 相当を除く必要があり、`git ls-files` で追跡ファイルだけを対象にすれば除外リストが要らない（`3.` が同じ理由で `git ls-files` を使っている）。macOS の `/usr/bin/grep`（BSD grep 2.6.0）は `-z` を受ける（2026-10-09 に `printf 'a\0b\0' | /usr/bin/grep -z -v '^a'` で確認）。

**D. `_s83` の検査**

- `tests/test_stream.py:557-568`。`_s83 = _between(_up_sh, "\n# ---- 8-3. Web ", "\n# ---- 8-5. ")` を取り、`re.match(r'\n# ---- 8-3\. Web -*\nif \[ -z "\$SKIP_STREAM" \] \|\| [^\n]*; then\n', _s83)`、`run_on_instance … systemctl restart $PREFIX-web.service; $WEB_ACTIVE` の行、`_s83.count("\nfi\n") == 1` の 3 つ。
- 現物の `ops/up.sh:1240-1245`:
  ```
  # ---- 8-3. Web ---------------------------------------------------------------------
  if [ -z "$SKIP_STREAM" ] || [ -z "$SKIP_GRAPH" ] || [ -n "$NAUTOBOT" ]; then
    log "8-3. Web を再起動する（起動時に SSM から Neptune のグラフの ID と Nautobot の有無を読むため）"
    run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"
    echo "Web が動いている"
  fi
  ```
- 検査の穴（016 の OC7）: (a) `if …; then` を 1 行目にせず `&&` で続ける形、(b) 字下げした `if`、(c) 呼ばない関数の中に包む形、を通す。(d) `fi` と `# ---- 8-5.` の間の空行を消すと `count("\nfi\n") == 1` が落ちる（`fi` の直後が `\n# ----` なので `\nfi\n` が無くなる）。

## 設計方針

**A. 一時ファイルはグローバルに持ち、EXIT の trap で消す（MSK_SCRAM_INPUT と同じ形）**

1. `ops/up-common.sh` に `SECRET_INPUT=""` を `ensure_secret` の直前（コメント行の上）に置く。コメントは `MSK_SCRAM_INPUT` の `:376` と同じ趣旨で 1 行（「ensure_secret / ensure_fixed_secret が値を書く一時ファイル。put-parameter の最中に止まっても ops/up.sh と ops/oss/up.sh の EXIT の trap が消す」）。
2. `ensure_secret` / `ensure_fixed_secret` の `local input rc=0` を `local rc=0` にし、`input=$(…mktemp…)` → `SECRET_INPUT=$(…mktemp…)`、`"file://$input"` → `"file://$SECRET_INPUT"`、Python の最後の引数 `"$input"` → `"$SECRET_INPUT"`、`rm -f -- "${input:?}"` → `rm -f -- "${SECRET_INPUT:?}"; SECRET_INPUT=""`。**2 つの関数で同時に開くことは無い**（直列）ので 1 変数でよい。
3. `ops/up.sh:163` の下に `if [ -n "$SECRET_INPUT" ]; then rm -f -- "$SECRET_INPUT"; fi` を足す（コメントは MSK の行に倣って 1 行）。
4. `ops/oss/up.sh:45` の 1 行の trap を、`ops/up.sh` と同じ `on_exit()` 関数にする（`TF_AWS_CONFIG` / `NAUTOBOT_CTX` / `ROLL_PLAN` / `SECRET_INPUT` の 4 つ。`GRAPH_PID` の待ちは OSS 版に無いので入れない）。`:650-653` の「`trap - EXIT` してから手で消す」は `trap - EXIT; on_exit` に置き換える（exec の前に同じ関数を 1 回呼ぶ。消し漏れていた `ROLL_PLAN` もここで消えるようになる）。
5. テスト。`tests/test_oss_ops.py` の偽 `aws` の `ssm put-parameter`（`:167`）にも `FAKE_SSM_PUT_SIGNAL` を足す（`:232-235` と同じ 4 行）。`:893` の隣に、`SECRET_SH` に `ops/up.sh` の `on_exit` を組み込んで（`:957` の `_on_exit` の抜き出しをそのまま使う）`FAKE_SSM_PUT_SIGNAL=INT` / `TERM` で打ち、`p.returncode < 0` かつ `put-parameter` が 1 回呼ばれ、SSM に `/x-nwc-oss/kafka/new-id` が**無く**、`nwc-secret.` が残らないことを見る check を SIGINT と SIGTERM の 2 つ足す。先に `on_exit` 無しで同じことをして「残る」ことを 1 回見て `build.md` に貼る（赤→緑）。`ops/oss/up.sh` の trap が `on_exit` 関数になったことは、`tests/test_oss.py` か `tests/test_oss_ops.py` の既存の `ops/oss/up.sh` を読む節に 1 check 足す（`re.search(r"^on_exit\(\) \{.*?^\}\ntrap on_exit EXIT\n", oss_up, re.S|re.M)` があり、本文に 4 変数が全部あり、`trap - EXIT\non_exit\n` が exec の前にある）。`tests/test_analytics.py:261,273-275` の字面（`"file://$input"`）は `"file://$SECRET_INPUT"` に直す。

**B. `ensure_topics` は本当に作ったものだけ返す**

1. `createTopics(new).all().get()` を `createTopics(new).values()` に替え、トピックごとに `future.get()` を回す。`TopicExistsException` のものは `made` に入れず、それ以外の例外はそのまま上げる（`close` は `finally` のまま）。戻り値は `made`（作れた順）。docstring の 1 行目を「無いトピックを作って、**作れた**名前を返す（あるものと、ほかが先に作ったものは触らない）」に直す。
2. `main` の `log` は変えない（`made` が空なら「（全部あった）」になり、それで正しい）。
3. テスト。`tests/test_analytics.py:674` の期待を `== []` にし、check の名前を「同時に作られて TopicExistsException になっても先へ進み、作ったとは言わない」にする。`_Admin` の偽物（`:625-635`）に `values()` を足す（`err` を持つなら各 future の `get()` が投げる。dict なら名前ごと）。**一部だけ TopicExists** の check を 1 つ足す（`["traps", "logs"]` を頼んで `logs` だけ既にある → 戻りは `["traps"]`、`_Admin.made == ["traps", "logs"]`）。`tests/test_oss.py:696` は戻り値を見ていないので、偽物の `values()` を足せばそのまま通るはず（通らなければ合わせる）。

**C. `ops/check.sh` に `5. 旧名 netops が戻っていない` を足す**

1. `4. 模擬テスト` のあと、`すべて通過` の前に 1 段足す。対象は `git ls-files`（追跡ファイルだけ。`.venv` / `.terraform` / `.claude` の除外が要らない）から `docs/cycles/` と `docs/verification/` を除いたもの。
   ```
   log "5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）"
   # 残ってよいのは tables.tf の moved（古い state のアドレスは字面で書くしかない。IaC/terraform/oss のはそのシンボリックリンク）と、
   # それを見る tests/test_analytics.py の 1 check だけ。moved を消したら ALLOWED と 5 行もここで直す
   HITS=$(git ls-files -z | grep -z -v -e '^docs/cycles/' -e '^docs/verification/' | xargs -0 grep -l -i netops -- 2>/dev/null | sort || true)
   ALLOWED=$'IaC/terraform/aws-managed/pipeline/analytics/tables.tf\nIaC/terraform/oss/pipeline/analytics/tables.tf\ntests/test_analytics.py'
   [ "$HITS" = "$ALLOWED" ] || die "netops が残っている（許すのは tables.tf の moved と test_analytics の check だけ）: $(printf '%s' "$HITS" | tr '\n' ' ')"
   n=$(grep -c -i netops IaC/terraform/aws-managed/pipeline/analytics/tables.tf tests/test_analytics.py | awk -F: '{s+=$2} END{print s}')
   [ "$n" -eq 5 ] || die "tables.tf と test_analytics.py の netops が 5 行でない（$n 行）。moved 3 行と check 2 行以外に増えている"
   echo "netops なし（許した 3 ファイル 5 行だけ）"
   ```
   `xargs -0 grep -l` の終了コードは「1 つも無い」で 1 になるので `|| true`。`sort` は `LC_ALL=C` を付けて順序を固定する（`ALLOWED` の並びは C ロケールの辞書順）。行数の 5 は `tables.tf:54,55,61` の 3 行 + `tests/test_analytics.py:2225-2226` の 2 行（2026-10-09 の実測。oss のリンクは同じ中身なので数えない）。
2. 冒頭のコメント（`:3-8`）に `5.` の 1 行を足す。`docs/development.md:34` は `bash ops/check.sh` を呼ぶだけで段の一覧は無いので触らない（一覧があれば 1 行足す）。
3. テスト。`tests/test_oss.py:1940-1948` の隣に `check("ops/check.sh: 5. で netops の grep を git ls-files に打ち、docs/cycles と docs/verification を除き、許すのは tables.tf と test_analytics.py", …)` を 1 つ（字面で `git ls-files` と `-e '^docs/cycles/'` と `-e '^docs/verification/'` と `tables.tf` と `tests/test_analytics.py` があること）。`ops/check.sh` を**実際に通す**のは検証 3。

**D. `_s83` は if ブロックを構文で切り出す**

1. `tests/test_stream.py:563-568` の `_s83` の 3 条件を、`_s83` 全体を 1 本の正規表現で `fullmatch` する形に置き換える。`_s83` の先頭は `\n# ---- 8-3. Web -*\n`、続けて**行頭（字下げなし）の** `if [ -z "$SKIP_STREAM" ] || … ; then\n`、本文は**2 スペース字下げの行だけ**（`(?:  [^\n]*\n)+`。空行とコメントも許す `(?:(?:  [^\n]*)?\n)+`）、その中に `  run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"\n` を含み、`fi\n` で閉じ、残りは空行だけ `\n*`。
   ```python
   _s83_re = re.compile(
       r'\n# ---- 8-3\. Web -*\n'
       r'if \[ -z "\$SKIP_STREAM" \] \|\| [^\n]*; then\n'
       r'(?:(?:  [^\n]*)?\n)*?'
       r'  run_on_instance "\$INSTANCE_ID" "systemctl restart \$PREFIX-web\.service; \$WEB_ACTIVE"\n'
       r'(?:(?:  [^\n]*)?\n)*?'
       r'fi\n\n*')
   … and _s83_re.fullmatch(_s83) is not None
   ```
   これで (a) `&&` で続ける形は 2 行目が `if` でないので落ちる、(b) 字下げした `if` は落ちる、(c) 関数で包むと `if` の前に `name() {` が来て落ちる、(d) `fi` の直後に空行が無くても `\n*` で通る。本文に字下げの無い行（`fi` 以外）が混ざっても落ちる（`if` の中に別の `fi` を置く形も `fi\n` が 2 回になって `fullmatch` が落ちる）。
2. check の名前は変えない（`check` の件数も変えない）。
3. 赤→緑。`ops/up.sh` の 8-3 を手元で (a)〜(d) の 4 通りに一時的に書き換え（commit しない）、(a)(b)(c) で**落ちる**こと、(d) で**通る**こと、元に戻して通ることを `build.md` に貼る。書き換えは `sed` で `/tmp` の写しを作って `_up_sh` の読み先を差し替えるのでもよい（テストの `_read("ops", "up.sh")` を環境変数で差し替える仕掛けは作らない。写しを `ops/up.sh` に一時的に上書きして打ち、`git checkout ops/up.sh` で戻す）。

**やらないこと。** A で `MSK_SCRAM_INPUT` と `SECRET_INPUT` を 1 変数に統合すること（`ensure_msk_scram_secret` の中で `ensure_secret` を呼ぶ構成ではなく、別々でよい）。B で `main` のログ文言の変更。C で `docs/cycles` / `docs/verification` の中の `netops` を直すこと（記録）。D で `_s75`（OSS 版 7-5）の検査の変更（既に `fullmatch`）。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `ops/up-common.sh:106-170` | `SECRET_INPUT` グローバル化（A） |
| `ops/up.sh:156-165` | `on_exit` に 1 行（A） |
| `ops/oss/up.sh:45, 650-653` | trap を `on_exit()` 関数にし、exec の前で呼ぶ（A） |
| `tests/test_oss_ops.py:167, 876-893` + `ops/oss/up.sh` を読む節 | `FAKE_SSM_PUT_SIGNAL`、割り込みの check 2 つ、oss の on_exit の check 1 つ（A） |
| `tests/test_analytics.py:261,273-275` | `"file://$SECRET_INPUT"`（A） |
| `app/spark/snmp_sinks.py:921-946` | `values()` でトピックごとに判定（B） |
| `tests/test_analytics.py:625-679` | `_Admin.values()`、`:674` の期待、一部 TopicExists の check（B） |
| `ops/check.sh` | `5.` の段とコメント（C） |
| `tests/test_oss.py:1940-1948` | check.sh の 5. の check（C） |
| `docs/development.md` | 段の一覧があれば 1 行（C） |
| `tests/test_stream.py:563-568` | `_s83_re.fullmatch`（D） |

## 再利用するもの

- `ops/up-common.sh:376-406` の `MSK_SCRAM_INPUT` の形と、`ops/up.sh:156-165` の `on_exit`（A の手本）。
- `tests/test_oss_ops.py:232-235` の `FAKE_SM_CREATE_SIGNAL` と `:956-966` の `_on_exit` の抜き出し（A のテストの手本。`ssm put-parameter` 用に写す）。
- `tests/test_stream.py:570` の `_s75` の `re.fullmatch`（D の手本）。
- `ops/check.sh:44-47` の `git ls-files` の使い方（C）。

## 実装ステップ

1 件 1 commit（A → B → C → D の順でなくてもよい。独立）。各 commit の前に関係するテストを打つ。

1. A: `up-common.sh` → `up.sh` → `oss/up.sh` → テスト（先に `on_exit` 無しで割り込みの check を打って**落ちる**ことを見る）。`bash -n ops/up.sh ops/oss/up.sh ops/up-common.sh`。
2. B: `snmp_sinks.py` → `test_analytics.py`（先に `:674` の期待だけ `[]` にして**落ちる**ことを見る）。
3. C: `check.sh` → `test_oss.py` → `ops/check.sh` を通しで 1 回。
4. D: `test_stream.py` → 4 通りの書き換えで赤→緑。
5. 全テストと `ops/check.sh` を回して `build.md` に出力を貼る。セルフレビュー（`/robust`）。

## 検証方法（期待出力まで）

1. テスト（check の件数は A で +3、B で +1、C で +1、D は ±0）。
   ```
   uv run --group dev --group web python tests/test_oss_ops.py    # 通過 199 / 失敗 0（2026-10-09 は 196。割り込み 2 + oss の on_exit 1）
   uv run --group dev --group web python tests/test_analytics.py  # 通過 514 / 失敗 0（2026-10-09 は 513）
   uv run --group dev --group web python tests/test_oss.py        # 通過 174 / 失敗 0（2026-10-09 は 173）
   uv run --group dev --group web python tests/test_stream.py     # 通過 106 / 失敗 0（2026-10-09 と同じ）
   ```
2. A の割り込み。`tests/test_oss_ops.py` の新しい 2 check が、`ops/up.sh` の `on_exit` を外した状態（`SECRET_SH` の組み込みを消す）で**失敗**し、入れると通る。出力を `build.md` に貼る。`grep -c 'SECRET_INPUT' ops/up-common.sh ops/up.sh ops/oss/up.sh` が **9 以上 / 1 / 1**（up-common は宣言 1 + 2 関数 × 4 か所）。`grep -n 'local input' ops/up-common.sh` が 0 行。
3. C の check.sh。`bash ops/check.sh` の最後が `すべて通過` で、その上に `netops なし（許した 3 ファイル 5 行だけ）`。わざと `README.md` に `netops` を 1 行足して打つと `!! netops が残っている … README.md` で止まる（戻す）。`tables.tf` の moved に `netops` を 1 行足して打つと `5 行でない（6 行）` で止まる（戻す）。
4. D の 4 通り。(a) `if …; then` を `[ -z "$SKIP_STREAM" ] || … && {` の形に、(b) `if` を 2 スペース字下げ、(c) `s83() {` で包んで呼ばない、(d) `fi` の直後の空行を消す。(a)(b)(c) で `test_stream.py` のその check が**失敗 1**、(d) と元の形で**失敗 0**。
5. B の挙動。`tests/test_analytics.py` の「同時に作られて TopicExistsException になっても先へ進み、作ったとは言わない」と「一部だけ TopicExists」の 2 check が通る。`main` の `log` の字面は変えていない（`git diff app/spark/snmp_sinks.py` に `log(` の行が無い）。
6. 構文。`bash -n ops/up.sh ops/oss/up.sh ops/up-common.sh ops/check.sh` が 0。`ops/check.sh` の `3.` も同じことをする。
7. AWS では確かめない（A は `ops/up.sh` を AWS で打つときに Ctrl+C で確かめるのが本筋だが、偽の `aws` の割り込みで代える。次に AWS で立てるときの手順には入れない）。

## 未確定事項とリスク

1. **`createTopics(...).values()` の py4j での回し方は未確認。** `values()` は Java の `Map<String, KafkaFuture<Void>>`。py4j では `m.entrySet().iterator()` か `for t in missing: m.get(t).get()` で回せるはず（キーは頼んだトピック名なので後者が簡単）。`get()` が `TopicExistsException` を含む `ExecutionException` を投げるのは `.all().get()` と同じ形（文字列に `TopicExistsException` が入る）。実機（MSK / compose の Kafka）では確かめない。偽物の `_Admin` で形だけ合わせる。
2. **Linux（WSL）の GNU grep でも同じ形で動くはず。** `-z` は GNU 由来で BSD にもある。`xargs -0` と `sort` は両方にある。WSL では未確認（ユーザーの環境）。
3. **`ops/check.sh` の `5.` の「3 ファイル 5 行」は固定値。** `tables.tf` の moved を将来消したら（namespace の state を作り直す運用に変えたら）この数を直す。コメントに書いてある。
4. **A で `ensure_secret` の `local input` を消すので、`set -u` の下で `SECRET_INPUT` が未定義になる経路は無いか。** `up-common.sh` の読み込みで `SECRET_INPUT=""` が必ず先に通る（`ops/up.sh:152`、`ops/oss/up.sh:38` の `. ops/up-common.sh`）。テストの `SECRET_SH` も `. ops/up-common.sh` を通る。
5. **D の正規表現は本文を 2 スペース字下げに固定する。** 8-3 の中に 4 スペースのネストを足しても `  [^\n]*` で通る（先頭 2 スペースがあればよい）。タブ字下げには対応しない（このリポジトリの .sh はスペース）。
