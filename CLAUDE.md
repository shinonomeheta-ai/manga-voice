# manga-voice — プロジェクトガイド（Claude Code 用）

> 別マシン/別セッションでもこのファイルが起動時に読まれます。まずこれと
> [docs/HANDOFF.md](docs/HANDOFF.md)（詳細な状況・決定・次の一手）を読んでから作業すること。

## 何のプロジェクトか
漫画/シナリオから **ElevenLabs v3** でキャラに沿った感情ボイスを自動生成する CLI。
さらに将来の **マルチエージェント（シナリオ→作画→音声）** 基盤、テキスト→音声の単発
コマンド `speak`、友達と共有する **Streamlit Webアプリ** を備える。
- GitHub: `shinonomeheta-ai/manga-voice`（private）。push ごとに CI(pytest)。

## セットアップ / よく使うコマンド
- 依存: `pip install -r requirements.txt`
- テスト: `pytest -q`（**全オフライン・APIキー不要**。変更したら必ず通す）
- CLI: `python -m src.cli <command>` … `analyze` `cast` `synth` `speak` `validate` `pipeline ...`
- Webアプリ: `streamlit run streamlit_app.py`
- 単発音声: `python -m src.cli speak "本文" --voice <id> --preset natural`

## 規約（重要・必ず守る）
- **オフラインファースト**: API依存(anthropic/elevenlabs)は関数内で遅延import。キーは
  `src/config.py` の `require_anthropic/elevenlabs/notion` でコマンド単位に要求。テストは全部APIなしで通る。
- **秘密情報をコミットしない**: `.env` / `.streamlit/secrets.toml` は `.gitignore` 済み。
- **CIを緑に保つ**: push 前に `pytest -q` と `python -m compileall -q src`。
- コミット/プッシュは**ユーザー承認時のみ**。コミットメッセージ末尾に
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- このプロジェクトの慣行: 機能を足したら必ずオフラインテストを追加し、コミット→push→CI確認まで。

## 構成
- `src/` … CLIエンジン。`analyze/voices/tts/assemble/audio_fx`、`agents/`(scenario/art/voice)、
  `pipeline/`(state/orchestrator/registry)、`scenario_ingest`(ネーム取り込み)、`works`(作品別設定)
- `streamlit_app.py` … 共有Webアプリ（共有キー＋パスワード＋文字数上限）
- `assets/characters/` … キャラバイブル(顔+プロフィール)
- `assets/pachinko-manga/` … 作品『ななしちゃん@がんばらない』のシナリオ制作キット
- `schemas/` … エージェント間のJSON契約 / `config/` … characters.json・scenario_rules.md
- `inputs/` `output/` `runs/` … 入出力（生成物・runは .gitignore）

## 環境メモ
- 開発機(Windows)では `python` が壊れたシムのことがある → **`py`** を使う。別OSなら通常 `python`/`python3`。

## 「次に何をすべき？」への即答
- ユーザーに次の作業を聞かれたら、**まず [docs/HANDOFF.md](docs/HANDOFF.md) 冒頭の「▶ 次にやること（NEXT）」を見て即答**する。
- 作業の区切り（機能完成・方針決定・コミット）ごとに、その NEXT リストを最新へ更新してから push する。

詳細（作った機能の一覧・設計判断・保留事項・現状）は **docs/HANDOFF.md** を参照。
