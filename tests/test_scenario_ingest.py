from src.scenario_ingest import parse_neme

NEME = """# 第03話「釘師という職人」

## ログライン
（一人の釘師の生き様を通じて、消えゆく職人技と店の盛衰を描く）

## 今回の題材
実話ベース。固有名詞はモデル化。

## 登場人物
- 語り手:（元店長の老人）
- 当事者:（タナカ / 釘師 / 寡黙で頑固な職人）

## 構成（起承転結）
- 起:（朝の開店前）

## ページ別ネーム指示

### P1
- コマ1:【画】（早朝、誰もいないホール）／【ナレ】「その日も、男は誰より早く来た」
- コマ2:【画】釘を見つめる手元のアップ ／【セリフ・タナカ】「……まだ甘いな」

### P2
- コマ1:【画】常連客が並ぶ開店前の外観 ／【セリフ・老人】「あの頃は、行列が当たり前でね」

## 監修メモ
- 脚色した点: 心情描写
"""


def test_parse_title_and_logline():
    scenario, _ = parse_neme(NEME)
    assert scenario["title"] == "釘師という職人"
    assert "釘師" in scenario["logline"]


def test_parse_characters():
    scenario, _ = parse_neme(NEME)
    names = [c["name"] for c in scenario["characters"]]
    assert "元店長の老人" in names
    assert "タナカ" in names  # （タナカ / 釘師 / ...）の先頭


def test_parse_scenes_and_speakers():
    scenario, _ = parse_neme(NEME)
    scenes = scenario["scenes"]
    assert [s["id"] for s in scenes] == ["P1", "P2"]
    p1 = scenes[0]["lines"]
    assert p1[0]["speaker"] == "ナレーター"
    assert p1[0]["text"] == "その日も、男は誰より早く来た"
    assert p1[1]["speaker"] == "タナカ"        # 【セリフ・タナカ】
    assert p1[1]["text"] == "……まだ甘いな"
    assert scenes[1]["lines"][0]["speaker"] == "老人"


def test_art_brief_panels():
    _, art = parse_neme(NEME)
    assert [p["page"] for p in art] == [1, 2]
    p1_panels = art[0]["panels"]
    assert any("ホール" in pan["art"] for pan in p1_panels)
    assert len(p1_panels) == 2  # 2コマとも【画】あり


def test_seriful_without_name_speaker_empty():
    md = "## ページ別ネーム指示\n### P1\n- コマ1:【セリフ】「誰かが言った」\n"
    scenario, _ = parse_neme(md)
    line = scenario["scenes"][0]["lines"][0]
    assert line["speaker"] == "" and line["text"] == "誰かが言った"
