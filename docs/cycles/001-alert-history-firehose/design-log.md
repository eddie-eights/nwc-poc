# Cycle 001 alert-history-firehose 設計の経緯

## Round 0（2026-10-04、main(opus-5.5) / effort: high）

### 前提になった会話

- 「いま障害の情報はどこに書いているか」への答え: どこにも無い（2026-10-02 から「未定」）。
- Temporal のワーカーが複数のソースから集めて S3 に書く案について:
  - 開いた・閉じたの記録は WORKFLOW=1 のときしか動かず、link_down 以外の kind は starter が捨てるので、取りこぼしが出る。
  - そこで、記録は SNS ですべてのアラートを受ける Lambda graph-status に置いた。Temporal は証拠集め（collect_evidence）に回し、別のサイクルにする。
- S3 は履歴の置き場としてだけ使う。「いま開いている障害」は Neptune を見る。経路は Firehose → S3 Tables（Iceberg）。
- MSK のトピックは要らない（Lambda から Firehose の Direct PUT で送る）。
- 実装はこのセッションではやらず、エンジニアセッションに頼む（ユーザーの指示）。

### 質問と回答

| # | 質問 | 推奨 | 回答 |
|---|---|---|---|
| 1 | 1 行に何を書くか | 届いた通知をそのまま | 推奨どおり |
| 2 | 記録する kind | 全部 | 推奨どおり |
| 3 | down.sh で履歴が消えてよいか | 消えてよい | 推奨どおり |
| 4 | 読む側（Athena）をこのサイクルで作るか | 書くだけにして、読む側は次 | **Athena まで作る**（推奨とは逆） |
| 5 | テーブル名 | alert_events | 推奨どおり |
| 6 | Neptune か Firehose のどちらかが失敗したとき | 両方試してから落とす | 推奨どおり |
| 7 | s3tablescatalog の作り方と消し方 | up.sh が無ければ作り、消さない | 推奨どおり |
| 8 | query_history が返すもの | 通知の行をそのまま（event_id で重複を落とす） | 推奨どおり |
| 9 | 技術的な前提の確認 | design.md の「合意した決定」9 にまとめたとおり | 合っている |

### 却下した案

- **開いた・閉じたの変化だけを書く（Lambda で重複を落とす）。**
  - Lambda が書く前に Neptune で前の状態を読む必要があり、Neptune が読めないと記録も止まる。
  - Splunk の resolved は starts_at が解消した時刻なので、同じ障害の行どうしを Lambda の中で突き合わせる規則が要る。
- **記録を link_down だけにする。** BGP / IS-IS の落ちと trap が残らない。
- **down.sh で消えない置き場にする。** 片付けの手順と、費用を誰が持つかを別に決める必要があり、PoC には重い。
- **テーブル名を incident_events にする。** 1 行 = 1 つの障害だと読み違えやすい。
- **テーブル名を anomaly_events にする。** 2026-10-02 に消したテーブル（発生ごとに行を突き合わせる設計）と混同しやすい。
- **Firehose の失敗はログに残すだけにする。** 履歴に欠けが出る。
- **s3tablescatalog を Terraform で持つ。** アカウントで共有しているので、ある OWNER の down でほかの OWNER の Firehose と Athena まで止まる。先に誰かが作っていれば、apply が重複で落ちる。
- **query_history で障害ごとに組み立てて返す。** 組み立ての規則（Grafana と Splunk で starts_at の意味が違う）を決めてテストするぶん、サイクルが大きくなる。

## Round 1（2026-10-04、実装からの差し戻し）

実装（エンジニア2、ブランチ `cycle-001-alert-history-firehose`）から、設計が原因の指摘が 2 件あった。どちらもユーザーが推奨案を選んだ。

| # | 指摘 | 原因 | 直し方 |
|---|---|---|---|
| 1 | Firehose が失敗すると例外でやり直しになり、Neptune の status の更新まで巻き込む。up.sh で graph が analytics より先にできるあいだは、ストリームが無くて必ず失敗する | 決定 6 が「履歴を欠かさない」を status の更新より上に置いていた。立ち上げの順番を確かめていなかった | Firehose の失敗では例外を投げない。Lambda の中で 3 回まで送り直し、残りはログに行ごと書く。例外を投げるのは Neptune の失敗だけ |
| 2 | 「device_id が無い通知も記録する」は実装できない。`alerts_from_message` が `device_id` か `kind` の無い通知を先に落としている | 決定 2 を書くときに `alerts_from_message` の実物を読まず、Neptune が無視する場合と混同した | 落とすのはそのまま。WARNING をログに出し、制限として design.md に書く |

### 変えた判断

- Round 0 で却下した「Firehose の失敗はログに残すだけにする」を、送り直しと行ごとのログを足した形で採った。履歴の欠けは Firehose が止まっているあいだだけで、ログから戻せる。status が遅れるほうが害が大きい。

### 却下した案

- **Firehose の失敗をデッドレターキューに送る。** 戻す仕組みまで作ることになり、PoC には重い。
- **device_id の無い通知を、仮の id を付けて記録する。** anomaly_id が作れず、ほかの行と突き合わせられない行が増えるだけ。
