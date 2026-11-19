// ここを書き換えるだけでサイト全体の名前・地域・URL が変わります。
export const SITE = {
  // サイト名（仮。ドメインを取るときに決め直してOK）
  name: 'Osaka & Kyoto Local Guide',
  // 対象地域
  region: 'Osaka & Kyoto',
  tagline: 'Honest, first-hand travel tips from someone who actually lives here.',
  // 公開 URL（独自ドメインを取ったら書き換える。sitemap と canonical に使われる）
  url: 'https://example.com',
  author: {
    name: 'Your Name',
    bio: 'A local resident who has explored Osaka and Kyoto for years. Every place on this site is one I have visited myself.',
  },
  lang: 'en',
};

export const CATEGORIES = {
  itineraries: 'Itineraries',
  'things-to-do': 'Things to Do',
  food: 'Food & Drink',
  stay: 'Where to Stay',
  practical: 'Practical Tips',
} as const;

export type Category = keyof typeof CATEGORIES;

// 記事内のリンクのうち、これらのドメインを含むものは自動で
// rel="sponsored nofollow noopener" と target="_blank" が付きます。
export const AFFILIATE_DOMAINS = [
  'klook.com',
  'getyourguide.com',
  'viator.com',
  'booking.com',
  'agoda.com',
  'airalo.com',
  'tp.media',
  'travelpayouts.com',
];
