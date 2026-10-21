import { fade } from "./chart-theme";

describe("fade", () => {
  it("turns a hex color into rgba", () => {
    expect(fade("#3987e5", 0.3)).toBe("rgba(57, 135, 229, 0.3)");
  });
  it("leaves other colors alone", () => {
    expect(fade("oklch(0.5 0.1 200)", 0.3)).toBe("oklch(0.5 0.1 200)");
  });
});
