# パイプライン契約スキーマ

エージェント間の受け渡しを固定する JSON Schema（バージョン付き）。各エージェントは
前段の成果物をこの契約として読み、自分の成果物をこの契約で書きます。これにより
エージェントの実装言語や中身に依存せず差し替えできます。

| ステージ | 出力ファイル(run_dir相対) | スキーマ |
|---|---|---|
| scenario | `scenario/scenario.json` | [scenario.schema.json](scenario.schema.json) |
| art | `art/art.json` | [art.schema.json](art.schema.json) |
| analyze | `voice/script.json` | [script.schema.json](script.schema.json) |
| cast | `voice/cast.json` | [cast.schema.json](cast.schema.json) |
| synth | `voice/voice_output.json` | [voice_output.schema.json](voice_output.schema.json) |

- `script.json` は既存 `output/script.json`、`cast.json` は `config/characters.json` と互換。
- 互換を壊す変更をする場合は `$id` のバージョン（例 `/1.0`）を上げ、旧版の読み取りを残すこと。
