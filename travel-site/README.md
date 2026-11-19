# 訪日旅行者向けガイドサイト（無料記事 + アフィリエイト）

地元に住む人の実体験をもとに、英語で旅行ガイドを書いて検索から集客し、ツアー・ホテル・eSIM などのアフィリエイトで収益化するサイトです。

- 構成: [Astro](https://astro.build/) 5 の静的サイト（速い・無料で公開できる・SEO に強い）
- 運用の手順は **[docs/運用ガイド.md](docs/運用ガイド.md)** を読んでください

## よく使うコマンド

```bash
cd travel-site
npm install        # 初回のみ
npm run dev        # http://localhost:4321 で確認しながら書く
npm run build      # 公開用ファイルを dist/ に作る
```

## 最初に書き換える場所

| ファイル | 内容 |
|---|---|
| `src/site.config.ts` | サイト名・地域名・公開 URL・著者名 |
| `src/pages/about.astro` | 自己紹介（信頼性に直結するので具体的に） |
| `src/pages/privacy.astro` | 問い合わせ先・アクセス解析の記載 |

## 記事の追加

`src/content/guides/` に Markdown ファイルを置くと記事になります。
`template-itinerary.md`（モデルコース）と `template-practical.md`（実用情報）をコピーして使ってください。
`draft: true` の記事は公開されません。

記事内のリンクのうち、`site.config.ts` の `AFFILIATE_DOMAINS` に含まれるドメインには自動で `rel="sponsored nofollow"` が付きます。
