/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The My Account switch "Use what I say and type to improve DMbot" (#1610): on by default, saves
// when pressed, says so, and an older API shows nothing.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { Improvement } from "../src/account/Improvement";
import { mockApi } from "../src/account/mock";
import { text } from "../src/content/improvement";

afterEach(cleanup);

describe("the improvement switch", () => {
  it("is on by default, with the sentence that says what turning it off does", async () => {
    render(<Improvement api={mockApi("table")} />);
    const box = (await screen.findByLabelText(text.label)) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(screen.getByText(text.note)).toBeTruthy();
    expect(text.note).toBe(
      "If you turn this off, DMbot still runs your games as usual. It just won't use your words to get better.",
    );
  });

  it("saves when pressed and says so, off and then on again", async () => {
    const api = mockApi("table");
    render(<Improvement api={api} />);
    const box = (await screen.findByLabelText(text.label)) as HTMLInputElement;
    fireEvent.click(box);
    expect(await screen.findByText(text.savedOff)).toBeTruthy();
    expect(api.calls).toContain("setImprovement:false");
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(await screen.findByText(text.savedOn)).toBeTruthy();
    expect(api.calls).toContain("setImprovement:true");
  });

  it("shows nothing on an older API without the route", () => {
    const { improvement: _a, setImprovement: _b, ...older } = mockApi("table");
    const { container } = render(<Improvement api={older as AccountApi} />);
    expect(container.textContent).toBe("");
  });

  it("shows nothing when the route answers not found", async () => {
    const api = {
      ...mockApi("table"),
      improvement: async () => {
        throw new ApiError("not-found");
      },
    } as AccountApi;
    const { container } = render(<Improvement api={api} />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("says plainly when saving failed and keeps the switch as it was", async () => {
    const api = {
      ...mockApi("table"),
      setImprovement: async () => {
        throw new ApiError("network");
      },
    } as AccountApi;
    render(<Improvement api={api} />);
    const box = (await screen.findByLabelText(text.label)) as HTMLInputElement;
    fireEvent.click(box);
    expect(await screen.findByText(text.failed)).toBeTruthy();
  });
});
