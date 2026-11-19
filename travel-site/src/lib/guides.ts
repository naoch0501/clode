import { getCollection } from 'astro:content';

export async function getPublishedGuides() {
  const all = await getCollection('guides', ({ data }) => !data.draft);
  return all.sort(
    (a, b) =>
      (b.data.updatedDate ?? b.data.pubDate).valueOf() -
      (a.data.updatedDate ?? a.data.pubDate).valueOf(),
  );
}

export const formatDate = (d: Date) =>
  d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
