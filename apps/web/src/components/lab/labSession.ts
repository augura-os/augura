import type { LabAudience } from "@shared";

/** Lab 会话（sessionStorage 持久化，刷新可恢复；传新参考 = 整体替换）。 */
export interface LabSession {
  assetId: string;
  filename: string;
  audience: LabAudience;
}

const SESSION_KEY = "augura-lab-session";

export function loadLabSession(): LabSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LabSession>;
    if (typeof parsed.assetId !== "string" || !parsed.assetId) return null;
    return {
      assetId: parsed.assetId,
      filename: typeof parsed.filename === "string" ? parsed.filename : "",
      audience:
        parsed.audience && typeof parsed.audience === "object" ? parsed.audience : {},
    };
  } catch {
    return null;
  }
}

export function saveLabSession(session: LabSession | null): void {
  try {
    if (session) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(SESSION_KEY);
    }
  } catch {
    // 隐私模式等持久化失败时仅本次页面会话有效
  }
}
