# manga-voice — 漫画・シナリオ → ElevenLabs v3 自動ボイス生成

漫画のコマ画像やシナリオ台本から、登場キャラクターに沿った声で、感情まで反映した
ボイスを **ElevenLabs v3 (`eleven_v3`)** で自動生成する Python CLI です。

- **感情・話者・状況は自動抽出**：Claude の Vision/テキスト解析が、絵の表情や台詞の文脈から
  話者・感情を読み取り、v3 の[オーディオタグ](https://elevenlabs.io/docs)（`[excited]` `[whispers]` 等）に変換します。
- **キャラ→声のマッピング**：`config/characters.json` で固定割当。未登録キャラは
  ElevenLabs の共有ボイスライブラリから自動で候補を提案・補完します。
- **2種類の出力**：セリフ単位の個別clip と、シーン丸ごとの掛け合いダイアログ（Text-to-Dialogue）。

## パイプライン

```
inputs/ (漫画画像 + 台本txt)
   │  analyze   … Claude が解析
   ▼
output/script.json (話者・感情・audio_tags 付き)
   │  cast      … キャラに voice_id を割当
   ▼
config/characters.json (補完済み)
   │  synth     … eleven_v3 で個別clip
   ▼
output/clips/*.mp3
   │  assemble  … pydub で連結 / text-to-dialogue で掛け合い
   ▼
output/scenes/*.mp3
```

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env   # ANTHROPIC_API_KEY と ELEVENLABS_API_KEY を記入
```

`assemble`（clip連結）には **ffmpeg** が必要です（pydub が利用）。未インストールでも
個別clip と text-to-dialogue は動作します。

## 使い方

```bash
# 1) 解析: inputs/ の画像・txt から script.json を生成
python -m src.cli analyze

# 2) キャスティング: 未割当キャラに ElevenLabs ボイスを提案（--apply で書き戻し）
python -m src.cli cast --apply

# 3) 合成: セリフ単位の個別clipを生成（--scene で限定、--force で再生成）
python -m src.cli synth --scene scene-1

# まとめて: 解析→割当→合成→連結 を一括（--dialogue で掛け合いも生成）
python -m src.cli run --dialogue
```

主なオプション:

| オプション | 説明 |
|---|---|
| `--inputs DIR` | 入力ディレクトリ（既定 `inputs/`） |
| `--model NAME` | 解析に使う Claude モデル（既定 `claude-opus-4-8`） |
| `--scene ID` | 対象シーンを限定 |
| `--force` | 既存音声を再生成 |
| `--dialogue` | Text-to-Dialogue で掛け合い音声も生成 |
| `--format FMT` | 出力フォーマット（既定 `mp3_44100_128`） |

## GitHub Actions で動かす

ローカルに環境を作らず、GitHub 上でパイプラインを実行できます。

1. **リポジトリの Secrets を登録**
   `Settings → Secrets and variables → Actions → New repository secret` で2つ登録:
   - `ANTHROPIC_API_KEY`
   - `ELEVENLABS_API_KEY`
2. **入力素材をコミット**：`inputs/` に漫画画像や台本(.txt)を追加して push
   （`config/characters.json` に voice_id を事前記入しておくと声が安定します）。
3. **ワークフローを実行**：`Actions → Generate Voices → Run workflow`。
   - `scene` … 対象シーンID（空で全シーン）
   - `dialogue` … Text-to-Dialogue 掛け合いも生成
   - `model` … 解析に使う Claude モデル
   - `commit_casting` … 自動補完した `characters.json` をコミットして戻す
4. **成果物**：実行後の `Artifacts → manga-voice-output` から
   `script.json` と生成音声(`clips/`, `scenes/`)をダウンロード。

`.github/workflows/ci.yml` は push/PR ごとに構文チェックとモデル層のスモークテスト
（APIキー不要）を実行します。

## 注意・コスト

- `eleven_v3` は1リクエスト最大約5,000文字。Text-to-Dialogue はシーンが長い場合に分割します。
- 初回は短いテキスト1行で疎通を確認してから本番実行することを推奨します（課金前の最小確認）。
- API キーは `.env`（gitignore 済み）で管理してください。
