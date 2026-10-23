import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-17",
  title: "FightBot follows your turn order",
  what: "FightBot listens to the people who agreed to be recorded. When it hears things like \"Mira, you're up\", \"players go first\" or \"declare your actions\", it guesses how your table takes turns. Your DM screen shows the guess, and you can change it just by saying it differently. FightBot then sends each player a short reminder just before their turn. It only guesses: you decide the order, and it never changes your fight.",
  held: "Held until dev1 has live-checked it on the test server (#1771).",
  order: 20,
};
