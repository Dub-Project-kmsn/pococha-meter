# ポコチャ メーター予想アプリ

ポコチャのランクボーダー（メーター）を月末データから予想するWebアプリ。
GitHub Actionsで毎日自動的にデータを取得・更新します。

- データ出典: [LIVER CAMPUS](https://one-carat.com/campus/archives/category/streamer-tips/pococha-rank-border)
- 公開URL: GitHub Pagesで `https://<ユーザー名>.github.io/<リポジトリ名>/`

## ファイル構成

```
.
├── index.html              # アプリ本体
├── data.json               # ボーダーデータ（毎日自動更新）
├── scraper.py              # スクレイパー（標準ライブラリのみ使用）
├── .github/workflows/
│   └── update.yml          # 毎日02:00 JSTに実行するワークフロー
└── README.md
```

## 初回セットアップ手順

### 1. GitHubアカウント作成
https://github.com/signup から無料アカウントを作成。

### 2. リポジトリを作成
1. https://github.com/new を開く
2. Repository name: `pococha-meter`（任意）
3. **Public** を選択
4. 「Create repository」をクリック

### 3. ローカルからプッシュ
表示されたコマンドの代わりに、以下を実行（このディレクトリで）:

```bash
cd /Users/basskmsn/pococha-meter-app
git init
git branch -M main
git add .
git commit -m "initial commit"
git remote add origin https://github.com/<ユーザー名>/pococha-meter.git
git push -u origin main
```

初回プッシュ時、GitHubがパスワード代わりに「Personal Access Token」を要求します。
https://github.com/settings/tokens から「Generate new token (classic)」で `repo` 権限のトークンを発行してください。

### 4. GitHub Pages を有効化
1. リポジトリページの **Settings** → **Pages**
2. **Source** を `Deploy from a branch` にする
3. **Branch** を `main` / `/ (root)` に設定 → Save
4. 数分後、`https://<ユーザー名>.github.io/pococha-meter/` でアクセスできるようになる

### 5. GitHub Actions の動作確認
1. リポジトリページの **Actions** タブを開く
2. 「Update Pococha Border Data」ワークフローを選択
3. 右上の **Run workflow** で手動実行して動作確認
4. 以降は毎日 02:00 JST に自動実行される

## 手動でデータを更新したいとき

ローカルで:

```bash
python3 scraper.py
git add data.json
git commit -m "manual update"
git push
```

## カスタマイズ

- **実行時刻を変える**: `.github/workflows/update.yml` の `cron` を編集（UTC指定）
- **取得する月数を増やす**: `scraper.py` の `INDEX_PAGES_TO_SCAN` を増やす
- **アプリの見た目を変える**: `index.html` の `<style>` セクションを編集

## ランニングコスト

- GitHub Actions: パブリックリポジトリは **完全無料・無制限**
- GitHub Pages: パブリックリポジトリは **完全無料**
- 合計: **¥0/月**
