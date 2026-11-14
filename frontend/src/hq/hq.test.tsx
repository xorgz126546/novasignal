import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

describe("HQ markdown renderer", () => {
  it("renders headings, lists and bold without injecting HTML", () => {
    const { container } = render(<Markdown text={"# Brief\n- **Needs you**: 2\n<img src=x onerror=alert(1)>"} />);
    expect(container.querySelector("h3")?.textContent).toBe("Brief");
    expect(container.querySelector("b")?.textContent).toBe("Needs you");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});
