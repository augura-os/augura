import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";

const CONSENT_KEY = "augura-consent-v1";

export function useConsentGiven(): [boolean, () => void] {
  const [given, setGiven] = useState(() => localStorage.getItem(CONSENT_KEY) === "1");
  const accept = () => {
    localStorage.setItem(CONSENT_KEY, "1");
    setGiven(true);
  };
  return [given, accept];
}

/**
 * First-run privacy consent (PRIVACY.md / 数据合规方案 §3.4).
 * Shown once; the acknowledgement is the "已明示" evidence point.
 */
export function ConsentDialog({ open, onAccept }: { open: boolean; onAccept: () => void }) {
  const [checked, setChecked] = useState(false);
  return (
    <Dialog open={open} onOpenChange={() => {}} title="欢迎使用 Augura">
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-lg bg-emerald-50 p-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
          <p className="text-sm text-emerald-900">
            原始素材和投放数据保存在你自己的机器上。
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-neutral-800">
          <input
            type="checkbox"
            className="h-4 w-4 accent-neutral-900"
            checked={checked}
            onChange={(event) => setChecked(event.target.checked)}
          />
          我已阅读并同意
          <a
            href="https://github.com/augura-os/augura/blob/main/PRIVACY.md"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-neutral-900 underline underline-offset-2"
          >
            《隐私政策》
          </a>
        </label>
        <div className="flex justify-end">
          <Button disabled={!checked} onClick={onAccept}>
            开始使用
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
