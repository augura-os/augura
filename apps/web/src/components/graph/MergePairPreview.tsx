import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Pause, Play, Volume2, VolumeX, X } from "lucide-react";
import { useT } from "../../lib/i18n";

export interface PreviewSide {
  id: string;
  name: string;
}

/** 漂移超过 0.3s 时把跟随侧校正到主视频时间（任务书阈值） */
const DRIFT_TOLERANCE = 0.3;

/**
 * 合并裁决双视频并排预览：两个 <video> 共享一套播放控制（播放/暂停/
 * 进度拖动联动，左为主），timeupdate 检测漂移 >0.3s 时校正；各自静音
 * 开关。图片素材单侧回退 <img>（不参与同步）。
 */
export function MergePairPreview({
  left,
  right,
  onClose,
}: {
  left: PreviewSide;
  right: PreviewSide;
  onClose: () => void;
}) {
  const t = useT();
  const leftRef = useRef<HTMLVideoElement | null>(null);
  const rightRef = useRef<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [muted, setMuted] = useState<{ left: boolean; right: boolean }>({
    left: true,
    right: true,
  });
  const [imageFallback, setImageFallback] = useState<{ left: boolean; right: boolean }>({
    left: false,
    right: false,
  });

  // 主视频（左）timeupdate：刷新共享进度条 + 漂移校正右侧
  const handleTimeUpdate = () => {
    const master = leftRef.current;
    if (!master) return;
    setCurrentTime(master.currentTime);
    const follower = rightRef.current;
    if (
      follower &&
      !imageFallback.right &&
      Math.abs(follower.currentTime - master.currentTime) > DRIFT_TOLERANCE
    ) {
      follower.currentTime = master.currentTime;
    }
  };

  const togglePlay = () => {
    const master = leftRef.current;
    const follower = rightRef.current;
    if (playing) {
      master?.pause();
      follower?.pause();
      setPlaying(false);
    } else {
      if (master && imageFallback.left === false) {
        void master.play().catch(() => setPlaying(false));
      }
      if (follower && imageFallback.right === false) {
        void follower.play().catch(() => undefined);
      }
      setPlaying(true);
    }
  };

  const seek = (value: number) => {
    if (leftRef.current) leftRef.current.currentTime = value;
    if (rightRef.current) rightRef.current.currentTime = value;
    setCurrentTime(value);
  };

  // 右侧 natural pause/ended 时保持状态一致（主视频结束即整体停）
  useEffect(() => {
    const master = leftRef.current;
    if (!master) return;
    const onEnded = () => {
      rightRef.current?.pause();
      setPlaying(false);
    };
    master.addEventListener("ended", onEnded);
    return () => master.removeEventListener("ended", onEnded);
  }, [imageFallback.left]);

  const side = (
    which: "left" | "right",
    sideData: PreviewSide,
    ref: { current: HTMLVideoElement | null },
  ) => (
    <span className="flex min-w-0 flex-1 flex-col">
      <span className="flex items-center justify-between gap-1 bg-neutral-900 px-2 py-1">
        <span className="truncate text-[11px] text-neutral-300">{sideData.name}</span>
        <button
          title={muted[which] ? t("review.merge.unmute") : t("review.merge.mute")}
          onClick={() => setMuted((prev) => ({ ...prev, [which]: !prev[which] }))}
          className="shrink-0 rounded p-0.5 text-neutral-400 transition hover:bg-neutral-800 hover:text-white"
        >
          {muted[which] ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5" />}
        </button>
      </span>
      {imageFallback[which] ? (
        <img
          src={`/assets/${sideData.id}/media`}
          alt={sideData.name}
          className="max-h-[65vh] w-full bg-black object-contain"
        />
      ) : (
        <video
          ref={ref}
          muted={muted[which]}
          playsInline
          preload="auto"
          className="max-h-[65vh] w-full bg-black"
          src={`/assets/${sideData.id}/media`}
          onTimeUpdate={which === "left" ? handleTimeUpdate : undefined}
          onLoadedMetadata={
            which === "left"
              ? (event) => setDuration(event.currentTarget.duration || 0)
              : undefined
          }
          onError={() => setImageFallback((prev) => ({ ...prev, [which]: true }))}
        />
      )}
    </span>
  );

  // 必须 portal 到 body：收件箱面板带 backdrop-blur（会形成 fixed 包含块）
  // 且 overflow-hidden，直接渲染 fixed 弹层会被裁剪在 400px 面板内。
  return createPortal(
    <span
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <span
        className="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-black"
        onClick={(e) => e.stopPropagation()}
      >
        <span className="flex items-center justify-between gap-2 bg-neutral-900 px-3 py-1.5">
          <span className="truncate text-xs text-neutral-300">
            {t("review.merge.previewBoth")}
          </span>
          <button
            title={t("inbox.merge.closePreview")}
            onClick={onClose}
            className="rounded p-0.5 text-neutral-400 transition hover:bg-neutral-800 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </span>
        <span className="flex gap-px">
          {side("left", left, leftRef)}
          {side("right", right, rightRef)}
        </span>
        <span className="flex items-center gap-2 bg-neutral-900 px-3 py-2">
          <button
            title={playing ? t("review.merge.pause") : t("review.merge.play")}
            onClick={togglePlay}
            className="rounded p-1 text-neutral-300 transition hover:bg-neutral-800 hover:text-white"
          >
            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </button>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={Math.min(currentTime, duration || 0)}
            onChange={(event) => seek(Number(event.target.value))}
            aria-label={t("review.merge.seek")}
            className="h-1 flex-1 accent-sky-500"
          />
          <span className="shrink-0 text-[11px] tabular-nums text-neutral-400">
            {currentTime.toFixed(1)} / {duration.toFixed(1)}s
          </span>
        </span>
      </span>
    </span>,
    document.body,
  );
}
