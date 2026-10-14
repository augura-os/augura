import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useT } from "../../lib/i18n";
import { initState, step, type SnakeState } from "../../lib/snakeGame";

const COLS = 28;
const ROWS = 12;
const TICK_MS = 120;
/** 画布最大高度：cell = min(容器宽/COLS, MAX_H/ROWS)，保证任何宽度下高 ~176px */
const MAX_CANVAS_H = 176;
/** 新生豆豆脉冲放大的帧数 */
const DOT_PULSE_TICKS = 3;
/** 吃豆闪白高亮的帧数 */
const EAT_FLASH_TICKS = 2;

const SNAKE_COLOR = "#171717"; // neutral-900
const DOT_COLOR = "#fbbf24"; // amber-400
const FLASH_COLOR = "#fcd34d"; // amber-300

function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function draw(canvas: HTMLCanvasElement, s: SnakeState): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.width / dpr;
  const cssH = canvas.height / dpr;
  if (cssW <= 0 || cssH <= 0) return;
  const cell = cssW / COLS;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  // 豆豆：amber 圆点，新生 3 帧脉冲放大
  for (const dot of s.dots) {
    const born = s.dotBirth[`${dot.x},${dot.y}`] ?? s.tick;
    const age = s.tick - born;
    const scale = age >= DOT_PULSE_TICKS ? 1 : 0.45 + (0.55 * (age + 1)) / DOT_PULSE_TICKS;
    ctx.fillStyle = DOT_COLOR;
    ctx.beginPath();
    ctx.arc((dot.x + 0.5) * cell, (dot.y + 0.5) * cell, cell * 0.32 * scale, 0, Math.PI * 2);
    ctx.fill();
  }

  // 吃豆瞬间：原位闪 2 帧扩散环
  if (s.lastEatenPos && s.tick - s.lastEatenTick < EAT_FLASH_TICKS) {
    const progress = s.tick - s.lastEatenTick;
    ctx.strokeStyle = FLASH_COLOR;
    ctx.globalAlpha = 1 - progress / EAT_FLASH_TICKS;
    ctx.lineWidth = Math.max(1, cell * 0.08);
    ctx.beginPath();
    ctx.arc(
      (s.lastEatenPos.x + 0.5) * cell,
      (s.lastEatenPos.y + 0.5) * cell,
      cell * (0.35 + 0.3 * progress),
      0,
      Math.PI * 2,
    );
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // 蛇身：圆角节，从尾画到头（头压在最上层）
  ctx.fillStyle = SNAKE_COLOR;
  for (let i = s.snake.length - 1; i >= 1; i--) {
    const seg = s.snake[i];
    const inset = cell * 0.14;
    roundedRect(ctx, seg.x * cell + inset, seg.y * cell + inset, cell - inset * 2, cell - inset * 2, cell * 0.22);
    ctx.fill();
  }

  // 头节略大 + 两点眼睛（按朝向偏移）
  const head = s.snake[0];
  const headInset = cell * 0.06;
  roundedRect(
    ctx,
    head.x * cell + headInset,
    head.y * cell + headInset,
    cell - headInset * 2,
    cell - headInset * 2,
    cell * 0.26,
  );
  ctx.fill();
  const cx = (head.x + 0.5) * cell;
  const cy = (head.y + 0.5) * cell;
  const perp = { x: -s.dir.y, y: s.dir.x };
  ctx.fillStyle = "#ffffff";
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.arc(
      cx + s.dir.x * cell * 0.16 + perp.x * cell * 0.2 * side,
      cy + s.dir.y * cell * 0.16 + perp.y * cell * 0.2 * side,
      cell * 0.08,
      0,
      Math.PI * 2,
    );
    ctx.fill();
  }
}

interface SnakeLoadingProps {
  /** 左侧主文案（如「正在诊断…」） */
  caption: string;
  /** 主文案后的浅色补充说明 */
  hint: string;
  /** 秒表起点（epoch ms，缺省 = 挂载时刻）：remount 场景从在途 store 续传，
   *  切走再回来秒表按真实经过时间走，不从 0 重置 */
  startedAt?: number;
}

/** 贪吃蛇吃豆豆加载动画：装饰性网格状态机（lib/snakeGame），canvas 绘制。
 *  prefers-reduced-motion 时不启动动画，只画一帧静态画面并补一个 spinner。 */
export function SnakeLoading({ caption, hint, startedAt }: SnakeLoadingProps) {
  const t = useT();
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<SnakeState | null>(null);
  if (stateRef.current === null) stateRef.current = initState(COLS, ROWS);
  const [eaten, setEaten] = useState(0);
  const [elapsed, setElapsed] = useState(() =>
    startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0,
  );
  const [reducedMotion] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  // 容器宽度自适应：cell 按宽度换算（封顶保证画布高 ~176px），DPR 对齐保持清晰
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const resize = () => {
      const cssW = container.clientWidth;
      if (cssW <= 0 || !stateRef.current) return;
      const cell = Math.min(cssW / COLS, MAX_CANVAS_H / ROWS);
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cell * COLS * dpr);
      canvas.height = Math.round(cell * ROWS * dpr);
      canvas.style.width = `${Math.round(cell * COLS)}px`;
      canvas.style.height = `${Math.round(cell * ROWS)}px`;
      draw(canvas, stateRef.current);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // 动画驱动：120ms 步进 + 重绘。reduced-motion 下不启动（静态帧由上面的 resize 画出）
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || reducedMotion) return;
    const ticker = window.setInterval(() => {
      if (!stateRef.current) return;
      const next = step(stateRef.current);
      stateRef.current = next;
      draw(canvas, next);
      setEaten((prev) => (prev === next.eaten ? prev : next.eaten));
    }, TICK_MS);
    return () => {
      window.clearInterval(ticker);
    };
  }, [reducedMotion]);

  // 1s 秒表独立驱动：startedAt 给定时按真实经过时间算（remount 连续），
  // 否则从挂载起每秒 +1；startedAt 变化只重启秒表，不动动画 ticker
  useEffect(() => {
    if (reducedMotion) return;
    const clock = window.setInterval(() => {
      setElapsed((value) =>
        startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : value + 1,
      );
    }, 1000);
    return () => {
      window.clearInterval(clock);
    };
  }, [reducedMotion, startedAt]);

  return (
    <div>
      <div
        ref={containerRef}
        className="flex w-full justify-center overflow-hidden rounded-lg border border-[#e5e5e5] bg-white"
      >
        <canvas ref={canvasRef} className="block" />
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="shrink-0 text-sm font-medium text-neutral-900">{caption}</span>
          <span className="truncate text-xs text-neutral-500">{hint}</span>
          {reducedMotion ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin self-center text-neutral-400" />
          ) : null}
        </div>
        <span className="shrink-0 text-xs tabular-nums text-neutral-500">
          {t("lab.report.snakeScore").replace("{n}", String(eaten))} · {elapsed}s
        </span>
      </div>
    </div>
  );
}
