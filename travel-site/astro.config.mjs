import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import rehypeAffiliateLinks from './src/lib/rehype-affiliate-links.mjs';
import { SITE, AFFILIATE_DOMAINS } from './src/site.config.ts';

export default defineConfig({
  site: SITE.url,
  integrations: [sitemap()],
  markdown: {
    rehypePlugins: [[rehypeAffiliateLinks, { domains: AFFILIATE_DOMAINS }]],
  },
});
