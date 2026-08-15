import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { formatEngineering, QuantityValue } from "./QuantityValue";

describe("formatEngineering", () => {
  it("keeps four significant digits by default", () => {
    expect(formatEngineering(0.012386)).toBe("0.01239");
  });

  it("does not insert thousands separators", () => {
    // Separators break copy-paste into a spreadsheet.
    expect(formatEngineering(114000)).toBe("114000");
  });

  it("falls back to exponential notation for extreme magnitudes", () => {
    expect(formatEngineering(1.143e8)).toBe("1.143e+8");
    expect(formatEngineering(3.48e-5)).toBe("3.480e-5");
  });

  it("renders a dash for non-finite values instead of NaN", () => {
    expect(formatEngineering(Number.NaN)).toBe("—");
  });
});

describe("QuantityValue", () => {
  it("always shows a unit alongside the number", () => {
    render(<QuantityValue value={34.8} unit="µm" />);
    expect(screen.getByText("34.8")).toBeInTheDocument();
    expect(screen.getByText("µm")).toBeInTheDocument();
  });

  it("marks assumed values distinctly from imported ones", () => {
    const { rerender } = render(
      <QuantityValue value={25} unit="µm" provenance="assumed" note="IPC-6012 Class 2" />,
    );
    const assumed = screen.getByTestId("provenance-badge");
    expect(assumed).toHaveTextContent("assumed");
    expect(assumed.className).toContain("badge--assumed");
    expect(assumed.title).toContain("IPC-6012 Class 2");

    rerender(<QuantityValue value={34.8} unit="µm" provenance="imported" />);
    expect(screen.getByTestId("provenance-badge").className).toContain("badge--imported");
  });

  it("renders the number in a copyable monospace span", () => {
    render(<QuantityValue value={0.85} unit="V" />);
    expect(screen.getByText("0.85").className).toContain("numeric");
  });
});

describe("ProvenanceBadge", () => {
  it("explains what each provenance means on hover", () => {
    render(<ProvenanceBadge provenance="derived" />);
    expect(screen.getByTestId("provenance-badge").title).toBe("Computed from other quantities");
  });
});
