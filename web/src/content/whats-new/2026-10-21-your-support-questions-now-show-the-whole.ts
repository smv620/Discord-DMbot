import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-21",
  title: "Your support questions now show the whole conversation",
  what: "On your account page, press \"Read the conversation\" under one of your questions to read all of it: what you wrote, the answers from DMbot's helper (a program, not a person), the replies you sent by email and the team's replies, with the dates. On our website, only you and the team can read it.",
  held: "Waits for the website's API change (#2193) to deploy, and for the support helper to be on (SUPPORT_AI_LIVE, #1431). When it is live, move this up with the helper's entry.",
  order: 15,
};
