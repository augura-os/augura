import { useState } from "react";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { useSettings, useUpdateSettings } from "../../hooks/useSettings";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

const GENRE_VALUES = [
  "casual_slg",
  "idle_tycoon",
  "merge2",
  "rpg",
  "match3",
  "other",
] as const;

/**
 * 首启引导（v0.12）：品类选择决定判定阈值默认值与遥测分桶。
 * consent 之后、project_category 未设置时显示一次。
 */
export function OnboardingDialog() {
  const t = useT();
  const { data, isLoading } = useSettings();
  const updateMutation = useUpdateSettings();
  const [selected, setSelected] = useState<string>("");

  const open = !isLoading && data !== undefined && data.project_category === "";
  if (!open) return null;

  const save = (genre: string) => {
    updateMutation.mutate({ project_category: genre });
  };

  return (
    <Dialog open={open} onOpenChange={() => {}} title={t("onboarding.title")}>
      <div className="space-y-4">
        <p className="text-sm text-neutral-500">
          {t("onboarding.intro")}
        </p>
        <div className="grid grid-cols-2 gap-2">
          {GENRE_VALUES.map((value) => (
            <button
              key={value}
              onClick={() => setSelected(value)}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-left transition",
                selected === value
                  ? "border-neutral-900 bg-neutral-900 text-white"
                  : "border-black/10 bg-white text-neutral-800 hover:border-neutral-400",
              )}
            >
              <p className="text-sm font-medium">{t(`onboarding.genre.${value}.label`)}</p>
              <p
                className={cn(
                  "text-[11px]",
                  selected === value ? "text-neutral-300" : "text-neutral-400",
                )}
              >
                {t(`onboarding.genre.${value}.desc`)}
              </p>
            </button>
          ))}
        </div>
        <div className="space-y-1.5 rounded-lg bg-neutral-50 p-3 text-[13px] text-neutral-600">
          <p>{t("onboarding.stepsTitle")}</p>
          <p>
            {t("onboarding.step1a")}
            <span className="font-medium text-neutral-900">Settings</span>
            {t("onboarding.step1b")}
          </p>
          <p>
            {t("onboarding.step2a")}
            <span className="font-medium text-neutral-900">Upload</span>
            {t("onboarding.step2b")}
            <span className="font-medium text-neutral-900">{t("onboarding.facebookReport")}</span>
            {t("onboarding.step2c")}
          </p>
        </div>
        <div className="flex items-center justify-between">
          <button
            onClick={() => save("other")}
            className="text-xs text-neutral-400 underline-offset-2 hover:text-neutral-600 hover:underline"
          >
            {t("onboarding.decideLater")}
          </button>
          <Button disabled={!selected || updateMutation.isPending} onClick={() => save(selected)}>
            {updateMutation.isPending ? t("common.saving") : t("onboarding.saveAndStart")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
