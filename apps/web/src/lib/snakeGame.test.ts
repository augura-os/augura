import { describe, expect, it } from "vitest";
import { dist, initState, spawnDot, step, type SnakeState, type Vec } from "./snakeGame";

/** 确定性伪随机（mulberry32）：注入 step 让长跑测试可复现。 */
function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function bareState(overrides: Partial<SnakeState>): SnakeState {
  return {
    cols: 10,
    rows: 10,
    snake: [
      { x: 3, y: 5 },
      { x: 2, y: 5 },
      { x: 1, y: 5 },
    ],
    dir: { x: 1, y: 0 },
    dots: [],
    eaten: 0,
    tick: 0,
    dotBirth: {},
    lastEatenPos: null,
    lastEatenTick: -1,
    ...overrides,
  };
}

const keyOf = (p: Vec) => `${p.x},${p.y}`;

describe("dist", () => {
  it("takes the wrap-around shortcut across the edge", () => {
    expect(dist({ x: 0, y: 0 }, { x: 9, y: 0 }, 10, 10)).toBe(1);
    expect(dist({ x: 0, y: 0 }, { x: 4, y: 3 }, 10, 10)).toBe(7);
  });
});

describe("step wall wrapping", () => {
  it("wraps the head to the opposite side horizontally", () => {
    const s = bareState({
      snake: [
        { x: 9, y: 5 },
        { x: 8, y: 5 },
        { x: 7, y: 5 },
      ],
    });
    const next = step(s, () => 0.5);
    expect(next.snake[0]).toEqual({ x: 0, y: 5 });
    expect(next.dir).toEqual({ x: 1, y: 0 });
  });

  it("wraps the head vertically when moving off the top", () => {
    const s = bareState({
      snake: [
        { x: 5, y: 0 },
        { x: 5, y: 1 },
        { x: 5, y: 2 },
      ],
      dir: { x: 0, y: -1 },
    });
    const next = step(s, () => 0.5);
    expect(next.snake[0]).toEqual({ x: 5, y: 9 });
  });
});

describe("step eating", () => {
  it("grows by one, bumps eaten, and removes the eaten dot", () => {
    const s = bareState({ dots: [{ x: 4, y: 5 }], dotBirth: { "4,5": 0 } });
    const next = step(s, () => 0.99);
    expect(next.snake).toHaveLength(4);
    expect(next.snake[0]).toEqual({ x: 4, y: 5 });
    expect(next.eaten).toBe(1);
    expect(next.lastEatenPos).toEqual({ x: 4, y: 5 });
    expect(next.lastEatenTick).toBe(1);
    // 补豆后原位置的豆消失，且新豆不落在蛇身上
    expect(next.dots.some((d) => d.x === 4 && d.y === 5)).toBe(false);
    expect(next.dots.length).toBeGreaterThanOrEqual(1);
    const body = new Set(next.snake.map(keyOf));
    for (const dot of next.dots) expect(body.has(keyOf(dot))).toBe(false);
  });
});

describe("dot invariants over a long run", () => {
  it("never exceeds 5 dots, keeps them on empty cells, and never reverses direction", () => {
    let s = initState(28, 12);
    const rand = mulberry32(42);
    for (let i = 0; i < 300; i++) {
      const prevDir = s.dir;
      s = step(s, rand);
      expect(s.dots.length).toBeLessThanOrEqual(5);
      expect(s.dots.length).toBeGreaterThanOrEqual(1);
      const occupied = new Set(s.snake.map(keyOf));
      const seen = new Set<string>();
      for (const dot of s.dots) {
        expect(occupied.has(keyOf(dot))).toBe(false);
        expect(seen.has(keyOf(dot))).toBe(false);
        seen.add(keyOf(dot));
        expect(s.dotBirth[keyOf(dot)]).toBeTypeOf("number");
      }
      // 禁止 180° 掉头
      expect(s.dir.x === -prevDir.x && s.dir.y === -prevDir.y).toBe(false);
    }
    expect(s.tick).toBe(300);
  });

  it("survives 500 ticks without the head ever hitting the body", () => {
    let s = initState(28, 12);
    const rand = mulberry32(7);
    for (let i = 0; i < 500; i++) {
      s = step(s, rand);
      const [head, ...body] = s.snake;
      expect(head.x).toBeGreaterThanOrEqual(0);
      expect(head.x).toBeLessThan(s.cols);
      expect(head.y).toBeGreaterThanOrEqual(0);
      expect(head.y).toBeLessThan(s.rows);
      expect(body.some((seg) => seg.x === head.x && seg.y === head.y)).toBe(false);
      expect(s.snake.length).toBeGreaterThanOrEqual(3);
    }
    expect(s.eaten).toBeGreaterThan(0);
  });
});

describe("spawnDot", () => {
  it("returns null when the board is full", () => {
    const snake: Vec[] = [];
    for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) snake.push({ x, y });
    expect(spawnDot({ cols: 4, rows: 4, snake, dots: [] }, () => 0)).toBeNull();
  });

  it("picks the only empty cell", () => {
    const snake: Vec[] = [];
    for (let y = 0; y < 2; y++) for (let x = 0; x < 2; x++) snake.push({ x, y });
    snake.pop();
    expect(spawnDot({ cols: 2, rows: 2, snake, dots: [] }, () => 0.9)).toEqual({ x: 1, y: 1 });
  });
});
