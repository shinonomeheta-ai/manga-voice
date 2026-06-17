"""Claude 解析用のプロンプトと v3 オーディオタグのホワイトリスト。"""
from __future__ import annotations

# eleven_v3 が解釈できる代表的オーディオタグ。Claude にはこの範囲から選ばせ、
# 誤ったタグ（モデルが無視/読み上げてしまう）を抑制する。
V3_AUDIO_TAGS: list[str] = [
    # 感情
    "[excited]", "[happy]", "[cheerful]", "[laughs]", "[laughs harder]",
    "[giggles]", "[chuckles]", "[nervous]", "[anxious]", "[hesitant]",
    "[sad]", "[sorrowful]", "[crying]", "[angry]", "[furious]", "[annoyed]",
    "[frustrated]", "[calm]", "[serious]", "[curious]", "[surprised]",
    "[shocked]", "[sarcastic]", "[mischievously]", "[warm]", "[tender]",
    # 声量・話し方
    "[whispers]", "[shouting]", "[quietly]", "[loud]",
    # 間・テンポ
    "[pause]", "[long pause]", "[rushed]", "[drawn out]", "[stammers]",
    # 非言語リアクション
    "[sigh]", "[sighs]", "[gasps]", "[gulps]", "[exhales]", "[clears throat]",
    "[breathes heavily]",
]

EMOTION_VALUES: list[str] = [
    "neutral", "happy", "excited", "sad", "angry", "nervous", "calm",
    "surprised", "fearful", "tender", "serious", "playful", "sarcastic",
]


def _bible_clause(has_bible: bool) -> str:
    if not has_bible:
        return ""
    return """
# キャラクター設定資料(キャラバイブル)
- 入力の冒頭に「キャラクター設定資料」として、各キャラの顔リファレンス画像と
  プロフィールを与える。**まず各人物の顔と名前を対応づけ**、以降の漫画コマでは
  その顔照合に基づいて speaker を一貫した名前で割り当てること。
- プロフィールの性格・口調・声質に沿って emotion / audio_tags / delivery_note を選ぶ。
- 資料に無い新規キャラが出たら、見た目から分かる名前を付けて追加してよい。
"""


def analysis_system_prompt(language: str = "ja", has_bible: bool = False) -> str:
    tags = " ".join(V3_AUDIO_TAGS)
    emotions = ", ".join(EMOTION_VALUES)
    return f"""あなたは漫画・シナリオを音声化するための解析ディレクターです。
{_bible_clause(has_bible)}
与えられた漫画のコマ画像やシナリオ台本を読み、ElevenLabs v3 (eleven_v3) で
キャラクターに沿った感情豊かな音声を生成するための構造化台本を作成します。

# 抽出ルール
- 出力言語(セリフ本文)は「{language}」を基本とする。元テキストの言語は保持する。
- 物語をシーン(scene)に分割し、各シーンを発話(line)の並びにする。
- 漫画画像の場合: コマの読み順(右上→左下、日本の漫画なら右から左)と吹き出しの順に従う。
  表情・効果線・背景・擬音から感情と状況を推測する。
- ナレーション/地の文は speaker を "ナレーター" とする。
- 心の声(モノローグ)は delivery_note に "monologue" と記し、必要なら [quietly] を付ける。

# 各 line のフィールド
- speaker: 話者名(一貫した表記で)。不明な脇役は "モブ" 等わかる名前を付ける。
- text: 実際に読み上げるセリフ本文のみ(ト書きや「（嬉しそうに）」等の演出注記は含めない)。
- emotion: 次から1つ -> {emotions}
- audio_tags: eleven_v3 用タグを **必ず次のホワイトリストからのみ** 選ぶ(0〜2個、無理に付けない):
  {tags}
- delivery_note: 演出メモ(自由記述、短く)。
- tts_text: 合成にそのまま渡す本文。audio_tags を本文の適切な位置(通常は冒頭、
  笑い等は該当箇所)に埋め込んだもの。例: "[excited] やったー！ [laughs]"

# 重要
- audio_tags はホワイトリスト外の文字列を絶対に作らない。
- 感情はまず emotion で表し、過剰なタグ付けは避ける(自然さ優先)。
- tts_text には演出注記の地の文を絶対に含めない(読み上げられてしまうため)。

必ず record_script ツールを使って構造化データとして返すこと。"""


# tool use 用 JSON Schema。構造化出力を強制する。
SCRIPT_TOOL = {
    "name": "record_script",
    "description": "解析した音声化台本を構造化データとして記録する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "作品/シーンのタイトル(任意)"},
            "language": {"type": "string", "description": "セリフ本文の主言語コード(例 ja)"},
            "characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "登場話者名の一覧",
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "speaker": {"type": "string"},
                                    "text": {"type": "string"},
                                    "emotion": {"type": "string", "enum": EMOTION_VALUES},
                                    "audio_tags": {
                                        "type": "array",
                                        "items": {"type": "string", "enum": V3_AUDIO_TAGS},
                                    },
                                    "delivery_note": {"type": "string"},
                                    "tts_text": {"type": "string"},
                                },
                                "required": ["speaker", "text", "emotion", "tts_text"],
                            },
                        },
                    },
                    "required": ["id", "lines"],
                },
            },
        },
        "required": ["scenes"],
    },
}


def voice_selection_prompt(character_desc: str, candidates_block: str) -> str:
    return f"""次のキャラクターに最も合う声を、候補の中から1つ選んでください。

# キャラクター
{character_desc}

# 候補(voice_id / 名前 / 特徴)
{candidates_block}

最も合致する voice_id を1つだけ、select_voice ツールで返してください。
合致度が低くても、相対的に最善のものを必ず1つ選ぶこと。"""


VOICE_SELECT_TOOL = {
    "name": "select_voice",
    "description": "キャラに最適な ElevenLabs voice を1つ選ぶ。",
    "input_schema": {
        "type": "object",
        "properties": {
            "voice_id": {"type": "string", "description": "選んだ候補の voice_id"},
            "reason": {"type": "string", "description": "選定理由(短く)"},
        },
        "required": ["voice_id"],
    },
}
