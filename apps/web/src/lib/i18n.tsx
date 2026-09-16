import {
  createContext,
  useContext,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { en } from "../locales/en";
import { zh } from "../locales/zh";

export type Lang = "en" | "zh";

const STORAGE_KEY = "augura-lang";
const DICTS: Record<Lang, Record<string, string>> = { en, zh };

function loadLang(): Lang {
  try {
    if (typeof localStorage !== "undefined") {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "en" || stored === "zh") return stored;
    }
  } catch {
    // localStorage 不可用时用默认语言
  }
  return "en";
}

// 模块级语言状态：React 外用 translate() 直接读，React 内经
// useSyncExternalStore 订阅，setLang 触发所有使用方重渲染。
let currentLang: Lang = loadLang();
const listeners = new Set<() => void>();

export function getLang(): Lang {
  return currentLang;
}

export function setLang(lang: Lang): void {
  if (lang === currentLang) return;
  currentLang = lang;
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, lang);
    }
  } catch {
    // 持久化失败仅本次会话生效
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 非组件环境（模块级常量、工具函数）的翻译入口：查当前语言字典，
 * 缺失回退 en，再缺失返回 key 本身并告警。插值用简单 replace：
 * translate("settings.models.loaded").replace("{n}", String(n))
 */
export function translate(key: string): string {
  const value = DICTS[currentLang][key] ?? DICTS.en[key];
  if (value === undefined) {
    console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  return value;
}

interface LanguageContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string) => string;
}

const LanguageContext = createContext<LanguageContextValue>({
  lang: "en",
  setLang,
  t: translate,
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const lang = useSyncExternalStore(subscribe, getLang);
  return (
    <LanguageContext.Provider value={{ lang, setLang, t: translate }}>
      {children}
    </LanguageContext.Provider>
  );
}

/** 组件内取 t()；语言切换时组件自动重渲染。 */
export function useT(): (key: string) => string {
  return useContext(LanguageContext).t;
}

/** 当前语言 + 切换器（Settings 语言切换用）。 */
export function useLanguage(): { lang: Lang; setLang: (lang: Lang) => void } {
  const { lang, setLang: set } = useContext(LanguageContext);
  return { lang, setLang: set };
}
