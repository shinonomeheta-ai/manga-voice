# 引き継ぎ（HANDOFF） — 状況・決定・次の一手

> 別マシン/別セッションで「続きから」作業するための引き継ぎ書。
> まず [/CLAUDE.md](../CLAUDE.md) を読み、次にここで現状を把握する。最終更新: 2026-06-20。

## ▶ 次にやること（NEXT）— 「次なにすべき？」はまずここを即答
- **済**: 共有Webアプリを **Streamlit Cloud にデプロイ済み**（リポジトリは public 化）。
  URL: https://manga-voice-kqkvw8ih63slfyoc4byquf.streamlit.app/ （APP_PASSWORDで保護）。
  キャラ別ブロックで物語を組む＋eleven_v3固定＋感情タグ挿入＋掛け合い(Text-to-Dialogue)に対応。
- **いますぐ**: 新UIを実機確認 — キャラ別にセリフブロックを足して **🔊生成**（掛け合い）→ 友達にURL＋合言葉を共有。
- **課金の安全確認**: ElevenLabs の **Auto Top Up / 使用量課金を OFF** のままにする（上限超過で自動課金されないように）。APIキー側のクレジット上限も設定済み(3万)。
- **やりたくなったら**:
  - 「各自が自分のキーを入れる」モード（あなたの課金ゼロ運用）
  - 口語変換(Claude)ボタン / 話速・スタイル調整 / `gap_ms`を実音声で微調整
  - パチンコ作品で `/scenario` から1話作成 → `pipeline --neme` で取り込み→音声化
- **保留**: API実機の本番検証一式（eleven_v3は有料プラン）。

> このリストは作業の区切りごとに更新する（増えたら上を最新の最優先に並べ替え）。

## 1. いまの全体像
漫画/シナリオ → ElevenLabs v3 ボイス生成のツール。CLI＋マルチエージェント基盤＋
共有Webアプリまで実装済み。テスト72件(全オフライン)、CI緑、すべて push 済み。

## 2. 実装済みの機能
- **音声CLI**: `analyze`(Claude Visionで話者/感情/v3タグ抽出) → `cast`(ElevenLabsボイス割当) →
  `synth`(eleven_v3 個別clip / Text-to-Dialogue) → `assemble`(連結)。`--dry-run`で課金なし計画。
- **掛け合い改善**: `assemble.py` の間を感情・話者交代・疑問→即答で動的調整(`gap_ms`)。
  `prompts.py` に「掛け合い」指針(emotionは直前発話への反応、相づち補完)。
- **単発 `speak`**: テキスト→eleven_v3→**整音(`audio_fx.py`: ffmpeg, natural/clean/warm)**→出力。
- **キャラバイブル**: `assets/characters/*.png+.md` を解析プロンプトに投入し話者同定/演技を安定化。
- **マルチエージェント基盤**: `runs/<id>/` 成果物 + `schemas/` 契約。人間ゲート4箇所
  (scenario/art/analyze/cast)で停止→`approve`→再開。`pipeline new/run/status/approve/reject/redo/works/init-work`。
  - `scenario`: premiseから生成 or **`--neme` で既成ネーム取り込み(API不要, `scenario_ingest.py`)**。
  - `art`: manual(人がpages/に画像) / auto(stub)。ネーム取り込み時は `brief.md` で作画指示提示。
  - `voice.*`: 既存処理をラップ。`--work` で作品別設定(characters/bible/rules)を切替。
- **共有Webアプリ** `streamlit_app.py`: テキスト→ボイス選択→整音プリセット→生成→再生/DL。
  共有キー(secrets)＋パスワード(APP_PASSWORD)＋文字数上限(MAX_CHARS)。Streamlit Cloud想定。

## 3. 作品『ななしちゃん@がんばらない』(確定設定)
`assets/pachinko-manga/` がシナリオ制作キット(Claude Codeの`/scenario`スキルでネーム指示書を量産)。
- パチンコ実在事件をフィクションで紹介する雑学ドキュメントコメディ。
- キャラ: **ななし**(主人公/聞き手/桐原事務所バイト) / **あかり**(起点/コメディ/自分で打つ) /
  **桐原**(元業界紙記者の探偵=解説役・ドラえもん的相談役) / **九重**(悪役) / **九頭竜**(小悪党)。
- 7パート構成: ①ツカミ(あかり)→②疑問(ななし)→③相談(ななし+桐原)→④解説(桐原)→
  ⑤事件パート(九重→九頭竜)→⑥現代に戻る→⑦オチ(あかり)。
- 鉄則: 実在事件は桐原の解説、固有名は架空名に置換／手口を教則化しない／名誉毀損回避／
  業界を一方的に悪と描かない／依存症はシリアス／7パート形式厳守。
- メインホール名は**未定**（旧案「烏啼/ウテ」はユーザー指示で不採用）。
- 注意: 別チャットの決定はリポジトリに書かないと伝わらない。ユーザーが渡す追加ファイルは
  ファイル名が文字化けする場合あり(中身は正常UTF-8) → 内容で判定して正しい名前/構成に復元する。

## 4. 保留・未確認（重要）
- **API実機の本番検証は未実施**(キー未設定運用)。analyze/cast/synth/scenario生成、Webアプリの
  実生成は ElevenLabs/Anthropic キーが要る。eleven_v3 は ElevenLabs 有料プラン必須。
- ElevenLabs SDK のレスポンス形状は防御的に実装したが初回実行で要調整の可能性。
- 整音(`audio_fx`/`assemble`)は **ffmpeg** 必須(無ければ自動でスキップ)。

## 5. 次にやること
→ 冒頭の **「▶ 次にやること（NEXT）」** を参照（常にそこを最新に保つ）。

## 6. 別マシンで「続きから」始める手順
1. `git clone https://github.com/shinonomeheta-ai/manga-voice && cd manga-voice`
2. `pip install -r requirements.txt`（必要なら `pip install pytest streamlit`）
3. そのフォルダで Claude Code を起動 → `CLAUDE.md` が自動で読まれ、このHANDOFFを参照して文脈を把握。
4. キーを使う作業なら `.env` / `.streamlit/secrets.toml` をその場で作成(gitには乗らない)。
5. 動作確認: `pytest -q`(全緑のはず) → 続きの作業へ。

> 会話の生ログ自体は同期されない。引き継ぎはこのHANDOFFと CLAUDE.md、コミット履歴で行う。
> 大きな決定をしたら、このファイルを更新してから push すること。
