// The words of the picture picker inside the Icon row (#3622). A file of its own, read only by the lazily loaded row, so the
// Story Graph's page keeps its weight budget.
export const pcPictureWords = {
  setPicture: "Set a picture",
  closePicker: "Cancel",
  pickerHeading: "Pick one of this campaign's pictures",
  usePicture: "Use this picture",
  usePictureNamed: (description: string): string => `Use this picture: ${description}`,
  lookFailed: "We couldn't load the pictures. Check your connection and try again.",
  tryAgain: "Try again",
  noPictures: "This campaign has no pictures you can pick yet. A player can pick only pictures they added themselves.",
  loading: "Looking at the campaign's pictures…",
  more: "Show more",
  pictureAlt: (description: string): string => (description ? `Picture: ${description}` : "A picture of this campaign"),
} as const;
