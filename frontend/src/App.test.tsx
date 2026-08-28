// spec: project-initialization
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("application shell", () => {
  it("renders the dashboard title", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Local model control center" })).toBeInTheDocument();
  });
});
