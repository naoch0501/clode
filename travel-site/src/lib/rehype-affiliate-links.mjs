// Markdown 内の外部リンクを処理する小さな rehype プラグイン。
// - アフィリエイト先のドメイン: rel="sponsored nofollow noopener", 新しいタブ
// - その他の外部リンク: rel="noopener", 新しいタブ
export default function rehypeAffiliateLinks({ domains = [] } = {}) {
  const visit = (node) => {
    if (node.type === 'element' && node.tagName === 'a') {
      const href = String(node.properties?.href ?? '');
      if (/^https?:\/\//.test(href)) {
        const isAffiliate = domains.some((d) => href.includes(d));
        node.properties.target = '_blank';
        node.properties.rel = isAffiliate ? ['sponsored', 'nofollow', 'noopener'] : ['noopener'];
        if (isAffiliate) node.properties.dataAffiliate = 'true';
      }
    }
    (node.children ?? []).forEach(visit);
  };
  return (tree) => visit(tree);
}
