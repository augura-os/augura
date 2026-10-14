import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { LabAudience } from "@shared";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import { useT } from "../../lib/i18n";

/** 性别选项："" = 不限（不提交）；提交值为稳定语义串。 */
const GENDER_OPTIONS = ["", "male", "female"] as const;

/** 年龄选项："" = 不限（不提交）；提交值即区间串。 */
const AGE_OPTIONS = ["", "18-24", "25-34", "35-44", "45-54", "55+"] as const;

/** 地区预设："" = 不限（不提交）；custom = 自定义（显示文本输入框）。 */
const REGION_PRESETS = [
  "",
  "T1-EN",
  "US",
  "PT-BR",
  "ES-LATAM",
  "SEA",
  "JP-KR",
  "EU",
  "MENA",
  "custom",
] as const;

const REGION_CUSTOM = "custom";

/** 地区显示文案的 locale key（"" 与 custom 也有各自文案）。 */
const regionLabelKey = (value: string): string =>
  value === REGION_CUSTOM
    ? "lab.audience.option.region.custom"
    : value === ""
      ? "lab.audience.option.any"
      : `lab.audience.option.region.${value}`;

interface AudienceFormProps {
  filename: string;
  initial: LabAudience;
  /** settings.project_category：品类字段的推荐默认（用户未填过时预填） */
  defaultCategory: string;
  onSubmit: (audience: LabAudience) => void;
}

/** 受众步：首屏性别/年龄下拉 + 地区「预设下拉 + 自定义输入」（全可选），
 *  品类放高级折叠（默认继承 Settings 的项目品类）。留空字段不进诊断
 *  prompt；整步可跳过。 */
export function AudienceForm({ filename, initial, defaultCategory, onSubmit }: AudienceFormProps) {
  const t = useT();
  const [gender, setGender] = useState(
    (GENDER_OPTIONS as readonly string[]).includes(initial.gender ?? "")
      ? (initial.gender ?? "")
      : "",
  );
  const [age, setAge] = useState(
    (AGE_OPTIONS as readonly string[]).includes(initial.age ?? "") ? (initial.age ?? "") : "",
  );
  // 地区：命中预设选预设；非预设的旧会话自由文本归为自定义并回填输入框
  const initialRegion = initial.region ?? "";
  const [region, setRegion] = useState(
    (REGION_PRESETS as readonly string[]).includes(initialRegion) ? initialRegion : REGION_CUSTOM,
  );
  const [customRegion, setCustomRegion] = useState(
    (REGION_PRESETS as readonly string[]).includes(initialRegion) ? "" : initialRegion,
  );
  const [category, setCategory] = useState(initial.category ?? defaultCategory);
  // 品类已预填时默认展开，让「继承了项目品类」这件事可见
  const [advancedOpen, setAdvancedOpen] = useState(
    Boolean(initial.category ?? defaultCategory),
  );

  const buildAudience = (): LabAudience => {
    const audience: LabAudience = {};
    if (gender) audience.gender = gender;
    if (age) audience.age = age;
    const regionValue = region === REGION_CUSTOM ? customRegion.trim() : region;
    if (regionValue) audience.region = regionValue;
    if (category.trim()) audience.category = category.trim();
    return audience;
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("lab.audience.title")}</CardTitle>
        <CardDescription>
          {t("lab.audience.subtitle")}
          <span className="ml-1 text-neutral-400">{filename}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="lab-gender">{t("lab.audience.gender")}</Label>
            <Select
              id="lab-gender"
              value={gender}
              onChange={(event) => setGender(event.target.value)}
            >
              {GENDER_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value === "" ? t("lab.audience.option.any") : t(`lab.audience.option.${value}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lab-age">{t("lab.audience.age")}</Label>
            <Select id="lab-age" value={age} onChange={(event) => setAge(event.target.value)}>
              {AGE_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value === ""
                    ? t("lab.audience.option.any")
                    : t(`lab.audience.option.age.${value}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lab-region">{t("lab.audience.region")}</Label>
            <Select
              id="lab-region"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
            >
              {REGION_PRESETS.map((value) => (
                <option key={value} value={value}>
                  {t(regionLabelKey(value))}
                </option>
              ))}
            </Select>
            {region === REGION_CUSTOM ? (
              <Input
                placeholder={t("lab.audience.regionCustomPlaceholder")}
                value={customRegion}
                onChange={(event) => setCustomRegion(event.target.value)}
                autoComplete="off"
              />
            ) : null}
          </div>
        </div>

        <div>
          <button
            type="button"
            onClick={() => setAdvancedOpen((open) => !open)}
            className="flex items-center gap-1 text-xs font-medium text-neutral-500 hover:text-neutral-800"
          >
            {advancedOpen ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            {t("lab.audience.advanced")}
          </button>
          {advancedOpen ? (
            <div className="mt-2 max-w-sm space-y-1.5">
              <Label htmlFor="lab-category">{t("lab.audience.category")}</Label>
              <Input
                id="lab-category"
                placeholder={t("lab.audience.categoryPlaceholder")}
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-neutral-400">{t("lab.audience.categoryHint")}</p>
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={() => onSubmit(buildAudience())}>{t("lab.audience.submit")}</Button>
          <Button variant="ghost" onClick={() => onSubmit({})}>
            {t("lab.audience.skip")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
