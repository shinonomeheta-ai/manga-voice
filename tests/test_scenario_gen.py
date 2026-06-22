"""自動シナリオ生成のプロンプト組み立て＋出力フォーマット整合のオフラインテスト。

実APIは呼ばない。生成フォーマットが scenario_ingest.parse_neme で解析できることを
固定サンプルで保証し、生成→音声化の連結が壊れていないかを担保する。
"""
from src import scenario_gen as sg
from src.scenario_ingest import parse_neme


def test_load_kit_and_build_system_have_settings_and_spec():
    kit = sg.load_kit()  # 既定キット(assets/pachinko-manga)
    assert "ななし" in kit and "桐原" in kit          # キャラ設定が入る
    assert "制作の鉄則" in kit                          # 鉄則(CLAUDE.md)が入る
    system = sg.build_system(kit)
    assert "ページ別ネーム指示" in system               # 出力フォーマット厳守ルール
    assert "監修メモ" in system
    assert "教則化しない" in system                     # 鉄則が効いている


def test_build_user_embeds_material_and_checkpoints():
    user = sg.build_user("遠隔操作の疑い事件メモ", ["あかりが大負けする", "桐原が新聞記事を出す"])
    assert "遠隔操作の疑い事件メモ" in user
    assert "チェックポイント" in user
    assert "あかりが大負けする" in user and "桐原が新聞記事を出す" in user


SAMPLE_NEME = """# 第01話「テスト回」
## ログライン
あかりの早合点から事件を辿る話。
## 登場キャラ
- 語り手: ななし
- 起点: あかり
- 解説: 桐原
## ページ別ネーム指示
### P1（① ツカミ）
- コマ1:【画】ホールで肩を落とすあかり ／【セリフ・あかり】「絶対これ遠隔だって！」
- コマ2:【画】隣で呆れるななし ／【セリフ・ななし】「また始まった…」
### P2（④ 解説）
- コマ1:【画】事務所で資料を広げる桐原 ／【ナレ】「これは昔、実際にあった話でね。」
## 監修メモ
- モデルにした実在事件: 遠隔操作疑惑
- 要・事実確認: 年号
"""


def test_output_format_parses_back_with_neme():
    """OUTPUT_SPEC が指示する形式が、そのまま parse_neme で解析できること。"""
    scenario, art = parse_neme(SAMPLE_NEME)
    assert scenario["title"] == "テスト回"
    # 2ページ分のシーンが取れる
    assert [s["id"] for s in scenario["scenes"]] == ["P1", "P2"]
    lines = [(ln["speaker"], ln["text"]) for s in scenario["scenes"] for ln in s["lines"]]
    assert ("あかり", "絶対これ遠隔だって！") in lines
    assert ("ななし", "また始まった…") in lines
    assert ("ナレーター", "これは昔、実際にあった話でね。") in lines
    # 作画ブリーフ(画指示)も取れている
    assert art and art[0]["page"] == 1 and art[0]["panels"]
