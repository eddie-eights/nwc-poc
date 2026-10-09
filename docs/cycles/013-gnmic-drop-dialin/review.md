## Round 1（2026-10-09）
cold review 1 回目を依頼した（opus。PM が PR #6 の 9aab83a に対して呼んだ）
# gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）: cold review 1 回目

- 対象: 9aab83a（base e5dda8b との差分）
- 正本: `docs/cycles/013-gnmic-drop-dialin/design.md`
- 1 回目なので、前回の Must fix は無い

## サマリ

Must fix は 0 件、Should fix は 0 件、Nit は 4 件。

design.md の範囲は全部、実装されている。

- **gnmic**
  - 5 つの subscription と 2 つの出力がある。
  - `gnmic.sh render` / `get` と、イメージがある。
- **Spark の読み替え**
  - `gnmic_struct` / `gnmic_message` が同じ表で読み替える。
  - `LAST_WIN` を使っている。
  - splunk のジョブにも `--device-map` を渡している。
- **Grafana / Splunk のルールとダッシュボード**
  - ルールとダッシュボードは、読み替え後の系列名（`snmp_interface_oper_up` / `admin_up` / `in_octets` / `out_error_packets`、`snmp_system_instant` / `utilization`、`slot` ラベル）と合っている。
  - この対応は、`tests/test_stream.py:316-330` が `prometheus_series` を通して縛っている。
- **IaC と ops**
  - stream / Nautobot / analytics の IaC が揃っている。
  - `ops/up.sh` と `ops/oss/up.sh` に、SG のガード、`build_gnmic`、SSM の資格情報、`gnmic_targets` の受け渡しがある。
  - down の側は、`snmp_agents` を外したうえで、既存の `delete_up_ssm_params` が旧 `/telegraf-dialin/` を消す。
- **compose**
  - gnmic のサービスがある。
  - `check.sh` に gnmi トピックの件数の検査がある。
- **lab.sh / telegraf.sh**
  - udp 161 の転送をやめている。
  - `test` / `gnmi` は、案内を出して終了コード 1 で止まる。

旧名の残りを grep した。docs 以外に残っているのは、コメントと、テストの否定の検査だけだった。

### 見た観点

- **design.md との整合**
  - 設計方針 1〜7、変更するファイルの一覧、テストの段取りを、差分と突き合わせた。
- **correctness**
  - gnmic の event を Telegraf の形へ読み替える処理を見た。対象は measurement、タグの接頭辞の除去と表引き、field 名、ns から秒への変換、deletes だけの event を捨てる処理。
  - 読み替えたあとの系列名を、Grafana のルール、Splunk のサーチ、`metrics.json` の各パネルのクエリと照らした。
- **security**
  - 資格情報が設定にも標準出力にも出ないことを見た。gnmic は `${...}` を実行時に展開する。テストは `tests/test_stream.py:398`。
  - SSM の SecureString の置き場と、SCRAM の利用者 `collectors` の共有を見た。
  - SG のガードを見た。
- **runtime bugs**
  - `ops/up.sh` と `ops/oss/up.sh` の順序を見た。build → secrets → apply → services-stable の待ち。
  - compose の gnmic のサービスを見た。host ネットワーク、`restart: on-failure:5`、`KAFKA_AUTH=none`。
  - `lab.sh forward` の iptables から udp 161 が抜けていることを見た。
- **data loss**
  - down 側で旧 `telegraf-dialin` の SSM パラメータがタグで拾われて消えることを見た。
  - SNMP のポーリングをやめる。代わりにダッシュボードと link_down が gnmic の系列を読む。
- **API compatibility**
  - Nautobot の同期先を `gnmi-targets` に替えた。`nb_map` / `nb_sync` / IaC の output がこの名前で揃っていることを見た。
  - `query_metrics` の例が新しい系列名になっていることを見た。
- **missing tests**
  - review root で `tests/test_*.py` を 1 本ずつ実行した（pytest が無いので python で直接走らせた）。
  - 全部 rc=0 だった。

| テスト | 結果 |
|---|---|
| test_alerts | 168 通過 |
| test_analytics | 513 通過 |
| test_app | 161 通過 |
| test_collectors | 79 通過 |
| test_dashboard_config | 3 通過 |
| test_graph | 78 通過 |
| test_kb_index | 7 通過 |
| test_lab_debug | 97 通過 |
| test_local_compose | 138 通過 |
| test_nautobot | 68 通過 |
| test_oss | 173 通過 |
| test_oss_ops | 196 通過 |
| test_oss_roll | 66 通過 |
| test_stream | 106 通過 |
| test_sync | 103 通過 |
| test_workflow | rc=0 |

test_workflow は、ログ（WARNING）の途中で出力が切れていて、件数は読めなかった。

### 見ていない観点

- **gnmic が実際に出す event の形**
  - 対象は、SR Linux 26.7 からの values のパス、タグのキー、64 bit カウンターが文字列で来るかどうか。
  - design.md の未確定 1 で、AWS での実測は PM の検証に回っている。コードとテストは、想定した形で縛っているだけ。
- **AWS の実行時の振る舞い**
  - 対象は、MSK の SCRAM / TLS で gnmic が実際につながるか、ECS の services-stable、Nautobot の同期が SSM に書くか。
  - AWS には触っていない。
- **`terraform validate` / `plan`**
  - review tree で `terraform init` をしない決まりなので、走らせていない。
- **手元の compose の起動**
  - docker で gnmic のイメージを build していない。design.md の検証 5 も走らせていない。

## Must fix

None

## Should fix

None

## Nit

- [design.md整合] `app/gnmic/gnmic.yaml.in:79-80`
  - **何がずれているか**: design.md:35 は、scram のときに `tls: {}` と書くとしている。実装は `tls: ca-file: /etc/ssl/certs/ca-certificates.crt` になっている。
  - **Nit にする理由**: 直上のコメント（:78）に、gnmic は ca-file 等が無いと TLS を張らないという理由が書いてあり、動作上は実装の方が正しい。ずれているのは design.md の記述の方だけ。
  - **直し方**: design.md の該当行を実装に合わせる。
- [design.md整合] `docs/cycles/013-gnmic-drop-dialin/design.md:78`, `:106`, `:120`
  - **何がずれているか**: design.md は `tools/tools.json` と書いている。リポジトリにあって実際に変えたファイルは `app/gateway/tools.json`。`tools/` というディレクトリは無い。
  - **Nit にする理由**: 中身の変更（PromQL の例）は design どおりで、ずれているのはパスの書き方だけ。
- [correctness] `app/containerlab/lab.sh:296`
  - **何がずれているか**: forward の分岐は、デバッグ用の EC2 と手元の compose の両方に当たる。コメントは、どちらにも「gNMI（手元の gnmic）」が届くと書いている。
  - **証拠**: このサイクルで gnmic を置くのは、compose（`compose.yaml` の gnmic サービス）と ECS だけ。デバッグ用の EC2 に gnmic は無い。
  - **Nit にする理由**: コメントの正確さの問題で、iptables の動作は変わらない。
- [correctness] `app/containerlab/lab.sh:290`
  - **何がずれているか**: 案内文は「gnmic の gNMI（IF の状態と IS-IS の隣接）」と書いている。gnmic が購読しているのは、IS-IS の IF の oper-state で、隣接ではない（`app/gnmic/gnmic.yaml.in:48-51` のコメントのとおり）。
  - **Nit にする理由**: 利用者に向けた表示文言のずれで、動作には影響しない。

## 良かった点

- **読み替えを 2 か所で揃えている**
  - 読み替えの表（`GNMI_MEASUREMENTS` / `GNMI_TAGS`）を、Spark の列の版（`gnmic_struct`）と Python の版（`gnmic_message`）が共有している。
  - `test_stream` が `gnmic_message` → `prometheus_series` を通し、ダッシュボードとルールが読む系列名までを 1 本の検査で縛っている（`tests/test_stream.py:316-330`）。
  - 名前のずれが入り込む余地が小さい。
- **端の入力を捨てる処理を検査している**
  - deletes だけの event、timestamp が整数でない event、キーが重なったときの後勝ち（`LAST_WIN`）を、明示的に検査している（`tests/test_stream.py:343-351`）。
- **target ごとに資格情報を書いている**
  - gnmic の全体設定の username / password が 2 つめ以降の target で崩れる挙動を避けている。
  - その理由を、gnmic v0.49.0 のソースの箇所つきでコメントに残している（`app/gnmic/gnmic.yaml.in:16-18`）。
- **SNMP_POLL を残しても壊れない**
  - `SNMP_POLL` を残した環境で up.sh を流しても、止めずに警告で済ませている。
  - 旧 SSM パラメータは、既存の一括削除がそのまま拾う。

## ユーザーへの質問

None

### PM の判断

2026-10-09。PM が 4 件とも review-013 の木で該当行を読んで再現した（読んだだけ。動かす種類の指摘ではない）。

- N1（design.md:35 の `tls: {}`）: design.md が事実と違う。実装（`tls: ca-file: /etc/ssl/certs/ca-certificates.crt`。gnmic は ca-file 等が無いと TLS を張らない）が正しいので、design.md:35 を実装に合わせて書き換える
- N2（design.md の `tools/tools.json` 2 か所）: 017 のあとのパス `app/gateway/tools.json` に書き換える
- N3（lab.sh:296 のコメント「デバッグ用の EC2 に gnmic」）: 直さない。文言だけで動作は変わらず、実装ファイルを変えると cold review 2 回目が要るので据え置き
- N4（lab.sh:290 の案内文「IS-IS の隣接」）: 直さない（N3 と同じ理由）。正しくは「IS-IS の IF の oper-state」

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261009-cycle-013-gnmic-drop-dialin-review.html -->
