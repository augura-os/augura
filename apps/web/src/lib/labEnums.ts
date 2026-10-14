import { translate } from "./i18n";

/**
 * Lab 受控词表枚举 → 展示名（locale key `lab.enum.<group>.<id>`；缺模板回退原 id）。
 * 词表 id 集与后端 services/lab_taxonomy.py 保持同步——前端常量副本惯例
 * （照 hooks/useMetricConfig.ts），不从后端导入。
 *
 * group 取值：hook_mechanism / form_tag / timeliness / opening_3s /
 * brand_implant / bridge_mode / b_narrative / b_ui / visual_route /
 * emotion_entry / spectrum / evidence / choice（选择链：正解/作死）
 */
export function labEnumLabel(group: string, id: string | null | undefined): string {
  if (!id) return "";
  const key = `lab.enum.${group}.${id}`;
  const label = translate(key);
  return label === key ? id : label;
}

/** 词表里的「判不出/说不清」兜底值（UI 用警示色标出，不当作正常结论）。 */
export const UNCERTAIN_IDS = new Set(["uncertain", "unclear"]);
