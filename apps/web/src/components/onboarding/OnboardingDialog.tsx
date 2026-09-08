import { useState } from "react";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { useSettings, useUpdateSettings } from "../../hooks/useSettings";
import { cn } from "../../lib/utils";

const GENRE_OPTIONS = [
  { value: "casual_slg", label: "休闲 SLG", desc: "建造/对抗/塔防类" },
  { value: "idle_tycoon", label: "模拟经营", desc: "放置 / 大亨类" },
  { value: "merge2", label: "二合（Merge）", desc: "合成玩法" },
  { value: "rpg", label: "RPG", desc: "角色扮演 / 卡牌" },
  { value: "match3", label: "三消（Match-3）", desc: "消除玩法" },
  { value: "other", label: "其他", desc: "暂不确定" },
] as const;

/**
 * 首启引导（v0.12）：品类选择决定判定阈值默认值与遥测分桶。
 * consent 之后、project_category 未设置时显示一次。
 */
export function OnboardingDialog() {
  const { data, isLoading } = useSettings();
  const updateMutation = useUpdateSettings();
  const [selected, setSelected] = useState<string>("");

  const open = !isLoading && data !== undefined && data.project_category === "";
  if (!open) return null;

  const save = (genre: string) => {
    updateMutation.mutate({ project_category: genre });
  };

  return (
    <Dialog open={open} onOpenChange={() => {}} title="先选一下你的项目品类">
      <div className="space-y-4">
        <p className="text-sm text-neutral-500">
          品类决定判定阈值的默认值（留存/回报线按品类口径），也让产品迭代按品类优化。
          之后在 Settings 页随时可以改。
          创意基因共建计划的统计数据按「市场 × 品类 × 创意家族」三维聚合，
          只有区间桶和计数（小样本桶不上传），不含素材内容与原始数值。
        </p>
        <div className="grid grid-cols-2 gap-2">
          {GENRE_OPTIONS.map((option) => (
            <button
              key={option.value}
              onClick={() => setSelected(option.value)}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-left transition",
                selected === option.value
                  ? "border-neutral-900 bg-neutral-900 text-white"
                  : "border-black/10 bg-white text-neutral-800 hover:border-neutral-400",
              )}
            >
              <p className="text-sm font-medium">{option.label}</p>
              <p
                className={cn(
                  "text-[11px]",
                  selected === option.value ? "text-neutral-300" : "text-neutral-400",
                )}
              >
                {option.desc}
              </p>
            </button>
          ))}
        </div>
        <div className="space-y-1.5 rounded-lg bg-neutral-50 p-3 text-[13px] text-neutral-600">
          <p>接下来两步：</p>
          <p>① 到 <span className="font-medium text-neutral-900">Settings</span> 配置 AI Key（OpenAI / Kimi 兼容接口）</p>
          <p>② 到 <span className="font-medium text-neutral-900">Upload</span> 上传素材（每条视频分析需几分钟）；
             投放数据请以 <span className="font-medium text-neutral-900">Facebook 报表</span>为评判基准</p>
        </div>
        <div className="flex items-center justify-between">
          <button
            onClick={() => save("other")}
            className="text-xs text-neutral-400 underline-offset-2 hover:text-neutral-600 hover:underline"
          >
            暂不选择
          </button>
          <Button disabled={!selected || updateMutation.isPending} onClick={() => save(selected)}>
            {updateMutation.isPending ? "保存中…" : "保存并开始"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
