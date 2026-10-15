// Words for the personal greeting on /pricing and /hello (#1231): what a link DMbot sends to one
// person (?uid=&t=) shows at the top of the page. Nothing here is shown without a valid link.
export const personal = {
  hello: (name: string): string => `Hello, ${name}`,
  points: (minutes: number): string => `Experience points: ${minutes}`,
  pointsNote: "The minutes DMbot has run for you, all time.",
};
