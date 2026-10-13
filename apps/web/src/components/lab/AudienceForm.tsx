import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { LabAudience } from "@shared";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { useT } from "../../lib/i18n";

interface AudienceFormProps {
  filename: string;
  initial: LabAudience;
  /** settings.project_category：品类字段的推荐默认（用户未填过时预填） */
  defaultCategory: string;
  onSubmit: (audience: LabAudience) => void;
}

/** 受众步：首屏性别/年龄/地区三字段（全可选），品类放高级折叠
 *  （默认继承 Settings 的项目品类）。留空字段不进诊断 prompt；整步可跳过。 */
export function AudienceForm({ filename, initial, defaultCategory, onSubmit }: AudienceFormProps) {
  const t = useT();
  const [gender, setGender] = useState(initial.gender ?? "");
  const [age, setAge] = useState(initial.age ?? "");
  const [region, setRegion] = useState(initial.region ?? "");
  const [category, setCategory] = useState(initial.category ?? defaultCategory);
  // 品类已预填时默认展开，让「继承了项目品类」这件事可见
  const [advancedOpen, setAdvancedOpen] = useState(
    Boolean(initial.category ?? defaultCategory),
  );

  const buildAudience = (): LabAudience => {
    const audience: LabAudience = {};
    if (gender.trim()) audience.gender = gender.trim();
    if (age.trim()) audience.age = age.trim();
    if (region.trim()) audience.region = region.trim();
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
            <Input
              id="lab-gender"
              placeholder={t("lab.audience.genderPlaceholder")}
              value={gender}
              onChange={(event) => setGender(event.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lab-age">{t("lab.audience.age")}</Label>
            <Input
              id="lab-age"
              placeholder={t("lab.audience.agePlaceholder")}
              value={age}
              onChange={(event) => setAge(event.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lab-region">{t("lab.audience.region")}</Label>
            <Input
              id="lab-region"
              placeholder={t("lab.audience.regionPlaceholder")}
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              autoComplete="off"
            />
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
