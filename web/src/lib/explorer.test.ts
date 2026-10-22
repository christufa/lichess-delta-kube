import { describe, expect, it } from "vitest";
import {
  extend,
  formatExplorerBand,
  numbered,
  parseExplorerBand,
  parseMoves,
  replay,
  validateExplorerSearch,
} from "./explorer";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

describe("replay", () => {
  it("starts from the initial position", () => {
    expect(replay([])).toEqual({ moves: [], fen: START, turn: "w" });
  });

  it("plays a line and stops at the first illegal move", () => {
    const line = replay(["e4", "c5", "Nf3", "e4", "d6"]);
    expect(line.moves).toEqual(["e4", "c5", "Nf3"]);
    expect(line.turn).toBe("b");
  });

  it("reaches one position by two move orders (the API keys on the first three FEN fields)", () => {
    const key = (fen: string) => fen.split(" ").slice(0, 3).join(" ");
    expect(key(replay(["Nf3", "d5", "d4"]).fen)).toBe(key(replay(["d4", "d5", "Nf3"]).fen));
  });
});

describe("extend", () => {
  it("adds a move by SAN or by a drag", () => {
    expect(extend(["e4"], "e5")).toEqual(["e4", "e5"]);
    expect(extend(["e4", "e5"], { from: "g1", to: "f3" })).toEqual(["e4", "e5", "Nf3"]);
  });

  it("refuses an illegal move", () => {
    expect(extend(["e4"], { from: "e4", to: "e6" })).toBeNull();
    expect(extend([], "Ke2")).toBeNull();
  });

  it("promotes to a queen", () => {
    const line = ["a4", "b5", "axb5", "a6", "bxa6", "Bb7", "a7", "e6"]; // the knight stays on b8
    expect(extend(line, { from: "a7", to: "b8" })?.at(-1)).toBe("axb8=Q");
  });
});

describe("the URL", () => {
  it("reads the moves, keeping the legal part", () => {
    expect(parseMoves("e4,c5,Nf3")).toEqual(["e4", "c5", "Nf3"]);
    expect(parseMoves("e4 e5  Nf3")).toEqual(["e4", "e5", "Nf3"]);
    expect(parseMoves("e4,Zz9,Nf3")).toEqual(["e4"]);
    expect(parseMoves(undefined)).toEqual([]);
    expect(parseMoves(42)).toEqual([]);
  });

  it("validates the whole search, dropping what's invalid", () => {
    expect(validateExplorerSearch({ moves: "d4,Nf6", from: "2026-01", to: "nope", speed: "blitz", band: "1600" })).toEqual(
      { moves: "d4,Nf6", from: "2026-01", to: undefined, speed: "blitz", band: 1600 },
    );
    expect(validateExplorerSearch({ moves: "", band: "1800", speed: "turbo" })).toEqual({
      moves: undefined,
      from: undefined,
      to: undefined,
      speed: undefined,
      band: undefined,
    });
  });
});

describe("labels", () => {
  it("numbers the line", () => {
    expect(numbered(["e4", "c5", "Nf3"]).map((m) => m.label)).toEqual(["1. e4", "c5", "2. Nf3"]);
  });

  it("names the bands", () => {
    expect([0, 1200, 2400].map((b) => formatExplorerBand(b as 0 | 1200 | 2400))).toEqual([
      "Under 1200",
      "1200–1599",
      "2400+",
    ]);
    expect(parseExplorerBand(0)).toBe(0);
    expect(parseExplorerBand("2000")).toBe(2000);
    expect(parseExplorerBand("1800")).toBeUndefined();
  });
});
