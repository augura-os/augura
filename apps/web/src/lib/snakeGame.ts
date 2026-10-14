/** 贪吃蛇吃豆豆加载动画的纯逻辑（无 DOM 依赖，可单测）。
 *  蛇贪心追最近豆豆：候选方向=直行/左转/右转，按走后与目标的环绕曼哈顿距离排序；
 *  禁止 180° 掉头；撞墙环绕；撞自身规避（尾尖当 tick 会挪走，可进）；
 *  实在无路就保持直行——装饰性动画，蛇永远不死。 */

export interface Vec {
  x: number;
  y: number;
}

export interface SnakeState {
  cols: number;
  rows: number;
  /** 头在 [0]，尾在末尾 */
  snake: Vec[];
  dir: Vec;
  dots: Vec[];
  eaten: number;
  tick: number;
  /** "x,y" → 出生 tick，渲染层做新生脉冲 */
  dotBirth: Record<string, number>;
  /** 最近一次吃豆位置/tick（渲染层做 2 帧闪白高亮），未吃过为 null/-1 */
  lastEatenPos: Vec | null;
  lastEatenTick: number;
}

const MAX_DOTS = 5;
const MIN_DOTS = 2;
/** 每 12 tick 补一颗新豆（未到上限时） */
export const DOT_SPAWN_INTERVAL = 12;

function key(p: Vec): string {
  return `${p.x},${p.y}`;
}

function eq(a: Vec, b: Vec): boolean {
  return a.x === b.x && a.y === b.y;
}

function add(a: Vec, b: Vec): Vec {
  return { x: a.x + b.x, y: a.y + b.y };
}

function wrap(p: Vec, cols: number, rows: number): Vec {
  return { x: (p.x + cols) % cols, y: (p.y + rows) % rows };
}

/** 环绕网格上的曼哈顿距离（撞墙环绕后仍朝最近方向追）。 */
export function dist(a: Vec, b: Vec, cols: number, rows: number): number {
  const dx = Math.min(Math.abs(a.x - b.x), cols - Math.abs(a.x - b.x));
  const dy = Math.min(Math.abs(a.y - b.y), rows - Math.abs(a.y - b.y));
  return dx + dy;
}

/** 左转（屏幕坐标 y 向下）：东→北。 */
export function turnLeft(d: Vec): Vec {
  return { x: d.y, y: -d.x };
}

/** 右转：东→南。 */
export function turnRight(d: Vec): Vec {
  return { x: -d.y, y: d.x };
}

export function nearest(from: Vec, dots: Vec[], cols: number, rows: number): Vec | null {
  let best: Vec | null = null;
  let bestD = Infinity;
  for (const dot of dots) {
    const d = dist(from, dot, cols, rows);
    if (d < bestD) {
      bestD = d;
      best = dot;
    }
  }
  return best;
}

/** 在空格（非蛇身、非已有豆豆）里随机挑一格；无空格返回 null。 */
export function spawnDot(
  s: Pick<SnakeState, "cols" | "rows" | "snake" | "dots">,
  rand: () => number,
): Vec | null {
  const occupied = new Set<string>([...s.snake, ...s.dots].map(key));
  const empty: Vec[] = [];
  for (let y = 0; y < s.rows; y++) {
    for (let x = 0; x < s.cols; x++) {
      if (!occupied.has(`${x},${y}`)) empty.push({ x, y });
    }
  }
  if (empty.length === 0) return null;
  return empty[Math.min(empty.length - 1, Math.floor(rand() * empty.length))];
}

export function initState(cols: number, rows: number): SnakeState {
  const midY = Math.floor(rows / 2);
  const headX = Math.floor(cols / 3);
  const snake: Vec[] = [
    { x: headX, y: midY },
    { x: headX - 1, y: midY },
    { x: headX - 2, y: midY },
  ];
  const dots: Vec[] = [
    { x: Math.floor((cols * 3) / 4), y: midY },
    { x: Math.floor(cols / 2), y: Math.floor((rows * 3) / 4) },
  ];
  const dotBirth: Record<string, number> = {};
  for (const dot of dots) dotBirth[key(dot)] = 0;
  return {
    cols,
    rows,
    snake,
    dir: { x: 1, y: 0 },
    dots,
    eaten: 0,
    tick: 0,
    dotBirth,
    lastEatenPos: null,
    lastEatenTick: -1,
  };
}

export function step(s: SnakeState, rand: () => number = Math.random): SnakeState {
  const head = s.snake[0];
  const target = nearest(head, s.dots, s.cols, s.rows);

  // 候选方向：直行/左转/右转（不含 180° 掉头）。吃豆格当 tick 尾尖不挪走，
  // 整身都不可进；否则尾尖格可进（走完它就空了）。
  const candidates = [s.dir, turnLeft(s.dir), turnRight(s.dir)].map((d) => {
    const next = wrap(add(head, d), s.cols, s.rows);
    const eats = s.dots.some((dot) => eq(dot, next));
    const body = eats ? s.snake : s.snake.slice(0, -1);
    return {
      dir: d,
      next,
      eats,
      blocked: body.some((seg) => eq(seg, next)),
      score: target ? dist(next, target, s.cols, s.rows) : 0,
    };
  });
  // 稳定排序：距离并列时保持 直行 > 左转 > 右转 的优先级
  const ranked = [...candidates].sort((a, b) => a.score - b.score);
  const chosen = ranked.find((c) => !c.blocked) ?? candidates[0];

  const tick = s.tick + 1;
  let snake = [chosen.next, ...s.snake];
  let dots = s.dots;
  let eaten = s.eaten;
  let lastEatenPos = s.lastEatenPos;
  let lastEatenTick = s.lastEatenTick;
  const dotBirth = { ...s.dotBirth };

  if (chosen.eats) {
    // 吃到豆豆：不弹尾（身长 +1），吃掉该豆并立刻补一颗新豆
    eaten += 1;
    lastEatenPos = chosen.next;
    lastEatenTick = tick;
    dots = dots.filter((dot) => !eq(dot, chosen.next));
    delete dotBirth[key(chosen.next)];
  } else {
    snake = snake.slice(0, -1);
  }

  const state = { ...s, snake, dir: chosen.dir, dots, eaten, tick, dotBirth, lastEatenPos, lastEatenTick };

  const spawn = (): void => {
    if (dots.length >= MAX_DOTS) return;
    const dot = spawnDot({ cols: s.cols, rows: s.rows, snake, dots }, rand);
    if (!dot) return;
    dots = [...dots, dot];
    dotBirth[key(dot)] = tick;
    state.dots = dots;
  };

  if (chosen.eats) spawn(); // 补豆
  if (tick % DOT_SPAWN_INTERVAL === 0) spawn(); // 定时冒新豆
  while (dots.length < MIN_DOTS) {
    const before = dots.length;
    spawn();
    if (dots.length === before) break; // 无空格，放弃
  }

  return state;
}
