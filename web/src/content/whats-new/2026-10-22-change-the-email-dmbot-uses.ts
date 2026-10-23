import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-22",
  title: "Change the email DMbot uses",
  what: "On your account page, see the email DMbot uses for recap emails and change it. DMbot sends a link to the new address and changes nothing until you open it. If it is the email on your payment account, you change it in Stripe instead.",
  held: "Waits for the email routes (#2349) to be merged and deployed. When they are live, move this up.",
  order: 14,
};
