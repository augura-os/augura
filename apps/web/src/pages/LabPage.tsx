import { useState } from "react";
import type { AssetListItem, LabAudience } from "@shared";
import { AudienceForm } from "../components/lab/AudienceForm";
import { LabStepper } from "../components/lab/LabStepper";
import { LockedStepCard } from "../components/lab/LockedStepCard";
import { ReferenceStep } from "../components/lab/ReferenceStep";
import { ReportStep } from "../components/lab/ReportStep";
import { loadLabSession, saveLabSession, type LabSession } from "../components/lab/labSession";
import { useLabAnalysis } from "../hooks/useLab";
import { useSettings } from "../hooks/useSettings";
import { getLang, useT } from "../lib/i18n";

/** Creative Lab（M1）：参考 → 受众 → 定性报告 三步实装；
 *  方向 / Brief 为 M2 锁态占位。会话（asset + 受众）存 sessionStorage。 */
export default function LabPage() {
  const t = useT();
  const [session, setSessionState] = useState<LabSession | null>(loadLabSession);
  const [step, setStep] = useState<number>(session ? 2 : 0);
  const { data: settings } = useSettings();
  // 会话存在即开始轮询分析状态（react-query 同 key 共享，ReportStep 复用缓存）；
  // lang 进 queryKey——语言切换后本组件随 useT 重渲染，自动按新语言 refetch
  useLabAnalysis(session?.assetId ?? null, getLang());

  const updateSession = (next: LabSession | null) => {
    setSessionState(next);
    saveLabSession(next);
  };

  // 传新参考 = 替换会话：清空受众，从受众步重来
  const onReferenceReady = (asset: AssetListItem) => {
    updateSession({ assetId: asset.id, filename: asset.filename, audience: {} });
    setStep(1);
  };

  const onAudienceSubmit = (audience: LabAudience) => {
    if (!session) return;
    updateSession({ ...session, audience });
    setStep(2);
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-6 p-8">
        <header>
          <h1 className="text-lg font-semibold text-neutral-900">{t("lab.title")}</h1>
          <p className="mt-1 text-sm text-neutral-500">{t("lab.subtitle")}</p>
        </header>

        <LabStepper current={step} maxUnlocked={session ? 2 : 0} onSelect={setStep} />

        {step === 0 ? <ReferenceStep current={session} onReady={onReferenceReady} /> : null}
        {step === 1 && session ? (
          <AudienceForm
            filename={session.filename}
            initial={session.audience}
            defaultCategory={settings?.project_category ?? ""}
            onSubmit={onAudienceSubmit}
          />
        ) : null}
        {step === 2 && session ? <ReportStep session={session} /> : null}
        {step === 3 ? (
          <LockedStepCard titleKey="lab.step.direction" descKey="lab.locked.directionDesc" />
        ) : null}
        {step === 4 ? (
          <LockedStepCard titleKey="lab.step.brief" descKey="lab.locked.briefDesc" />
        ) : null}
      </div>
    </div>
  );
}
