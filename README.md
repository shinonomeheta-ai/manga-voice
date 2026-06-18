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

### APIキー無しで確認する（課金前チェック）

```bash
# script.json と characters.json の整合を事前検査（未割当の声・無効タグ・文字数超過など）
python -m src.cli validate

# 実APIを呼ばず、合成計画を output/dryrun_*.json に書き出す（ElevenLabs課金なし）
python -m src.cli synth --dry-run --dialogue
```

`fetch-notion` / `analyze --notion-page` は Notion トークンのみ、`validate` と
`synth --dry-run` は **APIキー無し** で動きます（必要なキーだけコマンド単位で要求）。

### テスト

```bash
pip install pytest
pytest -q          # API不要のオフラインテスト
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

## マルチエージェント・パイプライン（シナリオ→作画→音声）

将来的に「シナリオ作成」「作画」「音声作成」をエージェントとして連結するための土台です。
各エージェントは `runs/<id>/` に成果物を出し、ステージ間は **バージョン付きJSON契約**
（`schemas/`）で受け渡します。要所に **人間の承認ゲート** が入り、停止→確認→再開できます。

```
[シナリオ作成] →(承認)→ [作画] →(承認)→ [解析:話者/感情] →(承認)→ [ボイス割当] →(承認)→ [音声合成]
   scenario          art           analyze              cast            synth
```

- **連携方式**: `runs/<id>/` の成果物 + `schemas/*.json` 契約（言語非依存・差し替え容易）
- **人間ゲート**: scenario / art / analyze / cast の各完了後に承認待ちで停止
- **状態管理**: `runs/<id>/state.json` に各ステージ状態とゲート承認を保存（再開可能）
- **エージェント**:
  - `scenario` … 人間が書いた前提（`runs/<id>/scenario/premise.txt`）から台本を生成（Anthropic）
  - `art` … `manual`（人間が `runs/<id>/art/pages/` にページ画像を配置, 既定）/ `auto`（画像生成=未実装stub）
  - `analyze` / `cast` / `synth` … 既存の音声処理をラップ（`synth` は `--dry-run` で課金なし）

### 使い方

```bash
# 1) run を作成（前提を渡すとシナリオ生成まで自動で進む）
python -m src.cli pipeline new --id demo --premise "理系コメディ。博士と助手のやりとり"

# 2) パイプラインを進める（各ゲート/素材待ちで自動停止）
python -m src.cli pipeline run --run demo

# 3) 進捗を見る
python -m src.cli pipeline status --run demo

# 4) 確認したらゲートを承認して続行（差戻しは reject）
python -m src.cli pipeline approve scenario --run demo
python -m src.cli pipeline run --run demo

# 全ゲートを自動承認して一気通貫（テスト/全自動）。--dry-run で合成は課金なし
python -m src.cli pipeline run --run demo --auto-approve --dry-run
```

作画は `runs/<id>/art/pages/` に画像を置いてから `pipeline run` を再実行 → `approve art`。
契約スキーマは `schemas/` を参照。

## キャラクター設定資料で精度を上げる（キャラバイブル）

`assets/characters/` にキャラの**顔リファレンス画像**と**プロフィール**を置いておくと、
解析（`analyze`/`run`）の精度が上がります。特に「漫画コマで誰が喋っているか」の
**話者同定**に効き、感情・口調・声の割当もキャラに沿って安定します。

```
assets/characters/
├─ 太郎.png   # 顔リファレンス（.png/.jpg/...）
├─ 太郎.md    # プロフィール（性格・口調・声質。.md/.txt）
├─ 花子.png
└─ 花子.md
```

- ファイル名の stem（拡張子を除く部分）＝キャラ名。漫画中の話者名と揃えると効果的。
- 画像だけ／プロフィールだけでも可。プロフィールは `characters.json` の
  `description` ともマージされます。
- 解析時、これらは漫画ページより**前**に「キャラクター設定資料」として渡され、
  Claude はまず顔と名前を対応づけてから各コマの話者を判定します。
- 既定ディレクトリは `assets/characters/`。`--assets <dir>` で変更可能。

詳しくは `assets/characters/README.md` を参照。

## Notion から取り込む

漫画を Notion ページで管理している場合、ページの画像とテキストを直接取り込めます。

1. **インテグレーション作成**：https://www.notion.so/my-integrations で内部インテグレーションを
   作成しトークン（`ntn_...`）を取得。`.env` に `NOTION_TOKEN=...` を設定。
2. **ページを共有**：対象ページの「・・・ → 接続 (Connections)」でそのインテグレーションを追加。
3. **取り込み**：

```bash
# Notion ページを inputs/ に展開（画像はDL、テキストは .txt 化）
python -m src.cli fetch-notion --page "https://www.notion.so/....."

# 取り込み + 解析 + 合成 まで一気通貫
python -m src.cli run --notion-page "https://www.notion.so/....." --dialogue
```

> Notion の画像URLは約1時間で失効するため、取り込みは実行時にその場でダウンロードします。
> 画像の出現位置はテキスト側に `【画像: ファイル名】` として残し、コマ順を解析に伝えます。

## GitHub Actions で動かす

ローカルに環境を作らず、GitHub 上でパイプラインを実行できます。

1. **リポジトリの Secrets を登録**
   `Settings → Secrets and variables → Actions → New repository secret` で2つ登録:
   - `ANTHROPIC_API_KEY`
   - `ELEVENLABS_API_KEY`
   - `NOTION_TOKEN`（Notion取り込みを使う場合のみ）
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
