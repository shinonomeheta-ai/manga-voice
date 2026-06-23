"""自動シナリオ生成のプロンプト組み立て＋出力フォーマット整合のオフラインテスト。

実APIは呼ばない。生成フォーマットが scenario_ingest.parse_neme で解析できることを
固定サンプルで保証し、生成→音声化の連結が壊れていないかを担保する。
"""
from src import scenario_gen as sg
from src.scenario_ingest import parse_script


def test_load_kit_and_build_system_have_settings_and_spec():
    kit = sg.load_kit()  # 既定キット(assets/pachinko-manga)
    assert "ななし" in kit and "桐原" in kit          # キャラ設定が入る
    assert "制作の鉄則" in kit                          # 鉄則(CLAUDE.md)が入る
    system = sg.build_system(kit)
    assert "読み物" in system                           # 読み物フォーマット指定
    assert "監修メモ" in system
    assert "教則化しない" in system                     # 鉄則が効いている


def test_build_user_embeds_material_and_checkpoints():
    user = sg.build_user("遠隔操作の疑い事件メモ", ["あかりが大負けする", "桐原が新聞記事を出す"])
    assert "遠隔操作の疑い事件メモ" in user
    assert "チェックポイント" in user
    assert "あかりが大負けする" in user and "桐原が新聞記事を出す" in user


SAMPLE_SCRIPT = """# 第01話「テスト回」

**ログライン**: あかりの早合点から事件を辿る話。
**登場キャラ**: ななし / あかり / 桐原

---

## ① ツカミ
ホールの隅で肩を落とすあかり。
あかり「絶対これ遠隔だって！」
ななし「また始まった……」

## ④ 解説
桐原「これは昔、実際にあった話でね。」

---

## 監修メモ
- モデルにした実在事件: 遠隔操作疑惑
- 要・事実確認: 年号
"""


def test_readable_format_parses_with_parse_script():
    """読み物形式(話者「…」＋地の文)が parse_script で話者×セリフに変換できる。"""
    sc = parse_script(SAMPLE_SCRIPT)
    assert sc["title"] == "テスト回"
    lines = [(ln["speaker"], ln["text"]) for s in sc["scenes"] for ln in s["lines"]]
    assert ("あかり", "絶対これ遠隔だって！") in lines
    assert ("ななし", "また始まった……") in lines
    assert ("桐原", "これは昔、実際にあった話でね。") in lines
    assert ("ナレーター", "ホールの隅で肩を落とすあかり。") in lines   # 地の文はナレーター
    assert all("遠隔操作疑惑" not in t for _, t in lines)             # 監修メモは除外
