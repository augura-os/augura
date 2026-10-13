import { Check, Compass, FileText, FileUp, Lock, ScrollText, Users } from "lucide-react";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

interface LabStepperProps {
  current: number;
  /** 功能步（0–2）中已解锁的最大下标（无会话时只有第 0 步） */
  maxUnlocked: number;
  onSelect: (index: number) => void;
}

const STEPS = [
  { labelKey: "lab.step.reference", icon: FileUp, locked: false },
  { labelKey: "lab.step.audience", icon: Users, locked: false },
  { labelKey: "lab.step.report", icon: FileText, locked: false },
  { labelKey: "lab.step.direction", icon: Compass, locked: true },
  { labelKey: "lab.step.brief", icon: ScrollText, locked: true },
] as const;

/** 五步进条：参考 → 受众 → 定性报告 → 方向（M2 锁态）→ Brief（M2 锁态）。
 *  锁态步可点击查看诚实占位卡；功能步只开放已解锁的。 */
export function LabStepper({ current, maxUnlocked, onSelect }: LabStepperProps) {
  const t = useT();
  return (
    <nav className="flex items-center">
      {STEPS.map((step, index) => {
        const reachable = step.locked || index <= maxUnlocked;
        const done = !step.locked && index < current && index <= maxUnlocked;
        const active = index === current;
        const Icon = step.locked ? Lock : done ? Check : step.icon;
        return (
          <div key={step.labelKey} className={cn("flex items-center", index > 0 && "flex-1")}>
            {index > 0 ? <div className="mx-2 h-px flex-1 bg-[#e5e5e5]" /> : null}
            <button
              type="button"
              disabled={!reachable}
              onClick={() => onSelect(index)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition",
                active
                  ? "bg-neutral-900 text-white"
                  : reachable
                    ? "text-neutral-600 hover:bg-neutral-100"
                    : "cursor-not-allowed text-neutral-300",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{t(step.labelKey)}</span>
              {step.locked ? (
                <span
                  className={cn(
                    "rounded px-1 py-px text-[10px] font-semibold",
                    active ? "bg-white/20 text-white" : "bg-neutral-100 text-neutral-400",
                  )}
                >
                  M2
                </span>
              ) : null}
            </button>
          </div>
        );
      })}
    </nav>
  );
}
