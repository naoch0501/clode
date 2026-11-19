import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { CATEGORIES } from './site.config';

const guides = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/guides' }),
  schema: z.object({
    title: z.string(),
    // 検索結果に出る説明文（120〜155文字程度が目安）
    description: z.string(),
    category: z.enum(Object.keys(CATEGORIES) as [string, ...string[]]),
    pubDate: z.coerce.date(),
    // 情報を確認し直した日。旅行記事は鮮度が信頼に直結するので必ず更新する
    updatedDate: z.coerce.date().optional(),
    heroImage: z.string().optional(),
    heroAlt: z.string().optional(),
    // true の記事はビルドに含まれない（下書き用）
    draft: z.boolean().default(false),
  }),
});

export const collections = { guides };
