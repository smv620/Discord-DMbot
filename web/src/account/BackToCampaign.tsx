/** @jsxImportSource preact */
// A small link from one of a campaign's own pages back to the campaign's page (#2416). It names the
// campaign when the page knows its name, and says "This campaign" when it doesn't.
import { campaignPage as words } from "../content/campaign-page";
import { campaignHref } from "./campaignLinks";

export default function BackToCampaign({ campaignRef, name }: { campaignRef: string; name?: string | null }) {
  return (
    <p class="small back-to-campaign">
      <a href={campaignHref(campaignRef)}>{name ? words.backTo(name) : words.backToThis}</a>
    </p>
  );
}
