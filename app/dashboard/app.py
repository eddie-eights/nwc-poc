"""チャット Web（Gradio）。127.0.0.1 だけで待ち受け、利用者は SSM のポートフォワーディングで開く。

このファイルは画面の組み立てだけ。中身は 4 つのモジュールに分けてある:
  config.py         環境変数（.env / systemd）の読み出しと、app/agentcore/ のモジュールへのパス通し
  chat.py           「チャット」タブ。質問を AgentCore Runtime に送る
  topology_view.py  「トポロジ」タブ。SVG の図・機器の表・Neptune でのリンク編集
  incident_view.py  「承認」タブ。S3 Tables の proposal_events（Athena で読む）と、決定のキューへの送信

app/agentcore/ の topology.py / graph.py / proposals.py / toolkit.py をそのまま同じディレクトリに置いて import する
（IaC/terraform/aws-managed/base/core の出力 upload_web_command が app/dashboard/*.py と一緒に S3 へ上げる）。
依存（gradio / boto3 / pyyaml）は S3 に置いた wheel から入れる（IaC/terraform/aws-managed/base/core の user_data）。インターネットには出ない。
"""

import gradio as gr
import pandas as pd

import chat
import incident_view as iv
import topology_view as tv
from config import MAX_PROMPT, PORT, TITLE

with gr.Blocks(title=f"{TITLE} チャット") as demo:
    gr.Markdown(f"## {TITLE} チャット")
    with gr.Tab("チャット"):
        session = gr.State("")
        chatbot = gr.Chatbot(type="messages", height=480, label="会話")
        with gr.Row():
            box = gr.Textbox(placeholder="質問を入力（Enter で送信、Shift+Enter で改行）", max_length=MAX_PROMPT,
                             show_label=False, scale=6, lines=2)
            send = gr.Button("送信", variant="primary", scale=1)
            reset = gr.Button("新しい会話", scale=1)
        box.submit(chat.respond, [box, chatbot, session], [box, chatbot, session])
        send.click(chat.respond, [box, chatbot, session], [box, chatbot, session])
        reset.click(chat.new_session, [chatbot, session], [chatbot, session])
        gr.Markdown("機器の一覧・接続・停止時の影響は、エージェントがトポロジのツールで調べて答えます（例: `dc1-a-leaf-01 の接続先は` / `dc1-spine-02 が落ちたら` / `dc1-a-leaf-01 の BGP のセッションは`）。")
    with gr.Tab("トポロジ"):
        topo_html = gr.HTML(tv.topology_svg())
        topo_table = gr.Dataframe(tv.device_table(), interactive=False, label="機器")
        layer_table = gr.Dataframe(tv.layer_table(), interactive=False, label="IP 層と EVPN・BGP 層（IS-IS の隣接 / iBGP EVPN のセッション / EVI / Ethernet Segment。下の層の ID で物理層とつながる。gNMI の検知で DOWN になる）")
        topo_refresh = gr.Button("再読み込み")
        with gr.Accordion("リンクを編集（IaC/terraform/aws-managed/pipeline/graph がある間だけ。Nautobot があれば Nautobot に書き、Nautobot の Job が Neptune に反映する）", open=False):
            edit_msg = gr.Markdown(tv.edit_note())
            gr.Markdown("**静的データを投入** = Neptune の中身をいったん全部消して、`app/agentcore/data/` の 7 台・12 本（と IP 層・EVPN 層）に戻す（初回と、編集をやり直したいとき）。"
                        "機器の追加・削除はこの画面にはないので `app/agentcore/data/` を直して投入し直す。リンクは下で 1 本ずつ足す・消す。"
                        "変えた内容はエージェントの次の質問から効く。"
                        "Nautobot があるあいだは投入は使えず、リンクの追加・削除は Nautobot に書く（数秒〜十数秒あとに Neptune に出る）。")
            with gr.Row():
                seed_btn = gr.Button("静的データを投入（Neptune を消して 7 台・12 本に戻す）", interactive=tv.can_seed())
            gr.Markdown("#### リンクを追加")
            with gr.Row():
                la = gr.Dropdown(tv.device_choices(), value=None, label="機器 A", scale=2)
                lai = gr.Dropdown([], value=None, label="A のインタフェース", allow_custom_value=True, scale=2,
                                  info="機器 A を選ぶと使用中の名前が出る。新しい名前（ethernet-1/3 など）も打てる")
                lb = gr.Dropdown(tv.device_choices(), value=None, label="機器 B", scale=2)
                lbi = gr.Dropdown([], value=None, label="B のインタフェース", allow_custom_value=True, scale=2,
                                  info="機器 B を選ぶと使用中の名前が出る。新しい名前も打てる")
            with gr.Row():
                lkind = gr.Dropdown([("fabric（Spine - Leaf）", "fabric"), ("l2（TRex - Leaf など）", "l2"), ("mgmt（管理）", "mgmt")],
                                    value="fabric", label="種別", info="Nautobot があるときは使われない（両端の役割と LAG から決まる）")
                lrole = gr.Dropdown([("なし", ""), ("primary（主回線。図は実線）", "primary"), ("secondary（副回線。図は破線）", "secondary")],
                                    value="", label="役割")
                lbw = gr.Number(label="帯域 Mbps", precision=0, info="空でもよい。1000 以上は図で太線")
                add_btn = gr.Button("リンクを追加", variant="primary", interactive=tv.can_edit())
            gr.Markdown("#### リンクを削除")
            with gr.Row():
                del_sel = gr.Dropdown(tv.link_choices(), value=None, label="削除するリンク", scale=4,
                                      info="「機器 A の IF - 機器 B の IF [種別 役割]」。追加・削除・再読み込みのたびに更新")
                del_btn = gr.Button("選んだリンクを削除", variant="stop", interactive=tv.can_edit())
            edit_out = [edit_msg, topo_html, topo_table, layer_table, la, lb, del_sel]
            la.change(tv.interface_choices, [la], [lai])
            lb.change(tv.interface_choices, [lb], [lbi])
            seed_btn.click(tv.seed_graph, [la, lb], edit_out)
            add_btn.click(tv.add_link, [la, lai, lb, lbi, lkind, lrole, lbw], edit_out)
            del_btn.click(tv.remove_link, [del_sel, la, lb], edit_out)
        topo_refresh.click(tv.refresh_topology, [la, lb], [topo_html, topo_table, layer_table, la, lb, del_sel])
    with gr.Tab("承認"):
        with gr.Row():
            pr_status = gr.Radio(iv.status_choices(iv.PROPOSAL_STATUS_JA), value="pending", label="状態", scale=4)
            pr_refresh = gr.Button("更新", scale=1)
        pr_msg = gr.Markdown()
        pr_table = gr.Dataframe(pd.DataFrame(columns=iv.PROPOSAL_COLS), interactive=False, wrap=True, column_widths=iv.PROPOSAL_WIDTHS,
                                label="修復案（ワーカーが S3 Tables に書いたもの。行を押すと下で選ばれる。長い列は折り返し。全文は下の「詳細」）")
        with gr.Row():
            pr_id = gr.Dropdown([], value=None, allow_custom_value=True, scale=4,
                                label="proposal_id（表の行を押すか、ここで選ぶ。選ぶと下に全文が出る）")
        pr_detail = gr.Markdown(label="詳細")
        # 承認の手順を上から順に並べる（①名前 → ②チェック → ③ボタン）。チェックは承認ボタンの真上に置き、
        # 名前とチェックがそろうまで承認ボタンを押せなくする（チェックが横に並んでいると気づかれず、押しても進まなかった。2026-09-24）
        with gr.Row():
            pr_who = gr.Textbox(label="① 決める人の名前（必須。決めた人の列に残る）", max_lines=1, max_length=iv.APPROVER_MAX, scale=2)
            with gr.Column(scale=2):
                pr_ok = gr.Checkbox(label=iv.APPROVE_CHECK_LABEL, value=False)
                pr_approve = gr.Button(iv.approve_button(False, "")["value"], variant="primary", interactive=False)
            pr_reject = gr.Button("却下（名前だけで押せる）", scale=1)
        # 承認・却下の結果（足りない入力の案内も）はボタンのすぐ下に出す。表の上の pr_msg は 30 秒ごとの描き直しが件数で上書きするので、
        # そこに出すと押しても何も起きないように見える（2026-09-24。「読んだ」のチェック漏れの案内が見えなかった）
        pr_result = gr.Markdown()
        pr_out = [pr_msg, pr_table, pr_id]
        pr_refresh.click(iv.proposal_table, [pr_status], pr_out)
        pr_status.change(iv.proposal_table, [pr_status], pr_out)
        demo.load(iv.proposal_table, [pr_status], pr_out)
        # 行を押したら、その行の proposal_id をプルダウンに入れる。詳細と「読んだ」の外しは、下の pr_id.change がそのまま続ける
        pr_table.select(iv.select_proposal, None, [pr_id])
        pr_id.change(iv.proposal_detail, [pr_id], [pr_detail])
        # 選び直したら「読んだ」を外す（前の案で入れたチェックのまま別の案を承認させない）
        pr_id.change(lambda _: False, [pr_id], [pr_ok])
        pr_ok.change(iv.approve_button, [pr_ok, pr_who], [pr_approve])
        pr_who.change(iv.approve_button, [pr_ok, pr_who], [pr_approve])
        # 30 秒ごとに描き直す（アラートが status に届くまで 1 分前後、承認・却下が行に出るまで数秒〜20 秒なので、ボタンを押さなくても追える。
        # 読むのは Neptune のクエリ（トポロジ）と Athena のクエリ 1 本（修復案）で、開いているブラウザの数だけ）。proposal_id の選択はそのまま残す
        ticker = gr.Timer(30)
        ticker.tick(tv.redraw_topology, None, [topo_html, topo_table, layer_table])
        ticker.tick(lambda st: iv.proposal_table(st)[:2], [pr_status], [pr_msg, pr_table])
        pr_approve.click(lambda i, s, w, ok: iv.decide_proposal(i, "approved", s, w, ok), [pr_id, pr_status, pr_who, pr_ok],
                          [pr_result, pr_table, pr_id])
        pr_reject.click(lambda i, s, w, ok: iv.decide_proposal(i, "rejected", s, w, ok), [pr_id, pr_status, pr_who, pr_ok],
                          [pr_result, pr_table, pr_id])
        gr.Markdown("修復案は Temporal のワークフロー（IaC/terraform/aws-managed/workflow の ECS Fargate のワーカー）が出し、承認を待っています。"
                    "承認すると同じワークフローが lab EC2 で `sudo lab <コマンド>` を打ち（EC2 への入口は SSM Run Command。SSH は開けていない）、"
                    "アラートの解消（resolved）が届いたら「復旧を確認」にします（verify_timeout_seconds のあいだに届かなければ「失敗」）。却下は何もしません。"
                    "承認には名前と「詳細を読んだ」のチェックが要ります。打つ前にアラートが解消していれば、打たずに「不要（先に解消）」にします。"
                    "いまの異常はトポロジのタブの状態（DOWN / ALARM）、アラートの履歴は Grafana / Splunk で見ます。"
                    "承認待ちのまま 2 時間（approval_timeout_minutes）で「期限切れ」になります。")

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=4).launch(
        server_name="127.0.0.1", server_port=PORT, share=False, show_api=False, quiet=True,
    )
