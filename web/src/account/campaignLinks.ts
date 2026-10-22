// Where a campaign's own page is, and a small link back to it from the campaign's other pages
// (#2416). Kept apart from the page itself so the pages that only link to it stay small.

/** The address of one campaign's page. `ref` is `<server>.<campaign>`. */
export const campaignHref = (ref: string): string => `/account/campaigns/${encodeURIComponent(ref)}`;
